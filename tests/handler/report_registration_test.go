package handler_test

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

type invalidReportRunner struct {
	runtimeRunner
	mode string
}

func (f *invalidReportRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	code, out, err := f.runtimeRunner.Run(ctx, args...)
	for i, arg := range args {
		if arg != "-f" || i+1 >= len(args) || filepath.Base(args[i+1]) != "compose.yaml" || args[len(args)-1] != "task" {
			continue
		}
		directory := filepath.Dir(filepath.Dir(filepath.Dir(args[i+1])))
		p := filepath.Join(directory, "manifest.json")
		raw, _ := os.ReadFile(p)
		var m map[string]any
		if json.Unmarshal(raw, &m) != nil {
			continue
		}
		attempts, _ := m["diagnostics"].([]any)
		if len(attempts) == 0 {
			continue
		}
		report := attempts[len(attempts)-1].(map[string]any)
		if f.mode == "component-failure" {
			m["diagnostics"] = append(attempts, map[string]any{
				"kind": "visualization", "status": "failed", "operation_id": report["operation_id"],
				"error": "prepare the offline global terrain and WMO basemap cache",
			})
			raw, _ = json.Marshal(m)
			_ = os.WriteFile(p, raw, 0o600)
			return 1, out, nil
		}
		if f.mode == "wrong-schema" || f.mode == "wrong-id" {
			if f.mode == "wrong-schema" {
				m["schema"] = "bad"
			} else {
				m["id"] = "other-run"
			}
			raw, _ = json.Marshal(m)
			_ = os.WriteFile(p, raw, 0o600)
		} else if f.mode == "missing" {
			_ = os.Remove(filepath.Join(directory, filepath.FromSlash(report["entry"].(string))))
		} else {
			delete(report, "path")
			raw, _ = json.Marshal(m)
			_ = os.WriteFile(p, raw, 0o600)
		}
	}
	return code, out, err
}
func TestAutomaticDiagnosticsDoesNotClaimMissingOrUnregisteredReport(t *testing.T) {
	for _, mode := range []string{"unregistered", "wrong-schema", "wrong-id"} {
		t.Run(mode, func(t *testing.T) {
			server, cfg := automaticServer(t)
			server.Runner = &invalidReportRunner{runtimeRunner: runtimeRunner{root: cfg.Paths.Runs}, mode: mode}
			job := waitJob(t, server, submitSelection(t, server, "S700003", []string{"wadepre"}, "metrics", mode).ID, "partial")
			if job.ReportEntry != "" || job.Error == "" {
				t.Fatalf("unservable report reported as success: %+v", job)
			}
			var state handler.DiagnosticOperation
			raw, err := os.ReadFile(filepath.Join(cfg.Paths.Runs, job.ID, "logs", "diagnostics-"+job.DiagnosticOperationID, "status.json"))
			if err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(raw, &state); err != nil {
				t.Fatal(err)
			}
			if state.Status != "failed" || state.ReportEntry != "" {
				t.Fatalf("wrong operation: %+v", state)
			}
		})
	}
}

func TestDiagnosticFailureExposesComponentCause(t *testing.T) {
	server, cfg := automaticServer(t)
	server.Runner = &invalidReportRunner{runtimeRunner: runtimeRunner{root: cfg.Paths.Runs}, mode: "component-failure"}
	job := waitJob(t, server, submitSelection(t, server, "S700003", []string{"wadepre"}, "metrics", "component-cause").ID, "partial")
	raw, err := os.ReadFile(filepath.Join(cfg.Paths.Runs, job.ID, "logs", "diagnostics-"+job.DiagnosticOperationID, "status.json"))
	if err != nil {
		t.Fatal(err)
	}
	var state handler.DiagnosticOperation
	if err := json.Unmarshal(raw, &state); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(state.Error, "offline global terrain and WMO basemap cache") || strings.Contains(state.Error, "<nil>") {
		t.Fatalf("operation must expose actionable failure without nil error: %+v", state)
	}
}
