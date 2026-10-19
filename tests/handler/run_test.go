package handler_test

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestPrepareValidArgs(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	path := writeConfig(t, t.TempDir(), "handler:\n  host: 127.0.0.1\n  port: 8765\nreview:\n  host: 127.0.0.1\n  port: 8766\n")
	srv, addr, err := handler.Prepare([]string{"-config", path})
	if err != nil {
		t.Fatal(err)
	}
	if addr != "127.0.0.1:8765" {
		t.Fatalf("addr=%q", addr)
	}
	if srv.Config.Source != path {
		t.Fatalf("source=%q", srv.Config.Source)
	}
}

func TestPrepareMissingConfig(t *testing.T) {
	_, _, err := handler.Prepare([]string{"-config", filepath.Join(t.TempDir(), "nope.yaml")})
	if err == nil {
		t.Fatal("expected error for missing config")
	}
}

func TestPrepareNonLoopbackHost(t *testing.T) {
	path := writeConfig(t, t.TempDir(), "handler:\n  host: 127.0.0.1\n  port: 8765\n")
	_, _, err := handler.Prepare([]string{"-config", path, "-host", "0.0.0.0"})
	if err == nil {
		t.Fatal("expected non-loopback host to fail")
	}
}

func TestPrepareNonLoopbackReviewHost(t *testing.T) {
	path := writeConfig(t, t.TempDir(), "handler:\n  host: 127.0.0.1\n  port: 8765\nreview:\n  host: 8.8.8.8\n  port: 8766\n")
	_, _, err := handler.Prepare([]string{"-config", path})
	if err == nil {
		t.Fatal("expected non-loopback review host to fail")
	}
	if !strings.Contains(err.Error(), "review host:") {
		t.Fatalf("error=%v", err)
	}
}
