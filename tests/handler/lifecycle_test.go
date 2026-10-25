package handler_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
	"gopkg.in/yaml.v3"
)

func stopRequest(server *handler.Server, origin, token string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/v1/control/stop", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Origin", origin)
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	return response
}

func TestIdentityIsLightweightAndContainsNoCredential(t *testing.T) {
	server, cfg, _ := liveHandler(t)
	server.Preflight = []func() handler.Check{func() handler.Check { panic("identity must not probe Docker") }}
	identity := func(s *handler.Server) map[string]any {
		response := httptest.NewRecorder()
		s.Routes().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://127.0.0.1/v1/identity", nil))
		if response.Code != 200 || strings.Contains(response.Body.String(), cfg.Runtime.Token) {
			t.Fatal(response.Code, response.Body.String())
		}
		var body map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
			t.Fatal(err)
		}
		return body
	}
	first, again, restarted := identity(server), identity(server), identity(handler.NewServer(cfg))
	if first["service"] != "nib-handler" || first["protocol"] != float64(1) || first["state"] != "running" {
		t.Fatal(first)
	}
	if first["config_id"] != restarted["config_id"] || first["instance_id"] != again["instance_id"] || first["instance_id"] == restarted["instance_id"] {
		t.Fatal("configuration and instance identities are not separated")
	}
}

