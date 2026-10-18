package handler_test

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
	"gopkg.in/yaml.v3"
)

func TestConnectURIRejectsAnythingExceptHubOrigin(t *testing.T) {
	for _, raw := range []string{
		"https://hub.example", "nib://run?origin=http://127.0.0.1:5173",
		"nib://connect?origin=http://user:pass@hub", "nib://connect/execute?origin=http://hub",
		"nib://connect?origin=http://hub&command=run", "nib://connect?origin=http://hub&origin=http://evil",
		"nib://connect?origin=http://hub&callback=http://evil", "nib://connect?origin=%XX",
	} {
		if _, err := handler.ParseConnectURI(raw); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
	origin, err := handler.ParseConnectURI("nib://connect?origin=http://127.0.0.1:5173/checkout")
	if err != nil || origin != "http://127.0.0.1:5173" {
		t.Fatal(origin, err)
	}
}

func liveHandler(t *testing.T) (*handler.Server, handler.Config, *httptest.Server) {
	t.Helper()
	httpServer := httptest.NewUnstartedServer(nil)
	port := httpServer.Listener.Addr().(*net.TCPAddr).Port
	cfg := handler.DefaultConfig()
	cfg.Handler.Port = port
	cfg.Source = filepath.Join(t.TempDir(), "config.yaml")
	cfg.Paths.Runs, cfg.Paths.Artifacts = t.TempDir(), t.TempDir()
	cfg.Paths.Resources = testResourcesPath(t)
	cfg.Runtime.Token = "local-token"
	cfg.Runtime.AllowedOrigins = []string{"http://hub.example:8123"}
	raw, err := yaml.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfg.Source, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	httpServer.Config.Handler = server.Routes()
	httpServer.Start()
	t.Cleanup(httpServer.Close)
	return server, cfg, httpServer
}

func TestSchemeUsesConfiguredInstanceWithoutEnrollingOrigins(t *testing.T) {
	_, cfg, _ := liveHandler(t)
	before, _ := os.ReadFile(cfg.Source)
	trusted := "nib://connect?origin=http://hub.example:8123"
	for _, args := range [][]string{
		{trusted, "-config", cfg.Source},
		{"-config", cfg.Source, trusted},
		{"connect", "-config", cfg.Source, trusted},
	} {
		if code := handler.Main(args); code != 0 {
			t.Fatalf("args=%v exit=%d", args, code)
		}
	}
	if code := handler.Main([]string{"connect", "-config", cfg.Source, "nib://connect?origin=http://evil.example"}); code != 1 {
		t.Fatalf("untrusted origin accepted: %d", code)
	}
	after, _ := os.ReadFile(cfg.Source)
	if string(before) != string(after) {
		t.Fatal("scheme changed local origin permissions")
	}
}

func TestPairPreflightUsesTrustedOriginsAndNeverReturnsCredentials(t *testing.T) {
	server, _, _ := liveHandler(t)
	for _, origin := range []string{"http://hub.example:8123", "http://evil.example"} {
		request := httptest.NewRequest(http.MethodOptions, "http://127.0.0.1/v1/pair?origin="+origin, nil)
		request.Header.Set("Origin", origin)
		request.Header.Set("Access-Control-Request-Private-Network", "true")
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, request)
		if strings.Contains(response.Body.String(), "local-token") || response.Header().Get("Cache-Control") != "no-store" {
			t.Fatal("preflight exposed or cached a credential")
		}
		if strings.Contains(origin, "hub.example") {
			if response.Code != http.StatusNoContent || response.Header().Get("Access-Control-Allow-Origin") != origin {
				t.Fatal(response.Code, response.Header())
			}
		} else if response.Code != http.StatusForbidden || response.Header().Get("Access-Control-Allow-Origin") != "" {
			t.Fatal(response.Code, response.Header())
		}
	}
}

func TestPairRequiresPreconfiguredOrigin(t *testing.T) {
	server, cfg, _ := liveHandler(t)
	for _, test := range []struct {
		origin string
		status int
	}{
		{"http://hub.example:8123", http.StatusOK},
		{"http://evil.example", http.StatusForbidden},
	} {
		request := httptest.NewRequest(http.MethodGet, "http://127.0.0.1/v1/pair?origin="+test.origin, nil)
		request.Header.Set("Origin", test.origin)
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, request)
		if response.Code != test.status {
			t.Fatal(response.Code, response.Body.String())
		}
		if test.status == http.StatusOK {
			var body map[string]string
			if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if body["token"] != "local-token" || body["url"] != fmt.Sprintf("http://127.0.0.1:%d", cfg.Handler.Port) {
				t.Fatal(body)
			}
		} else if strings.Contains(response.Body.String(), "local-token") {
			t.Fatal("denied pairing leaked a token")
		}
	}
}
