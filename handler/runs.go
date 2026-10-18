package handler

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

func (s *Server) runManifest(id string) (map[string]any, error) {
	if !idPattern.MatchString(id) {
		return nil, fmt.Errorf("invalid run ID")
	}
	directory, err := filepath.EvalSymlinks(filepath.Join(s.Config.Paths.Runs, id))
	if err != nil {
		return nil, err
	}
	root, err := filepath.EvalSymlinks(s.Config.Paths.Runs)
	if err != nil {
		return nil, err
	}
	relative, err := filepath.Rel(root, directory)
	if err != nil || relative != id {
		return nil, fmt.Errorf("run escapes configured root")
	}
	raw, err := os.ReadFile(filepath.Join(directory, "manifest.json"))
	var manifest map[string]any
	if err == nil {
		if json.Unmarshal(raw, &manifest) != nil || manifest["schema"] != "nib.run.v1" || manifest["id"] != id {
			return nil, fmt.Errorf("invalid run manifest")
		}
	} else if os.IsNotExist(err) {
		manifest = map[string]any{"id": id, "manifest": nil}
	} else {
		return nil, err
	}
	if raw, err := os.ReadFile(filepath.Join(directory, "job.json")); err == nil {
		var job Job
		if json.Unmarshal(raw, &job) != nil || job.ID != id {
			return nil, fmt.Errorf("invalid recorded job")
		}
		s.jobsMu.Lock()
		_, live := s.jobs[id]
		s.jobsMu.Unlock()
		if !live && (job.Status == "running" || job.Status == "queued") {
			job.Status = "interrupted"
			job.Error = "handler restarted before this job completed"
		}
		manifest["job"] = job
		manifest["status"] = job.Status
	} else if manifest["schema"] == nil {
		return nil, fmt.Errorf("run not found")
	}
	latest, _ := os.ReadFile(filepath.Join(directory, "logs", "latest-diagnostics.json"))
	var reference DiagnosticOperation
	if json.Unmarshal(latest, &reference) != nil {
		// Older records had no pointer: use filesystem creation order, never random IDs.
		files, _ := filepath.Glob(filepath.Join(directory, "logs", "diagnostics-*", "status.json"))
		var newest time.Time
		for _, file := range files {
			if info, err := os.Stat(file); err == nil && info.ModTime().After(newest) {
				newest = info.ModTime()
				raw, _ := os.ReadFile(file)
				_ = json.Unmarshal(raw, &reference)
			}
		}
	}
	if idPattern.MatchString(reference.ID) {
		raw, err := os.ReadFile(filepath.Join(directory, "logs", "diagnostics-"+reference.ID, "status.json"))
		var operation DiagnosticOperation
		if err == nil && json.Unmarshal(raw, &operation) == nil && operation.RunID == id {
			job, _ := manifest["job"].(Job)
			if job.DiagnosticOperationID != "" || job.CreatedAt.IsZero() || !job.CreatedAt.After(operation.CreatedAt) {
				if operation.Status == "running" && operation.InstanceID != s.instanceID {
					operation.Status, operation.Error = "interrupted", "Handler restarted before diagnostics completed; verify stopped containers before retrying from saved Forecasts"
				}
				manifest["operation"] = operation
			}
		}
	}
	return manifest, nil
}

func (s *Server) handleRuns(w http.ResponseWriter, r *http.Request) {
	offset, limit, err := pagination(r)
	if err != nil {
		runtimeError(w, 400, err)
		return
	}
	entries, err := os.ReadDir(s.Config.Paths.Runs)
	if os.IsNotExist(err) {
		writeJSON(w, 200, map[string]any{"total": 0, "runs": []any{}})
		return
	}
	if err != nil {
		runtimeError(w, 500, err)
		return
	}
	ids := []string{}
	for _, entry := range entries {
		if entry.IsDir() && idPattern.MatchString(entry.Name()) {
			ids = append(ids, entry.Name())
		}
	}
	sort.Sort(sort.Reverse(sort.StringSlice(ids)))
	values := []map[string]any{}
	for _, id := range ids {
		if manifest, err := s.runManifest(id); err == nil {
			values = append(values, manifest)
		}
	}
	total := len(values)
	if offset > total {
		offset = total
	}
	end := offset + limit
	if end > total {
		end = total
	}
	writeJSON(w, 200, map[string]any{"total": total, "offset": offset, "limit": limit, "runs": values[offset:end]})
}

func (s *Server) handleRun(w http.ResponseWriter, r *http.Request) {
	manifest, err := s.runManifest(r.PathValue("id"))
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	writeJSON(w, 200, manifest)
}

func (s *Server) resultFilePath(id, name string, manifest map[string]any) (string, int, error) {
	clean := filepath.FromSlash(name)
	if filepath.IsAbs(clean) || filepath.Clean(clean) != clean || strings.Contains(name, "\\") {
		return "", 400, fmt.Errorf("invalid artifact path")
	}
	switch strings.ToLower(filepath.Ext(clean)) {
	case ".html", ".png", ".gif", ".svg":
	default:
		return "", 403, fmt.Errorf("only registered result files are served")
	}
	allowed := false
	attempts, _ := manifest["diagnostics"].([]any)
	for _, value := range attempts {
		attempt, ok := value.(map[string]any)
		if !ok || attempt["status"] != "succeeded" && attempt["status"] != "partial" {
			continue
		}
		prefix, _ := attempt["path"].(string)
		if prefix != "" && strings.HasPrefix(name, prefix+"/") {
			allowed = true
			break
		}
	}
	if !allowed {
		return "", 403, fmt.Errorf("result is not registered")
	}
	root, err := filepath.EvalSymlinks(filepath.Join(s.Config.Paths.Runs, id))
	if err != nil {
		return "", 404, err
	}
	target, err := filepath.EvalSymlinks(filepath.Join(root, clean))
	if err != nil {
		return "", 404, err
	}
	rel, err := filepath.Rel(root, target)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", 403, fmt.Errorf("artifact escapes run")
	}
	info, err := os.Stat(target)
	if err != nil || !info.Mode().IsRegular() {
		return "", 404, fmt.Errorf("result file unavailable")
	}
	return target, http.StatusOK, nil
}

func (s *Server) handleResultFile(w http.ResponseWriter, r *http.Request) {
	id, name := r.PathValue("id"), r.PathValue("file")
	manifest, err := s.runManifest(id)
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	target, status, err := s.resultFilePath(id, name, manifest)
	if err != nil {
		runtimeError(w, status, err)
		return
	}
	info, err := os.Stat(target)
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
	file, err := os.Open(target)
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	defer file.Close()
	http.ServeContent(w, r, info.Name(), info.ModTime(), file)
}
