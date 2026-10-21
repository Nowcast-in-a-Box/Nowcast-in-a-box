package handler

import (
	"context"
	"strings"
	"testing"
)

type imageTestRunner struct {
	present  map[string]bool
	failPull map[string]bool
	calls    []string
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
		if r.failPull[args[1]] {
			return 1, []byte("registry unavailable"), nil
		}
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
	remote, err := server.ensureImage(context.Background(), "nib-diagnostics:automatic-flow")
	if err != nil {
		t.Fatal(err)
	}
	if remote != "ghcr.io/example/nib-diagnostics:automatic-flow" {
		t.Fatalf("remote = %q", remote)
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
	if _, err := server.ensureImage(context.Background(), "nib-diagnostics:automatic-flow"); err == nil {
		t.Fatal("expected missing image error")
	}
}

func TestEnsureImageFallsBackFromOfficialToMirror(t *testing.T) {
	runner := &imageTestRunner{
		present:  map[string]bool{},
		failPull: map[string]bool{"ghcr.io/example/nib-diagnostics:automatic-flow": true},
	}
	server := &Server{
		Config: Config{Runtime: Runtime{
			ImageRegistry: "ghcr.io/example",
			ImageMirror:   "mirror.example/example",
			ImageSource:   "auto",
		}},
		Runner: runner,
	}
	remote, err := server.ensureImage(context.Background(), "nib-diagnostics:automatic-flow")
	if err != nil {
		t.Fatal(err)
	}
	if remote != "mirror.example/example/nib-diagnostics:automatic-flow" {
		t.Fatalf("remote = %q", remote)
	}
	joined := strings.Join(runner.calls, "\n")
	for _, want := range []string{
		"pull ghcr.io/example/nib-diagnostics:automatic-flow",
		"pull mirror.example/example/nib-diagnostics:automatic-flow",
		"tag mirror.example/example/nib-diagnostics:automatic-flow nib-diagnostics:automatic-flow",
	} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing %q in calls:\n%s", want, joined)
		}
	}
}

func TestExplicitMirrorDoesNotTryOfficialFirst(t *testing.T) {
	runner := &imageTestRunner{present: map[string]bool{}}
	server := &Server{
		Config: Config{Runtime: Runtime{
			ImageRegistry: "ghcr.io/example",
			ImageMirror:   "mirror.example/example",
			ImageSource:   "mirror",
		}},
		Runner: runner,
	}
	if _, err := server.ensureImage(context.Background(), "nib-diagnostics:automatic-flow"); err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(runner.calls, "\n")
	if strings.Contains(joined, "pull ghcr.io/example/") {
		t.Fatalf("explicit mirror tried official first:\n%s", joined)
	}
	if !strings.Contains(joined, "pull mirror.example/example/") {
		t.Fatalf("mirror not tried:\n%s", joined)
	}
}
