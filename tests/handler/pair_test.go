package handler_test

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestConnectURIRejectsAnythingExceptHubOrigin(t *testing.T) {
	for _, raw := range []string{"https://hub.example", "nib://run?origin=http://127.0.0.1:5173", "nib://connect?origin=http://user:pass@hub"} {
		if _, err := handler.ParseConnectURI(raw); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
	origin, err := handler.ParseConnectURI("nib://connect?origin=http://127.0.0.1:5173/checkout")
	if err != nil || origin != "http://127.0.0.1:5173" {
		t.Fatal(origin, err)
	}
}

func TestPairRequiresRememberedOrigin(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte("runtime:\n  token: local-token\n  allowed_origins: []\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("NIB_CONFIG", path)
	cfg, err := handler.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	denied := httptest.NewRequest(http.MethodGet, "http://127.0.0.1/v1/pair?origin=http://127.0.0.1:5173", nil)
	denied.Header.Set("Origin", "http://127.0.0.1:5173")
	out := httptest.NewRecorder()
	server.Routes().ServeHTTP(out, denied)
	if out.Code != http.StatusForbidden {
		t.Fatal(out.Code, out.Body.String())
	}
	handler.SetHandlerRunningForTest(func() bool { return true })
	t.Cleanup(func() { handler.SetHandlerRunningForTest(nil) })
	if code := handler.Main([]string{"nib://connect?origin=http://127.0.0.1:5173"}); code != 0 {
		t.Fatal(code)
	}
	allowed := httptest.NewRequest(http.MethodGet, "http://127.0.0.1/v1/pair?origin=http://127.0.0.1:5173", nil)
	allowed.Header.Set("Origin", "http://127.0.0.1:5173")
	out = httptest.NewRecorder()
	server.Routes().ServeHTTP(out, allowed)
	if out.Code != http.StatusOK || !strings.Contains(out.Body.String(), "local-token") || !strings.Contains(out.Body.String(), "http://127.0.0.1:8765") {
		t.Fatal(out.Code, out.Body.String())
	}
	cfg.Handler.Port = 8775
	custom := handler.NewServer(cfg)
	out = httptest.NewRecorder()
	custom.Routes().ServeHTTP(out, allowed)
	if out.Code != http.StatusOK || !strings.Contains(out.Body.String(), "http://127.0.0.1:8775") {
		t.Fatal(out.Code, out.Body.String())
	}
}
