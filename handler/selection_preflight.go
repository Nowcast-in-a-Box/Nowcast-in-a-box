package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

func (s *Server) handlePreflight(w http.ResponseWriter, r *http.Request) {
	var request struct {
		ProductID    string   `json:"data_product_id,omitempty"`
		DataID       string   `json:"data_id,omitempty"`
		EventID      string   `json:"event_id"`
		StartTime    string   `json:"context_start_time"`
		SampleStarts []string `json:"sample_starts"`
		Models       []string `json:"models"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		runtimeError(w, 400, err)
		return
	}
	dataID := request.DataID
	if dataID == "" && request.ProductID != "" {
		if resource, ok := s.Resources.DataByProductID(request.ProductID); ok {
			dataID = resource.ID
		}
	}
	if _, err := s.dataResource(dataID); err != nil {
		runtimeError(w, 409, err)
		return
	}
	checks := []Check{}
	data, catalog, err := s.dataPaths(dataID)
	if err != nil {
		checks = append(checks, Check{ID: "data", OK: false, Message: err.Error()})
	} else {
		checks = append(checks, Check{ID: "data", OK: true, Message: "selected data files are present"})
		if request.EventID != "" || request.StartTime != "" {
			starts := request.SampleStarts
			if len(starts) == 0 {
				starts = []string{request.StartTime}
			}
			for index, start := range starts {
				eventID := request.EventID
				if dataID == "cma_radar" {
					eventID = strings.ReplaceAll(start[:min(10, len(start))], "-", "")
				}
				id := "window"
				if len(starts) > 1 {
					id = fmt.Sprintf("window:%d", index+1)
				}
				checks = append(checks, s.windowCheck(r.Context(), dataID, data, catalog, eventID, start, id))
			}
		}
	}
	if len(request.Models) == 0 {
		checks = append(checks, Check{ID: "weights", OK: false, Message: "select a model before checking weights"})
	}
	for _, model := range request.Models {
		checks = append(checks, s.weightCheck(dataID, model))
	}
	ok := true
	for _, check := range checks {
		ok = ok && check.OK
	}
	writeJSON(w, 200, map[string]any{"ok": ok, "checks": checks})
}

func (s *Server) windowCheck(parent context.Context, dataID, data, catalog, eventID, start, id string) Check {
	if eventID == "" || !strings.HasSuffix(start, "Z") {
		return Check{ID: id, OK: false, Message: "event id and a UTC window ending in Z are required"}
	}
	ctx, cancel := context.WithTimeout(parent, time.Minute)
	defer cancel()
	args, err := s.dataCommandArgs(dataID, data, catalog, "--check-window", "--event", eventID, "--start", start)
	if err != nil {
		return Check{ID: id, OK: false, Message: err.Error()}
	}
	args = append([]string{"run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp"}, args...)
	code, out, err := s.docker(ctx, args...)
	if err != nil || code != 0 {
		return Check{ID: id, OK: false, Message: fmt.Sprintf("metadata check failed: %v %s", err, strings.TrimSpace(string(out)))}
	}
	var value struct {
		Ready bool `json:"ready"`
	}
	if json.Unmarshal(out, &value) != nil || !value.Ready {
		return Check{ID: id, OK: false, Message: "selected window metadata was not ready"}
	}
	return Check{ID: id, OK: true, Message: "selected window exists and catalog integrity passed"}
}

func (s *Server) weightCheck(dataID, modelID string) Check {
	resource, err := s.modelResource(dataID, modelID)
	if err != nil {
		return Check{ID: "weight:" + modelID, OK: false, Message: err.Error()}
	}
	path, err := s.weightPath(dataID, modelID)
	if err != nil {
		return Check{ID: "weight:" + modelID, OK: false, Message: err.Error()}
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return Check{ID: "weight:" + modelID, OK: true, Message: "weight is not present yet; Validate copies it from paths.weight_source"}
	}
	if err := verifyWeight(path, resource.Weights.SHA256); err != nil {
		return Check{ID: "weight:" + modelID, OK: true, Message: "local weight does not match the pinned SHA-256; Validate replaces it from paths.weight_source"}
	}
	return Check{ID: "weight:" + modelID, OK: true, Message: "local weight matches the pinned SHA-256"}
}
