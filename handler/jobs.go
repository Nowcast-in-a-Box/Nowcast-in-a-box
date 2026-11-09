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
	TriggerID             string         `json:"trigger_id,omitempty"`
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
	mux.HandleFunc("GET /v1/resources", s.authorize(s.handleResources))
	mux.HandleFunc("POST /v1/resources/prepare", s.authorize(s.handlePrepareResources))
	mux.HandleFunc("GET /v1/datasets/{dataset}/samples", s.authorize(s.handleSamples))
	s.triggerRoutes(mux)
	mux.HandleFunc("GET /v1/orders", s.authorize(s.handleOrdersList))
	mux.HandleFunc("POST /v1/orders", s.authorize(s.handleOrderSave))
	mux.HandleFunc("POST /v1/orders/{package_id}/runs", s.authorize(s.handleOrderRun))
	mux.HandleFunc("POST /v1/jobs", s.authorize(s.handleCreateJob))
	mux.HandleFunc("GET /v1/jobs/{id}", s.authorize(s.handleJob))
	mux.HandleFunc("GET /v1/jobs/{id}/log", s.authorize(s.handleJobLog))
	mux.HandleFunc("GET /v1/runs", s.authorize(s.handleRuns))
	mux.HandleFunc("GET /v1/runs/{id}", s.authorize(s.handleRun))
	mux.HandleFunc("GET /v1/runs/{id}/files/{file...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /api/{path...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /static/{path...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /visualization", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /visualization/{path...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /map-viewer/{path...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /map-basemap/{path...}", s.handleDiagnosticsPage)
}

func (s *Server) docker(ctx context.Context, args ...string) (int, []byte, error) {
	if s.Config.Runtime.DockerContext != "" {
		args = append([]string{"--context", s.Config.Runtime.DockerContext}, args...)
	}
	return s.Runner.Run(ctx, args...)
}

// requireDocker refuses a new run when the daemon cannot be reached. Starting a
// run is the only operation that needs Docker; stopping, restarting and browsing
// saved orders do not. The message reaches both the Hub and the local console.
func (s *Server) requireDocker() error {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	code, out, err := s.docker(ctx, "info", "--format", "{{.ServerVersion}}")
	if err == nil && code == 0 {
		return nil
	}
	detail := strings.TrimSpace(string(out))
	if line, _, more := strings.Cut(detail, "\n"); more {
		detail = line
	}
	if detail == "" && err != nil {
		detail = err.Error()
	}
	if detail == "" {
		return fmt.Errorf("Docker is not running; start Docker and retry")
	}
	return fmt.Errorf("Docker is not running; start Docker and retry (%s)", detail)
}

func (s *Server) handleCapabilities(w http.ResponseWriter, r *http.Request) {
	if err := s.resourceFailure(); err != nil {
		runtimeError(w, 503, err)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	code, out, err := s.docker(ctx, "info", "--format", "{{.ServerVersion}}")
	dockerReady := err == nil && code == 0
	dataSources := []map[string]any{}
	readyModels := []map[string]any{}
	for _, data := range s.Resources.DataSources() {
		_, _, dataErr := s.dataPaths(data.ID)
		source := map[string]any{"id": data.ID, "product_id": data.ProductID, "name": data.Name, "data_root": s.dataRoot(data.ID), "ready": dataErr == nil, "reason": ""}
		if dataErr != nil {
			source["reason"] = dataErr.Error()
		}
		dataSources = append(dataSources, source)
		for _, modelID := range s.Resources.ModelIDs(data.ID) {
			model, _ := s.modelResource(data.ID, modelID)
			imageCode, _, imageErr := s.docker(ctx, "image", "inspect", model.Container.Image)
			var weightErr error
			if !model.Weights.Embedded {
				var weight string
				weight, weightErr = s.weightPath(data.ID, modelID)
				if weightErr == nil {
					weightErr = verifyWeight(weight, model.Weights.SHA256)
				}
			}
			readyModels = append(readyModels, map[string]any{"data_id": data.ID, "id": modelID, "ready": dockerReady && dataErr == nil && imageErr == nil && imageCode == 0 && weightErr == nil})
		}
	}
	diagnosticsReady := false
	if s.Resources.Diagnostics != nil {
		diagnosticCode, _, diagnosticErr := s.docker(ctx, "image", "inspect", s.Resources.Diagnostics.Container.Image)
		diagnosticsReady = dockerReady && diagnosticErr == nil && diagnosticCode == 0
	}
	writeJSON(w, 200, map[string]any{"data_sources": dataSources, "models": readyModels,
		"docker": map[string]any{"ready": dockerReady, "detail": strings.TrimSpace(string(out))}, "atlas_revision": s.Config.Runtime.AtlasRevision,
		"diagnostics": map[string]any{"verification": diagnosticsReady, "visualization": diagnosticsReady}})
}

func verifyWeight(path, expected string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return err
	}
	if expected == "" || hex.EncodeToString(hash.Sum(nil)) != expected {
		return fmt.Errorf("weight checksum mismatch")
	}
	return nil
}

func (s *Server) handleSamples(w http.ResponseWriter, r *http.Request) {
	dataset := r.PathValue("dataset")
	if _, err := s.dataResource(dataset); err != nil {
		runtimeError(w, 409, err)
		return
	}
	offset, limit, err := pagination(r)
	if err != nil {
		runtimeError(w, 400, err)
		return
	}
	if _, _, err := s.dataPaths(dataset); err != nil {
		runtimeError(w, 409, err)
		return
	}
	value, err := s.listSamples(r.Context(), dataset, offset, limit, strings.TrimSpace(r.URL.Query().Get("day")))
	if err != nil {
		runtimeError(w, 502, err)
		return
	}
	writeJSON(w, 200, value)
}

// listSamples asks the data reader which windows exist. It is the only way to
// answer that question: the Handler never parses the data files itself. The
// sample route and the trigger poller share this one call.
func (s *Server) listSamples(ctx context.Context, dataID string, offset, limit int, day string) (map[string]any, error) {
	data, catalog, err := s.dataPaths(dataID)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, time.Minute)
	defer cancel()
	extra := []string{"--list-samples", "--offset", strconv.Itoa(offset), "--limit", strconv.Itoa(limit)}
	if day != "" {
		extra = append(extra, "--day", day)
	}
	args, err := s.dataCommandArgs(dataID, data, catalog, extra...)
	if err != nil {
		return nil, err
	}
	args = append([]string{"run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp"}, args...)
	code, out, err := s.docker(ctx, args...)
	if err != nil || code != 0 {
		return nil, fmt.Errorf("sample reader failed: %v %s", err, out)
	}
	var value map[string]any
	if json.Unmarshal(containerJSON(out), &value) != nil {
		return nil, fmt.Errorf("invalid sample reader response")
	}
	return value, nil
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
	job, status, err := s.createJobFromPackage(upload.Package, upload.SHA256, key)
	if err != nil {
		runtimeError(w, status, err)
		return
	}
	writeJSON(w, status, *job)
}

// createJobFromPackage admits a verified package ZIP as a new run, or returns
// the existing job when the idempotency key repeats. It is shared by the Hub
// upload path and the local console's saved-order start.
func (s *Server) createJobFromPackage(pkg []byte, expected, key string) (*Job, int, error) {
	request, err := ParseRuntimePackage(pkg, expected, s.Resources)
	if err != nil {
		return nil, 422, err
	}
	return s.admitJob(request, pkg, key, "")
}

// admitJob validates one request and starts it. A trigger calls it after
// replacing the start window in a parsed package; triggerID is empty for the
// Hub upload and saved-order paths.
func (s *Server) admitJob(request RuntimeRequest, pkg []byte, key, triggerID string) (*Job, int, error) {
	if err := s.requireDataSupported(request.DataID); err != nil {
		return nil, 422, err
	}
	if len(request.SampleStarts) == 0 {
		request.SampleStarts = []string{request.StartTime}
	}
	limit := s.Config.Runtime.MaxBatchSamples
	if limit <= 0 {
		limit = defaultMaxBatchSamples
	}
	if len(request.SampleStarts) > limit {
		return nil, 422, fmt.Errorf("this Handler allows at most %d sample windows per run", limit)
	}
	initial, parseErr := time.Parse(time.RFC3339, request.StartTime)
	_, zoneOffset := initial.Zone()
	if parseErr != nil || zoneOffset != 0 {
		return nil, 422, fmt.Errorf("sample context time must be explicit UTC")
	}
	if _, _, err := s.dataPaths(request.DataID); err != nil {
		return nil, 409, err
	}
	if len(s.Config.Runtime.AtlasRevision) != 40 {
		return nil, 409, fmt.Errorf("configure the pinned atlas revision first")
	}
	if err := s.reconcileExecutions(); err != nil {
		return nil, http.StatusConflict, err
	}
	s.jobsMu.Lock()
	defer s.jobsMu.Unlock()
	if s.stopping {
		return nil, http.StatusServiceUnavailable, fmt.Errorf("Handler is stopping; reconnect after starting it again")
	}
	// A frozen package is idempotent; explicit rerun is a separate request identity.
	if key != "" {
		for _, job := range s.jobs {
			if job.IdempotencyKey == key {
				if job.Request.PackageSHA256 != request.PackageSHA256 {
					return nil, 409, fmt.Errorf("idempotency key belongs to another package")
				}
				copy := *job
				return &copy, 200, nil
			}
		}
		entries, _ := os.ReadDir(s.Config.Paths.Runs)
		for _, entry := range entries {
			if !entry.IsDir() || !idPattern.MatchString(entry.Name()) {
				continue
			}
			recordedRaw, readErr := os.ReadFile(filepath.Join(s.Config.Paths.Runs, entry.Name(), "job.json"))
			if readErr != nil {
				continue
			}
			var recorded Job
			if json.Unmarshal(recordedRaw, &recorded) != nil || recorded.ID != entry.Name() || recorded.IdempotencyKey != key {
				continue
			}
			if recorded.Request.PackageSHA256 != request.PackageSHA256 {
				return nil, 409, fmt.Errorf("idempotency key belongs to another package")
			}
			if live := s.jobs[recorded.ID]; live != nil && (live.Status == "queued" || live.Status == "running") {
				return nil, http.StatusConflict, fmt.Errorf("this run has a newer active request; observe it in Runs before retrying an older request")
			}
			if recorded.Status == "running" || recorded.Status == "queued" {
				recorded.Status = "interrupted"
				recorded.Error = "handler restarted before this job completed"
				_ = writeJob(filepath.Join(s.Config.Paths.Runs, entry.Name()), &recorded)
			}
			s.jobs[recorded.ID] = &recorded
			return &recorded, 200, nil
		}
	}
	for _, job := range s.jobs {
		if job.Request.PackageSHA256 == request.PackageSHA256 && (job.Status == "queued" || job.Status == "running") {
			return nil, http.StatusConflict, fmt.Errorf("this package is already queued or running; observe it in Runs and wait before rerunning")
		}
	}
	if err := s.requireDocker(); err != nil {
		return nil, http.StatusServiceUnavailable, err
	}
	id, err := newRunID(time.Now())
	if err != nil {
		return nil, 500, err
	}
	directory := filepath.Join(s.Config.Paths.Runs, id)
	if err = os.MkdirAll(s.Config.Paths.Runs, 0o755); err != nil {
		return nil, 500, err
	}
	if err = os.Mkdir(directory, 0o755); err != nil {
		return nil, 500, err
	}
	job := &Job{ID: id, Status: "queued", Stage: "queued", Request: request, IdempotencyKey: key, TriggerID: triggerID, CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	if err := writeJob(directory, job); err != nil {
		return nil, 500, err
	}
	if err := os.WriteFile(filepath.Join(directory, "package.zip"), pkg, 0o600); err != nil {
		return nil, 500, err
	}
	s.jobs[id] = job
	copy := *job
	go s.executeJob(id)
	return &copy, http.StatusAccepted, nil
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
	if len(request.SampleStarts) > 1 {
		s.executeBatchJob(id, request, directory)
		return
	}
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
		service := map[string]any{"image": args[0], "command": args[1:], "volumes": volumes, "network_mode": "none", "read_only": true, "tmpfs": []string{"/tmp"}, "pull_policy": "never"}
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
	data, catalog, err := s.dataPaths(request.DataID)
	if err != nil {
		s.updateJob(id, "failed", "preflight", err)
		return
	}
	// Ingestion creates a child run inside the job workspace, then publishes its contents.
	args, err := s.dataCommandArgs(request.DataID, data, catalog, "--event", request.EventID, "--start", request.StartTime, "--runs", "/workspace", "--id", id, "--atlas-revision", s.Config.Runtime.AtlasRevision)
	if err != nil {
		s.updateJob(id, "failed", "preflight", err)
		return
	}
	if request.CenterLon != 0 || request.CenterLat != 0 {
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
		args, specErr := s.modelCommandArgs(request.DataID, model, directory)
		if specErr != nil {
			s.updateJob(id, "failed", model, specErr)
			return
		}
		err = run(model, args...)
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
		config["visualization"] = map[string]any{"models": request.VisualizationModels}
	}
	operation, err := s.prepareDiagnostics(id, config)
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

func (s *Server) runStage(ctx context.Context, directory, id, stage string, args ...string) error {
	name := "nib-" + strings.ToLower(id) + "-" + stage
	volumes := []string{}
	for len(args) >= 2 && args[0] == "-v" {
		volumes = append(volumes, args[1])
		args = args[2:]
	}
	if len(args) == 0 {
		return fmt.Errorf("missing configured image")
	}
	service := map[string]any{"image": args[0], "command": args[1:], "volumes": volumes, "network_mode": "none", "read_only": true, "tmpfs": []string{"/tmp"}, "pull_policy": "never"}
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

func (s *Server) writeManifestJSON(directory string, manifest map[string]any) error {
	raw, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(directory, "manifest.json"), append(raw, '\n'), 0o644)
}

func (s *Server) executeBatchJob(id string, request RuntimeRequest, directory string) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	data, catalog, err := s.dataPaths(request.DataID)
	if err != nil {
		s.updateJob(id, "failed", "preflight", err)
		return
	}
	samplesRoot := filepath.Join(directory, "samples")
	if err := os.MkdirAll(samplesRoot, 0o755); err != nil {
		s.updateJob(id, "failed", "ingestion", err)
		return
	}
	starts := request.SampleStarts
	if len(starts) == 0 {
		starts = []string{request.StartTime}
	}
	samples := []map[string]any{}
	var firstManifest map[string]any
	failures := []string{}
	for index, start := range starts {
		childID := fmt.Sprintf("%03d", index)
		childDir := filepath.Join(samplesRoot, childID)
		eventID := request.EventID
		if request.DataID == "cma_radar" {
			if len(start) < 10 {
				failures = append(failures, fmt.Sprintf("%s: invalid start time", childID))
				continue
			}
			eventID = strings.ReplaceAll(start[:10], "-", "")
		}
		s.updateJob(id, "running", fmt.Sprintf("sample %d/%d", index+1, len(starts)), nil)
		args, argErr := s.dataCommandArgs(request.DataID, data, catalog, "--event", eventID, "--start", start, "--runs", "/workspace", "--id", childID, "--atlas-revision", s.Config.Runtime.AtlasRevision)
		if argErr != nil {
			failures = append(failures, childID+": "+argErr.Error())
			continue
		}
		if request.CenterLon != 0 || request.CenterLat != 0 {
			args = append(args, "--center-lon", strconv.FormatFloat(request.CenterLon, 'f', -1, 64), "--center-lat", strconv.FormatFloat(request.CenterLat, 'f', -1, 64))
		}
		args = append([]string{"-v", volumeSpec(samplesRoot, "/workspace", false)}, args...)
		if err := s.runStage(ctx, directory, id, "sample-"+childID+"-ingestion", args...); err != nil {
			failures = append(failures, childID+": ingestion: "+err.Error())
			continue
		}
		for _, model := range request.Models {
			args, specErr := s.modelCommandArgs(request.DataID, model, childDir)
			if specErr != nil {
				failures = append(failures, childID+": "+specErr.Error())
				continue
			}
			if err := s.runStage(ctx, directory, id, "sample-"+childID+"-"+model, args...); err != nil {
				failures = append(failures, childID+": "+model+": "+err.Error())
				continue
			}
		}
		childManifest := map[string]any{}
		raw, readErr := os.ReadFile(filepath.Join(childDir, "manifest.json"))
		if readErr != nil || json.Unmarshal(raw, &childManifest) != nil {
			failures = append(failures, childID+": child manifest missing")
			continue
		}
		if len(request.ModelNames) > 0 {
			names, _ := childManifest["model_names"].(map[string]any)
			if names == nil {
				names = map[string]any{}
			}
			for model, name := range request.ModelNames {
				names[model] = name
			}
			childManifest["model_names"] = names
			if err := s.writeManifestJSON(childDir, childManifest); err != nil {
				failures = append(failures, childID+": "+err.Error())
				continue
			}
		}
		if firstManifest == nil {
			firstManifest = childManifest
		}
		sample := map[string]any{}
		if rawSample, ok := childManifest["sample"].(map[string]any); ok {
			for key, value := range rawSample {
				sample[key] = value
			}
		}
		sample["path"] = filepath.ToSlash(filepath.Join("samples", childID))
		samples = append(samples, sample)
	}
	if firstManifest == nil {
		s.updateJob(id, "failed", "ingestion", fmt.Errorf("no sample window completed: %s", strings.Join(failures, "; ")))
		return
	}
	batchManifest := map[string]any{
		"schema": "nib.run.v1", "id": id, "batch": true,
		"status": "ingested", "sample_count": len(samples), "samples": samples,
		"sample": firstManifest["sample"], "data": firstManifest["data"],
		"observations": firstManifest["observations"], "forecasts": firstManifest["forecasts"],
		"diagnostics": []any{},
	}
	if names, ok := firstManifest["model_names"]; ok {
		batchManifest["model_names"] = names
	}
	if len(failures) > 0 {
		batchManifest["failed_samples"] = failures
	}
	if err := s.writeManifestJSON(directory, batchManifest); err != nil {
		s.updateJob(id, "failed", "ingestion", err)
		return
	}
	if len(request.Metrics) == 0 && !request.Visualization {
		status := "succeeded"
		if len(failures) > 0 {
			status = "partial"
		}
		s.updateJob(id, status, "finished", nil)
		return
	}
	s.updateJob(id, "running", "diagnostics", nil)
	config := map[string]any{"version": 2, "run": "/run"}
	if len(request.Metrics) > 0 {
		config["metrics"] = request.Metrics
		config["verification_models"] = request.VerificationModels
	}
	if request.Visualization {
		config["visualization"] = map[string]any{"models": request.VisualizationModels}
	}
	operation, err := s.prepareDiagnostics(id, config)
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
	}
	if status == "succeeded" && len(failures) > 0 {
		status = "partial"
	}
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
			tail.WriteString(entry.Name() + "\n" + string(raw) + "\n")
		}
	}
	text := tail.String()
	if len(text) > 8000 {
		start := len(text) - 8000
		if newline := strings.IndexByte(text[start:], '\n'); newline >= 0 {
			start += newline + 1
		}
		text = "[earlier output truncated]\n" + text[start:]
	}
	writeJSON(w, 200, map[string]string{"path": filepath.Join(filepath.Dir(s.Config.Source), "logs", "handler.log"), "tail": text})
}

func runtimeError(w http.ResponseWriter, status int, err error) {
	writeJSON(w, status, map[string]string{"code": "RUNTIME_ERROR", "message": err.Error()})
}
