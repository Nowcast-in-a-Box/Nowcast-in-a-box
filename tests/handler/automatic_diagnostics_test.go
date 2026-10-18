package handler_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

type failingDiagnosticRunner struct{ runtimeRunner }

func (f *failingDiagnosticRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	for i, arg := range args {
		if arg == "-f" && i+1 < len(args) && strings.HasSuffix(args[i+1], "compose.yaml") && args[len(args)-1] == "task" {
			return 1, []byte("diagnostic crash"), nil
		}
	}
	return f.runtimeRunner.Run(ctx, args...)
}

func automaticServer(t *testing.T) (*handler.Server, handler.Config) {
	t.Helper()
	cfg := handler.DefaultConfig()
	cfg.Paths.Runs, cfg.Paths.Artifacts, cfg.Paths.DataRoot = t.TempDir(), t.TempDir(), t.TempDir()
	cfg.Runtime.Token = "token"
	cfg.Runtime.AtlasRevision = strings.Repeat("a", 40)
	for _, name := range []string{cfg.Runtime.DataFile, cfg.Runtime.CatalogFile} {
		p := filepath.Join(cfg.Paths.DataRoot, name)
		if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte("fixture"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return handler.NewServer(cfg), cfg
}

func TestAutomaticDiagnosticsFailureKeepsModelStageAndExactFailedOperation(t *testing.T) {
	server, cfg := automaticServer(t)
	server.Runner = &failingDiagnosticRunner{runtimeRunner: runtimeRunner{root: cfg.Paths.Runs}}
	job := waitJob(t, server, submitSelection(t, server, "S700001", []string{"wadepre"}, "metrics", "automatic-failure").ID, "partial")
	if job.Stage != "finished" || job.DiagnosticOperationID == "" || job.ReportEntry != "" || !strings.Contains(job.Error, "diagnostics") {
		t.Fatalf("wrong terminal state: %+v", job)
	}
	if _, err := os.Stat(filepath.Join(cfg.Paths.Runs, job.ID, "compose-wadepre.yaml")); err != nil {
		t.Fatal("model stage missing", err)
	}
	raw, err := os.ReadFile(filepath.Join(cfg.Paths.Runs, job.ID, "logs", "diagnostics-"+job.DiagnosticOperationID, "status.json"))
	if err != nil {
		t.Fatal(err)
	}
	var operation handler.DiagnosticOperation
	if err := json.Unmarshal(raw, &operation); err != nil {
		t.Fatal(err)
	}
	if operation.Status != "failed" || operation.ReportEntry != "" || operation.RunID != job.ID {
		t.Fatalf("wrong operation: %+v", operation)
	}
}

func TestInterruptedOperationIsObservableWithoutRestartingIt(t *testing.T) {
	server, cfg := automaticServer(t)
	directory := filepath.Join(cfg.Paths.Runs, "saved-run")
	operationDir := filepath.Join(directory, "logs", "diagnostics-20261017T010000Z-abcdef")
	if err := os.MkdirAll(operationDir, 0o700); err != nil {
		t.Fatal(err)
	}
	manifest := []byte(`{"schema":"nib.run.v1","id":"saved-run","diagnostics":[],"forecasts":{"wadepre":{}}}`)
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), manifest, 0o600); err != nil {
		t.Fatal(err)
	}
	state := handler.DiagnosticOperation{ID: "20261017T010000Z-abcdef", RunID: "saved-run", Status: "running", InstanceID: "old-process"}
	raw, _ := json.Marshal(state)
	if err := os.WriteFile(filepath.Join(operationDir, "status.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest("GET", "http://127.0.0.1/v1/runs/saved-run", nil)
	request.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	if response.Code != 200 {
		t.Fatal(response.Code, response.Body.String())
	}
	var result struct {
		Operation handler.DiagnosticOperation `json:"operation"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Operation.Status != "interrupted" || result.Operation.ReportEntry != "" {
		t.Fatalf("stale operation: %+v", result.Operation)
	}
	if !server.ExecutionAvailableForTest() {
		t.Fatal("observing operation started execution")
	}
}

func TestSuccessfulAutomaticRerunPreservesPreviousReportRegistration(t *testing.T) {
	server, cfg := automaticServer(t)
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	first := waitJob(t, server, submitSelection(t, server, "S700002", []string{"wadepre"}, "metrics", "first").ID, "succeeded")
	deadline := time.Now().Add(time.Second)
	for !server.ExecutionAvailableForTest() {
		if time.Now().After(deadline) {
			t.Fatal("execution not released")
		}
		time.Sleep(time.Millisecond)
	}
	second := waitJob(t, server, submitSelection(t, server, "S700002", []string{"wadepre"}, "metrics", "second").ID, "succeeded")
	if second.ID != first.ID || second.DiagnosticOperationID == first.DiagnosticOperationID || second.ReportEntry == first.ReportEntry {
		t.Fatal("operation identity was reused", first, second)
	}
	raw, err := os.ReadFile(filepath.Join(cfg.Paths.Runs, first.ID, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest struct {
		Diagnostics []struct {
			OperationID string `json:"operation_id"`
		}
	}
	if err := json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	if len(manifest.Diagnostics) != 2 || manifest.Diagnostics[0].OperationID != first.DiagnosticOperationID || manifest.Diagnostics[1].OperationID != second.DiagnosticOperationID {
		t.Fatal("history registration lost", string(raw))
	}
}
