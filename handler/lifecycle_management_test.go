package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type lifecycleOfflineRunner struct{}

func (lifecycleOfflineRunner) Run(context.Context, ...string) (int, []byte, error) {
	return 1, nil, fmt.Errorf("Docker unavailable")
}

func lifecycleServer(t *testing.T) *Server {
	t.Helper()
	root, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(path, []byte(fmt.Sprintf("paths:\n  resources: %q\n", root)), 0600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(cfg)
	s.Runner = lifecycleOfflineRunner{}
	s.Preflight = []func() Check{func() Check { return Check{ID: "docker"} }}
	t.Cleanup(s.serviceCancel)
	return s
}

func lifecycleRequest(s *Server, action, origin, token string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8765/v1/control/"+action, nil)
	if origin != "" {
		r.Header.Set("Origin", origin)
	}
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	s.Routes().ServeHTTP(w, r)
	return w
}

func TestLifecycleManagementAuthorization(t *testing.T) {
	for _, tc := range []struct {
		action, origin string
		token          bool
		want           int
	}{
		{"stop", "http://127.0.0.1:8765", true, 202},
		{"stop", "", true, 202},
		{"restart", "http://127.0.0.1:8765", true, 202},
		{"restart", "", true, 403},
		{"stop", "https://hub.example", true, 403},
		{"restart", "https://hub.example", true, 403},
		{"stop", "http://127.0.0.1:8765", false, 401},
		{"restart", "http://127.0.0.1:8765", false, 401},
	} {
		t.Run(fmt.Sprintf("%s/%s/%v", tc.action, tc.origin, tc.token), func(t *testing.T) {
			s := lifecycleServer(t)
			s.Config.Runtime.AllowedOrigins = []string{"https://hub.example"}
			token := ""
			if tc.token {
				token = s.Token
			}
			w := lifecycleRequest(s, tc.action, tc.origin, token)
			if w.Code != tc.want {
				t.Fatalf("got %d %s, want %d", w.Code, w.Body, tc.want)
			}
			if s.restartServer != nil {
				defer s.restartServer.serviceCancel()
			}
		})
	}
}

func TestLifecycleManagementBusyAndAdmission(t *testing.T) {
	for _, busy := range []string{"queued", "running", "diagnostics", "resources", "request"} {
		for _, action := range []string{"stop", "restart"} {
			t.Run(busy+"/"+action, func(t *testing.T) {
				s := lifecycleServer(t)
				switch busy {
				case "queued", "running":
					s.jobs["test"] = &Job{Status: busy}
				case "diagnostics":
					s.executions = map[string]*execution{"diagnostics": {id: "diagnostics"}}
				case "resources":
					s.preparingResources = true
				case "request":
					s.admissionMu.RLock()
					defer s.admissionMu.RUnlock()
				}
				w := lifecycleRequest(s, action, "http://127.0.0.1:8765", s.Token)
				if w.Code != 409 || s.stopping || s.serviceContext().Err() != nil {
					t.Fatalf("busy transition: %d %s", w.Code, w.Body)
				}
			})
		}
	}
	s := lifecycleServer(t)
	if err := s.requestShutdown(); err != nil {
		t.Fatal(err)
	}
	if s.serviceContext().Err() != context.Canceled {
		t.Fatal("service context not canceled")
	}
	w := httptest.NewRecorder()
	s.Routes().ServeHTTP(w, httptest.NewRequest("GET", "http://127.0.0.1:8765/", nil))
	if w.Code != 503 {
		t.Fatalf("admitted request after shutdown: %d", w.Code)
	}
	if err := s.requestShutdown(); err == nil {
		t.Fatal("duplicate transition silently accepted")
	}
}

func TestLifecycleManagementReconstruction(t *testing.T) {
	s := lifecycleServer(t)
	s.Config.Handler.Port = 12345
	s.Config.Review.Port = 12346
	settings := localSettings{DataRoots: map[string]string{"test": t.TempDir()}, ImageSource: "mirror"}
	if err := writeSettings(s.Config, settings); err != nil {
		t.Fatal(err)
	}
	next, err := s.reconstruct()
	if err != nil {
		t.Fatal(err)
	}
	defer next.serviceCancel()
	if next.instanceID == s.instanceID || next.Token != s.Token || next.Config.Handler != s.Config.Handler || next.Config.Review != s.Config.Review || next.Config.Runtime.ImageSource != "mirror" || next.Config.DataRoots["test"] != settings.DataRoots["test"] {
		t.Fatalf("incorrect reconstruction: %+v", next.Config)
	}
	if err := os.WriteFile(s.Config.Source, []byte("[invalid yaml"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := s.requestLifecycle(true); err == nil || s.stopping {
		t.Fatalf("bad config accepted: %v", err)
	}
}

func TestLifecycleManagementDockerUnavailable(t *testing.T) {
	for _, restart := range []bool{false, true} {
		s := lifecycleServer(t)
		run := filepath.Join(s.Config.Paths.Runs, "old-run")
		if err := os.MkdirAll(run, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(run, "compose-model.yaml"), []byte("services: {}"), 0600); err != nil {
			t.Fatal(err)
		}
		if err := s.requestLifecycle(restart); err != nil {
			t.Fatalf("Docker blocked lifecycle: %v", err)
		}
		if s.restartServer != nil {
			s.restartServer.serviceCancel()
		}
		s.diagnosticsViewerContainer = "offline-viewer"
		if err := s.closeLifecycleViewer(); err != nil {
			t.Fatal(err)
		}
	}
}

func TestLifecycleManagementConfiguredTokenChange(t *testing.T) {
	s := lifecycleServer(t)
	raw, err := os.ReadFile(s.Config.Source)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(s.Config.Source, append(raw, []byte("runtime:\n  token: new-configured-token\n")...), 0600); err != nil {
		t.Fatal(err)
	}
	next, err := s.reconstruct()
	if err != nil {
		t.Fatal(err)
	}
	defer next.serviceCancel()
	if next.Token != "new-configured-token" {
		t.Fatal("configured token change ignored")
	}
}

func TestLifecycleManagementListenerFailure(t *testing.T) {
	s := lifecycleServer(t)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	s.Config.Handler.Port = listener.Addr().(*net.TCPAddr).Port
	ready := false
	next, err := serveInstance(s, "test", func() { ready = true }, nil)
	if err == nil || next != nil || ready || s.serviceContext().Err() != context.Canceled {
		t.Fatalf("listener failure silently accepted: %v", err)
	}
}

func TestLifecycleManagementStatus(t *testing.T) {
	s := lifecycleServer(t)
	s.jobs["queued"] = &Job{Status: "queued"}
	s.jobs["old"] = &Job{Status: "failed"}
	w := httptest.NewRecorder()
	s.Routes().ServeHTTP(w, httptest.NewRequest("GET", "http://127.0.0.1:8765/v1/status", nil))
	var status statusResponse
	if err := json.Unmarshal(w.Body.Bytes(), &status); err != nil {
		t.Fatal(err)
	}
	if status.InstanceID != s.instanceID || status.StartedAt.IsZero() || status.HandlerState != "running" || status.ActiveJobs != 1 || status.Docker.ID != "docker" {
		t.Fatalf("bad status: %+v", status)
	}
}

func TestLifecycleManagementServeGenerations(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CACHE_HOME", home)
	t.Setenv("LOCALAPPDATA", home)
	s := lifecycleServer(t)
	// Reserve two distinct ports together, then release them for serveInstance.
	a, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	b, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		a.Close()
		t.Fatal(err)
	}
	s.Config.Handler.Port = a.Addr().(*net.TCPAddr).Port
	s.Config.Review.Port = b.Addr().(*net.TCPAddr).Port
	a.Close()
	b.Close()
	type outcome struct {
		next *Server
		err  error
	}
	firstID, token := s.instanceID, s.Token
	for generation := 0; generation < 2; generation++ {
		ready := make(chan struct{})
		done := make(chan outcome, 1)
		taskDone := make(chan struct{})
		current := s
		go func() {
			next, err := serveInstance(current, "test-digest", func() { close(ready) }, nil, func() { <-current.serviceContext().Done(); close(taskDone) })
			done <- outcome{next, err}
		}()
		select {
		case <-ready:
		case out := <-done:
			t.Fatalf("startup: %v", out.err)
		case <-time.After(5 * time.Second):
			t.Fatal("startup timeout")
		}
		session, err := readSession(s.Config)
		if err != nil || session.InstanceID != s.instanceID || session.Token != token || session.PID != os.Getpid() {
			t.Fatalf("session: %+v %v", session, err)
		}
		if generation == 1 && session.InstanceID == firstID {
			t.Fatal("instance id unchanged")
		}
		action := "restart"
		if generation == 1 {
			action = "stop"
		}
		req, err := http.NewRequest("POST", session.URL+"/v1/control/"+action, nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Origin", session.URL)
		req.Header.Set("Authorization", "Bearer "+token)
		client := localHTTPClient()
		response, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		client.CloseIdleConnections()
		if response.StatusCode != 202 {
			t.Fatalf("control returned %d", response.StatusCode)
		}
		select {
		case out := <-done:
			if out.err != nil {
				t.Fatal(out.err)
			}
			s = out.next
		case <-time.After(5 * time.Second):
			t.Fatal("shutdown timeout")
		}
		select {
		case <-taskDone:
		default:
			t.Fatal("background task leaked")
		}
		if !listenerClosed(session.URL) || !listenerClosed(session.ReviewURL) {
			t.Fatal("listeners remain open")
		}
	}
	if s != nil {
		t.Fatal("stop reconstructed server")
	}
}
