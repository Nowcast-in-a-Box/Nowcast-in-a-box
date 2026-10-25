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
	"slices"
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

func TestSelectedFamiliesOmitUnusedMetricParameters(t *testing.T) {
	mae := mustParseMetrics(t, map[string]any{
		"methods": []string{"mae"}, "levels": []int{}, "half_windows": []int{},
	})
	if len(mae.Metrics) != 1 {
		t.Fatalf("%#v", mae.Metrics)
	}
	group := mae.Metrics[0]
	if group["family"] != "continuous" || !slices.Equal(metricItems(t, group), []string{"mae"}) {
		t.Fatalf("%#v", group)
	}
	if _, ok := group["levels"]; ok {
		t.Fatalf("unused levels entered the group: %#v", group)
	}
	if _, ok := group["half_windows"]; ok {
		t.Fatalf("unused windows entered the group: %#v", group)
	}

	omitted := mustParseMetrics(t, map[string]any{"methods": []string{"corr"}})
	if len(omitted.Metrics) != 1 || omitted.Metrics[0]["family"] != "moments" {
		t.Fatalf("%#v", omitted.Metrics)
	}
	if _, ok := omitted.Metrics[0]["levels"]; ok {
		t.Fatalf("missing unused levels were required: %#v", omitted.Metrics[0])
	}

	garbage := mustParseMetrics(t, map[string]any{
		"methods": []string{"mae"}, "levels": "nope", "half_windows": map[string]any{"bad": true},
	})
	if len(garbage.Metrics) != 1 || garbage.Metrics[0]["family"] != "continuous" {
		t.Fatalf("malformed unused lists rejected mae: %#v", garbage.Metrics)
	}

	yesNo := mustParseMetrics(t, map[string]any{
		"methods": []string{"ts"}, "levels": []float64{0, 255}, "half_windows": []int{},
	})
	if len(yesNo.Metrics) != 1 {
		t.Fatalf("%#v", yesNo.Metrics)
	}
	levels, ok := yesNo.Metrics[0]["levels"].([]float64)
	if yesNo.Metrics[0]["family"] != "yes_or_no" || !ok || !slices.Equal(levels, []float64{0, 255}) {
		t.Fatalf("%#v", yesNo.Metrics[0])
	}
	if _, ok := yesNo.Metrics[0]["half_windows"]; ok {
		t.Fatalf("yes/no group carried windows: %#v", yesNo.Metrics[0])
	}

	spatial := mustParseMetrics(t, map[string]any{
		"methods": []string{"fss"}, "levels": []float64{16}, "half_windows": []int{0, 16},
	})
	windows, ok := spatial.Metrics[0]["half_windows"].([]int)
	if spatial.Metrics[0]["family"] != "spatial" || !ok || !slices.Equal(windows, []int{0, 16}) {
		t.Fatalf("%#v", spatial.Metrics[0])
	}

	mixed := mustParseMetrics(t, map[string]any{
		"methods":      []string{"corr", "fss", "dts", "pod", "mae", "ts"},
		"levels":       []float64{10, 20},
		"half_windows": []int{0, 4},
	})
	want := []struct {
		family  string
		items   []string
		levels  bool
		windows bool
	}{
		{"continuous", []string{"mae"}, false, false},
		{"yes_or_no", []string{"dts", "pod", "ts"}, true, false},
		{"spatial", []string{"fss"}, true, true},
		{"moments", []string{"corr"}, false, false},
	}
	if len(mixed.Metrics) != len(want) {
		t.Fatalf("%#v", mixed.Metrics)
	}
	for i, group := range mixed.Metrics {
		if group["family"] != want[i].family || !slices.Equal(metricItems(t, group), want[i].items) {
			t.Fatalf("group %d %#v", i, group)
		}
		if _, ok := group["levels"]; ok != want[i].levels {
			t.Fatalf("group %d levels presence %#v", i, group)
		}
		if _, ok := group["half_windows"]; ok != want[i].windows {
			t.Fatalf("group %d windows presence %#v", i, group)
		}
	}
	seen := map[string]bool{}
	for _, group := range mixed.Metrics {
		for _, item := range metricItems(t, group) {
			seen[item] = true
		}
	}
	for _, method := range []string{"corr", "fss", "dts", "pod", "mae", "ts"} {
		if !seen[method] {
			t.Fatalf("selected %s missing from %#v", method, mixed.Metrics)
		}
	}
	if len(seen) != 6 {
		t.Fatalf("groups contained an unselected method: %#v", mixed.Metrics)
	}
}

