package handler

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type managementRunner struct {
	base    *imageTestRunner
	offline bool
	failRun bool
	alive   bool
}

func (r *managementRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	if len(args) > 0 && ((args[0] == "info" && r.offline) || args[0] == "run" || args[0] == "container") {
		r.base.calls = append(r.base.calls, strings.Join(args, " "))
		if args[0] == "container" && r.alive {
			return 0, []byte("true\n"), nil
		}
		if args[0] == "run" && !r.failRun {
			return 0, nil, nil
		}
		return 1, []byte("unavailable"), nil
	}
	return r.base.Run(ctx, args...)
}

func TestDiagnosticsImagePrepareOfflineNeverPulls(t *testing.T) {
	s, base := managementServer(t)
	s.Runner = &managementRunner{base: base, offline: true}
	if _, err := s.ensureImage(context.Background(), "diagnostics:test"); err == nil {
		t.Fatal("accepted offline daemon")
	}
	for _, call := range base.calls {
		if strings.HasPrefix(call, "pull ") {
			t.Fatalf("offline pull: %s", call)
		}
	}
}

func TestDiagnosticsImagePrepareEmbeddedVerification(t *testing.T) {
	s, base := managementServer(t)
	runner := &managementRunner{base: base}
	s.Runner = runner
	s.Resources.Models["data"]["model"].Weights.Embedded = true
	if err := s.prepareModelWeight(context.Background(), "data", "model"); err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(base.calls, "\n")
	for _, want := range []string{"pull registry.example/nib/model:test", "run --rm --network none --read-only --pull never --entrypoint python model:test -c", "hashlib.sha256()", "/opt/nib/weights/weight.bin"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing %q in %s", want, joined)
		}
	}
	runner.failRun = true
	if err := s.prepareModelWeight(context.Background(), "data", "model"); err == nil {
		t.Fatal("accepted failed embedded checksum")
	}
}

func TestDiagnosticsImagePrepareRetryBeforeCompose(t *testing.T) {
	for _, offline := range []bool{false, true} {
		t.Run(map[bool]string{false: "online", true: "offline"}[offline], func(t *testing.T) {
			s, base := managementServer(t)
			s.Runner = &managementRunner{base: base, offline: offline}
			s.Config.Paths.Runs = t.TempDir()
			dir := filepath.Join(s.Config.Paths.Runs, "run", "logs", "diagnostics-op")
			if err := os.MkdirAll(dir, 0700); err != nil {
				t.Fatal(err)
			}
			s.executeDiagnostics("run", DiagnosticOperation{ID: "op"})
			pull, up := -1, -1
			for i, call := range base.calls {
				if strings.HasPrefix(call, "pull ") {
					pull = i
				}
				if strings.Contains(call, " up ") {
					up = i
				}
			}
			if offline {
				if pull != -1 || up != -1 {
					t.Fatalf("offline launch: %v", base.calls)
				}
			} else if pull < 0 || up <= pull {
				t.Fatalf("compose before preparation: %v", base.calls)
			}
		})
	}
}

func TestDiagnosticsImagePrepareViewerChecksCurrentDaemon(t *testing.T) {
	s, base := managementServer(t)
	runner := &managementRunner{base: base, alive: true}
	s.Runner = runner
	s.diagnosticsViewerContainer = "viewer"
	if !s.diagnosticsViewerAlive() {
		t.Fatal("running viewer rejected")
	}
	runner.alive = false
	if s.diagnosticsViewerAlive() {
		t.Fatal("stale viewer reused")
	}
}
