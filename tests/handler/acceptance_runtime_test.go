package handler_test

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
	"gopkg.in/yaml.v3"
)

func selectionPackage(t *testing.T, event string, models []string, diagnostics string) ([]byte, string) {
	t.Helper()
	items := []map[string]any{{
		"order_item_id": "data", "product_id": "sevir-vil", "version_id": "v1", "category": "data",
		"resolved_configuration": map[string]any{"event_id": event, "context_start_time": "2017-06-27T00:35:00Z"},
		"input_bindings":         []any{},
	}}
	for _, model := range models {
		items = append(items, map[string]any{
			"order_item_id": model, "product_id": model + "-sevir", "version_id": "v1", "category": "model",
			"product_name":           "Display name " + model,
			"resolved_configuration": map[string]any{},
			"input_bindings": []map[string]any{{
				"consumer_order_item_id": model, "producer_order_item_id": "data",
				"input_name": "observations", "output_name": "observations", "compatibility": "compatible",
			}},
		})
	}
	forecastBindings := func(consumer string) []map[string]any {
		bindings := []map[string]any{}
		for _, model := range models {
			bindings = append(bindings, map[string]any{
				"consumer_order_item_id": consumer, "producer_order_item_id": model,
				"input_name": "forecast", "output_name": "forecast", "compatibility": "compatible",
			})
		}
		return bindings
	}
	if diagnostics == "metrics" || diagnostics == "both" {
		items = append(items, map[string]any{
			"order_item_id": "metrics", "product_id": "vil-verification", "version_id": "v1", "category": "evaluation",
			"resolved_configuration": map[string]any{"methods": []string{"mae"}, "levels": []float64{16}, "half_windows": []int{0}},
			"input_bindings": append(forecastBindings("metrics"), map[string]any{
				"consumer_order_item_id": "metrics", "producer_order_item_id": "data", "input_name": "observations", "output_name": "observations", "compatibility": "compatible",
			}),
		})
	}
	if diagnostics == "visualization" || diagnostics == "both" || diagnostics == "both-visualizations" {
		items = append(items, map[string]any{
			"order_item_id": "maps", "product_id": "vil-visualization", "version_id": "v1", "category": "visualization",
			"resolved_configuration": map[string]any{},
			"input_bindings":         forecastBindings("maps"),
		})
	}
	if diagnostics == "animation" || diagnostics == "both-visualizations" {
		items = append(items, map[string]any{
			"order_item_id": "animation", "product_id": "time-leadtime-animation", "version_id": "v1", "category": "visualization",
			"resolved_configuration": map[string]any{},
			"input_bindings": append(forecastBindings("animation"), map[string]any{
				"consumer_order_item_id": "animation", "producer_order_item_id": "data", "input_name": "reference_observation", "output_name": "observations", "compatibility": "compatible",
			}),
		})
	}
	files := map[string][]byte{}
	files["order.json"], _ = json.Marshal(map[string]any{"order_id": "order-" + event, "items": items})
	var entries []map[string]any
	for name, body := range files {
		sum := sha256.Sum256(body)
		entries = append(entries, map[string]any{"path": name, "sha256": hex.EncodeToString(sum[:])})
	}
	files["manifest.json"], _ = json.Marshal(map[string]any{
		"schema_version": "nib-package-v0", "order_id": "order-" + event, "package_id": "package-" + event,
		"items": items, "entries": entries,
	})
	checks := ""
	for name, body := range files {
		sum := sha256.Sum256(body)
		checks += fmt.Sprintf("%x  %s\n", sum, name)
	}
	files["checksums.sha256"] = []byte(checks)
	var raw bytes.Buffer
	archive := zip.NewWriter(&raw)
	for name, body := range files {
		entry, err := archive.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw.Bytes())
	return raw.Bytes(), hex.EncodeToString(sum[:])
}

func TestGenericAnimationAndCombinedVisualizationPackages(t *testing.T) {
	for _, diagnostic := range []string{"animation", "both-visualizations"} {
		t.Run(diagnostic, func(t *testing.T) {
			raw, hash := selectionPackage(t, "S710140", []string{"nowcastnet", "wadepre"}, diagnostic)
			request, err := handler.ParseRuntimePackage(raw, hash, testResources(t))
			if err != nil {
				t.Fatal(err)
			}
			if !request.Visualization || strings.Join(request.VisualizationModels, ",") != "nowcastnet,wadepre" {
				t.Fatalf("lost or duplicated visualization models: %+v", request)
			}
		})
	}
}