func TestMetricConfigurationRejectsUnknownAndIncompleteFields(t *testing.T) {
	tooMany := make([]string, len(handler.MetricFamilies)+1)
	for i := range tooMany {
		tooMany[i] = "mae"
	}
	cases := []struct {
		name   string
		config map[string]any
		want   string
	}{
		{"unknown field", map[string]any{"methods": []string{"mae"}, "unit": "raw"}, `unknown metric field "unit"`},
		{"missing methods", map[string]any{"levels": []int{16}}, "invalid metric configuration"},
		{"empty methods", map[string]any{"methods": []string{}}, "invalid metric configuration"},
		{"too many methods", map[string]any{"methods": tooMany}, "invalid metric configuration"},
		{"duplicate method", map[string]any{"methods": []string{"mae", "mae"}}, "unsupported or repeated metric"},
		{"yes/no missing levels", map[string]any{"methods": []string{"ts"}, "half_windows": []int{1}}, "invalid metric configuration"},
		{"yes/no empty levels", map[string]any{"methods": []string{"ts"}, "levels": []int{}}, "invalid metric configuration"},
		{"yes/no null levels", map[string]any{"methods": []string{"ts"}, "levels": []int(nil)}, "invalid metric configuration"},
		{"yes/no levels type", map[string]any{"methods": []string{"ts"}, "levels": "35"}, "invalid metric configuration"},
		{"spatial missing windows", map[string]any{"methods": []string{"fss"}, "levels": []int{16}}, "invalid metric configuration"},
		{"spatial empty windows", map[string]any{"methods": []string{"fss"}, "levels": []int{16}, "half_windows": []int{}}, "invalid metric configuration"},
		{"spatial missing levels", map[string]any{"methods": []string{"fss"}, "half_windows": []int{1}}, "invalid metric configuration"},
		{"level out of range", map[string]any{"methods": []string{"ts"}, "levels": []float64{300}}, "thresholds must be distinct"},
		{"repeated level", map[string]any{"methods": []string{"ts"}, "levels": []float64{16, 16}}, "thresholds must be distinct"},
		{"window above 16", map[string]any{"methods": []string{"fss"}, "levels": []int{16}, "half_windows": []int{17}}, "invalid or repeated spatial window"},
		{"negative window", map[string]any{"methods": []string{"fss"}, "levels": []int{16}, "half_windows": []int{-1}}, "invalid or repeated spatial window"},
		{"repeated window", map[string]any{"methods": []string{"fss"}, "levels": []int{16}, "half_windows": []int{2, 2}}, "invalid or repeated spatial window"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := parseMetrics(t, tc.config)
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("got %v, want %s", err, tc.want)
			}
		})
	}
}

func parseMetrics(t *testing.T, config map[string]any) (handler.RuntimeRequest, error) {
	t.Helper()
	raw, hash := verificationPackageConfig(t, config)
	return handler.ParseRuntimePackage(raw, hash, testResources(t))
}

func mustParseMetrics(t *testing.T, config map[string]any) handler.RuntimeRequest {
	t.Helper()
	request, err := parseMetrics(t, config)
	if err != nil {
		t.Fatal(err)
	}
	return request
}

func metricItems(t *testing.T, group map[string]any) []string {
	t.Helper()
	items, ok := group["items"].([]string)
	if !ok {
		t.Fatalf("items %#v", group["items"])
	}
	return items
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

// An arm64 host running an amd64 image makes Docker print a platform warning to
// stderr; CombinedOutput merges it into the container reply.
type noisyRunner struct{}

func (noisyRunner) Run(context.Context, ...string) (int, []byte, error) {
	return 0, []byte("WARNING: The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8) and no specific platform was requested\n{\"ready\": true}\n"), nil
}

func TestPreflightIgnoresDockerPlatformWarning(t *testing.T) {
	cfg := testConfig(t)
	cfg.Runtime.Token = "token"
	server := handler.NewServer(cfg)
	writeSevirData(t, &cfg)
	server.Config = cfg
	server.Runner = noisyRunner{}
	body := `{"data_id":"sevir","event_id":"S710140","context_start_time":"2017-06-16T21:15:00Z","models":["wadepre"]}`
	joined := mustJSON(t, postPreflight(t, server, body))
	if !strings.Contains(joined, "selected window exists and catalog integrity passed") {
		t.Fatal(joined)
	}
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
	return verificationPackageConfig(t, map[string]any{
		"methods": methods, "levels": []int{16}, "half_windows": []int{0},
	})
}

func verificationPackageConfig(t *testing.T, config map[string]any) ([]byte, string) {
	t.Helper()
	items := []map[string]any{
		{"order_item_id": "data", "product_id": "sevir-vil", "version_id": "v1", "category": "data", "resolved_configuration": map[string]any{"event_id": "S710140", "context_start_time": "2017-06-16T21:15:00Z"}, "input_bindings": []any{}},
		{"order_item_id": "model", "product_id": "wadepre-sevir", "version_id": "v1", "category": "model", "resolved_configuration": map[string]any{}, "input_bindings": []map[string]any{{"consumer_order_item_id": "model", "producer_order_item_id": "data", "input_name": "observations", "output_name": "observations", "compatibility": "compatible"}}},
		{"order_item_id": "metrics", "product_id": "vil-verification", "version_id": "v1", "category": "evaluation", "resolved_configuration": config, "input_bindings": []map[string]any{
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
