package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

func (s *Server) handleRetryDiagnostics(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	manifest, err := s.runManifest(id)
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	var selection struct {
		Models        []string  `json:"models"`
		Methods       []string  `json:"methods"`
		Levels        []float64 `json:"levels"`
		Windows       []int     `json:"half_windows"`
		Visualization bool      `json:"visualization"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&selection); err != nil {
		runtimeError(w, 400, err)
		return
	}
	if decoder.Decode(&struct{}{}) != io.EOF || len(selection.Models) == 0 || len(selection.Models) > 3 {
		runtimeError(w, 400, fmt.Errorf("select saved models and diagnostics"))
		return
	}
	forecasts, _ := manifest["forecasts"].(map[string]any)
	seen := map[string]bool{}
	for _, model := range selection.Models {
		if forecasts[model] == nil || seen[model] {
			runtimeError(w, 422, fmt.Errorf("model has no registered Forecast or is repeated"))
			return
		}
		seen[model] = true
	}
	config := map[string]any{"version": 2, "run": "/run"}
	if len(selection.Methods) > 0 {
		mapping := map[string]json.RawMessage{}
		mapping["methods"], _ = json.Marshal(selection.Methods)
		mapping["levels"], _ = json.Marshal(selection.Levels)
		mapping["half_windows"], _ = json.Marshal(selection.Windows)
		groups, err := metricGroups(mapping)
		if err != nil {
			runtimeError(w, 422, err)
			return
		}
		config["metrics"] = groups
		config["verification_models"] = selection.Models
	}
	data, _ := manifest["data"].(map[string]any)
	needsBasemap := data["id"] != "cma_radar"
	if selection.Visualization {
		if needsBasemap && !s.basemapReady() {
			runtimeError(w, 409, fmt.Errorf("prepare offline WMO basemap"))
			return
		}
		config["visualization"] = map[string]any{"models": selection.Models, "basemap": "/basemap"}
	}
	if len(selection.Methods) == 0 && !selection.Visualization {
		runtimeError(w, 422, fmt.Errorf("select at least one diagnostic"))
		return
	}
	if !s.executionMu.TryLock() {
		runtimeError(w, 409, fmt.Errorf("wait for the current execution"))
		return
	}
	s.jobsMu.Lock()
	if s.stopping {
		s.jobsMu.Unlock()
		s.executionMu.Unlock()
		runtimeError(w, http.StatusServiceUnavailable, fmt.Errorf("Handler is stopping; reconnect after starting it again"))
		return
	}
	active := false
	for _, job := range s.jobs {
		if job.Status == "queued" || job.Status == "running" {
			active = true
			break
		}
	}
	s.jobsMu.Unlock()
	if active {
		s.executionMu.Unlock()
		runtimeError(w, 409, fmt.Errorf("wait for queued jobs"))
		return
	}
	attempt, err := newRunID(time.Now())
	if err != nil {
		s.executionMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	directory := filepath.Join(s.Config.Paths.Runs, id)
	operation := filepath.Join(directory, "logs", "diagnostics-"+attempt)
	if err := os.MkdirAll(operation, 0o755); err != nil {
		s.executionMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	raw, err := json.Marshal(config)
	if err != nil {
		s.executionMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	if err := os.WriteFile(filepath.Join(operation, "request.json"), raw, 0o600); err != nil {
		s.executionMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	volumes := []string{volumeSpec(directory, "/run", false),
		volumeSpec(filepath.Join(directory, "inputs"), "/run/inputs", true),
		volumeSpec(filepath.Join(directory, "forecasts"), "/run/forecasts", true)}
	if selection.Visualization && needsBasemap {
		volumes = append(volumes, volumeSpec(filepath.Join(s.Config.Paths.Artifacts, "basemap", "wmo"), "/basemap", true))
	}
	service := map[string]any{"image": s.Config.Diagnostics.Image, "command": []string{"--config", "/run/logs/diagnostics-" + attempt + "/request.json"}, "volumes": volumes, "network_mode": "none", "read_only": true, "tmpfs": []string{"/tmp"}}
	if user := containerUser(); user != "" {
		service["user"] = user
	}
	composition := map[string]any{"services": map[string]any{"task": service}}
	raw, err = yaml.Marshal(composition)
	if err != nil {
		s.executionMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	composePath := filepath.Join(operation, "compose.yaml")
	if err := os.WriteFile(composePath, raw, 0o600); err != nil {
		s.executionMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	statusPath := filepath.Join(operation, "status.json")
	state := map[string]any{"id": attempt, "status": "running"}
	raw, _ = json.Marshal(state)
	if err := os.WriteFile(statusPath, raw, 0o600); err != nil {
		s.executionMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	go func() {
		defer s.executionMu.Unlock()
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
		defer cancel()
		name := "nib-diag-" + strings.ToLower(attempt)
		code, out, err := s.docker(ctx, "compose", "-f", composePath, "-p", name, "up", "--abort-on-container-exit", "--exit-code-from", "task")
		_ = os.WriteFile(filepath.Join(operation, "container.log"), out, 0o600)
		cleanup, c := context.WithTimeout(context.Background(), 15*time.Second)
		_, _, cleanupErr := s.docker(cleanup, "compose", "-f", composePath, "-p", name, "down", "--remove-orphans")
		c()
		if ctx.Err() != nil {
			err = ctx.Err()
		}
		if err == nil && cleanupErr != nil {
			err = cleanupErr
		}
		if err != nil || code != 0 {
			state["status"] = "failed"
			state["error"] = fmt.Sprintf("diagnostics exited %d: %v", code, err)
		} else {
			state["status"] = "succeeded"
		}
		raw, _ := json.Marshal(state)
		_ = os.WriteFile(statusPath, raw, 0o600)
	}()
	writeJSON(w, http.StatusAccepted, map[string]any{"id": attempt, "run_id": id, "status": "running"})
}
