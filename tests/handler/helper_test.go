package handler_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestMain(m *testing.M) {
	os.Setenv("NIB_SKIP_URI_REGISTRATION", "1")
	if os.Getenv("NIB_TEST_SERVE_CHILD") == "1" || os.Getenv("NIB_TEST_CLI_CHILD") == "1" {
		os.Exit(m.Run())
	}
	home, err := os.MkdirTemp("", "nib-handler-tests-")
	if err != nil {
		panic(err)
	}
	os.Setenv("HOME", home)
	os.Setenv("XDG_CACHE_HOME", filepath.Join(home, "cache"))
	os.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	os.Setenv("LOCALAPPDATA", filepath.Join(home, "local"))
	os.Setenv("APPDATA", filepath.Join(home, "app"))
	code := m.Run()
	os.RemoveAll(home)
	os.Exit(code)
}
func repoConfigPath(t *testing.T) string {
	t.Helper()
	path := filepath.Join("..", "..", "config.yaml")
	abs, err := filepath.Abs(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(abs); err != nil {
		t.Skip(err)
	}
	return abs
}

func testResourcesPath(t *testing.T) string {
	t.Helper()
	path, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	return path
}

func testResources(t *testing.T) *handler.Resources {
	t.Helper()
	resources, err := handler.LoadResources(testResourcesPath(t))
	if err != nil {
		t.Fatal(err)
	}
	return resources
}

func testConfig(t *testing.T) handler.Config {
	t.Helper()
	cfg := handler.DefaultConfig()
	cfg.Paths.Resources = testResourcesPath(t)
	cfg.DataRoots = map[string]string{}
	return cfg
}

func writeSevirData(t *testing.T, cfg *handler.Config) string {
	t.Helper()
	root := t.TempDir()
	if cfg.DataRoots == nil {
		cfg.DataRoots = map[string]string{}
	}
	cfg.DataRoots["sevir"] = root
	for _, name := range []string{"SEVIR/processed/sevir_vil.h5", "source/CATALOG.csv"} {
		target := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(target, []byte("fixture"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func writeCmaData(t *testing.T, cfg *handler.Config) string {
	t.Helper()
	root := t.TempDir()
	if cfg.DataRoots == nil {
		cfg.DataRoots = map[string]string{}
	}
	cfg.DataRoots["cma_radar"] = root
	return root
}

func testServer() *handler.Server {
	srv := handler.NewServer(handler.Config{
		Handler: handler.Addr{Host: "127.0.0.1", Port: 8765},
		Review:  handler.Addr{Host: "127.0.0.1", Port: 8766},
		Paths:   handler.Paths{Artifacts: "/tmp/artifacts", Resources: "../.."},
		Source:  "/tmp/config.yaml",
	})
	srv.Preflight = []func() handler.Check{
		func() handler.Check {
			return handler.Check{ID: "docker", OK: true, Message: "test"}
		},
	}
	return srv
}

func writeConfig(t *testing.T, dir, raw string) string {
	t.Helper()
	path := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}