func TestThreeModelPackageBindsEveryDiagnosticModel(t *testing.T) {
	models := []string{"nowcastnet", "exprecast", "wadepre"}
	raw, hash := selectionPackage(t, "S710140", models, "both")
	request, err := handler.ParseRuntimePackage(raw, hash, testResources(t))
	if err != nil {
		t.Fatal(err)
	}
	for _, model := range models {
		if request.ModelNames[model] != "Display name "+model {
			t.Fatalf("catalog name was lost: %+v", request.ModelNames)
		}
	}
	if strings.Join(request.VerificationModels, ",") != strings.Join(models, ",") || strings.Join(request.VisualizationModels, ",") != strings.Join(models, ",") {
		t.Fatalf("models were lost: %+v", request)
	}
}

func submitSelection(t *testing.T, server *handler.Server, event string, models []string, diagnostics, key string) handler.Job {
	t.Helper()
	raw, hash := selectionPackage(t, event, models, diagnostics)
	body, _ := json.Marshal(map[string]any{"package": raw, "sha256": hash})
	req := httptest.NewRequest("POST", "http://127.0.0.1/v1/jobs", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer token")
	req.Header.Set("Idempotency-Key", key)
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, req)
	if response.Code != 200 && response.Code != 202 {
		t.Fatalf("submit %s: %d %s", key, response.Code, response.Body.String())
	}
	var job handler.Job
	if err := json.Unmarshal(response.Body.Bytes(), &job); err != nil {
		t.Fatal(err)
	}
	return job
}

func waitJob(t *testing.T, server *handler.Server, id, want string) handler.Job {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for {
		req := httptest.NewRequest("GET", "http://127.0.0.1/v1/jobs/"+id, nil)
		req.Header.Set("Authorization", "Bearer "+server.Token)
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, req)
		var job handler.Job
		_ = json.Unmarshal(response.Body.Bytes(), &job)
		if job.Status == want {
			return job
		}
		if job.Status == "failed" || job.Status == "partial" || time.Now().After(deadline) {
			t.Fatalf("job %s status %s: %s", id, job.Status, job.Error)
		}
		time.Sleep(time.Millisecond)
	}
}

