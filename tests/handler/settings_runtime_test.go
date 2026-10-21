package handler_test

import (
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func settingsConfig(t *testing.T) string {
	t.Helper()
	source := filepath.Join(t.TempDir(), "config.yaml")
	body := "# keep this comment\nhandler:\n  port: 9000\npaths:\n  runs: runs\n  resources: " + testResourcesPath(t) + "\nfuture: retained\n"
	if err := os.WriteFile(source, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return source
}

func TestSettingsSavesDataRootPerDataIDAndPreservesConfig(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	source := settingsConfig(t)
	cfg, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	cfg.Runtime.Token = "token"
	server := handler.NewServer(cfg)
	root := t.TempDir()
	patch := func(body string) int {
		req := httptest.NewRequest("PATCH", "http://127.0.0.1/v1/settings", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer token")
		result := httptest.NewRecorder()
		server.Routes().ServeHTTP(result, req)
		return result.Code
	}
	if code := patch(`{"data_root":"` + filepath.ToSlash(root) + `"}`); code != 400 {
		t.Fatalf("legacy body without data_id: %d", code)
	}
	if code := patch(`{"data_id":"unknown","data_root":"` + filepath.ToSlash(root) + `"}`); code != 422 {
		t.Fatalf("unknown data id: %d", code)
	}
	if code := patch(`{"data_id":"sevir","data_root":"` + filepath.ToSlash(filepath.Join(root, "missing")) + `"}`); code != 422 {
		t.Fatalf("missing folder: %d", code)
	}
	if code := patch(`{"data_id":"sevir","data_root":"` + filepath.ToSlash(root) + `"}`); code != 200 {
		t.Fatalf("save: %d", code)
	}
	raw, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "keep this comment") || !strings.Contains(string(raw), "future: retained") {
		t.Fatal(string(raw))
	}
	if strings.Contains(string(raw), root) {
		t.Fatal("data root must not be written into config.yaml")
	}
	settings, err := os.ReadFile(filepath.Join(filepath.Dir(source), "nib-settings.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(settings), "data_roots") || !strings.Contains(string(settings), "sevir") {
		t.Fatalf("settings = %s", settings)
	}
	reloaded, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.DataRoots["sevir"] != root || reloaded.Handler.Port != 9000 {
		t.Fatal(reloaded.DataRoots, reloaded.Handler.Port)
	}
}

func TestImageSourceSettingPersistsAndIsValidated(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	source := settingsConfig(t)
	cfg, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	cfg.Runtime.Token = "token"
	server := handler.NewServer(cfg)
	request := func(method, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, "http://127.0.0.1/v1/settings", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer token")
		result := httptest.NewRecorder()
		server.Routes().ServeHTTP(result, req)
		return result
	}

	initial := request("GET", "")
	if initial.Code != 200 || !strings.Contains(initial.Body.String(), `"image_source":"auto"`) {
		t.Fatalf("initial settings: %d %s", initial.Code, initial.Body.String())
	}
	if response := request("PATCH", `{"image_source":"mirror"}`); response.Code != 200 {
		t.Fatalf("save mirror: %d %s", response.Code, response.Body.String())
	}
	updated := request("GET", "")
	if !strings.Contains(updated.Body.String(), `"image_source":"mirror"`) {
		t.Fatalf("updated settings: %s", updated.Body.String())
	}
	reloaded, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.Runtime.ImageSource != "mirror" {
		t.Fatalf("reloaded image source = %q", reloaded.Runtime.ImageSource)
	}
	if response := request("PATCH", `{"image_source":"invalid"}`); response.Code != 422 {
		t.Fatalf("invalid source: %d %s", response.Code, response.Body.String())
	}
}

func TestPickDataRootSavesPerDataIDWithoutAcceptingABrowserPath(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	source := settingsConfig(t)
	cfg, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	cfg.Runtime.Token = "token"
	server := handler.NewServer(cfg)
	first := t.TempDir()
	second := t.TempDir()
	server.PickFolder = func() (string, error) { return first, nil }
	post := func(body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "http://127.0.0.1/v1/settings/pick", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer token")
		result := httptest.NewRecorder()
		server.Routes().ServeHTTP(result, req)
		return result
	}
	if response := post(`{"data_root":"/tmp"}`); response.Code != 400 {
		t.Fatal(response.Code, response.Body.String())
	}
	if response := post(`{"data_id":"sevir"}`); response.Code != 200 || !strings.Contains(response.Body.String(), first) {
		t.Fatal(response.Code, response.Body.String())
	}
	server.PickFolder = func() (string, error) { return second, nil }
	if response := post(`{"data_id":"sevir"}`); response.Code != 200 {
		t.Fatal(response.Code, response.Body.String())
	}
	reloaded, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.DataRoots["sevir"] != second {
		t.Fatal(reloaded.DataRoots)
	}
	server.PickFolder = func() (string, error) { return "", fmt.Errorf("folder selection was canceled") }
	if response := post(`{"data_id":"sevir"}`); response.Code != 409 {
		t.Fatal(response.Code, response.Body.String())
	}
	reloaded, err = handler.Load(source)
	if err != nil || reloaded.DataRoots["sevir"] != second {
		t.Fatal(reloaded.DataRoots, err)
	}
}

func TestSettingsReadPerDataAndLegacyFallback(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	source := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(source, []byte("paths:\n  resources: "+testResourcesPath(t)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	sevirRoot, cmaRoot := t.TempDir(), t.TempDir()
	settings := `{"data_roots":{"sevir":"` + sevirRoot + `","cma_radar":"` + cmaRoot + `"}}`
	if err := os.WriteFile(filepath.Join(filepath.Dir(source), "nib-settings.json"), []byte(settings), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.DataRoots["sevir"] != sevirRoot || cfg.DataRoots["cma_radar"] != cmaRoot {
		t.Fatal(cfg.DataRoots)
	}

	legacyRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(filepath.Dir(source), "nib-settings.json"), []byte(`{"data_root":"`+legacyRoot+`"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err = handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.DataRootFallback != legacyRoot || len(cfg.DataRoots) != 0 {
		t.Fatal(cfg.DataRootFallback, cfg.DataRoots)
	}

	envRoot := t.TempDir()
	t.Setenv("NIB_DATA_ROOT", envRoot)
	cfg, err = handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.DataRootFallback != envRoot {
		t.Fatalf("fallback=%q want env", cfg.DataRootFallback)
	}
}

func TestCorruptSettingsFileIsReported(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	source := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(source, []byte("paths:\n  runs: runs\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(filepath.Dir(source), "nib-settings.json"), []byte("not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := handler.Load(source); err == nil || !strings.Contains(err.Error(), "nib-settings.json") {
		t.Fatalf("expected a readable settings error, got %v", err)
	}
}

func TestDiagnosticRetryRefusesMissingForecastAndNoProducts(t *testing.T) {
	cfg := testConfig(t)
	cfg.Paths.Runs = t.TempDir()
	cfg.Runtime.Token = "token"
	directory := filepath.Join(cfg.Paths.Runs, "run-one")
	if err := os.Mkdir(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	raw := `{"schema":"nib.run.v1","id":"run-one","forecasts":{"wadepre":{}},"diagnostics":[]}`
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	for _, body := range []string{`{"models":["unknown"],"visualization":true}`, `{"models":["wadepre"]}`} {
		req := httptest.NewRequest("POST", "http://127.0.0.1/v1/runs/run-one/diagnostics", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer token")
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, req)
		if response.Code != 422 {
			t.Fatal(response.Code, response.Body.String())
		}
	}
}
