package handler

import (
	"net"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAutomaticReplacementRefusesBusyHandler(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CACHE_HOME", home)
	t.Setenv("LOCALAPPDATA", home)
	listener := httptest.NewUnstartedServer(nil)
	defer listener.Close()
	cfg := DefaultConfig()
	cfg.Handler.Port = listener.Listener.Addr().(*net.TCPAddr).Port
	cfg.Source = filepath.Join(home, "old-config.yaml")
	cfg.Paths.Resources = ".."
	server := NewServer(cfg)
	listener.Config.Handler = server.Routes()
	listener.Start()
	session := localSession{ConfigID: configID(cfg), InstanceID: server.instanceID, URL: addressURL(cfg.Handler), ReviewURL: addressURL(cfg.Review), PID: os.Getpid(), Token: server.Token, BinarySHA256: "previous-build", ConfigSource: cfg.Source}
	if err := saveSession(cfg, session); err != nil {
		t.Fatal(err)
	}
	server.executionMu.Lock()
	defer server.executionMu.Unlock()
	if start, err := ensureSingleton(cfg); start || err == nil || !strings.Contains(err.Error(), "409") {
		t.Fatalf("busy upgrade: start=%v err=%v", start, err)
	}
	saved, err := readSession(cfg)
	if err != nil || saved.InstanceID != session.InstanceID {
		t.Fatalf("busy instance lost control state: %v", err)
	}
	if server.stopping {
		t.Fatal("busy instance entered shutdown")
	}
}

func TestConfigIdentityDoesNotDependOnWorkingDirectoryForEmptyPaths(t *testing.T) {
	cfg := DefaultConfig()
	cfg.Source = filepath.Join(t.TempDir(), "config.yaml")
	cfg.Paths.Resources = filepath.Dir(cfg.Source)
	cfg.Paths.Runs = cfg.Paths.Resources
	cfg.Paths.Artifacts = cfg.Paths.Resources
	before := configID(cfg)
	original, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(t.TempDir()); err != nil {
		t.Fatal(err)
	}
	defer os.Chdir(original)
	if after := configID(cfg); after != before {
		t.Fatal("identity changed solely with working directory")
	}
}

func TestControlRootDoesNotDependOnConfigDirectory(t *testing.T) {
	first := DefaultConfig()
	other := first
	other.Source = filepath.Join(t.TempDir(), "renamed.yaml")
	other.Handler.Port++
	a, err := sessionDirectory(first)
	if err != nil {
		t.Fatal(err)
	}
	b, err := sessionDirectory(other)
	if err != nil {
		t.Fatal(err)
	}
	if a != b {
		t.Fatal("control root remains installation scoped")
	}
}
