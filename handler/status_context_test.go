package handler

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

type statusContextRunner struct {
	args []string
	code int
}

func (r *statusContextRunner) Run(_ context.Context, args ...string) (int, []byte, error) {
	r.args = append([]string(nil), args...)
	if r.code != 0 {
		return r.code, nil, fmt.Errorf("daemon offline")
	}
	return 0, []byte("27.5.1\n"), nil
}

func TestStatusUsesConfiguredDockerContext(t *testing.T) {
	cfg := DefaultConfig()
	cfg.Runtime.DockerContext = "configured-daemon"
	s := NewServer(cfg)
	defer s.serviceCancel()
	runner := &statusContextRunner{}
	s.Runner = runner
	check := s.Preflight[0]()
	if !check.OK || check.Message != "27.5.1" {
		t.Fatalf("check = %+v", check)
	}
	if got := strings.Join(runner.args, " "); got != "--context configured-daemon version --format {{.Server.Version}}" {
		t.Fatalf("wrong Docker target: %s", got)
	}
	runner.code = 1
	if check := s.Preflight[0](); check.OK {
		t.Fatalf("offline Docker reported ready: %+v", check)
	}
}
