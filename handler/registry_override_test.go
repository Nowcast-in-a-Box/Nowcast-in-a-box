package handler

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRegistryOverridePrecedence(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(path, []byte("paths:\n  data_root: yaml\nruntime:\n  image_override: registry.example/yaml\n  image_registry: official.example/nib\n  image_mirror: mirror.example/nib\n"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("NIB_IMAGE_OVERRIDE", "")
	t.Setenv("NIB_DATA_ROOT", "")
	check := func(layer string) {
		t.Helper()
		cfg, err := Load(path)
		if err != nil {
			t.Fatal(err)
		}
		if got := cfg.DataRootFallback; got != filepath.Join(dir, layer) {
			t.Fatalf("%s: data root = %q", layer, got)
		}
		for _, preset := range []string{"auto", "official", "mirror"} {
			cfg.Runtime.ImageSource = preset
			s := Server{Config: cfg}
			got := s.imageCandidates("model:test")
			if len(got) != 1 || got[0] != "registry.example/"+layer+"/model:test" {
				t.Fatalf("%s, %s: candidates = %v", layer, preset, got)
			}
		}
	}
	check("yaml")
	if err := os.WriteFile(filepath.Join(dir, settingsFileName), []byte(`{"data_root":"saved","image_override":"registry.example/saved"}`), 0600); err != nil {
		t.Fatal(err)
	}
	check("saved")
	t.Setenv("NIB_DATA_ROOT", filepath.Join(dir, "env"))
	t.Setenv("NIB_IMAGE_OVERRIDE", "registry.example/env/")
	check("env")
	t.Setenv("NIB_IMAGE_OVERRIDE", "https://invalid.example")
	if _, err := Load(path); err == nil {
		t.Fatal("accepted invalid NIB_IMAGE_OVERRIDE")
	}
}
