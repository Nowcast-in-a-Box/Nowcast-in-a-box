package handler_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func consoleConfig(t *testing.T) handler.Config {
	t.Helper()
	cfg := testConfig(t)
	cfg.Paths.Runs = t.TempDir()
	cfg.Paths.Orders = t.TempDir()
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Runtime.Token = "token"
	cfg.Runtime.AtlasRevision = strings.Repeat("b", 40)
	writeSevirData(t, &cfg)
	return cfg
}

func consoleSave(t *testing.T, server *handler.Server, order handler.Order) *httptest.ResponseRecorder {
	t.Helper()
	out, _ := triggerDo(t, server, "POST", "/v1/orders", map[string]any{"order": order})
	return out
}

func consoleList(t *testing.T, server *handler.Server) []map[string]any {
	t.Helper()
	out, _ := triggerDo(t, server, "GET", "/v1/orders", nil)
	if out.Code != 200 {
		t.Fatalf("list orders: %d %s", out.Code, out.Body.String())
	}
	var page struct {
		Orders []map[string]any `json:"orders"`
	}
	if err := json.Unmarshal(out.Body.Bytes(), &page); err != nil {
		t.Fatal(err)
	}
	return page.Orders
}

func consoleStart(t *testing.T, server *handler.Server, orderID, key string) (*httptest.ResponseRecorder, handler.Job) {
	t.Helper()
	req := httptest.NewRequest("POST", "http://127.0.0.1/v1/orders/"+orderID+"/runs", nil)
	req.Header.Set("Authorization", "Bearer "+server.Token)
	if key != "" {
		req.Header.Set("Idempotency-Key", key)
	}
	out := httptest.NewRecorder()
	server.Routes().ServeHTTP(out, req)
	var job handler.Job
	_ = json.Unmarshal(out.Body.Bytes(), &job)
	return out, job
}

func TestConsolePageServedWithSessionToken(t *testing.T) {
	server := handler.NewServer(consoleConfig(t))
	req := httptest.NewRequest("GET", "http://127.0.0.1:8765/", nil)
	out := httptest.NewRecorder()
	server.Routes().ServeHTTP(out, req)
	if out.Code != 200 {
		t.Fatalf("console page: %d %s", out.Code, out.Body.String())
	}
	if value := out.Header().Get("Cache-Control"); value != "no-store" {
		t.Fatalf("console page cache control: %q", value)
	}
	body := out.Body.String()
	if !strings.Contains(body, "NiB local console") || !strings.Contains(body, `const TOKEN = "token";`) {
		t.Fatal("console page is missing its marker or the injected token")
	}
	for _, route := range []string{"dashboard", "settings", "orders", "triggers"} {
		if !strings.Contains(body, `class="nav-item" href="#`+route+`"`) {
			t.Errorf("missing navigation for %s", route)
		}
	}
	req = httptest.NewRequest("GET", "http://example.com:8765/", nil)
	out = httptest.NewRecorder()
	server.Routes().ServeHTTP(out, req)
	if out.Code != 403 {
		t.Fatalf("non-loopback console host: %d", out.Code)
	}
	req = httptest.NewRequest("GET", "http://127.0.0.1:8765/v1/orders", nil)
	out = httptest.NewRecorder()
	server.Routes().ServeHTTP(out, req)
	if out.Code != 401 {
		t.Fatalf("orders list without token: %d", out.Code)
	}
}

func TestConsoleOrderSaveListAndConflict(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	order := orderFixture()
	if out := consoleSave(t, server, order); out.Code != 201 {
		t.Fatalf("first save: %d %s", out.Code, out.Body.String())
	}
	// The two input forms normalize to the same saved content.
	out, detail := triggerDo(t, server, "POST", "/v1/orders", map[string]any{"yaml": "# portable order\n" + orderYAML(t, order)})
	if out.Code != 200 {
		t.Fatalf("same-content save must be idempotent: %d %s", out.Code, out.Body.String())
	}
	if detail["revision"] == "" || detail["yaml"] == "" || detail["order"] == nil {
		t.Fatalf("missing editable detail: %#v", detail)
	}
	other := order
	other.Models = []string{"nowcastnet"}
	if out := consoleSave(t, server, other); out.Code != 409 {
		t.Fatalf("same id with different content must be refused: %d %s", out.Code, out.Body.String())
	}
	if _, err := os.Stat(filepath.Join(cfg.Paths.Orders, "order-one.yaml")); err != nil {
		t.Fatal(err)
	}
	orders := consoleList(t, server)
	if len(orders) != 1 {
		t.Fatalf("expected one saved order, got %#v", orders)
	}
	summary := orders[0]
	if summary["package_id"] != "order-one" || summary["order_id"] != "order-one" || summary["source"] != "saved" || summary["read_only"] != false {
		t.Fatalf("unexpected summary: %#v", summary)
	}
	models, _ := summary["models"].([]any)
	if len(models) != 1 || models[0] != "wadepre" {
		t.Fatalf("unexpected models: %#v", summary["models"])
	}
}

