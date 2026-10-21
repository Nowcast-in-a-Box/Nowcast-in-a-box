package handler_test

import (
	"context"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

type pullPreflightRunner struct {
	present map[string]bool
	calls   []string
}

func (r *pullPreflightRunner) Run(_ context.Context, args ...string) (int, []byte, error) {
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
	return 0, []byte(`{"ready":true}`), nil
}

func TestSelectionPreflightPullsEverySelectedImage(t *testing.T) {
	cfg := testConfig(t)
	writeCmaData(t, &cfg)
	cfg.Runtime.ImageRegistry = "ghcr.io/example"
	cfg.Runtime.Token = "token"
	server := handler.NewServer(cfg)
	runner := &pullPreflightRunner{present: map[string]bool{}}
	server.Runner = runner

	body := `{"data_id":"cma_radar","event_id":"20260701","context_start_time":"2026-07-01T00:00:00Z","sample_starts":["2026-07-01T00:00:00Z"],"models":["nowcastnet"]}`
	result := postPreflight(t, server, body)
	if result["ok"] != true {
		t.Fatalf("Validate failed: %s", mustJSON(t, result))
	}
	joined := strings.Join(runner.calls, "\n")
	for _, want := range []string{
		"pull ghcr.io/example/nib-diagnostics:automatic-flow",
		"pull ghcr.io/example/nib-ingestion:cma-demo",
		"pull ghcr.io/example/nib-nowcastnet:cma-demo",
	} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing %q in calls:\n%s", want, joined)
		}
	}
}
