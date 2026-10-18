package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// A dead Handler does not prove its offline Docker project has stopped.
func (s *Server) reconcileExecutions() error {
	s.recoveryMu.Lock()
	defer s.recoveryMu.Unlock()
	s.jobsMu.Lock()
	active := map[string]bool{}
	for id, job := range s.jobs {
		// Only an execution this instance is actually running is exempt. A queued
		// same-run submission has not started and must not hide its old containers.
		active[id] = job.Status == "running"
	}
	s.jobsMu.Unlock()
	entries, err := os.ReadDir(s.Config.Paths.Runs)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("cannot inspect prior execution records: %w", err)
	}
	projects := map[string]bool{}
	for _, entry := range entries {
		if !entry.IsDir() || !idPattern.MatchString(entry.Name()) {
			continue
		}
		directory := filepath.Join(s.Config.Paths.Runs, entry.Name())
		if !active[entry.Name()] {
			files, _ := filepath.Glob(filepath.Join(directory, "compose-*.yaml"))
			for _, file := range files {
				stage := strings.TrimSuffix(strings.TrimPrefix(filepath.Base(file), "compose-"), ".yaml")
				if idPattern.MatchString(stage) {
					projects["nib-"+strings.ToLower(entry.Name())+"-"+stage] = true
				}
			}
		}
		files, _ := filepath.Glob(filepath.Join(directory, "logs", "diagnostics-*", "status.json"))
		for _, file := range files {
			raw, err := os.ReadFile(file)
			if err != nil {
				return err
			}
			var operation DiagnosticOperation
			if json.Unmarshal(raw, &operation) != nil {
				return fmt.Errorf("invalid prior diagnostic status; inspect %s", file)
			}
			if !(operation.Status == "running" && operation.InstanceID == s.instanceID) && idPattern.MatchString(operation.ID) {
				projects["nib-diag-"+strings.ToLower(operation.ID)] = true
			}
		}
	}
	if len(projects) == 0 {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	code, out, err := s.docker(ctx, "ps", "--all", "--filter", "label=com.docker.compose.project", "--format", `{{.Label "com.docker.compose.project"}} {{.State}}`)
	cancel()
	if err != nil || code != 0 {
		return fmt.Errorf("cannot verify prior Docker projects are stopped; start Docker and retry")
	}
	for _, line := range strings.Split(string(out), "\n") {
		parts := strings.Fields(line)
		if len(parts) == 0 {
			continue
		}
		if len(parts) != 2 {
			return fmt.Errorf("cannot interpret prior Docker project state; inspect Docker before retrying")
		}
		if len(parts) == 2 && projects[parts[0]] && parts[1] != "exited" && parts[1] != "dead" {
			return fmt.Errorf("prior Docker project %s is still %s; inspect it in Docker and wait for it to stop before another execution", parts[0], parts[1])
		}
	}
	return nil
}
