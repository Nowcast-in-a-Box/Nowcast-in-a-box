package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"slices"
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
	data, _ := manifest["data"].(map[string]any)
	dataID, _ := data["id"].(string)
	dataResource, err := s.dataResource(dataID)
	if err != nil {
		runtimeError(w, 422, err)
		return
	}
	config := map[string]any{"version": 2, "run": "/run"}
	if len(selection.Methods) > 0 {
		mapping := map[string]json.RawMessage{}
		mapping["methods"], _ = json.Marshal(selection.Methods)
		mapping["levels"], _ = json.Marshal(selection.Levels)
		mapping["half_windows"], _ = json.Marshal(selection.Windows)
		groups, err := metricGroups(mapping, dataResource)
		if err != nil {
			runtimeError(w, 422, err)
			return
		}
		config["metrics"] = groups
		config["verification_models"] = selection.Models
	}
	needsBasemap := dataResource.VisualizationBasemap
	if selection.Visualization {
		if needsBasemap && !s.basemapReady() {
			runtimeError(w, 409, fmt.Errorf("prepare the offline global terrain and WMO basemap cache"))
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
	if err := s.reconcileExecutions(); err != nil {
		s.executionMu.Unlock()
		runtimeError(w, http.StatusConflict, err)
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
	operation, err := s.prepareDiagnostics(id, config, selection.Visualization && needsBasemap)
	if err != nil {
		s.executionMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	go func() {
		defer s.executionMu.Unlock()
		s.executeDiagnostics(id, operation)
	}()
	writeJSON(w, http.StatusAccepted, operation)
}

// DiagnosticOperation binds a single execution and report, independently of old attempts.
type DiagnosticOperation struct {
	ID          string    `json:"id"`
	RunID       string    `json:"run_id"`
	Status      string    `json:"status"`
	InstanceID  string    `json:"instance_id"`
	ReportEntry string    `json:"report_entry,omitempty"`
	Error       string    `json:"error,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
}

func saveOperation(path string, state DiagnosticOperation) error {
	raw, err := json.Marshal(state)
	if err != nil {
		return err
	}
	if err := os.WriteFile(path+".tmp", raw, 0o600); err != nil {
		return err
	}
	return os.Rename(path+".tmp", path)
}

// The caller owns executionMu, for both initial diagnostics and an explicit retry.
func (s *Server) prepareDiagnostics(id string, config map[string]any, basemap bool) (DiagnosticOperation, error) {
	attempt, err := newRunID(time.Now())
	if err != nil {
		return DiagnosticOperation{}, err
	}
	state := DiagnosticOperation{ID: attempt, RunID: id, Status: "running", InstanceID: s.instanceID, CreatedAt: time.Now().UTC()}
	directory := filepath.Join(s.Config.Paths.Runs, id)
	operation := filepath.Join(directory, "logs", "diagnostics-"+attempt)
	if err := os.MkdirAll(operation, 0o755); err != nil {
		return state, err
	}
	config["operation_id"] = attempt
	raw, err := json.Marshal(config)
	if err != nil {
		return state, err
	}
	if err := os.WriteFile(filepath.Join(operation, "request.json"), raw, 0o600); err != nil {
		return state, err
	}
	if s.Resources == nil || s.Resources.Diagnostics == nil {
		return state, fmt.Errorf("diagnostics resources are not loaded")
	}
	diagnostics := s.Resources.Diagnostics
	requestPath := "/run/logs/diagnostics-" + attempt + "/request.json"
	volumes := []string{}
	for _, mount := range diagnostics.Container.Mounts {
		if mount.Name == "basemap" && !basemap {
			continue
		}
		host, err := s.mountHost(mount.Name, "", "", directory, "", "")
		if err != nil {
			return state, err
		}
		volumes = append(volumes, volumeSpec(host, mount.Target, !mount.Write))
	}
	command := expandCommand(diagnostics.Container.Command, map[string]string{"request": requestPath})
	service := map[string]any{"image": diagnostics.Container.Image, "command": command, "volumes": volumes, "network_mode": "none", "read_only": true, "tmpfs": []string{"/tmp"}, "pull_policy": "never"}
	if user := containerUser(); user != "" {
		service["user"] = user
	}
	raw, err = yaml.Marshal(map[string]any{"services": map[string]any{"task": service}})
	if err != nil {
		return state, err
	}
	if err := os.WriteFile(filepath.Join(operation, "compose.yaml"), raw, 0o600); err != nil {
		return state, err
	}
	if err := saveOperation(filepath.Join(operation, "status.json"), state); err != nil {
		return state, err
	}
	return state, saveOperation(filepath.Join(directory, "logs", "latest-diagnostics.json"), state)
}

func (s *Server) executeDiagnostics(id string, state DiagnosticOperation) DiagnosticOperation {
	operation := filepath.Join(s.Config.Paths.Runs, id, "logs", "diagnostics-"+state.ID)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	name := "nib-diag-" + strings.ToLower(state.ID)
	composePath := filepath.Join(operation, "compose.yaml")
	code, out, err := s.docker(ctx, "compose", "-f", composePath, "-p", name, "up", "--abort-on-container-exit", "--exit-code-from", "task")
	logErr := os.WriteFile(filepath.Join(operation, "container.log"), out, 0o600)
	cleanup, c := context.WithTimeout(context.Background(), 15*time.Second)
	cleanupCode, _, cleanupErr := s.docker(cleanup, "compose", "-f", composePath, "-p", name, "down", "--remove-orphans")
	if cleanupErr == nil && cleanupCode != 0 {
		cleanupErr = fmt.Errorf("container cleanup exited %d; verify Docker project stopped before retrying", cleanupCode)
	}
	c()
	if ctx.Err() != nil {
		err = ctx.Err()
	}
	if err == nil {
		err = cleanupErr
	}
	if err == nil {
		err = logErr
	}
	state.Status = "failed"
	// A saved report must name this operation. A historical success is never adopted.
	raw, readErr := os.ReadFile(filepath.Join(s.Config.Paths.Runs, id, "manifest.json"))
	var manifest struct {
		Diagnostics []struct {
			Kind, Status, Entry, Path, Error string
			OperationID                      string `json:"operation_id"`
		}
	}
	var identity struct{ Schema, ID string }
	if readErr == nil && json.Unmarshal(raw, &manifest) == nil && json.Unmarshal(raw, &identity) == nil && identity.Schema == "nib.run.v1" && identity.ID == id {
		for _, item := range manifest.Diagnostics {
			if item.Kind == "report" && item.OperationID == state.ID && (item.Status == "succeeded" || item.Status == "partial") && item.Entry != "" && item.Path != "" {
				state.Status, state.ReportEntry = item.Status, item.Entry
			}
		}
	}
	if err != nil || code != 0 || state.ReportEntry == "" {
		if state.ReportEntry != "" {
			state.Status = "partial"
		}
		state.Error = fmt.Sprintf("diagnostics exited %d; inspect this operation's container log", code)
		if err != nil {
			state.Error = fmt.Sprintf("diagnostics exited %d: %v; inspect this operation's container log", code, err)
		}
		var causes []string
		for _, item := range manifest.Diagnostics {
			if item.OperationID == state.ID && item.Status == "failed" && item.Error != "" && !slices.Contains(causes, item.Error) {
				causes = append(causes, item.Error)
			}
		}
		if len(causes) > 0 {
			state.Error += ": " + strings.Join(causes, "; ")
		}
		if err == nil && code == 0 {
			state.Error = "diagnostics did not register a report for this operation; rebuild the diagnostics image and inspect its log"
		}
	}
	if err := saveOperation(filepath.Join(operation, "status.json"), state); err != nil {
		state.Status, state.Error = "failed", "cannot save diagnostic operation status: "+err.Error()
	}
	return state
}
