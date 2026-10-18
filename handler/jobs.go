package handler

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

type Job struct {
	ID                    string         `json:"id"`
	Status                string         `json:"status"`
	Stage                 string         `json:"stage"`
	Error                 string         `json:"error,omitempty"`
	DiagnosticOperationID string         `json:"diagnostic_operation_id,omitempty"`
	ReportEntry           string         `json:"report_entry,omitempty"`
	Request               RuntimeRequest `json:"request"`
	CreatedAt             time.Time      `json:"created_at"`
	IdempotencyKey        string         `json:"idempotency_key,omitempty"`
	UpdatedAt             time.Time      `json:"updated_at"`
}

func (s *Server) runtimeRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /v1/pair", s.handlePair)
	mux.HandleFunc("OPTIONS /v1/pair", s.handlePair)
	mux.HandleFunc("GET /v1/settings", s.authorize(s.handleSettings))
	mux.HandleFunc("PATCH /v1/settings", s.authorize(s.handleSettings))
	mux.HandleFunc("POST /v1/settings/pick", s.authorize(s.handlePickDataRoot))
	mux.HandleFunc("POST /v1/runs/{id}/diagnostics", s.authorize(s.handleRetryDiagnostics))
	mux.HandleFunc("GET /v1/capabilities", s.authorize(s.handleCapabilities))
	mux.HandleFunc("POST /v1/preflight", s.authorize(s.handlePreflight))
	mux.HandleFunc("GET /v1/datasets/{dataset}/samples", s.authorize(s.handleSamples))
	mux.HandleFunc("POST /v1/jobs", s.authorize(s.handleCreateJob))
	mux.HandleFunc("GET /v1/jobs/{id}", s.authorize(s.handleJob))
	mux.HandleFunc("GET /v1/jobs/{id}/log", s.authorize(s.handleJobLog))
	mux.HandleFunc("GET /v1/runs", s.authorize(s.handleRuns))
	mux.HandleFunc("GET /v1/runs/{id}", s.authorize(s.handleRun))
	mux.HandleFunc("GET /v1/runs/{id}/files/{file...}", s.handleResultFile)
}

func (s *Server) docker(ctx context.Context, args ...string) (int, []byte, error) {
	if s.Config.Runtime.DockerContext != "" {
		args = append([]string{"--context", s.Config.Runtime.DockerContext}, args...)
	}
	return s.Runner.Run(ctx, args...)
}

func (s *Server) dataPaths() (string, string, error) {
	root := s.currentConfig().Paths.DataRoot
	if root == "" {
		return "", "", fmt.Errorf("configure paths.data_root before execution")
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return "", "", fmt.Errorf("configured data root is unavailable")
	}
	resolve := func(relative string) (string, error) {
		if filepath.IsAbs(relative) {
			return "", fmt.Errorf("input files must be relative to configured data root")
		}
		path, err := ResolveDataDir(root, relative)
		if err != nil {
			return "", err
		}
		real, err := filepath.EvalSymlinks(path)
		if err != nil {
			return "", err
		}
		realRoot, err := filepath.EvalSymlinks(root)
		if err != nil {
			return "", err
		}
		rel, err := filepath.Rel(realRoot, real)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return "", fmt.Errorf("input escapes data root")
		}
		info, err := os.Stat(real)
		if err != nil || (!info.Mode().IsRegular() && !(s.dataID() == "cma_radar" && info.IsDir())) {
			return "", fmt.Errorf("input must be a regular file")
		}
		return real, nil
	}
	data, err := resolve(s.Config.Runtime.DataFile)
	if err != nil {
		return "", "", err
	}
	if s.dataID() == "cma_radar" {
		return data, "", nil
	}
	if s.dataID() != "sevir" {
		return "", "", fmt.Errorf("unsupported configured dataset")
	}
	catalog, err := resolve(s.Config.Runtime.CatalogFile)
	return data, catalog, err
}

