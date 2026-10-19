package handler_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestUnixLaunchersForwardPathsArgumentsAndExitCode(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix entry points require bash")
	}
	bash, err := exec.LookPath("bash")
	if err != nil {
		t.Skip("bash is not installed")
	}
	repo := filepath.Dir(repoConfigPath(t))
	for _, tc := range []struct {
		name, os, arch, script, binary string
	}{
		{"linux-amd64", "Linux", "x86_64", "bin/linux/run.sh", "bin/linux/nib-handler-linux-amd64"},
		{"linux-root-entry", "Linux", "x86_64", "Start NiB.sh", "bin/linux/nib-handler-linux-amd64"},
		{"macos-amd64", "Darwin", "x86_64", "bin/macos/start.command", "bin/macos/nib-handler-darwin-amd64"},
		{"macos-root-entry", "Darwin", "arm64", "Start NiB.command", "bin/macos/nib-handler-darwin-arm64"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := filepath.Join(t.TempDir(), "runtime with spaces")
			for _, path := range []string{"bin/linux/run.sh", "bin/macos/start.command", "Start NiB.sh", "Start NiB.command"} {
				body, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(path)))
				if err != nil {
					t.Fatal(err)
				}
				writeExecutable(t, filepath.Join(root, filepath.FromSlash(path)), string(body))
			}
			tools := filepath.Join(root, "tools")
			writeExecutable(t, filepath.Join(tools, "uname"), "#!/bin/bash\ncase \"$1\" in -s) echo "+tc.os+" ;; -m) echo "+tc.arch+" ;; esac\n")
			writeExecutable(t, filepath.Join(tools, "curl"), "#!/bin/bash\necho unexpected-download >&2\nexit 90\n")
			writeExecutable(t, filepath.Join(root, filepath.FromSlash(tc.binary)), "#!/bin/bash\nprintf '%s\\n' \"$@\"\nexit 23\n")
			for _, invocation := range []struct {
				args, want []string
			}{
				{
					[]string{"-host", "localhost", "-config", "override with spaces.yaml"},
					[]string{"-config", filepath.Join(root, "config.yaml"), "-host", "localhost", "-config", "override with spaces.yaml", ""},
				},
				{
					[]string{"diagnostics", "-request", "request with spaces.yaml", "-config", "override.yaml"},
					[]string{"diagnostics", "-config", filepath.Join(root, "config.yaml"), "-request", "request with spaces.yaml", "-config", "override.yaml", ""},
				},
				{
					[]string{"status"},
					[]string{"status", "-config", filepath.Join(root, "config.yaml"), ""},
				},
				{
					[]string{"stop"},
					[]string{"stop", "-config", filepath.Join(root, "config.yaml"), ""},
				},
				{
					[]string{"nib://connect?origin=http://hub.example:8000"},
					[]string{"-config", filepath.Join(root, "config.yaml"), "nib://connect?origin=http://hub.example:8000", ""},
				},
			} {
				cmd := exec.Command(bash, append([]string{filepath.Join(root, filepath.FromSlash(tc.script))}, invocation.args...)...)
				cmd.Dir = t.TempDir()
				cmd.Env = append(os.Environ(), "PATH="+tools+string(os.PathListSeparator)+os.Getenv("PATH"), "NIB_SKIP_URI_REGISTRATION=1")
				out, err := cmd.CombinedOutput()
				exit, ok := err.(*exec.ExitError)
				if !ok || exit.ExitCode() != 23 {
					t.Fatalf("exit=%v output=%s", err, out)
				}
				expected := invocation.want
				if tc.os == "Darwin" {
					if strings.HasPrefix(invocation.args[0], "-") {
						expected = append([]string{"start"}, expected...)
					} else if strings.HasPrefix(invocation.args[0], "nib://") {
						expected = append([]string{"connect"}, expected...)
					}
				}
				want := strings.Join(expected, "\n")
				if string(out) != want {
					t.Fatalf("output=%q want=%q", out, want)
				}
			}
		})
	}
}

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
	if !strings.Contains(string(out), "background startup failed") || !strings.Contains(string(out), "serve\n-config\n") {
		t.Fatalf("launcher did not fall back to foreground serve: %s", out)
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
