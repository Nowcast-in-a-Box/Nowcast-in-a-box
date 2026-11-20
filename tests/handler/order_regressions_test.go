package handler_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestOrderWindowsRequireYAMLIntegers(t *testing.T) {
	resources := testResources(t)
	base := orderYAML(t, orderFixture()) + "verification:\n  models: [wadepre]\n  methods: [fss]\n  levels: [16]\n  half_windows: "
	for _, value := range []string{"[1.9]", "[-0.5]", "[\"1\"]", "[1.0]", "[true]", "1"} {
		t.Run(value, func(t *testing.T) {
			if _, _, err := handler.ParseOrder([]byte(base+value+"\n"), resources, 32); err == nil {
				t.Fatal("non-integer window accepted")
			}
		})
	}
	if _, _, err := handler.ParseOrder([]byte(base+"[0, 1]\n"), resources, 32); err != nil {
		t.Fatalf("integer windows rejected: %v", err)
	}
}

func TestStructuredOrderCannotExceedSerializedYAMLLimit(t *testing.T) {
	server := handler.NewServer(consoleConfig(t))
	server.Runner = forbiddenOrderRunner{t}
	order := orderFixture()
	order.EventID = strings.Repeat("a", 1<<20)
	for _, path := range []string{"/v1/orders/validate", "/v1/orders", "/v1/jobs"} {
		out, _ := triggerDo(t, server, "POST", path, map[string]any{"order": order})
		if out.Code != 422 || !strings.Contains(out.Body.String(), "order exceeds 1 MiB") {
			t.Fatalf("%s: %d %s", path, out.Code, out.Body.String())
		}
	}
	if len(consoleList(t, server)) != 0 || len(runIDs(t, server.Config)) != 0 {
		t.Fatal("oversized order persisted")
	}
}

func TestCMAOrderEventMatchesFirstSampleDate(t *testing.T) {
	order := orderFixture()
	order.DataID, order.Models = "cma_radar", []string{"nowcastnet"}
	order.SampleStarts = []string{"2017-06-27T23:54:00Z", "2017-06-28T00:00:00Z"}
	resources := testResources(t)
	for _, event := range []string{"20170628", "S696651"} {
		order.EventID = event
		if _, _, err := handler.ParseOrder([]byte(orderYAML(t, order)), resources, 32); err == nil || !strings.Contains(err.Error(), "first sample date") {
			t.Fatalf("event %s: %v", event, err)
		}
	}
	order.EventID = "20170627"
	if _, _, err := handler.ParseOrder([]byte(orderYAML(t, order)), resources, 32); err != nil {
		t.Fatalf("cross-midnight batch rejected: %v", err)
	}
}