func TestIdentityIsStableWhenMissingDirectoriesAreCreatedUnderSymlink(t *testing.T) {
	base := t.TempDir()
	actual, alias := filepath.Join(base, "actual"), filepath.Join(base, "alias")
	if err := os.Mkdir(actual, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(actual, alias); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	cfg := handler.DefaultConfig()
	cfg.Source = filepath.Join(alias, "config.yaml")
	cfg.Paths.Runs, cfg.Paths.Artifacts = filepath.Join(alias, "runs"), filepath.Join(alias, "artifacts")
	if err := os.WriteFile(cfg.Source, []byte(""), 0o600); err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	identity := func() string {
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://127.0.0.1/v1/identity", nil))
		var value map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &value); err != nil {
			t.Fatal(err)
		}
		return value["config_id"].(string)
	}
	before := identity()
	for _, directory := range []string{cfg.Paths.Runs, cfg.Paths.Artifacts} {
		if err := os.Mkdir(directory, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if identity() != before {
		t.Fatal("directory creation changed process identity")
	}
}

func TestStatusUsesConfiguredPortAndRejectsDifferentConfiguration(t *testing.T) {
	_, cfg, _ := liveHandler(t)
	if code := handler.Main([]string{"status", "-config", cfg.Source}); code != 0 {
		t.Fatalf("configured live instance not found: %d", code)
	}
	other := cfg
	other.Source = filepath.Join(t.TempDir(), "other.yaml")
	raw, _ := yaml.Marshal(other)
	if err := os.WriteFile(other.Source, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	if code := handler.Main([]string{"status", "-config", other.Source}); code != 1 {
		t.Fatalf("another configuration was reused: %d", code)
	}
}

func TestStatusRejectsChangedExecutionSettings(t *testing.T) {
	for name, change := range map[string]func(*handler.Config){
		"resources root": func(c *handler.Config) { c.Paths.Resources = t.TempDir() },
		"Docker context": func(c *handler.Config) { c.Runtime.DockerContext = "other" },
		"Atlas revision": func(c *handler.Config) { c.Runtime.AtlasRevision = strings.Repeat("b", 40) },
	} {
		t.Run(name, func(t *testing.T) {
			_, cfg, _ := liveHandler(t)
			next, err := handler.Load(cfg.Source)
			if err != nil {
				t.Fatal(err)
			}
			change(&next)
			raw, _ := yaml.Marshal(next)
			if err := os.WriteFile(cfg.Source, raw, 0o600); err != nil {
				t.Fatal(err)
			}
			if code := handler.Main([]string{"start", "-config", cfg.Source}); code != 1 {
				t.Fatalf("changed %s was silently reused: %d", name, code)
			}
		})
	}
}

func TestStatusAllowsMutableOriginAndDataRootSettings(t *testing.T) {
	_, cfg, _ := liveHandler(t)
	cfg.Runtime.AllowedOrigins = []string{"http://other.example:8123"}
	raw, _ := yaml.Marshal(cfg)
	if err := os.WriteFile(cfg.Source, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	if code := handler.Main([]string{"status", "-config", cfg.Source}); code != 0 {
		t.Fatal("mutable settings changed process compatibility")
	}
}

func TestStatusRejectsGenericHTTPServiceAndHasTimeout(t *testing.T) {
	for _, unresponsive := range []bool{false, true} {
		httpServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if unresponsive {
				<-r.Context().Done()
				return
			}
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		}))
		cfg := handler.DefaultConfig()
		cfg.Handler.Port = httpServer.Listener.Addr().(*net.TCPAddr).Port
		cfg.Source = filepath.Join(t.TempDir(), "config.yaml")
		raw, _ := yaml.Marshal(cfg)
		if err := os.WriteFile(cfg.Source, raw, 0o600); err != nil {
			t.Fatal(err)
		}
		started := time.Now()
		code := handler.Main([]string{"status", "-config", cfg.Source})
		httpServer.Close()
		if code != 1 {
			t.Fatal("ordinary or unresponsive HTTP service was treated as Handler")
		}
		if time.Since(started) > 4*time.Second {
			t.Fatal("identity probe had no bounded timeout")
		}
	}
}

func TestStopRequiresLocalCLIAndIgnoresHistoricalJobs(t *testing.T) {
	server, cfg, _ := liveHandler(t)
	oldRun := filepath.Join(cfg.Paths.Runs, "old-run")
	if err := os.MkdirAll(oldRun, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(oldRun, "job.json"), []byte(`{"id":"old-run","status":"running"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		origin, token string
		status        int
	}{
		{"http://hub.example:8123", "local-token", 403},
		{"", "wrong", 401},
		{"", "local-token", 202},
		{"", "local-token", 202},
	} {
		if response := stopRequest(server, test.origin, test.token); response.Code != test.status {
			t.Fatal(response.Code, response.Body.String())
		}
	}
}

type blockedLifecycleRunner struct {
	entered chan struct{}
	release chan struct{}
	once    sync.Once
}

func (runner *blockedLifecycleRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	for _, arg := range args {
		if arg == "up" {
			runner.once.Do(func() { close(runner.entered) })
			select {
			case <-runner.release:
				return 1, nil, fmt.Errorf("test execution released")
			case <-ctx.Done():
				return 1, nil, ctx.Err()
			}
		}
	}
	return 0, nil, nil
}

func TestStopBlocksActiveDiagnosticAttempt(t *testing.T) {
	server, cfg, _ := liveHandler(t)
	run := filepath.Join(cfg.Paths.Runs, "saved-run")
	if err := os.MkdirAll(filepath.Join(run, "forecasts"), 0o755); err != nil {
		t.Fatal(err)
	}
	manifest := `{"schema":"nib.run.v1","id":"saved-run","data":{"id":"cma_radar"},"forecasts":{"nowcastnet":{}},"diagnostics":[]}`
	if err := os.WriteFile(filepath.Join(run, "manifest.json"), []byte(manifest), 0o600); err != nil {
		t.Fatal(err)
	}
	runner := &blockedLifecycleRunner{entered: make(chan struct{}), release: make(chan struct{})}
	server.Runner = runner
	request := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/v1/runs/saved-run/diagnostics", strings.NewReader(`{"models":["nowcastnet"],"methods":["mae"],"levels":[10],"half_windows":[0]}`))
	request.Header.Set("Authorization", "Bearer local-token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	if response.Code != 202 {
		t.Fatal(response.Code, response.Body.String())
	}
	select {
	case <-runner.entered:
	case <-time.After(time.Second):
		t.Fatal("diagnostic attempt never started")
	}
	blocked := stopRequest(server, "", "local-token")
	close(runner.release)
	if blocked.Code != 409 {
		t.Fatal(blocked.Code, blocked.Body.String())
	}
	deadline := time.Now().Add(time.Second)
	for !server.ExecutionAvailableForTest() {
		if time.Now().After(deadline) {
			t.Fatal("diagnostics did not finish")
		}
		time.Sleep(time.Millisecond)
	}
	if response := stopRequest(server, "", "local-token"); response.Code != 202 {
		t.Fatal(response.Code, response.Body.String())
	}
}

func TestLiveRunCannotBeReplacedByAnotherIdempotencyKey(t *testing.T) {
	server, cfg, _ := liveHandler(t)
	cfg.Runtime.AtlasRevision = strings.Repeat("a", 40)
	writeSevirData(t, &cfg)
	server.Config = cfg
	runner := &blockedLifecycleRunner{entered: make(chan struct{}), release: make(chan struct{})}
	server.Runner = runner
	raw, digest := packageFixture(t, "wadepre-sevir", nil)
	body, _ := json.Marshal(map[string]any{"package": raw, "sha256": digest})
	submit := func(key string) *httptest.ResponseRecorder {
		request := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/v1/jobs", bytes.NewReader(body))
		request.Header.Set("Authorization", "Bearer local-token")
		request.Header.Set("Idempotency-Key", key)
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, request)
		return response
	}
	first := submit("first-key")
	if first.Code != http.StatusAccepted {
		close(runner.release)
		t.Fatal(first.Code, first.Body.String())
	}
	var job handler.Job
	if err := json.Unmarshal(first.Body.Bytes(), &job); err != nil {
		close(runner.release)
		t.Fatal(err)
	}
	defer func() { close(runner.release); waitJob(t, server, job.ID, "failed") }()
	select {
	case <-runner.entered:
	case <-time.After(time.Second):
		t.Fatal("job never started")
	}
	if duplicate := submit("second-key"); duplicate.Code != http.StatusConflict {
		t.Fatal("live job was replaced", duplicate.Code, duplicate.Body.String())
	}
	if retry := submit("first-key"); retry.Code != http.StatusOK {
		t.Fatal(retry.Code)
	}
	historical := job
	historical.IdempotencyKey = "historical-key"
	historical.Status = "queued"
	legacy, _ := json.Marshal(historical)
	if err := os.WriteFile(filepath.Join(cfg.Paths.Runs, job.ID, "job.json"), legacy, 0o600); err != nil {
		t.Fatal(err)
	}
	if retry := submit("historical-key"); retry.Code != http.StatusConflict {
		t.Fatal("historical idempotency retry replaced a live job", retry.Code)
	}
	if stopped := stopRequest(server, "", "local-token"); stopped.Code != http.StatusConflict {
		t.Fatal(stopped.Code)
	}
}

func TestHistoricalPollingCannotOverwriteConcurrentAdmission(t *testing.T) {
	for attempt := 0; attempt < 30; attempt++ {
		t.Run(fmt.Sprint(attempt), func(t *testing.T) {
			server, cfg, _ := liveHandler(t)
			cfg.Runtime.AtlasRevision = strings.Repeat("a", 40)
			writeSevirData(t, &cfg)
			server.Config = cfg
			runner := &blockedLifecycleRunner{entered: make(chan struct{}), release: make(chan struct{})}
			server.Runner = runner
			raw, digest := packageFixture(t, "wadepre-sevir", nil)
			request, err := handler.ParseRuntimePackage(raw, digest, server.Resources)
			if err != nil {
				t.Fatal(err)
			}
			directory := filepath.Join(cfg.Paths.Runs, "historical-run")
			if err := os.Mkdir(directory, 0o700); err != nil {
				t.Fatal(err)
			}
			old, _ := json.Marshal(handler.Job{ID: "historical-run", Status: "running", Request: request, IdempotencyKey: "old-key"})
			if err := os.WriteFile(filepath.Join(directory, "job.json"), old, 0o600); err != nil {
				t.Fatal(err)
			}
			body, _ := json.Marshal(map[string]any{"package": raw, "sha256": digest})
			admit := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/v1/jobs", bytes.NewReader(body))
			admit.Header.Set("Authorization", "Bearer local-token")
			admit.Header.Set("Idempotency-Key", "new-key")
			poll := httptest.NewRequest(http.MethodGet, "http://127.0.0.1/v1/jobs/historical-run", nil)
			poll.Header.Set("Authorization", "Bearer local-token")
			admission, observed := httptest.NewRecorder(), httptest.NewRecorder()
			var done sync.WaitGroup
			start := make(chan struct{})
			done.Add(2)
			go func() { defer done.Done(); <-start; server.Routes().ServeHTTP(admission, admit) }()
			go func() { defer done.Done(); <-start; server.Routes().ServeHTTP(observed, poll) }()
			close(start)
			done.Wait()
			var admitted handler.Job
			if err := json.Unmarshal(admission.Body.Bytes(), &admitted); err != nil {
				close(runner.release)
				t.Fatal(err)
			}
			defer func() { close(runner.release); waitJob(t, server, admitted.ID, "failed") }()
			if admission.Code != http.StatusAccepted || observed.Code != http.StatusOK {
				t.Fatal(admission.Code, observed.Code)
			}
			saved, err := os.ReadFile(filepath.Join(directory, "job.json"))
			if err != nil {
				t.Fatal(err)
			}
			var current handler.Job
			if err := json.Unmarshal(saved, &current); err != nil {
				t.Fatal(err)
			}
			if current.IdempotencyKey != "old-key" || current.Status != "interrupted" || admitted.ID == "historical-run" {
				t.Fatal("historical polling and new admission must retain independent records", string(saved), admitted.ID)
			}
		})
	}
}

func TestJobAdmissionCannotRaceSuccessfulStop(t *testing.T) {
	for attempt := 0; attempt < 12; attempt++ {
		server, cfg, _ := liveHandler(t)
		cfg.Runtime.AtlasRevision = strings.Repeat("a", 40)
		writeSevirData(t, &cfg)
		server.Config = cfg
		runner := &blockedLifecycleRunner{entered: make(chan struct{}), release: make(chan struct{})}
		server.Runner = runner
		raw, digest := packageFixture(t, "wadepre-sevir", nil)
		body, _ := json.Marshal(map[string]any{"package": raw, "sha256": digest})
		request := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/v1/jobs", bytes.NewReader(body))
		request.Header.Set("Authorization", "Bearer local-token")
		admission := httptest.NewRecorder()
		var stopped *httptest.ResponseRecorder
		var done sync.WaitGroup
		start := make(chan struct{})
		done.Add(2)
		go func() { defer done.Done(); <-start; server.Routes().ServeHTTP(admission, request) }()
		go func() { defer done.Done(); <-start; stopped = stopRequest(server, "", "local-token") }()
		close(start)
		done.Wait()
		close(runner.release)
		if stopped.Code == 202 {
			if admission.Code != 503 {
				t.Fatalf("stop accepted but job admitted: %d", admission.Code)
			}
		} else {
			if stopped.Code != 409 || admission.Code != 202 {
				t.Fatalf("stop=%d %s, admission=%d %s", stopped.Code, stopped.Body.String(), admission.Code, admission.Body.String())
			}
			var job handler.Job
			if err := json.Unmarshal(admission.Body.Bytes(), &job); err != nil {
				t.Fatal(err)
			}
			waitJob(t, server, job.ID, "failed")
		}
	}
}
