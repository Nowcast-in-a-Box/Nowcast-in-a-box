package handler_test

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestMetricFamiliesMatchAtlasCatalog(t *testing.T) {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("caller")
	}
	raw, err := os.ReadFile(filepath.Join(filepath.Dir(file), "..", "..", "atlas", "catalog", "metrics", "verification-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	var catalog struct {
		Metrics []struct {
			Method string `json:"method"`
			Family string `json:"family"`
		} `json:"metrics"`
	}
	if err := json.Unmarshal(raw, &catalog); err != nil {
		t.Fatal(err)
	}
	if len(catalog.Metrics) != len(handler.MetricFamilies) {
		t.Fatalf("atlas has %d metrics, handler has %d", len(catalog.Metrics), len(handler.MetricFamilies))
	}
	for _, metric := range catalog.Metrics {
		if handler.MetricFamilies[metric.Method] != metric.Family {
			t.Fatalf("%s family drifted: atlas %s handler %s", metric.Method, metric.Family, handler.MetricFamilies[metric.Method])
		}
	}
}

func TestUnknownMetricStillRejected(t *testing.T) {
	raw, hash := verificationPackage(t, []string{"nope"})
	_, err := handler.ParseRuntimePackage(raw, hash, testResources(t))
	if err == nil || !strings.Contains(err.Error(), "unsupported or repeated metric") {
		t.Fatal(err)
	}
}

func TestSelectionPreflightReportsMissingDataAndWeights(t *testing.T) {
	cfg := testConfig(t)
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Runtime.Token = "token"
	server := handler.NewServer(cfg)
	body := `{"data_id":"sevir","event_id":"S710140","context_start_time":"2017-06-16T21:15:00Z","models":["wadepre"]}`
	response := postPreflight(t, server, body)
	if response["ok"] != false || response["checks"].([]any)[0].(map[string]any)["id"] != "data" {
		t.Fatal(response)
	}

	writeSevirData(t, &cfg)
	server.Config = cfg
	server.Runner = readyRunner{}
	joined := mustJSON(t, postPreflight(t, server, body))
	if !strings.Contains(joined, "Validate copies it from paths.weight_source") {
		t.Fatal(joined)
	}
	if result := postPreflight(t, server, body); result["ok"] != true {
		t.Fatalf("a missing weight is prepared later, not a preflight failure: %s", mustJSON(t, result))
	}
	weight := filepath.Join(cfg.Paths.Artifacts, "sevir", "wadepre", "WADEPre.onnx")
	if err := os.MkdirAll(filepath.Dir(weight), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(weight, []byte("not the weight"), 0o644); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(mustJSON(t, postPreflight(t, server, body)), "does not match the pinned SHA-256") {
		t.Fatal("checksum mismatch was not reported")
	}
}

type readyRunner struct{}

func (readyRunner) Run(context.Context, ...string) (int, []byte, error) {
	return 0, []byte(`{"ready":true}`), nil
}

func postPreflight(t *testing.T, server *handler.Server, body string) map[string]any {
	t.Helper()
	request := httptest.NewRequest("POST", "http://127.0.0.1/v1/preflight", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	if response.Code != 200 {
		t.Fatalf("status %d %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	return payload
}

func mustJSON(t *testing.T, value any) string {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func verificationPackage(t *testing.T, methods []string) ([]byte, string) {
	t.Helper()
	items := []map[string]any{
		{"order_item_id": "data", "product_id": "sevir-vil", "version_id": "v1", "category": "data", "resolved_configuration": map[string]any{"event_id": "S710140", "context_start_time": "2017-06-16T21:15:00Z"}, "input_bindings": []any{}},
		{"order_item_id": "model", "product_id": "wadepre-sevir", "version_id": "v1", "category": "model", "resolved_configuration": map[string]any{}, "input_bindings": []map[string]any{{"consumer_order_item_id": "model", "producer_order_item_id": "data", "input_name": "observations", "output_name": "observations", "compatibility": "compatible"}}},
		{"order_item_id": "metrics", "product_id": "vil-verification", "version_id": "v1", "category": "evaluation", "resolved_configuration": map[string]any{"methods": methods, "levels": []int{16}, "half_windows": []int{0}}, "input_bindings": []map[string]any{
			{"consumer_order_item_id": "metrics", "producer_order_item_id": "data", "input_name": "observations", "output_name": "observations", "compatibility": "compatible"},
			{"consumer_order_item_id": "metrics", "producer_order_item_id": "model", "input_name": "forecast", "output_name": "forecast", "compatibility": "compatible"},
		}},
	}
	files := map[string][]byte{}
	files["order.json"], _ = json.Marshal(map[string]any{"order_id": "order-one", "items": items})
	sum := sha256.Sum256(files["order.json"])
	files["manifest.json"], _ = json.Marshal(map[string]any{
		"schema_version": "nib-package-v0", "order_id": "order-one", "package_id": "package-one", "items": items,
		"entries": []map[string]any{{"path": "order.json", "sha256": hex.EncodeToString(sum[:])}},
	})
	manifestSum := sha256.Sum256(files["manifest.json"])
	files["checksums.sha256"] = []byte(fmt.Sprintf("%x  order.json\n%x  manifest.json\n", sum, manifestSum))
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
	digest := sha256.Sum256(raw.Bytes())
	return raw.Bytes(), hex.EncodeToString(digest[:])
}
