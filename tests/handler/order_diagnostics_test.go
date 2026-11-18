package handler_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestStructuredDiagnosticsUseSavedForecastSubsets(t *testing.T) {
	cfg := consoleConfig(t)
	run := filepath.Join(cfg.Paths.Runs, "saved-run")
	if err := os.MkdirAll(run, 0o700); err != nil {
		t.Fatal(err)
	}
	manifest := `{"schema":"nib.run.v1","id":"saved-run","data":{"id":"sevir"},"forecasts":{"wadepre":{"path":"forecasts/wadepre.npz"},"nowcastnet":{"path":"forecasts/nowcastnet.npz"}},"diagnostics":[]}`
	if err := os.WriteFile(filepath.Join(run, "order.yaml"), []byte(orderYAML(t, orderFixture())), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(run, "manifest.json"), []byte(manifest), 0o600); err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	server.Runner = forbiddenOrderRunner{t}
	for name, body := range map[string]any{
		"nothing selected":              map[string]any{},
		"verification absent forecast":  map[string]any{"verification": map[string]any{"models": []string{"missing"}, "methods": []string{"mae"}}},
		"visualization absent forecast": map[string]any{"visualization": map[string]any{"models": []string{"missing"}}},
		"duplicate models":              map[string]any{"visualization": map[string]any{"models": []string{"wadepre", "wadepre"}}},
		"mixed formats":                 map[string]any{"models": []string{"wadepre"}, "verification": map[string]any{"models": []string{"wadepre"}, "methods": []string{"mae"}}},
	} {
		t.Run(name, func(t *testing.T) {
			out, _ := triggerDo(t, server, "POST", "/v1/runs/saved-run/diagnostics", body)
			if out.Code != 422 {
				t.Fatalf("invalid diagnostics: %d %s", out.Code, out.Body.String())
			}
		})
	}
	runner := &runtimeRunner{root: cfg.Paths.Runs}
	server.Runner = runner
	for _, selection := range []map[string]any{
		{"verification": map[string]any{"models": []string{"wadepre"}, "methods": []string{"mae"}}, "visualization": map[string]any{"models": []string{"nowcastnet"}}},
		{"visualization": map[string]any{"models": []string{"wadepre"}}},
	} {
		out, operation := triggerDo(t, server, "POST", "/v1/runs/saved-run/diagnostics", selection)
		if out.Code != 202 {
			t.Fatalf("structured retry: %d %s", out.Code, out.Body.String())
		}
		deadline := time.Now().Add(2 * time.Second)
		for !server.ExecutionAvailableForTest() {
			if time.Now().After(deadline) {
				t.Fatal("diagnostic retry did not finish")
			}
			time.Sleep(time.Millisecond)
		}
		raw, err := os.ReadFile(filepath.Join(run, "logs", "diagnostics-"+operation["id"].(string), "request.json"))
		if err != nil {
			t.Fatal(err)
		}
		var request map[string]any
		if err := json.Unmarshal(raw, &request); err != nil {
			t.Fatal(err)
		}
		visualization := request["visualization"].(map[string]any)
		want := selection["visualization"].(map[string]any)["models"].([]string)[0]
		if !reflect.DeepEqual(visualization["models"], []any{want}) {
			t.Fatalf("visualization subset lost: %#v", request)
		}
		if selection["verification"] != nil {
			if !reflect.DeepEqual(request["verification_models"], []any{"wadepre"}) || request["metrics"] == nil {
				t.Fatalf("verification subset lost: %#v", request)
			}
		} else if request["metrics"] != nil {
			t.Fatalf("visualization-only retry added verification: %#v", request)
		}
	}
	out, detail := triggerDo(t, server, "GET", "/v1/runs/saved-run", nil)
	operations, _ := detail["operations"].([]any)
	if out.Code != 200 || len(operations) != 2 || detail["diagnostics_status"] != "succeeded" {
		t.Fatalf("retry history: %d %s", out.Code, out.Body.String())
	}
	for _, value := range operations {
		if value.(map[string]any)["status"] != "succeeded" {
			t.Fatalf("retry failed: %#v", value)
		}
	}
	runner.mu.Lock()
	defer runner.mu.Unlock()
	for _, call := range runner.calls {
		joined := strings.Join(call, " ")
		if strings.Contains(joined, "nib-reader-") || strings.Contains(joined, "nib-wadepre") || strings.Contains(joined, "nib-nowcastnet") {
			t.Fatalf("retry reran inference: %s", joined)
		}
	}
}

func TestRunSeparatesInferenceFromFailedDiagnostics(t *testing.T) {
	cfg := consoleConfig(t)
	run := filepath.Join(cfg.Paths.Runs, "saved-run")
	operationDir := filepath.Join(run, "logs", "diagnostics-op-one")
	if err := os.MkdirAll(operationDir, 0o700); err != nil {
		t.Fatal(err)
	}
	files := map[string]string{
		"manifest.json": `{"schema":"nib.run.v1","id":"saved-run","forecasts":{"wadepre":{"path":"forecasts/wadepre.npz"}},"diagnostics":[]}`,
		"job.json":      `{"id":"saved-run","status":"partial","stage":"finished","request":{"models":["wadepre"]}}`,
		"order.yaml":    orderYAML(t, orderFixture()),
		filepath.Join("logs", "diagnostics-op-one", "status.json"): `{"id":"op-one","run_id":"saved-run","status":"failed","created_at":"2026-01-01T00:00:00Z","error":"render failed"}`,
	}
	for name, content := range files {
		if err := os.WriteFile(filepath.Join(run, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	server := handler.NewServer(cfg)
	server.Runner = forbiddenOrderRunner{t}
	out, detail := triggerDo(t, server, "GET", "/v1/runs/saved-run", nil)
	operations, _ := detail["operations"].([]any)
	if out.Code != 200 || detail["inference_status"] != "succeeded" || detail["diagnostics_status"] != "failed" || len(operations) != 1 || detail["order"] == nil {
		t.Fatalf("separate statuses: %d %s", out.Code, out.Body.String())
	}
	if operations[0].(map[string]any)["error"] != "render failed" {
		t.Fatalf("diagnostics error lost: %#v", operations)
	}
}
