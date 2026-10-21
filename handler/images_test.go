package handler

import (
	"context"
	"strings"
	"testing"
)

type imageTestRunner struct {
	present map[string]bool
	calls   []string
}

func (r *imageTestRunner) Run(_ context.Context, args ...string) (int, []byte, error) {
	r.calls = append(r.calls, strings.Join(args, " "))
	if len(args) >= 3 && args[0] == "image" && args[1] == "inspect" {
		if r.present[args[2]] {
			return 0, []byte("ok"), nil
		}
		return 1, []byte("missing"), nil
	}
	if len(args) >= 2 && args[0] == "pull" {
		r.present[args[1]] = true
		return 0, []byte("pulled"), nil
	}
	if len(args) >= 3 && args[0] == "tag" {
		r.present[args[2]] = true
		return 0, []byte("tagged"), nil
	}
	return 0, []byte("{}"), nil
}

func TestEnsureImagePullsRegistryReferenceAndTagsLocalName(t *testing.T) {
	runner := &imageTestRunner{present: map[string]bool{}}
	server := &Server{
		Config: Config{Runtime: Runtime{ImageRegistry: "ghcr.io/example/"}},
		Runner: runner,
	}
	if err := server.ensureImage(context.Background(), "nib-diagnostics:automatic-flow"); err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(runner.calls, "\n")
	for _, want := range []string{
		"image inspect nib-diagnostics:automatic-flow",
		"pull ghcr.io/example/nib-diagnostics:automatic-flow",
		"tag ghcr.io/example/nib-diagnostics:automatic-flow nib-diagnostics:automatic-flow",
	} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing %q in calls:\n%s", want, joined)
		}
	}
}

func TestEnsureImageRequiresRegistryWhenLocalImageIsMissing(t *testing.T) {
	server := &Server{Config: Config{}, Runner: &imageTestRunner{present: map[string]bool{}}}
	if err := server.ensureImage(context.Background(), "nib-diagnostics:automatic-flow"); err == nil {
		t.Fatal("expected missing image error")
	}
}
