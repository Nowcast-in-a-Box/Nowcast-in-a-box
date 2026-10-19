package handler_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestLinuxUninstallerPreservesRunsAndArtifacts(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Linux uninstall script requires bash")
	}
	bash, err := exec.LookPath("bash")
	if err != nil {
		t.Skip("bash is not installed")
	}
	root, home, script := uninstallFixture(t, bash)
	out := runUninstall(t, bash, script, home, "--yes")
	for _, path := range []string{
		filepath.Join(home, ".local", "share", "applications", "nib-handler.desktop"),
		filepath.Join(home, ".cache", "NiB", "handler", "session.json"),
		filepath.Join(root, "nib-settings.json"),
		filepath.Join(root, "logs", "handler.log"),
	} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("expected %s to be removed; stat error=%v output=%s", path, err, out)
		}
	}
	for _, path := range []string{
		filepath.Join(root, "runs", "keep.txt"),
		filepath.Join(root, "artifacts", "keep.onnx"),
	} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("expected %s to be preserved: %v", path, err)
		}
	}
}

func TestLinuxUninstallerDryRunChangesNothing(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Linux uninstall script requires bash")
	}
	bash, err := exec.LookPath("bash")
	if err != nil {
		t.Skip("bash is not installed")
	}
	root, home, script := uninstallFixture(t, bash)
	out := runUninstall(t, bash, script, home, "--dry-run")
	if !strings.Contains(out, "would remove") {
		t.Fatalf("dry-run did not explain planned removals: %s", out)
	}
	for _, path := range []string{
		filepath.Join(home, ".local", "share", "applications", "nib-handler.desktop"),
		filepath.Join(home, ".cache", "NiB", "handler", "session.json"),
		filepath.Join(root, "nib-settings.json"),
		filepath.Join(root, "runs", "keep.txt"),
		filepath.Join(root, "artifacts", "keep.onnx"),
	} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("dry-run removed %s: %v", path, err)
		}
	}
}

func TestLinuxUninstallerPurgeKeepsMarkers(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Linux uninstall script requires bash")
	}
	bash, err := exec.LookPath("bash")
	if err != nil {
		t.Skip("bash is not installed")
	}
	root, home, script := uninstallFixture(t, bash)
	out := runUninstall(t, bash, script, home, "--yes", "--purge")
	for _, path := range []string{
		filepath.Join(root, "runs", "keep.txt"),
		filepath.Join(root, "artifacts", "keep.onnx"),
		filepath.Join(root, "resources"),
		filepath.Join(root, "tmp"),
		filepath.Join(root, "bin", "linux", "nib-handler-linux-amd64"),
	} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("expected %s to be purged; stat error=%v output=%s", path, err, out)
		}
	}
	for _, path := range []string{
		filepath.Join(root, "runs", ".gitkeep"),
		filepath.Join(root, "artifacts", ".gitkeep"),
	} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("expected marker %s to remain: %v", path, err)
		}
	}
}

func uninstallFixture(t *testing.T, bash string) (root, home, script string) {
	t.Helper()
	_ = bash
	repo := filepath.Dir(repoConfigPath(t))
	root = filepath.Join(t.TempDir(), "runtime with spaces")
	home = filepath.Join(t.TempDir(), "home")
	script = filepath.Join(root, "bin", "linux", "uninstall.sh")
	for _, path := range []string{
		"bin/linux/uninstall.sh",
	} {
		body, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(path)))
		if err != nil {
			t.Fatal(err)
		}
		writeExecutable(t, filepath.Join(root, filepath.FromSlash(path)), string(body))
	}
	writeExecutable(t, filepath.Join(root, "bin", "linux", "nib-handler-linux-amd64"), "#!/bin/bash\ncase \"$1\" in status) exit 1 ;; stop) exit 0 ;; *) exit 0 ;; esac\n")
	writeFile(t, filepath.Join(root, "config.yaml"), "handler: {}\\n")
	writeFile(t, filepath.Join(root, "nib-settings.json"), "{}\\n")
	writeFile(t, filepath.Join(root, "runs", "keep.txt"), "keep\\n")
	writeFile(t, filepath.Join(root, "artifacts", "keep.onnx"), "keep\\n")
	writeFile(t, filepath.Join(root, "resources", "generated.txt"), "generated\\n")
	writeFile(t, filepath.Join(root, "tmp", "generated.txt"), "generated\\n")
	writeFile(t, filepath.Join(root, "logs", "handler.log"), "log\\n")
	writeFile(t, filepath.Join(home, ".local", "share", "applications", "nib-handler.desktop"), "desktop\\n")
	writeFile(t, filepath.Join(home, ".cache", "NiB", "handler", "session.json"), "{}\\n")
	writeFile(t, filepath.Join(root, "runs", ".gitkeep"), "")
	writeFile(t, filepath.Join(root, "artifacts", ".gitkeep"), "")
	return root, home, script
}

func runUninstall(t *testing.T, bash, script, home string, args ...string) string {
	t.Helper()
	cmd := exec.Command(bash, append([]string{script}, args...)...)
	cmd.Dir = filepath.Dir(script)
	cmd.Env = append(os.Environ(),
		"HOME="+home,
		"XDG_DATA_HOME="+filepath.Join(home, ".local", "share"),
		"XDG_CACHE_HOME="+filepath.Join(home, ".cache"),
		"NIB_HANDLER_BIN="+filepath.Join(filepath.Dir(script), "nib-handler-linux-amd64"),
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("uninstall failed: %v\\n%s", err, out)
	}
	return string(out)
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}
