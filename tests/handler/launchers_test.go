package handler_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestMacLauncherFallsBackToForegroundOnExitFive(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix entry points require bash")
	}
	bash, err := exec.LookPath("bash")
	if err != nil {
		t.Skip("bash is not installed")
	}
	repo := filepath.Dir(repoConfigPath(t))
	root := filepath.Join(t.TempDir(), "runtime with spaces")
	body, err := os.ReadFile(filepath.Join(repo, "bin", "macos", "start.command"))
	if err != nil {
		t.Fatal(err)
	}
	writeExecutable(t, filepath.Join(root, "bin", "macos", "start.command"), string(body))
	tools := filepath.Join(root, "tools")
	writeExecutable(t, filepath.Join(tools, "uname"), "#!/bin/bash\ncase \"$1\" in -s) echo Darwin ;; -m) echo arm64 ;; esac\n")
	fake := `#!/bin/bash
if [[ "$1" == "start" ]]; then
  echo "launchd bootstrap failed" >&2
  exit 5
fi
if [[ "$1" == "serve" ]]; then
  printf '%s\n' "$@"
  exit 23
fi
exit 24
`
	writeExecutable(t, filepath.Join(root, "bin", "macos", "nib-handler-darwin-arm64"), fake)
	cmd := exec.Command(bash, filepath.Join(root, "bin", "macos", "start.command"))
	cmd.Dir = t.TempDir()
	cmd.Env = append(os.Environ(), "PATH="+tools+string(os.PathListSeparator)+os.Getenv("PATH"), "NIB_SKIP_URI_REGISTRATION=1")
	out, err := cmd.CombinedOutput()
	exit, ok := err.(*exec.ExitError)
	if !ok || exit.ExitCode() != 23 {
		t.Fatalf("exit=%v output=%s", err, out)
	}
}

func writeExecutable(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
}
