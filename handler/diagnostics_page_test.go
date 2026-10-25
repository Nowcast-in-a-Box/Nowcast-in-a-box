package handler

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func TestDiagnosticsProxyUsesTheConfiguredLoopbackAddress(t *testing.T) {
	viewer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/visualization" {
			t.Errorf("path=%q", r.URL.Path)
		}
		_, _ = io.WriteString(w, "viewer")
	}))
	defer viewer.Close()

	host, portText, err := net.SplitHostPort(viewer.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(portText)
	if err != nil {
		t.Fatal(err)
	}
	server := &Server{Config: Config{Diagnostics: Addr{Host: host, Port: port}}}
	request := httptest.NewRequest(http.MethodGet, "http://127.0.0.1/visualization", nil)
	response := httptest.NewRecorder()
	server.diagnosticsProxy().ServeHTTP(response, request)
	if response.Code != http.StatusOK || response.Body.String() != "viewer" {
		t.Fatalf("status=%d body=%q", response.Code, response.Body.String())
	}
}

type recordingRunner struct {
	calls [][]string
}

func (r *recordingRunner) Run(_ context.Context, args ...string) (int, []byte, error) {
	r.calls = append(r.calls, append([]string(nil), args...))
	return 0, []byte("container-id\n"), nil
}

func TestDiagnosticsViewerStartsOnceFromTheConfiguredPort(t *testing.T) {
	viewer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"ok":true}`)
	}))
	defer viewer.Close()
	host, portText, err := net.SplitHostPort(viewer.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(portText)
	if err != nil {
		t.Fatal(err)
	}
	runs := t.TempDir()
	runID := "run-one"
	if err := os.MkdirAll(filepath.Join(runs, runID), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(runs, runID, "manifest.json"), []byte(`{"diagnostics":[]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	runner := &recordingRunner{}
	server := &Server{
		Config: Config{
			Paths:       Paths{Runs: runs},
			Diagnostics: Addr{Host: host, Port: port},
		},
		Runner: runner,
		Resources: &Resources{
			Diagnostics: &DiagnosticsResource{Container: ResourceContainer{Image: "nib-diagnostics:test"}},
		},
	}
	if err := server.ensureDiagnosticsViewer(runID, true); err != nil {
		t.Fatal(err)
	}
	if len(runner.calls) != 1 {
		t.Fatalf("calls=%d", len(runner.calls))
	}
	joined := strings.Join(runner.calls[0], " ")
	for _, want := range []string{"--entrypoint python", "--port " + portText, "-p " + net.JoinHostPort(host, portText) + ":" + portText, "diagnostics.diagnostics_server"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing %q in %s", want, joined)
		}
	}
	server.stopDiagnosticsViewer()
	if len(runner.calls) != 2 || !strings.Contains(strings.Join(runner.calls[1], " "), "rm -f") {
		t.Fatalf("stop calls=%v", runner.calls)
	}
}