func (s *Server) handleCapabilities(w http.ResponseWriter, r *http.Request) {
	_, _, dataErr := s.dataPaths()
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	code, out, err := s.docker(ctx, "info", "--format", "{{.ServerVersion}}")
	dockerReady := err == nil && code == 0
	readyModels := []map[string]any{}
	for _, model := range []string{"nowcastnet", "exprecast", "wadepre"} {
		image := s.modelImage(model)
		imageCode, _, imageErr := s.docker(ctx, "image", "inspect", image)
		weightErr := verifyModelWeights(s.weightPath(model), model)
		readyModels = append(readyModels, map[string]any{"id": model, "ready": dockerReady && dataErr == nil && image != "" && imageErr == nil && imageCode == 0 && weightErr == nil})
	}
	reason := ""
	if dataErr != nil {
		reason = dataErr.Error()
	}
	diagnosticCode, _, diagnosticErr := s.docker(ctx, "image", "inspect", s.Config.Diagnostics.Image)
	diagnosticsReady := dockerReady && diagnosticErr == nil && diagnosticCode == 0
	writeJSON(w, 200, map[string]any{"data": map[string]any{"id": s.dataID(), "product_id": map[string]string{"sevir": "sevir-vil", "cma_radar": "cma-radar"}[s.dataID()], "name": map[string]string{"sevir": "SEVIR VIL", "cma_radar": "CMA Radar · July 2026"}[s.dataID()], "ready": dataErr == nil, "reason": reason}, "models": readyModels,
		"docker": map[string]any{"ready": dockerReady, "detail": strings.TrimSpace(string(out))}, "atlas_revision": s.Config.Runtime.AtlasRevision,
		"diagnostics": map[string]any{"verification": diagnosticsReady, "visualization": diagnosticsReady && (s.dataID() == "cma_radar" || s.basemapReady())}})
}

func verifyModelWeights(path, model string) error {
	pins := map[string]string{"nowcastnet": "773b6cbe0b1e0c8cea3b62932dc60b108b60885a54f7445a0074a48d4810b37d", "exprecast": "534b91b63524f775c460334385f0f0b0b92759fa1596a053d1cac489c84cd00d", "wadepre": "55c396165f620934b1262eeac19997f14b00fae5f3a150015a5a84c9cbd6ee3b"}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return err
	}
	if expected, ok := pins[model]; !ok || hex.EncodeToString(hash.Sum(nil)) != expected {
		return fmt.Errorf("weight checksum mismatch")
	}
	return nil
}

func modelWeightName(model string) string {
	switch model {
	case "nowcastnet":
		return "NowcastNet.onnx"
	case "exprecast":
		return "exPreCast.onnx"
	case "wadepre":
		return "WADEPre.onnx"
	}
	return ""
}
func (s *Server) basemapReady() bool {
	info, err := os.Stat(filepath.Join(s.Config.Paths.Artifacts, "basemap", "wmo", "acquisition.json"))
	return err == nil && info.Mode().IsRegular()
}

func (s *Server) handleSamples(w http.ResponseWriter, r *http.Request) {
	if dataset := r.PathValue("dataset"); dataset != "" && dataset != s.dataID() {
		runtimeError(w, 409, fmt.Errorf("dataset is not configured on this handler"))
		return
	}
	offset, limit, err := pagination(r)
	if err != nil {
		runtimeError(w, 400, err)
		return
	}
	data, catalog, err := s.dataPaths()
	if err != nil {
		runtimeError(w, 409, err)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), time.Minute)
	defer cancel()
	args := []string{"run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp"}
	args = append(args, s.ingestionArgs(data, catalog, "--list-samples", "--offset", strconv.Itoa(offset), "--limit", strconv.Itoa(limit))...)
	code, out, err := s.docker(ctx, args...)
	if err != nil || code != 0 {
		runtimeError(w, 502, fmt.Errorf("sample reader failed: %v %s", err, out))
		return
	}
	var value any
	if json.Unmarshal(out, &value) != nil {
		runtimeError(w, 502, fmt.Errorf("invalid sample reader response"))
		return
	}
	writeJSON(w, 200, value)
}

