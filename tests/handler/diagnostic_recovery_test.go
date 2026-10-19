package handler_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

type recoveryRunner struct {
	mu    sync.Mutex
	state string
	runtimeRunner
}

func (f *recoveryRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	if len(args) > 0 && args[0] == "ps" {
		f.mu.Lock()
		defer f.mu.Unlock()
		return 0, []byte(f.state), nil
	}
	return f.runtimeRunner.Run(ctx, args...)
}
func TestRecoveryRefusesPriorContainerBeforeNewDiagnosticAdmission(t *testing.T) {
	server, cfg := automaticServer(t)
	directory := filepath.Join(cfg.Paths.Runs, "saved-run")
	operationDir := filepath.Join(directory, "logs", "diagnostics-old-operation")
	if err := os.MkdirAll(operationDir, 0o700); err != nil {
		t.Fatal(err)
	}
	raw := []byte(`{"schema":"nib.run.v1","id":"saved-run","data":{"id":"sevir"},"forecasts":{"wadepre":{}},"diagnostics":[]}`)
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	raw, _ = json.Marshal(handler.DiagnosticOperation{ID: "old-operation", RunID: "saved-run", Status: "running", InstanceID: "dead-handler"})
	if err := os.WriteFile(filepath.Join(operationDir, "status.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	runner := &recoveryRunner{state: "nib-diag-old-operation running\n", runtimeRunner: runtimeRunner{root: cfg.Paths.Runs}}
	server.Runner = runner
	submit := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "http://127.0.0.1/v1/runs/saved-run/diagnostics", strings.NewReader(`{"models":["wadepre"],"methods":["mae"],"levels":[16],"half_windows":[0]}`))
		req.Header.Set("Authorization", "Bearer token")
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, req)
		return response
	}
	if blocked := submit(); blocked.Code != 409 || !strings.Contains(blocked.Body.String(), "prior Docker project") {
		t.Fatal(blocked.Code, blocked.Body.String())
	}
	runner.mu.Lock()
	runner.state = "nib-diag-old-operation exited\n"
	runner.mu.Unlock()
	if allowed := submit(); allowed.Code != 202 {
		t.Fatal(allowed.Code, allowed.Body.String())
	}
	deadline := time.Now().Add(time.Second)
	for !server.ExecutionAvailableForTest() {
		if time.Now().After(deadline) {
			t.Fatal("retry stalled")
		}
		time.Sleep(time.Millisecond)
	}
	runner.runtimeRunner.mu.Lock()
	defer runner.runtimeRunner.mu.Unlock()
	for _, call := range runner.calls {
		if strings.Contains(strings.Join(call, " "), "compose-wadepre") {
			t.Fatal("retry reran inference")
		}
	}
}

func TestLatestOperationPointerOverridesSameSecondRandomSuffixOrder(t *testing.T) {
	server, cfg := automaticServer(t)
	directory := filepath.Join(cfg.Paths.Runs, "saved-run")
	if err := os.MkdirAll(filepath.Join(directory, "logs"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), []byte(`{"schema":"nib.run.v1","id":"saved-run","diagnostics":[]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"20261017T120000Z-ffff", "20261017T120000Z-0000"} {
		state := handler.DiagnosticOperation{ID: id, RunID: "saved-run", Status: "failed"}
		if strings.HasSuffix(id, "ffff") {
			state.Status = "succeeded"
			state.ReportEntry = "diagnostics/report/old/index.html"
		}
		p := filepath.Join(directory, "logs", "diagnostics-"+id)
		if err := os.Mkdir(p, 0o700); err != nil {
			t.Fatal(err)
		}
		raw, _ := json.Marshal(state)
		if err := os.WriteFile(filepath.Join(p, "status.json"), raw, 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(directory, "logs", "latest-diagnostics.json"), raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	request := httptest.NewRequest("GET", "http://127.0.0.1/v1/runs/saved-run", nil)
	request.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	var manifest struct{ Operation handler.DiagnosticOperation }
	if err := json.Unmarshal(response.Body.Bytes(), &manifest); err != nil {
		t.Fatal(err)
	}
	if manifest.Operation.ID != "20261017T120000Z-0000" || manifest.Operation.ReportEntry != "" {
		t.Fatal(response.Body.String())
	}
}
