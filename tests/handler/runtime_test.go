package handler_test

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func packageFixture(t *testing.T, modelProduct string, extra map[string][]byte) ([]byte, string) {
	t.Helper()
	items := []map[string]any{
		{"order_item_id": "data", "product_id": "sevir-vil", "version_id": "v1", "category": "data", "resolved_configuration": map[string]any{"event_id": "S696651", "context_start_time": "2017-06-27T00:35:00Z"}, "input_bindings": []any{}},
		{"order_item_id": "model", "product_id": modelProduct, "version_id": "v1", "category": "model", "resolved_configuration": map[string]any{}, "input_bindings": []map[string]any{{"consumer_order_item_id": "model", "producer_order_item_id": "data", "input_name": "observations", "output_name": "observations", "compatibility": "compatible"}}},
	}
	files := map[string][]byte{}
	files["order.json"], _ = json.Marshal(map[string]any{"order_id": "order-one", "items": items})
	entries := []map[string]any{}
	for name, body := range files {
		sum := sha256.Sum256(body)
		entries = append(entries, map[string]any{"path": name, "sha256": hex.EncodeToString(sum[:])})
	}
	files["manifest.json"], _ = json.Marshal(map[string]any{"schema_version": "nib-package-v0", "order_id": "order-one", "package_id": "package-one", "items": items, "entries": entries})
	checks := ""
	for name, body := range files {
		sum := sha256.Sum256(body)
		checks += fmt.Sprintf("%x  %s\n", sum, name)
	}
	files["checksums.sha256"] = []byte(checks)
	for name, body := range extra {
		files[name] = body
	}
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

func TestRuntimePackageTrustBoundary(t *testing.T) {
	raw, hash := packageFixture(t, "wadepre-sevir", nil)
	request, err := handler.ParseRuntimePackage(raw, hash, testResources(t))
	if err != nil {
		t.Fatal(err)
	}
	if request.EventID != "S696651" || len(request.Models) != 1 || request.Models[0] != "wadepre" {
		t.Fatal(request)
	}
	if _, err := handler.ParseRuntimePackage(raw, "bad", testResources(t)); err == nil {
		t.Fatal("bad package digest accepted")
	}
	for _, name := range []string{"../outside", "/absolute", "unregistered.txt"} {
		raw, hash := packageFixture(t, "wadepre-sevir", map[string][]byte{name: []byte("bad")})
		if _, err := handler.ParseRuntimePackage(raw, hash, testResources(t)); err == nil {
			t.Fatalf("unsafe package accepted: %s", name)
		}
	}
	raw, hash = packageFixture(t, "arbitrary-shell", nil)
	if _, err := handler.ParseRuntimePackage(raw, hash, testResources(t)); err == nil {
		t.Fatal("unknown runtime product accepted")
	}
}

func TestOriginAndLocalToken(t *testing.T) {
	cfg := handler.DefaultConfig()
	cfg.Runtime.AllowedOrigins = []string{"https://hub.example", "http://hub.example"}
	cfg.Runtime.Token = "test-token"
	server := handler.NewServer(cfg)
	for _, tc := range []struct {
		method, origin, path, token string
		status                      int
	}{
		{"GET", "https://evil.example", "/health", "", 403},
		{"OPTIONS", "https://hub.example", "/v1/jobs", "", 204},
		{"GET", "http://hub.example", "/v1/runs", "", 401},
		{"GET", "https://hub.example", "/v1/runs", "", 401},
		{"GET", "", "/v1/runs", "wrong", 401},
	} {
		req := httptest.NewRequest(tc.method, "http://127.0.0.1"+tc.path, nil)
		req.Header.Set("Origin", tc.origin)
		req.Header.Set("Authorization", "Bearer "+tc.token)
		out := httptest.NewRecorder()
		server.Routes().ServeHTTP(out, req)
		if out.Code != tc.status {
			t.Fatalf("%s %s: %d", tc.method, tc.path, out.Code)
		}
		if (tc.origin == "https://hub.example" || tc.origin == "http://hub.example") && out.Header().Get("Access-Control-Allow-Origin") != tc.origin {
			t.Fatal("missing exact CORS origin")
		}
	}
}

func TestRunsFilterByStableOrderID(t *testing.T) {
	cfg := handler.DefaultConfig()
	cfg.Paths.Runs = t.TempDir()
	cfg.Runtime.Token = "token"
	for _, run := range []struct{ id, order string }{
		{"run-a", "order-a"},
		{"run-b", "order-a"},
		{"run-c", "order-b"},
	} {
		directory := filepath.Join(cfg.Paths.Runs, run.id)
		if err := os.Mkdir(directory, 0o755); err != nil {
			t.Fatal(err)
		}
		manifest := map[string]any{"schema": "nib.run.v1", "id": run.id, "diagnostics": []any{}}
		raw, _ := json.Marshal(manifest)
		if err := os.WriteFile(filepath.Join(directory, "manifest.json"), raw, 0o644); err != nil {
			t.Fatal(err)
		}
		job := map[string]any{
			"id": run.id, "status": "succeeded", "stage": "complete",
			"request": map[string]any{"order_id": run.order},
		}
		raw, _ = json.Marshal(job)
		if err := os.WriteFile(filepath.Join(directory, "job.json"), raw, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	server := handler.NewServer(cfg)
	request := func(path string) map[string]any {
		req := httptest.NewRequest("GET", "http://127.0.0.1"+path, nil)
		req.Header.Set("Authorization", "Bearer token")
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, req)
		if response.Code != 200 {
			t.Fatalf("%s: %d %s", path, response.Code, response.Body.String())
		}
		var body map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
			t.Fatal(err)
		}
		return body
	}

	page := request("/v1/runs?order_id=order-a")
	if page["total"] != float64(2) || len(page["runs"].([]any)) != 2 {
		t.Fatalf("filtered page: %#v", page)
	}
	for _, value := range page["runs"].([]any) {
		run := value.(map[string]any)
		if run["job"].(map[string]any)["request"].(map[string]any)["order_id"] != "order-a" {
			t.Fatalf("unexpected order in %#v", run)
		}
	}
	if page := request("/v1/runs?order_id=order-b&offset=1"); page["total"] != float64(1) || len(page["runs"].([]any)) != 0 {
		t.Fatalf("filtered pagination: %#v", page)
	}
	if page := request("/v1/runs?order_id=missing"); page["total"] != float64(0) {
		t.Fatalf("empty filter: %#v", page)
	}
}

func TestRegisteredResultsAndRestartedJob(t *testing.T) {
	cfg := handler.DefaultConfig()
	cfg.Paths.Runs = t.TempDir()
	cfg.Runtime.Token = "token"
	run := filepath.Join(cfg.Paths.Runs, "run-one")
	artifact := filepath.Join(run, "diagnostics", "verification", "attempt", "index.html")
	if err := os.MkdirAll(filepath.Dir(artifact), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(artifact, []byte("<h1>score</h1>"), 0o644); err != nil {
		t.Fatal(err)
	}
	manifest := map[string]any{"schema": "nib.run.v1", "id": "run-one", "diagnostics": []map[string]any{{"status": "succeeded", "path": "diagnostics/verification/attempt"}}}
	raw, _ := json.Marshal(manifest)
	if err := os.WriteFile(filepath.Join(run, "manifest.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
	job := handler.Job{ID: "run-one", Status: "running"}
	raw, _ = json.Marshal(job)
	if err := os.WriteFile(filepath.Join(run, "job.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	req := httptest.NewRequest("GET", "http://127.0.0.1/v1/jobs/run-one", nil)
	req.Header.Set("Authorization", "Bearer token")
	out := httptest.NewRecorder()
	server.Routes().ServeHTTP(out, req)
	if out.Code != 200 || !bytes.Contains(out.Body.Bytes(), []byte("interrupted")) {
		t.Fatal(out.Code, out.Body.String())
	}
	for _, tc := range []struct {
		path   string
		status int
	}{
		{"diagnostics/verification/attempt/index.html", 200},
		{"manifest.json", 403},
		{"diagnostics/verification/unknown/index.html", 403},
	} {
		req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1/v1/runs/run-one/files/"+tc.path, nil)
		out := httptest.NewRecorder()
		server.Routes().ServeHTTP(out, req)
		if out.Code != tc.status {
			t.Fatalf("%s: %d %s", tc.path, out.Code, out.Body.String())
		}
	}
}
