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

func TestSettingsOnlyUpdatesExistingDataRootAndPreservesConfig(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	source := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(source, []byte("# keep this comment\nhandler:\n  port: 9000\npaths:\n  runs: runs\nfuture: retained\n"), 0o600); err != nil {
		t.Fatal(err)
	}
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
	if code := patch(`{"docker_args":"bad"}`); code != 400 {
		t.Fatal(code)
	}
	if code := patch(`{"data_root":"` + filepath.ToSlash(filepath.Join(root, "missing")) + `"}`); code != 422 {
		t.Fatal(code)
	}
	if code := patch(`{"data_root":"` + filepath.ToSlash(root) + `"}`); code != 200 {
		t.Fatal(code)
	}
	raw, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "keep this comment") || !strings.Contains(string(raw), "future: retained") {
		t.Fatal(string(raw))
	}
	if strings.Contains(string(raw), root) {
		t.Fatal("data_root must not be written into config.yaml")
	}
	settings, err := os.ReadFile(filepath.Join(filepath.Dir(source), "nib-settings.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(settings), root) {
		t.Fatalf("settings = %s", settings)
	}
	reloaded, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.Paths.DataRoot != root || reloaded.Handler.Port != 9000 {
		t.Fatal(reloaded)
	}
}

func TestPickDataRootSavesAndOverwritesWithoutAcceptingABrowserPath(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	source := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(source, []byte("paths:\n  runs: runs\n"), 0o600); err != nil {
		t.Fatal(err)
	}
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
	if response := post(""); response.Code != 200 || !strings.Contains(response.Body.String(), first) {
		t.Fatal(response.Code, response.Body.String())
	}
	server.PickFolder = func() (string, error) { return second, nil }
	if response := post(""); response.Code != 200 {
		t.Fatal(response.Code, response.Body.String())
	}
	reloaded, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.Paths.DataRoot != second {
		t.Fatal(reloaded.Paths.DataRoot)
	}
	server.PickFolder = func() (string, error) { return "", fmt.Errorf("folder selection was canceled") }
	if response := post(""); response.Code != 409 {
		t.Fatal(response.Code, response.Body.String())
	}
	reloaded, err = handler.Load(source)
	if err != nil || reloaded.Paths.DataRoot != second {
		t.Fatal(reloaded.Paths.DataRoot, err)
	}
}

func TestSettingsPreferSavedFileOverYAMLAndEnvWins(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	source := filepath.Join(t.TempDir(), "config.yaml")
	yamlRoot := t.TempDir()
	if err := os.WriteFile(source, []byte("paths:\n  data_root: "+yamlRoot+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	savedRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(filepath.Dir(source), "nib-settings.json"), []byte(`{"data_root":"`+savedRoot+`"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Paths.DataRoot != savedRoot {
		t.Fatalf("data_root=%q want saved %q", cfg.Paths.DataRoot, savedRoot)
	}
	t.Setenv("NIB_DATA_ROOT", t.TempDir())
	cfg, err = handler.Load(source)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Paths.DataRoot != os.Getenv("NIB_DATA_ROOT") {
		t.Fatalf("data_root=%q want env", cfg.Paths.DataRoot)
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
	cfg := handler.DefaultConfig()
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