func pagination(r *http.Request) (int, int, error) {
	offset, limit := 0, 100
	for key, target := range map[string]*int{"offset": &offset, "limit": &limit} {
		if raw := r.URL.Query().Get(key); raw != "" {
			value, err := strconv.Atoi(raw)
			if err != nil {
				return 0, 0, fmt.Errorf("invalid pagination")
			}
			*target = value
		}
	}
	if offset < 0 || limit < 1 || limit > 500 {
		return 0, 0, fmt.Errorf("invalid pagination")
	}
	return offset, limit, nil
}

func (s *Server) handleCreateJob(w http.ResponseWriter, r *http.Request) {
	var upload struct {
		Package []byte `json:"package"`
		SHA256  string `json:"sha256"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 12<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&upload); err != nil {
		runtimeError(w, 400, err)
		return
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		runtimeError(w, 400, fmt.Errorf("one request object required"))
		return
	}
	key := r.Header.Get("Idempotency-Key")
	if len(key) > 128 {
		runtimeError(w, 400, fmt.Errorf("idempotency key is too long"))
		return
	}
	request, err := ParseRuntimePackage(upload.Package, upload.SHA256)
	if err != nil {
		runtimeError(w, 422, err)
		return
	}
	if err := s.requireDataset(request.DataID); err != nil {
		runtimeError(w, 422, err)
		return
	}
	initial, parseErr := time.Parse(time.RFC3339, request.StartTime)
	_, zoneOffset := initial.Zone()
	if parseErr != nil || zoneOffset != 0 {
		runtimeError(w, 422, fmt.Errorf("sample context time must be explicit UTC"))
		return
	}
	if _, _, err := s.dataPaths(); err != nil {
		runtimeError(w, 409, err)
		return
	}
	if request.Visualization && request.DataID != "cma_radar" && !s.basemapReady() {
		runtimeError(w, 409, fmt.Errorf("prepare the WMO basemap cache first"))
		return
	}
	if len(s.Config.Runtime.AtlasRevision) != 40 {
		runtimeError(w, 409, fmt.Errorf("configure the pinned atlas revision first"))
		return
	}
	if err := s.reconcileExecutions(); err != nil {
		runtimeError(w, http.StatusConflict, err)
		return
	}
	s.jobsMu.Lock()
	if s.stopping {
		s.jobsMu.Unlock()
		runtimeError(w, http.StatusServiceUnavailable, fmt.Errorf("Handler is stopping; reconnect after starting it again"))
		return
	}
	// A frozen package is idempotent; explicit rerun is a separate request identity.
	if key != "" {
		for _, job := range s.jobs {
			if job.IdempotencyKey == key {
				if job.Request.PackageSHA256 != request.PackageSHA256 {
					s.jobsMu.Unlock()
					runtimeError(w, 409, fmt.Errorf("idempotency key belongs to another package"))
					return
				}
				copy := *job
				s.jobsMu.Unlock()
				writeJSON(w, 200, copy)
				return
			}
		}
		entries, _ := os.ReadDir(s.Config.Paths.Runs)
		for _, entry := range entries {
			if !entry.IsDir() || !idPattern.MatchString(entry.Name()) {
				continue
			}
			raw, readErr := os.ReadFile(filepath.Join(s.Config.Paths.Runs, entry.Name(), "job.json"))
			if readErr != nil {
				continue
			}
			var recorded Job
			if json.Unmarshal(raw, &recorded) != nil || recorded.ID != entry.Name() || recorded.IdempotencyKey != key {
				continue
			}
			if recorded.Request.PackageSHA256 != request.PackageSHA256 {
				s.jobsMu.Unlock()
				runtimeError(w, 409, fmt.Errorf("idempotency key belongs to another package"))
				return
			}
			if live := s.jobs[recorded.ID]; live != nil && (live.Status == "queued" || live.Status == "running") {
				s.jobsMu.Unlock()
				runtimeError(w, http.StatusConflict, fmt.Errorf("this run has a newer active request; observe it in Runs before retrying an older request"))
				return
			}
			if recorded.Status == "running" || recorded.Status == "queued" {
				recorded.Status = "interrupted"
				recorded.Error = "handler restarted before this job completed"
				_ = writeJob(filepath.Join(s.Config.Paths.Runs, entry.Name()), &recorded)
			}
			s.jobs[recorded.ID] = &recorded
			s.jobsMu.Unlock()
			writeJSON(w, 200, recorded)
			return
		}
	}
	id := s.matchingRunID(request)
	reuse := id != ""
	if live := s.jobs[id]; reuse && live != nil && (live.Status == "queued" || live.Status == "running") {
		s.jobsMu.Unlock()
		runtimeError(w, http.StatusConflict, fmt.Errorf("this run is already queued or running; observe it in Runs and wait before rerunning"))
		return
	}
	if !reuse {
		id, err = newRunID(time.Now())
		if err != nil {
			s.jobsMu.Unlock()
			runtimeError(w, 500, err)
			return
		}
	}
	directory := filepath.Join(s.Config.Paths.Runs, id)
	if err = os.MkdirAll(s.Config.Paths.Runs, 0o755); err != nil {
		s.jobsMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	if !reuse {
		if err = os.Mkdir(directory, 0o755); err != nil {
			s.jobsMu.Unlock()
			runtimeError(w, 500, err)
			return
		}
	}
	job := &Job{ID: id, Status: "queued", Stage: "queued", Request: request, IdempotencyKey: key, CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	if err := writeJob(directory, job); err != nil {
		s.jobsMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	if err := os.WriteFile(filepath.Join(directory, "package.zip"), upload.Package, 0o600); err != nil {
		s.jobsMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	s.jobs[id] = job
	copy := *job
	s.jobsMu.Unlock()
	go s.executeJob(id)
	writeJSON(w, http.StatusAccepted, copy)
}

func sameRunConfig(stored, next RuntimeRequest) bool {
	left := append([]string(nil), stored.Models...)
	right := append([]string(nil), next.Models...)
	sort.Strings(left)
	sort.Strings(right)
	return stored.DataID == next.DataID && stored.CenterLon == next.CenterLon && stored.CenterLat == next.CenterLat && stored.EventID == next.EventID && stored.StartTime == next.StartTime && strings.Join(left, "\x00") == strings.Join(right, "\x00")
}

// matchingRunID reuses one run when the sample and selected models are unchanged.
// A different sample or model set starts a new run. Metric choices do not.
func (s *Server) matchingRunID(request RuntimeRequest) string {
	for id, job := range s.jobs {
		if sameRunConfig(job.Request, request) {
			return id
		}
	}
	entries, err := os.ReadDir(s.Config.Paths.Runs)
	if err != nil {
		return ""
	}
	var found string
	for _, entry := range entries {
		if !entry.IsDir() || !idPattern.MatchString(entry.Name()) {
			continue
		}
		raw, readErr := os.ReadFile(filepath.Join(s.Config.Paths.Runs, entry.Name(), "job.json"))
		var recorded Job
		if readErr != nil || json.Unmarshal(raw, &recorded) != nil || !sameRunConfig(recorded.Request, request) {
			continue
		}
		if found == "" || entry.Name() > found {
			found = entry.Name()
		}
	}
	return found
}

func writeJob(directory string, job *Job) error {
	raw, err := json.MarshalIndent(job, "", "  ")
	if err != nil {
		return err
	}
	temporary := filepath.Join(directory, "job.tmp")
	if err := os.WriteFile(temporary, append(raw, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(temporary, filepath.Join(directory, "job.json"))
}

func (s *Server) note(message string) {
	if s.Config.Source == "" {
		return
	}
	dir := filepath.Join(filepath.Dir(s.Config.Source), "logs")
	_ = os.MkdirAll(dir, 0o755)
	file, err := os.OpenFile(filepath.Join(dir, "handler.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer file.Close()
	_, _ = fmt.Fprintf(file, "%s %s\n", time.Now().UTC().Format(time.RFC3339), message)
}

func (s *Server) updateJob(id, status, stage string, err error) {
	s.jobsMu.Lock()
	defer s.jobsMu.Unlock()
	job := s.jobs[id]
	job.Status = status
	job.Stage = stage
	job.UpdatedAt = time.Now().UTC()
	if err != nil {
		job.Error = err.Error()
	}
	if err := writeJob(filepath.Join(s.Config.Paths.Runs, id), job); err != nil {
		job.Error = err.Error()
		job.Status = "failed"
	}
}

func (s *Server) ExecutionAvailableForTest() bool {
	ok := s.executionMu.TryLock()
	if ok {
		s.executionMu.Unlock()
	}
	return ok
}

func (s *Server) executeJob(id string) {
	s.executionMu.Lock()
	defer s.executionMu.Unlock()
	if err := s.reconcileExecutions(); err != nil {
		s.updateJob(id, "failed", "preflight", err)
		return
	}
	s.jobsMu.Lock()
	request := s.jobs[id].Request
	s.jobsMu.Unlock()
	directory := filepath.Join(s.Config.Paths.Runs, id)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	run := func(stage string, args ...string) error {
		s.updateJob(id, "running", stage, nil)
		name := "nib-" + strings.ToLower(id) + "-" + stage
		volumes := []string{}
		for len(args) >= 2 && args[0] == "-v" {
			volumes = append(volumes, args[1])
			args = args[2:]
		}
		if len(args) == 0 {
			return fmt.Errorf("missing configured image")
		}
		service := map[string]any{"image": args[0], "command": args[1:], "volumes": volumes, "network_mode": "none", "read_only": true, "tmpfs": []string{"/tmp"}}
		if user := containerUser(); user != "" {
			service["user"] = user
		}
		composition := map[string]any{"services": map[string]any{"task": service}}
		raw, err := yaml.Marshal(composition)
		if err != nil {
			return err
		}
		composePath := filepath.Join(directory, "compose-"+stage+".yaml")
		if err := os.WriteFile(composePath, raw, 0o600); err != nil {
			return err
		}
		code, out, err := s.docker(ctx, "compose", "-f", composePath, "-p", name, "up", "--abort-on-container-exit", "--exit-code-from", "task")
		logs := filepath.Join(directory, "logs")
		_ = os.MkdirAll(logs, 0o755)
		_ = os.WriteFile(filepath.Join(logs, stage+".log"), out, 0o644)
		cleanup, c := context.WithTimeout(context.Background(), 15*time.Second)
		cleanupCode, _, cleanupErr := s.docker(cleanup, "compose", "-f", composePath, "-p", name, "down", "--remove-orphans")
		if cleanupErr == nil && cleanupCode != 0 {
			cleanupErr = fmt.Errorf("container cleanup exited %d; inspect Docker before retrying", cleanupCode)
		}
		c()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if cleanupErr != nil && err == nil {
			err = cleanupErr
		}
		if err != nil {
			return err
		}
		if code != 0 {
			return fmt.Errorf("%s exited %d: %s", stage, code, out)
		}
		return nil
	}
	data, catalog, err := s.dataPaths()
	if err != nil {
		s.updateJob(id, "failed", "preflight", err)
		return
	}
	// Ingestion creates a child run inside the job workspace, then publishes its contents.
	args := s.ingestionArgs(data, catalog, "--event", request.EventID, "--start", request.StartTime, "--runs", "/workspace", "--id", id, "--atlas-revision", s.Config.Runtime.AtlasRevision)
	if request.DataID == "cma_radar" {
		args = append(args, "--center-lon", strconv.FormatFloat(request.CenterLon, 'f', -1, 64), "--center-lat", strconv.FormatFloat(request.CenterLat, 'f', -1, 64))
	}
	args = append([]string{"-v", volumeSpec(directory, "/workspace", false)}, args...)
	err = run("ingestion", args...)
	if err == nil {
		child := filepath.Join(directory, id)
		entries, readErr := os.ReadDir(child)
		if readErr != nil {
			err = readErr
		} else {
			for _, entry := range entries {
				if entry.Name() == "forecasts" {
					continue
				}
				target := filepath.Join(directory, entry.Name())
				if entry.Name() == "manifest.json" {
					oldRaw, _ := os.ReadFile(target)
					newRaw, readErr := os.ReadFile(filepath.Join(child, entry.Name()))
					var oldManifest, newManifest map[string]any
					if json.Unmarshal(oldRaw, &oldManifest) == nil && readErr == nil && json.Unmarshal(newRaw, &newManifest) == nil {
						if history, ok := oldManifest["diagnostics"].([]any); ok {
							newManifest["diagnostics"] = history
						}
						forecasts, _ := oldManifest["forecasts"].(map[string]any)
						kept := map[string]any{}
						for model, descriptor := range forecasts {
							if !slices.Contains(request.Models, model) {
								kept[model] = descriptor
							}
						}
						newManifest["forecasts"] = kept
						newManifest["model_names"] = oldManifest["model_names"]
						newRaw, _ = json.Marshal(newManifest)
						if err := os.WriteFile(filepath.Join(child, entry.Name()), newRaw, 0o644); err != nil {
							s.updateJob(id, "failed", "ingestion", err)
							return
						}
					}
				}
				if entry.Name() == "inputs" || entry.Name() == "manifest.json" {
					_ = os.RemoveAll(target)
				}
				if moveErr := os.Rename(filepath.Join(child, entry.Name()), target); moveErr != nil {
					err = moveErr
					break
				}
			}
			if err == nil {
				err = os.RemoveAll(child)
			}
		}
	}
	if err != nil {
		s.updateJob(id, "failed", "ingestion", err)
		return
	}
	if len(request.ModelNames) > 0 {
		rawManifest, readErr := os.ReadFile(filepath.Join(directory, "manifest.json"))
		manifest := map[string]any{}
		if readErr == nil {
			readErr = json.Unmarshal(rawManifest, &manifest)
		}
		if readErr != nil {
			s.updateJob(id, "failed", "ingestion", readErr)
			return
		}
		names, _ := manifest["model_names"].(map[string]any)
		if names == nil {
			names = map[string]any{}
		}
		for model, name := range request.ModelNames {
			names[model] = name
		}
		manifest["model_names"] = names
		raw, marshalErr := json.MarshalIndent(manifest, "", "  ")
		if marshalErr != nil {
			s.updateJob(id, "failed", "ingestion", marshalErr)
			return
		}
		if writeErr := os.WriteFile(filepath.Join(directory, "manifest.json"), raw, 0o644); writeErr != nil {
			s.updateJob(id, "failed", "ingestion", writeErr)
			return
		}
	}
	for _, model := range request.Models {
		// Rerunning this configuration replaces only this model's previous forecast.
		_ = os.RemoveAll(filepath.Join(directory, "forecasts", model))
		image := s.modelImage(model)
		if image == "" {
			s.updateJob(id, "failed", "model", fmt.Errorf("model image not configured"))
			return
		}
		weights := s.weightPath(model)
		err = run(model, "-v", volumeSpec(directory, "/run", false), "-v", volumeSpec(filepath.Join(directory, "inputs"), "/run/inputs", true), "-v", volumeSpec(weights, "/weights/model.onnx", true), image, "--run", "/run", "--weights", "/weights/model.onnx")
		if err != nil {
			s.note(id + " model failed: " + err.Error())
			s.updateJob(id, "failed", model, err)
			return
		}
	}
	if len(request.Metrics) == 0 && !request.Visualization {
		s.updateJob(id, "succeeded", "finished", nil)
		return
	}
	s.updateJob(id, "running", "diagnostics", nil)
	config := map[string]any{"version": 2, "run": "/run"}
	if len(request.Metrics) > 0 {
		config["metrics"] = request.Metrics
		config["verification_models"] = request.VerificationModels
	}
	if request.Visualization {
		config["visualization"] = map[string]any{"models": request.VisualizationModels, "basemap": "/basemap"}
	}
	operation, err := s.prepareDiagnostics(id, config, request.Visualization && request.DataID != "cma_radar")
	if err != nil {
		s.updateJob(id, "partial", "diagnostics", err)
		return
	}
	s.jobsMu.Lock()
	s.jobs[id].DiagnosticOperationID = operation.ID
	err = writeJob(directory, s.jobs[id])
	s.jobsMu.Unlock()
	if err != nil {
		s.updateJob(id, "partial", "diagnostics", err)
		return
	}
	operation = s.executeDiagnostics(id, operation)
	s.jobsMu.Lock()
	s.jobs[id].ReportEntry = operation.ReportEntry
	s.jobsMu.Unlock()
	var diagnosticError error
	if operation.Error != "" {
		diagnosticError = fmt.Errorf("%s", operation.Error)
	}
	status := operation.Status
	if status == "failed" {
		status = "partial"
	} // Forecasts remain available.
	s.updateJob(id, status, "finished", diagnosticError)
}

func (s *Server) handleJob(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !idPattern.MatchString(id) {
		runtimeError(w, 400, fmt.Errorf("invalid job ID"))
		return
	}
	s.jobsMu.Lock()
	job := s.jobs[id]
	if job != nil {
		copy := *job
		s.jobsMu.Unlock()
		writeJSON(w, 200, copy)
		return
	}
	// Reconcile history under the same lock as new admissions and job writes.
	raw, err := os.ReadFile(filepath.Join(s.Config.Paths.Runs, id, "job.json"))
	if err != nil {
		s.jobsMu.Unlock()
		runtimeError(w, 404, fmt.Errorf("job not found"))
		return
	}
	var recorded Job
	if json.Unmarshal(raw, &recorded) != nil {
		s.jobsMu.Unlock()
		runtimeError(w, 500, fmt.Errorf("invalid recorded job"))
		return
	}
	if recorded.Status == "running" || recorded.Status == "queued" {
		recorded.Status = "interrupted"
		recorded.Error = "handler restarted before this job completed"
		recorded.UpdatedAt = time.Now().UTC()
		_ = writeJob(filepath.Join(s.Config.Paths.Runs, id), &recorded)
	}
	s.jobsMu.Unlock()
	writeJSON(w, 200, recorded)
}

func (s *Server) handleJobLog(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !idPattern.MatchString(id) {
		runtimeError(w, 400, fmt.Errorf("invalid job ID"))
		return
	}
	logs := filepath.Join(s.Config.Paths.Runs, id, "logs")
	entries, err := os.ReadDir(logs)
	if err != nil {
		writeJSON(w, 200, map[string]string{"path": filepath.Join(filepath.Dir(s.Config.Source), "logs", "handler.log"), "tail": ""})
		return
	}
	var tail strings.Builder
	for _, entry := range entries {
		logPath := filepath.Join(logs, entry.Name())
		if entry.IsDir() && strings.HasPrefix(entry.Name(), "diagnostics-") {
			logPath = filepath.Join(logPath, "container.log")
		}
		raw, readErr := os.ReadFile(logPath)
		if readErr == nil {
			tail.WriteString(entry.Name() + "\n" + string(raw))
		}
	}
	text := tail.String()
	if len(text) > 8000 {
		text = text[len(text)-8000:]
	}
	writeJSON(w, 200, map[string]string{"path": filepath.Join(filepath.Dir(s.Config.Source), "logs", "handler.log"), "tail": text})
}

func runtimeError(w http.ResponseWriter, status int, err error) {
	writeJSON(w, status, map[string]string{"code": "RUNTIME_ERROR", "message": err.Error()})
}
