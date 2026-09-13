package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadConfigFile(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	raw := []byte("handler:\n  host: 127.0.0.1\n  port: 9001\nreview:\n  host: 127.0.0.1\n  port: 9002\npaths:\n  artifacts: blobs\n")
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := loadConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Handler.Port != 9001 {
		t.Fatalf("handler.port=%d", cfg.Handler.Port)
	}
	if cfg.Review.Port != 9002 {
		t.Fatalf("review.port=%d", cfg.Review.Port)
	}
	wantArtifacts, err := filepath.Abs(filepath.Join(dir, "blobs"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Paths.Artifacts != wantArtifacts {
		t.Fatalf("artifacts=%q want %q", cfg.Paths.Artifacts, wantArtifacts)
	}
	if cfg.Source != path {
		t.Fatalf("source=%q", cfg.Source)
	}
}

func TestLoadConfigMissingExplicit(t *testing.T) {
	_, err := loadConfig(filepath.Join(t.TempDir(), "nope.yaml"))
	if err == nil {
		t.Fatal("expected error for missing -config path")
	}
}

func TestLoadConfigDefaultsFillGaps(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(path, []byte("handler:\n  port: 9100\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := loadConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Handler.Host != defaultHandlerHost {
		t.Fatalf("host=%q", cfg.Handler.Host)
	}
	if cfg.Handler.Port != 9100 {
		t.Fatalf("port=%d", cfg.Handler.Port)
	}
	if cfg.Review.Port != defaultReviewPort {
		t.Fatalf("review.port=%d", cfg.Review.Port)
	}
}

func TestRepoRootConfigYAML(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	path := filepath.Join("..", "config.yaml")
	if _, err := os.Stat(path); err != nil {
		t.Skip(err)
	}
	cfg, err := loadConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := requireLoopbackHost(cfg.Handler.Host); err != nil {
		t.Fatal(err)
	}
	if err := requireLoopbackHost(cfg.Review.Host); err != nil {
		t.Fatal(err)
	}
	if cfg.Handler.Port != 8765 {
		t.Fatalf("handler.port=%d", cfg.Handler.Port)
	}
	if cfg.Review.Port != 8766 {
		t.Fatalf("review.port=%d", cfg.Review.Port)
	}
	if !filepath.IsAbs(cfg.Paths.Artifacts) {
		t.Fatalf("artifacts should be absolute, got %q", cfg.Paths.Artifacts)
	}
	if filepath.Base(cfg.Paths.Artifacts) != "artifacts" {
		t.Fatalf("artifacts=%q", cfg.Paths.Artifacts)
	}
}

func TestApplyOverrides(t *testing.T) {
	cfg := defaultConfig()
	got, err := applyOverrides(cfg, "localhost", "9400")
	if err != nil {
		t.Fatal(err)
	}
	if got.Handler.Host != "localhost" {
		t.Fatalf("host=%q", got.Handler.Host)
	}
	if got.Handler.Port != 9400 {
		t.Fatalf("port=%d", got.Handler.Port)
	}
	if _, err := applyOverrides(cfg, "", "nope"); err == nil {
		t.Fatal("expected invalid port error")
	}
}

func TestFindConfigFileWalksParents(t *testing.T) {
	root := t.TempDir()
	child := filepath.Join(root, "nested", "cwd")
	if err := os.MkdirAll(child, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, configFileName)
	if err := os.WriteFile(path, []byte("handler:\n  port: 8765\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("NIB_CONFIG", "")
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = os.Chdir(wd)
	}()
	if err := os.Chdir(child); err != nil {
		t.Fatal(err)
	}
	found, ok := findConfigFile()
	if !ok {
		t.Fatal("expected to find config.yaml in a parent directory")
	}
	got, err := filepath.EvalSymlinks(found)
	if err != nil {
		got = found
	}
	want, err := filepath.EvalSymlinks(path)
	if err != nil {
		want = path
	}
	if got != want {
		t.Fatalf("found %q want %q", got, want)
	}
}