func TestSameConfigurationCreatesNewRunAndPreservesPreviousForecasts(t *testing.T) {
	cfg := testConfig(t)
	cfg.Paths.Runs = t.TempDir()
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Runtime.Token = "token"
	cfg.Runtime.AtlasRevision = strings.Repeat("b", 40)
	writeSevirData(t, &cfg)
	server := handler.NewServer(cfg)
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	first := waitJob(t, server, submitSelection(t, server, "S710140", []string{"wadepre"}, "", "first").ID, "succeeded")
	other := filepath.Join(cfg.Paths.Runs, first.ID, "forecasts", "nowcastnet", "fields.npz")
	if err := os.MkdirAll(filepath.Dir(other), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(other, []byte("keep"), 0o644); err != nil {
		t.Fatal(err)
	}
	manifestPath := filepath.Join(cfg.Paths.Runs, first.ID, "manifest.json")
	storedRaw, _ := os.ReadFile(manifestPath)
	var stored map[string]any
	if err := json.Unmarshal(storedRaw, &stored); err != nil {
		t.Fatal(err)
	}
	stored["forecasts"] = map[string]any{"nowcastnet": map[string]any{"path": "forecasts/nowcastnet/fields.npz", "sha256": "kept"}}
	stored["model_names"] = map[string]any{"nowcastnet": "Previously saved NowcastNet"}
	storedRaw, _ = json.Marshal(stored)
	if err := os.WriteFile(manifestPath, storedRaw, 0o600); err != nil {
		t.Fatal(err)
	}
	second := waitJob(t, server, submitSelection(t, server, "S710140", []string{"wadepre"}, "", "second").ID, "succeeded")
	if second.ID == first.ID {
		t.Fatalf("explicit rerun reused %s", first.ID)
	}
	raw, err := os.ReadFile(other)
	if err != nil || string(raw) != "keep" {
		t.Fatalf("other model forecast changed: %q %v", raw, err)
	}
	retainedRaw, _ := os.ReadFile(manifestPath)
	var retained struct {
		Forecasts map[string]struct {
			SHA256 string `json:"sha256"`
		}
		ModelNames map[string]string `json:"model_names"`
	}
	if err := json.Unmarshal(retainedRaw, &retained); err != nil {
		t.Fatal(err)
	}
	if retained.ModelNames["nowcastnet"] != "Previously saved NowcastNet" || retained.Forecasts["nowcastnet"].SHA256 != "kept" {
		t.Fatal("extra model registration lost", string(retainedRaw))
	}
	changed := waitJob(t, server, submitSelection(t, server, "S710140", []string{"nowcastnet"}, "", "changed").ID, "succeeded")
	if changed.ID == first.ID {
		t.Fatal("a different model selection reused the run")
	}
}

func TestThreeModelsDiagnosticsAndDistinctReruns(t *testing.T) {
	cfg := testConfig(t)
	cfg.Paths.Runs = t.TempDir()
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Runtime.Token = "token"
	cfg.Runtime.AtlasRevision = strings.Repeat("b", 40)
	writeSevirData(t, &cfg)
	server := handler.NewServer(cfg)
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	models := []string{"nowcastnet", "exprecast", "wadepre"}
	first := waitJob(t, server, submitSelection(t, server, "S696651", models, "both", "all-models").ID, "succeeded")
	second := waitJob(t, server, submitSelection(t, server, "S696652", models[:1], "", "other-event").ID, "succeeded")
	if first.DiagnosticOperationID == "" || first.ReportEntry == "" || second.DiagnosticOperationID != "" {
		t.Fatal("automatic diagnostics did not follow the frozen selection", first, second)
	}
	if first.ID == second.ID || first.Request.EventID == second.Request.EventID || first.Request.StartTime != second.Request.StartTime {
		t.Fatalf("events were not kept apart: %+v %+v", first.Request, second.Request)
	}
	if _, err := os.Stat(filepath.Join(cfg.Paths.Runs, first.ID, "manifest.json")); err != nil {
		t.Fatal(err)
	}
	for _, model := range models {
		raw, err := os.ReadFile(filepath.Join(cfg.Paths.Runs, first.ID, "compose-"+model+".yaml"))
		if err != nil {
			t.Fatal(err)
		}
		var compose struct {
			Services map[string]struct {
				Image   string   `yaml:"image"`
				Volumes []string `yaml:"volumes"`
			} `yaml:"services"`
		}
		if err := yaml.Unmarshal(raw, &compose); err != nil {
			t.Fatal(err)
		}
		modelResource, ok := server.Resources.Models["sevir"][model]
		if !ok || compose.Services["task"].Image != modelResource.Container.Image {
			t.Fatalf("%s image: %s", model, raw)
		}
	}
	matches, err := filepath.Glob(filepath.Join(cfg.Paths.Runs, first.ID, "logs", "diagnostics-*", "request.json"))
	if err != nil || len(matches) != 1 {
		t.Fatal(matches, err)
	}
	raw, err := os.ReadFile(matches[0])
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "mae") || !strings.Contains(string(raw), "visualization") {
		t.Fatalf("combined diagnostics request: %s", raw)
	}
	if _, err := os.Stat(filepath.Join(cfg.Paths.Runs, second.ID, "compose-diagnostics.yaml")); !os.IsNotExist(err) {
		t.Fatal("data and model job started diagnostics")
	}
	metrics := waitJob(t, server, submitSelection(t, server, "S696653", models[:1], "metrics", "metrics-only").ID, "succeeded")
	diagMatches, err := filepath.Glob(filepath.Join(cfg.Paths.Runs, metrics.ID, "logs", "diagnostics-*", "compose.yaml"))
	if err != nil || len(diagMatches) != 1 {
		t.Fatal(diagMatches, err)
	}
	diag, err := os.ReadFile(diagMatches[0])
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(diag), "/basemap") {
		t.Fatal("metrics-only job mounted a basemap")
	}
	maps := waitJob(t, server, submitSelection(t, server, "S696654", models[:1], "visualization", "maps-only").ID, "succeeded")
	mapMatches, err := filepath.Glob(filepath.Join(cfg.Paths.Runs, maps.ID, "logs", "diagnostics-*", "compose.yaml"))
	if err != nil || len(mapMatches) != 1 {
		t.Fatal(mapMatches, err)
	}
}

