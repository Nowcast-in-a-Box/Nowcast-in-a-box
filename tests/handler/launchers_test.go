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
		{"linux-arm64-wrapper", "Linux", "aarch64", "scripts/run.sh", "bin/linux/nib-handler-linux-arm64"},
		{"macos-amd64", "Darwin", "x86_64", "bin/macos/start.command", "bin/macos/nib-handler-darwin-amd64"},
		{"macos-arm64-wrapper", "Darwin", "arm64", "scripts/start.command", "bin/macos/nib-handler-darwin-arm64"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := filepath.Join(t.TempDir(), "runtime with spaces")
			for _, path := range []string{"bin/linux/run.sh", "bin/macos/start.command", "scripts/run.sh", "scripts/start.command"} {
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
			} {
				cmd := exec.Command(bash, append([]string{filepath.Join(root, filepath.FromSlash(tc.script))}, invocation.args...)...)
				cmd.Dir = t.TempDir()
				cmd.Env = append(os.Environ(), "PATH="+tools+string(os.PathListSeparator)+os.Getenv("PATH"))
				out, err := cmd.CombinedOutput()
				exit, ok := err.(*exec.ExitError)
				if !ok || exit.ExitCode() != 23 {
					t.Fatalf("exit=%v output=%s", err, out)
				}
				want := strings.Join(invocation.want, "\n")
				if string(out) != want {
					t.Fatalf("output=%q want=%q", out, want)
				}
			}
		})
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
