package handler

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
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

func (s *Server) handleResultFile(w http.ResponseWriter, r *http.Request) {
	id, name := r.PathValue("id"), r.PathValue("file")
	manifest, err := s.runManifest(id)
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	clean := filepath.FromSlash(name)
	if filepath.IsAbs(clean) || filepath.Clean(clean) != clean || strings.Contains(name, "\\") {
		runtimeError(w, 400, fmt.Errorf("invalid artifact path"))
		return
	}
	switch strings.ToLower(filepath.Ext(clean)) {
	case ".html", ".png", ".gif", ".svg":
	default:
		runtimeError(w, 403, fmt.Errorf("only registered result files are served"))
		return
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
		runtimeError(w, 403, fmt.Errorf("result is not registered"))
		return
	}
	root, err := filepath.EvalSymlinks(filepath.Join(s.Config.Paths.Runs, id))
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	target, err := filepath.EvalSymlinks(filepath.Join(root, clean))
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	rel, err := filepath.Rel(root, target)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		runtimeError(w, 403, fmt.Errorf("artifact escapes run"))
		return
	}
	info, err := os.Stat(target)
	if err != nil || !info.Mode().IsRegular() {
		runtimeError(w, 404, fmt.Errorf("result file unavailable"))
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
