package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func (s *Server) handlePreflight(w http.ResponseWriter, r *http.Request) {
	var request struct {
		EventID   string   `json:"event_id"`
		StartTime string   `json:"context_start_time"`
		Models    []string `json:"models"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		runtimeError(w, 400, err)
		return
	}
	checks := []Check{}
	data, catalog, err := s.dataPaths()
	if err != nil {
		checks = append(checks, Check{ID: "data", OK: false, Message: err.Error()})
	} else {
		checks = append(checks, Check{ID: "data", OK: true, Message: "data file and catalog are present"})
		if request.EventID != "" || request.StartTime != "" {
			checks = append(checks, s.windowCheck(r.Context(), data, catalog, request.EventID, request.StartTime))
		}
	}
	if len(request.Models) == 0 {
		checks = append(checks, Check{ID: "weights", OK: false, Message: "select a model before checking weights"})
	}
	for _, model := range request.Models {
		checks = append(checks, weightCheck(s.Config, model))
	}
	ok := true
	for _, check := range checks {
		ok = ok && check.OK
	}
	writeJSON(w, 200, map[string]any{"ok": ok, "checks": checks})
}

func (s *Server) windowCheck(parent context.Context, data, catalog, eventID, start string) Check {
	if eventID == "" || !strings.HasSuffix(start, "Z") {
		return Check{ID: "window", OK: false, Message: "event id and a UTC window ending in Z are required"}
	}
	ctx, cancel := context.WithTimeout(parent, time.Minute)
	defer cancel()
	code, out, err := s.docker(ctx, "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
		"-v", volumeSpec(data, "/inputs/data.h5", true), "-v", volumeSpec(catalog, "/inputs/CATALOG.csv", true),
		s.Config.Runtime.IngestionImage, "--data", "/inputs/data.h5", "--catalog", "/inputs/CATALOG.csv",
		"--check-window", "--event", eventID, "--start", start)
	if err != nil || code != 0 {
		return Check{ID: "window", OK: false, Message: fmt.Sprintf("metadata check failed: %v %s", err, strings.TrimSpace(string(out)))}
	}
	var value struct {
		Ready bool `json:"ready"`
	}
	if json.Unmarshal(out, &value) != nil || !value.Ready {
		return Check{ID: "window", OK: false, Message: "selected window metadata was not ready"}
	}
	return Check{ID: "window", OK: true, Message: "selected window exists and catalog integrity passed"}
}

func weightCheck(cfg Config, model string) Check {
	name := modelWeightName(model)
	if name == "" {
		return Check{ID: "weight:" + model, OK: false, Message: "unknown model"}
	}
	path := filepath.Join(cfg.Paths.Artifacts, "sevir", model, name)
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return Check{ID: "weight:" + model, OK: false, Message: "weight is not on this machine; download is required before running. Revision lookup stays paused."}
	}
	if err := verifyModelWeights(path, model); err != nil {
		return Check{ID: "weight:" + model, OK: false, Message: "local weight does not match the pinned SHA-256"}
	}
	return Check{ID: "weight:" + model, OK: true, Message: "local weight matches the pinned SHA-256"}
}
