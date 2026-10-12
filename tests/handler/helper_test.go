package handler_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

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

func testServer() *handler.Server {
	srv := handler.NewServer(handler.Config{
		Handler: handler.Addr{Host: "127.0.0.1", Port: 8765},
		Review:  handler.Addr{Host: "127.0.0.1", Port: 8766},
		Paths:   handler.Paths{Artifacts: "/tmp/artifacts"},
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
