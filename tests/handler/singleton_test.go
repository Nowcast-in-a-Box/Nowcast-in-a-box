package handler_test

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func singletonRecord(t *testing.T) (string, map[string]any) {
	t.Helper()
	cache, err := os.UserCacheDir()
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(cache, "NiB", "handler", "session.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var record map[string]any
	if err := json.Unmarshal(raw, &record); err != nil {
		t.Fatal(err)
	}
	return path, record
}

func TestSingletonReusesSameBuildAcrossDirectoriesAndPorts(t *testing.T) {
	isolatedInstall(t)
	first := writeInstallConfig(t)
	process := serveInTerminal(t, first)
	_, before := singletonRecord(t)
	other := writeInstallConfig(t)
	if code := handler.Main([]string{"start", "-config", other.Source}); code != 0 {
		t.Fatalf("duplicate start exit %d", code)
	}
	_, after := singletonRecord(t)
	if before["instance_id"] != after["instance_id"] || before["pid"] != after["pid"] {
		t.Fatal("duplicate start replaced running instance")
	}
	moved := filepath.Join(filepath.Dir(other.Source), "renamed.yaml")
	if err := os.Rename(other.Source, moved); err != nil {
		t.Fatal(err)
	}
	if code := handler.Main([]string{"uninstall", "-config", moved}); code != 0 {
		t.Fatalf("uninstall from different installation exit %d", code)
	}
	waitProcessExit(t, process)
	if _, err := os.Stat(filepath.Join(first.Paths.Runs)); err != nil {
		t.Fatalf("uninstall removed runs: %v", err)
	}
	if _, err := os.Stat(filepath.Join(first.Paths.Artifacts)); err != nil {
		t.Fatalf("uninstall removed artifacts: %v", err)
	}
	if response, err := http.Get(before["url"].(string) + "/v1/identity"); err == nil {
		response.Body.Close()
		t.Fatal("uninstalled Handler still listening")
	}
}

func TestSingletonMigratesLegacyRecordAndPreservesData(t *testing.T) {
	isolatedInstall(t)
	cfg := writeInstallConfig(t)
	process := serveInTerminal(t, cfg)
	path, record := singletonRecord(t)
	delete(record, "binary_sha256")
	legacy := filepath.Join(filepath.Dir(path), "old-profile", "session.json")
	if err := os.MkdirAll(filepath.Dir(legacy), 0700); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(record)
	if err := os.WriteFile(legacy, raw, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if code := handler.Main([]string{"stop", "-config", cfg.Source}); code != 0 {
		t.Fatalf("legacy stop exit %d", code)
	}
	waitProcessExit(t, process)
	if _, err := os.Stat(legacy); !os.IsNotExist(err) {
		t.Fatalf("legacy control not retired: %v", err)
	}
}
