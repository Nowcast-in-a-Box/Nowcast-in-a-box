package handler_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestLoadConfigFile(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	dir := t.TempDir()
	path := writeConfig(t, dir, "handler:\n  host: 127.0.0.1\n  port: 9001\nreview:\n  host: 127.0.0.1\n  port: 9002\npaths:\n  artifacts: blobs\n")
	cfg, err := handler.Load(path)
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
	_, err := handler.Load(filepath.Join(t.TempDir(), "nope.yaml"))
	if err == nil {
		t.Fatal("expected error for missing -config path")
	}
}

func TestLoadConfigDefaultsFillGaps(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	dir := t.TempDir()
	path := writeConfig(t, dir, "handler:\n  port: 9100\n")
	cfg, err := handler.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	d := handler.DefaultConfig()
	if cfg.Handler.Host != d.Handler.Host {
		t.Fatalf("host=%q", cfg.Handler.Host)
	}
	if cfg.Handler.Port != 9100 {
		t.Fatalf("port=%d", cfg.Handler.Port)
	}
	if cfg.Review.Port != d.Review.Port {
		t.Fatalf("review.port=%d", cfg.Review.Port)
	}
}

func TestLoadConfigInvalidYAML(t *testing.T) {
	dir := t.TempDir()
	path := writeConfig(t, dir, "handler: [\n")
	_, err := handler.Load(path)
	if err == nil {
		t.Fatal("expected parse error")
	}
}

func TestLoadConfigUnknownKeysIgnored(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	dir := t.TempDir()
	path := writeConfig(t, dir, "handler:\n  port: 9200\nfuture_hub_key: true\n")
	cfg, err := handler.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Handler.Port != 9200 {
		t.Fatalf("port=%d", cfg.Handler.Port)
	}
}

func TestLoadRelativeDataRoot(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	dir := t.TempDir()
	path := writeConfig(t, dir, "paths:\n  data_root: data\n")
	cfg, err := handler.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	want, err := filepath.Abs(filepath.Join(dir, "data"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Paths.DataRoot != want {
		t.Fatalf("data_root=%q want %q", cfg.Paths.DataRoot, want)
	}
}

func TestLoadDataRootEnvOverridesYAML(t *testing.T) {
	dir := t.TempDir()
	envRoot := filepath.Join(dir, "from-env")
	t.Setenv("NIB_DATA_ROOT", envRoot)
	path := writeConfig(t, dir, "paths:\n  data_root: from-yaml\n")
	cfg, err := handler.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Paths.DataRoot != envRoot {
		t.Fatalf("data_root=%q want %q", cfg.Paths.DataRoot, envRoot)
	}
}

func TestLoadNIBConfigEnv(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	dir := t.TempDir()
	path := writeConfig(t, dir, "handler:\n  port: 9300\n")
	t.Setenv("NIB_CONFIG", path)
	cfg, err := handler.Load("")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Handler.Port != 9300 {
		t.Fatalf("port=%d", cfg.Handler.Port)
	}
	if cfg.Source != path {
		t.Fatalf("source=%q", cfg.Source)
	}
}

func TestRepoRootConfigYAML(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	path := repoConfigPath(t)
	cfg, err := handler.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := handler.RequireLoopbackHost(cfg.Handler.Host); err != nil {
		t.Fatal(err)
	}
	if err := handler.RequireLoopbackHost(cfg.Review.Host); err != nil {
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
	cfg := handler.DefaultConfig()
	got, err := handler.ApplyOverrides(cfg, "localhost", "9400")
	if err != nil {
		t.Fatal(err)
	}
	if got.Handler.Host != "localhost" {
		t.Fatalf("host=%q", got.Handler.Host)
	}
	if got.Handler.Port != 9400 {
		t.Fatalf("port=%d", got.Handler.Port)
	}
	if got.Review.Port != cfg.Review.Port {
		t.Fatalf("review.port=%d", got.Review.Port)
	}
}

func TestApplyOverridesInvalidPort(t *testing.T) {
	cfg := handler.DefaultConfig()
	for _, port := range []string{"nope", "0", "65536", "-1"} {
		if _, err := handler.ApplyOverrides(cfg, "", port); err == nil {
			t.Fatalf("expected invalid port %q", port)
		}
	}
}

func TestFindConfigFileWalksParents(t *testing.T) {
	root := t.TempDir()
	child := filepath.Join(root, "nested", "cwd")
	if err := os.MkdirAll(child, 0o700); err != nil {
		t.Fatal(err)
	}
	path := writeConfig(t, root, "handler:\n  port: 8765\n")
	t.Setenv("NIB_CONFIG", "")
	t.Setenv("NIB_DATA_ROOT", "")
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
	cfg, err := handler.Load("")
	if err != nil {
		t.Fatal(err)
	}
	got, err := filepath.EvalSymlinks(cfg.Source)
	if err != nil {
		got = cfg.Source
	}
	want, err := filepath.EvalSymlinks(path)
	if err != nil {
		want = path
	}
	if got != want {
		t.Fatalf("found %q want %q", got, want)
	}
}