func TestConsoleSavedOrdersDirIsScannedWithoutLegacyZIPFallback(t *testing.T) {
	cfg := consoleConfig(t)
	files := map[string][]byte{
		"order-one.yaml":    []byte(orderYAML(t, orderFixture())),
		"order-broken.yaml": []byte("not an order"),
	}
	files["package-legacy.zip"], _ = packageFixture(t, "wadepre-sevir", nil)
	for name, raw := range files {
		if err := os.WriteFile(filepath.Join(cfg.Paths.Orders, name), raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	server := handler.NewServer(cfg)
	orders := consoleList(t, server)
	if len(orders) != 2 {
		t.Fatalf("expected only YAML orders, got %#v", orders)
	}
	byID := map[string]map[string]any{}
	for _, order := range orders {
		byID[order["order_id"].(string)] = order
	}
	if byID["order-one"]["read_only"] != false {
		t.Fatalf("saved order not parsed: %#v", byID["order-one"])
	}
	if reason, _ := byID["order-broken"]["error"].(string); reason == "" {
		t.Fatalf("broken order must carry an error: %#v", byID["order-broken"])
	}
	if out, _ := consoleStart(t, server, "package-legacy", "legacy"); out.Code != 404 {
		t.Fatalf("legacy ZIP started: %d %s", out.Code, out.Body.String())
	}
}

func TestConsoleStartSavedOrder(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	writeExecutionWeights(t, server)
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	if out := consoleSave(t, server, orderFixture()); out.Code != 201 {
		t.Fatalf("save: %d %s", out.Code, out.Body.String())
	}
	out, job := consoleStart(t, server, "order-one", "order-one:first")
	if out.Code != 202 {
		t.Fatalf("start saved order: %d %s", out.Code, out.Body.String())
	}
	finished := waitJob(t, server, job.ID, "succeeded")
	if _, err := os.Stat(filepath.Join(cfg.Paths.Runs, finished.ID, "order.yaml")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(cfg.Paths.Runs, finished.ID, "package.zip")); !os.IsNotExist(err) {
		t.Fatalf("new run retained a ZIP: %v", err)
	}
	out, again := consoleStart(t, server, "order-one", "order-one:first")
	if out.Code != 200 || again.ID != finished.ID {
		t.Fatalf("repeated key must return the same run: %d %s vs %s", out.Code, again.ID, finished.ID)
	}
	out, rerun := consoleStart(t, server, "order-one", "order-one:rerun")
	if out.Code != 202 || rerun.ID == finished.ID {
		t.Fatalf("rerun must create a new run: %d %s", out.Code, rerun.ID)
	}
	waitJob(t, server, rerun.ID, "succeeded")
}

func TestConsoleRunsWithoutSavedCopyAreReadOnly(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	writeExecutionWeights(t, server)
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	job := waitJob(t, server, submitSelection(t, server, "S710140", []string{"wadepre"}, "", "hub-submit").ID, "succeeded")
	orders := consoleList(t, server)
	if len(orders) != 1 {
		t.Fatalf("expected run order, got %#v", orders)
	}
	if orders[0]["package_id"] != "order-s710140" || orders[0]["order_id"] != "order-s710140" || orders[0]["source"] != "runs" || orders[0]["read_only"] != true || orders[0]["run_id"] != job.ID {
		t.Fatalf("unexpected run-sourced summary: %#v", orders[0])
	}
	out, _ := consoleStart(t, server, "order-s710140", "historical-rerun")
	if out.Code != 404 {
		t.Fatalf("historical copy must not start as saved order: %d %s", out.Code, out.Body.String())
	}
}

func TestConsoleRejectsUnsafeOrUnknownOrderID(t *testing.T) {
	server := handler.NewServer(consoleConfig(t))
	for _, id := range []string{"con", "trailing.", ".hidden", "with%20space", "a%3Ab", "UPPERCASE", "valid-but-missing"} {
		out, _ := consoleStart(t, server, id, "")
		if out.Code != 404 {
			t.Fatalf("unsafe or missing id %q: %d %s", id, out.Code, out.Body.String())
		}
	}
}

// offlineRunner answers every docker call with a daemon that is not running.
type offlineRunner struct{}

func (offlineRunner) Run(context.Context, ...string) (int, []byte, error) {
	return 1, []byte("failed to connect to the docker API; is the daemon running?"), nil
}

func TestConsoleStartRefusesWhenDockerIsNotRunning(t *testing.T) {
	server := handler.NewServer(consoleConfig(t))
	server.Runner = offlineRunner{}
	if out := consoleSave(t, server, orderFixture()); out.Code != 201 {
		t.Fatalf("save: %d %s", out.Code, out.Body.String())
	}
	out, _ := consoleStart(t, server, "order-one", "order-one-offline")
	if out.Code != 503 || !strings.Contains(out.Body.String(), "Docker is not running") {
		t.Fatalf("start without Docker: %d %s", out.Code, out.Body.String())
	}
}