func postDiagnostics(t *testing.T, server *handler.Server, id string, visualization bool) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for {
		if server.ExecutionAvailableForTest() || time.Now().After(deadline) {
			break
		}
		time.Sleep(time.Millisecond)
	}
	raw, err := os.ReadFile(filepath.Join(server.Config.Paths.Runs, id, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest map[string]any
	if err := json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	manifest["forecasts"] = map[string]any{"wadepre": map[string]any{"path": "forecasts/wadepre.npz"}}
	raw, _ = json.Marshal(manifest)
	if err := os.WriteFile(filepath.Join(server.Config.Paths.Runs, id, "manifest.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
	body := fmt.Sprintf(`{"models":["wadepre"],"methods":["mae"],"levels":[16],"half_windows":[0],"visualization":%t}`, visualization)
	req := httptest.NewRequest("POST", "http://127.0.0.1/v1/runs/"+id+"/diagnostics", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, req)
	if response.Code != 202 && response.Code != 200 {
		t.Fatalf("diagnostics: %d %s", response.Code, response.Body.String())
	}
}

func TestDiagnosticRetryDoesNotRerunModels(t *testing.T) {
	cfg := testConfig(t)
	cfg.Paths.Runs = t.TempDir()
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Runtime.Token = "token"
	run := filepath.Join(cfg.Paths.Runs, "saved-run")
	if err := os.MkdirAll(filepath.Join(run, "forecasts"), 0o755); err != nil {
		t.Fatal(err)
	}
	manifest := `{"schema":"nib.run.v1","id":"saved-run","data":{"id":"sevir"},"forecasts":{"wadepre":{"path":"forecasts/wadepre.npz"}},"diagnostics":[]}`
	if err := os.WriteFile(filepath.Join(run, "manifest.json"), []byte(manifest), 0o644); err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	runner := &runtimeRunner{root: cfg.Paths.Runs}
	server.Runner = runner
	body := `{"models":["wadepre"],"methods":["mae"],"levels":[16],"half_windows":[0],"visualization":true}`
	req := httptest.NewRequest("POST", "http://127.0.0.1/v1/runs/saved-run/diagnostics", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, req)
	if response.Code != 202 && response.Code != 200 {
		t.Fatalf("retry: %d %s", response.Code, response.Body.String())
	}
	deadline := time.Now().Add(2 * time.Second)
	for {
		runner.mu.Lock()
		count := len(runner.calls)
		runner.mu.Unlock()
		if count > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("diagnostic retry did not start")
		}
		time.Sleep(time.Millisecond)
	}
	for !server.ExecutionAvailableForTest() {
		if time.Now().After(deadline) {
			t.Fatal("diagnostic retry did not finish")
		}
		time.Sleep(time.Millisecond)
	}
	runner.mu.Lock()
	defer runner.mu.Unlock()
	for _, call := range runner.calls {
		joined := strings.Join(call, " ")
		if strings.Contains(joined, "nib-nowcastnet") || strings.Contains(joined, "nib-wadepre") || strings.Contains(joined, "nib-ingestion") {
			t.Fatalf("retry reran a model or ingestion: %s", joined)
		}
	}
	matches, err := filepath.Glob(filepath.Join(run, "logs", "diagnostics-*", "compose.yaml"))
	if err != nil || len(matches) != 1 {
		t.Fatalf("retry output: %v %v", matches, err)
	}
}

func TestRestartKeepsInterruptedRun(t *testing.T) {
	cfg := testConfig(t)
	cfg.Paths.Runs = t.TempDir()
	cfg.Runtime.Token = "token"
	run := filepath.Join(cfg.Paths.Runs, "run-live")
	if err := os.Mkdir(run, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(run, "manifest.json"), []byte(`{"schema":"nib.run.v1","id":"run-live","diagnostics":[]}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(run, "job.json"), []byte(`{"id":"run-live","status":"running","stage":"wadepre"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	req := httptest.NewRequest("GET", "http://127.0.0.1/v1/runs", nil)
	req.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, req)
	if response.Code != 200 || !strings.Contains(response.Body.String(), "interrupted") || !strings.Contains(response.Body.String(), "run-live") {
		t.Fatalf("history: %d %s", response.Code, response.Body.String())
	}
	if _, err := os.Stat(filepath.Join(run, "manifest.json")); err != nil {
		t.Fatal(err)
	}
}