func TestDiagnosticRetryRequiresDecodedFrozenOrder(t *testing.T) {
	for _, snapshot := range []string{"", "schema_version: [broken\n"} {
		t.Run(snapshot, func(t *testing.T) {
			cfg := consoleConfig(t)
			run := filepath.Join(cfg.Paths.Runs, "legacy-run")
			if err := os.MkdirAll(run, 0o700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(run, "manifest.json"), []byte(`{"schema":"nib.run.v1","id":"legacy-run","data":{"id":"sevir"},"forecasts":{"wadepre":{}}}`), 0o600); err != nil {
				t.Fatal(err)
			}
			if snapshot != "" {
				if err := os.WriteFile(filepath.Join(run, "order.yaml"), []byte(snapshot), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			server := handler.NewServer(cfg)
			server.Runner = forbiddenOrderRunner{t}
			for _, body := range []any{map[string]any{"models": []string{"wadepre"}, "visualization": true}, map[string]any{"visualization": map[string]any{"models": []string{"wadepre"}}}} {
				out, _ := triggerDo(t, server, "POST", "/v1/runs/legacy-run/diagnostics", body)
				if out.Code != 409 || !strings.Contains(out.Body.String(), "legacy run is read-only") {
					t.Fatalf("legacy retry: %d %s", out.Code, out.Body.String())
				}
			}
			out, _ := triggerDo(t, server, "GET", "/v1/runs/legacy-run", nil)
			if out.Code != 200 {
				t.Fatalf("legacy run no longer inspectable: %s", out.Body.String())
			}
		})
	}
}

func TestBatchDiagnosticSubsetMustExistInEveryChild(t *testing.T) {
	cfg := consoleConfig(t)
	run := filepath.Join(cfg.Paths.Runs, "batch-run")
	for _, child := range []string{"one", "two"} {
		if err := os.MkdirAll(filepath.Join(run, child), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	files := map[string]string{
		"order.yaml":        orderYAML(t, orderFixture()),
		"manifest.json":     `{"schema":"nib.run.v1","id":"batch-run","data":{"id":"sevir"},"forecasts":{"wadepre":{},"nowcastnet":{}},"samples":[{"path":"one"},{"path":"two"}]}`,
		"one/manifest.json": `{"forecasts":{"wadepre":{},"nowcastnet":{}}}`,
		"two/manifest.json": `{"forecasts":{"wadepre":{}}}`,
	}
	for name, raw := range files {
		if err := os.WriteFile(filepath.Join(run, filepath.FromSlash(name)), []byte(raw), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	server := handler.NewServer(cfg)
	server.Runner = forbiddenOrderRunner{t}
	for _, product := range []string{"verification", "visualization"} {
		selection := map[string]any{"models": []string{"nowcastnet"}}
		if product == "verification" {
			selection["methods"] = []string{"mae"}
		}
		out, _ := triggerDo(t, server, "POST", "/v1/runs/batch-run/diagnostics", map[string]any{product: selection})
		if out.Code != 422 {
			t.Fatalf("%s accepted absent child forecast: %d %s", product, out.Code, out.Body.String())
		}
	}
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	out, _ := triggerDo(t, server, "POST", "/v1/runs/batch-run/diagnostics", map[string]any{"visualization": map[string]any{"models": []string{"wadepre"}}})
	if out.Code != 202 {
		t.Fatalf("common subset rejected: %d %s", out.Code, out.Body.String())
	}
	deadline := time.Now().Add(2 * time.Second)
	for !server.ExecutionAvailableForTest() {
		if time.Now().After(deadline) {
			t.Fatal("retry did not finish")
		}
		time.Sleep(time.Millisecond)
	}
}

func TestTriggerResetsCursorForChangedDataSource(t *testing.T) {
	cfg, server, runner := triggerServer(t)
	orderID := saveOrder(t, server)
	const stamp = "2017-06-27T00:35:00Z"
	writeTriggerStore(t, cfg, map[string]any{
		"id": "t_changed1", "package_id": orderID, "enabled": true, "kind": "new_data", "poll_minutes": 5,
		"window_mode": "latest", "start_source": "latest_ready", "created_at": time.Now().UTC(),
		"last_data_id": "cma_radar", "last_start": "2020-01-01T00:00:00Z",
	})
	runner.setSamples(sampleList(t, [2]string{"S710140", stamp}))
	out, fired := triggerDo(t, server, "POST", "/v1/triggers/t_changed1/fire", nil)
	if out.Code != 202 || fired["last_outcome"] != "started" || fired["last_data_id"] != "sevir" || fired["last_start"] != stamp {
		t.Fatalf("cursor not reset: %d %s", out.Code, out.Body.String())
	}
	id := fired["last_run_id"].(string)
	waitForJobEnd(t, server, id)
	_, job := triggerDo(t, server, "GET", "/v1/jobs/"+id, nil)
	if job["idempotency_key"] != "trigger:t_changed1:sevir:"+stamp {
		t.Fatalf("data source missing from key: %#v", job)
	}
	_, again := triggerDo(t, server, "POST", "/v1/triggers/t_changed1/fire", nil)
	if again["last_outcome"] != "skipped" {
		t.Fatalf("new cursor not retained: %#v", again)
	}
}
