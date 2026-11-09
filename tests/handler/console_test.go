package handler_test

import (
	"bytes"
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

func consoleSave(t *testing.T, server *handler.Server, raw []byte, hash string) *httptest.ResponseRecorder {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"package": raw, "sha256": hash})
	req := httptest.NewRequest("POST", "http://127.0.0.1/v1/orders", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+server.Token)
	req.Header.Set("Content-Type", "application/json")
	out := httptest.NewRecorder()
	server.Routes().ServeHTTP(out, req)
	return out
}

func consoleList(t *testing.T, server *handler.Server) []map[string]any {
	t.Helper()
	req := httptest.NewRequest("GET", "http://127.0.0.1/v1/orders", nil)
	req.Header.Set("Authorization", "Bearer "+server.Token)
	out := httptest.NewRecorder()
	server.Routes().ServeHTTP(out, req)
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

func consoleStart(t *testing.T, server *handler.Server, packageID, key string) (*httptest.ResponseRecorder, handler.Job) {
	t.Helper()
	req := httptest.NewRequest("POST", "http://127.0.0.1/v1/orders/"+packageID+"/runs", nil)
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
	raw, hash := packageFixture(t, "wadepre-sevir", nil)

	if out := consoleSave(t, server, raw, hash); out.Code != 201 {
		t.Fatalf("first save: %d %s", out.Code, out.Body.String())
	}
	if out := consoleSave(t, server, raw, hash); out.Code != 200 {
		t.Fatalf("same-content save must be idempotent: %d %s", out.Code, out.Body.String())
	}
	other, otherHash := packageFixture(t, "nowcastnet-sevir", nil)
	if out := consoleSave(t, server, other, otherHash); out.Code != 409 {
		t.Fatalf("same id with different content must be refused: %d %s", out.Code, out.Body.String())
	}
	if out := consoleSave(t, server, raw, "bad"); out.Code != 422 {
		t.Fatalf("checksum mismatch must be refused: %d %s", out.Code, out.Body.String())
	}
	if _, err := os.Stat(filepath.Join(cfg.Paths.Orders, "package-one.zip")); err != nil {
		t.Fatal(err)
	}

	orders := consoleList(t, server)
	if len(orders) != 1 {
		t.Fatalf("expected one saved order, got %#v", orders)
	}
	order := orders[0]
	if order["package_id"] != "package-one" || order["order_id"] != "order-one" || order["source"] != "orders" {
		t.Fatalf("unexpected summary: %#v", order)
	}
	models, _ := order["models"].([]any)
	if len(models) != 1 || models[0] != "wadepre" {
		t.Fatalf("unexpected models: %#v", order["models"])
	}
}

func TestConsoleSavedOrdersDirIsScanned(t *testing.T) {
	cfg := consoleConfig(t)
	raw, _ := packageFixture(t, "wadepre-sevir", nil)
	if err := os.MkdirAll(cfg.Paths.Orders, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(cfg.Paths.Orders, "package-one.zip"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	// A corrupt second entry is listed with its reason instead of disappearing.
	if err := os.WriteFile(filepath.Join(cfg.Paths.Orders, "package-broken.zip"), []byte("not a zip"), 0o600); err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	orders := consoleList(t, server)
	if len(orders) != 2 {
		t.Fatalf("expected two listed orders, got %#v", orders)
	}
	byID := map[string]map[string]any{}
	for _, order := range orders {
		byID[order["package_id"].(string)] = order
	}
	if byID["package-one"]["order_id"] != "order-one" {
		t.Fatalf("saved package not parsed: %#v", byID["package-one"])
	}
	if reason, _ := byID["package-broken"]["error"].(string); reason == "" {
		t.Fatalf("broken package must carry an error: %#v", byID["package-broken"])
	}
}

func TestConsoleStartSavedOrder(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	raw, hash := packageFixture(t, "wadepre-sevir", nil)
	if out := consoleSave(t, server, raw, hash); out.Code != 201 {
		t.Fatalf("save: %d %s", out.Code, out.Body.String())
	}

	out, job := consoleStart(t, server, "package-one", "package:package-one")
	if out.Code != 202 {
		t.Fatalf("start saved order: %d %s", out.Code, out.Body.String())
	}
	finished := waitJob(t, server, job.ID, "succeeded")
	if _, err := os.Stat(filepath.Join(cfg.Paths.Runs, finished.ID, "package.zip")); err != nil {
		t.Fatal(err)
	}

	out, again := consoleStart(t, server, "package-one", "package:package-one")
	if out.Code != 200 || again.ID != finished.ID {
		t.Fatalf("repeated key must return the same run: %d %s vs %s", out.Code, again.ID, finished.ID)
	}
	out, rerun := consoleStart(t, server, "package-one", "rerun:package-one:new")
	if out.Code != 202 || rerun.ID == finished.ID {
		t.Fatalf("rerun must create a new run: %d %s", out.Code, rerun.ID)
	}
	waitJob(t, server, rerun.ID, "succeeded")
}

func TestConsoleRunsPackagesWithoutSavedCopy(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	job := waitJob(t, server, submitSelection(t, server, "S710140", []string{"wadepre"}, "", "hub-submit").ID, "succeeded")

	orders := consoleList(t, server)
	if len(orders) != 1 {
		t.Fatalf("expected the run's package to be listed, got %#v", orders)
	}
	if orders[0]["package_id"] != "package-S710140" || orders[0]["source"] != "runs" || orders[0]["run_id"] != job.ID {
		t.Fatalf("unexpected run-sourced summary: %#v", orders[0])
	}
	out, rerun := consoleStart(t, server, "package-S710140", "rerun:package-S710140:x")
	if out.Code != 202 {
		t.Fatalf("start from run copy: %d %s", out.Code, out.Body.String())
	}
	waitJob(t, server, rerun.ID, "succeeded")
}

func TestConsoleRejectsUnsafeOrUnknownPackageID(t *testing.T) {
	server := handler.NewServer(consoleConfig(t))
	for _, id := range []string{"con", "trailing.", ".hidden", "with%20space", "a%3Ab"} {
		out, _ := consoleStart(t, server, id, "")
		if out.Code != 400 {
			t.Fatalf("unsafe id %q: %d %s", id, out.Code, out.Body.String())
		}
	}
	for _, id := range []string{"UPPERCASE", "valid-but-missing"} {
		out, _ := consoleStart(t, server, id, "")
		if out.Code != 404 {
			t.Fatalf("unknown id %q: %d %s", id, out.Code, out.Body.String())
		}
	}
}

// offlineRunner answers every docker call with a daemon that is not running.
type offlineRunner struct{}

func (offlineRunner) Run(context.Context, ...string) (int, []byte, error) {
	return 1, []byte("failed to connect to the docker API; is the daemon running?"), nil
}

func TestConsoleStartRefusesWhenDockerIsNotRunning(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	server.Runner = offlineRunner{}
	raw, hash := packageFixture(t, "wadepre-sevir", nil)
	os.MkdirAll(cfg.Paths.Orders, 0o755)
	if err := os.WriteFile(filepath.Join(cfg.Paths.Orders, "package-one.zip"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	_ = hash
	out, _ := consoleStart(t, server, "package-one", "package-one-offline")
	if out.Code != 503 || !strings.Contains(out.Body.String(), "Docker is not running") {
		t.Fatalf("start without Docker: %d %s", out.Code, out.Body.String())
	}
}
