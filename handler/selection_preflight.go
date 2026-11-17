package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

func (s *Server) handlePreflight(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Order        *Order   `json:"order,omitempty"`
		YAML         *string  `json:"yaml,omitempty"`
		ProductID    string   `json:"data_product_id,omitempty"`
		DataID       string   `json:"data_id,omitempty"`
		EventID      string   `json:"event_id"`
		StartTime    string   `json:"context_start_time"`
		SampleStarts []string `json:"sample_starts"`
		Models       []string `json:"models"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2*maxOrderBytes))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		runtimeError(w, 400, err)
		return
	}
	if decoder.Decode(new(any)) != io.EOF {
		runtimeError(w, 400, fmt.Errorf("expected one preflight document"))
		return
	}
	var region *OrderRegion
	if request.Order != nil || request.YAML != nil {
		if request.Order != nil && request.YAML != nil || request.DataID != "" || request.ProductID != "" || request.EventID != "" || request.StartTime != "" || len(request.Models) != 0 || len(request.SampleStarts) != 0 {
			runtimeError(w, 422, fmt.Errorf("provide one order or YAML without legacy selection fields"))
			return
		}
		var order Order
		var normalized RuntimeRequest
		var err error
		if request.YAML != nil {
			order, normalized, err = ParseOrder([]byte(*request.YAML), s.Resources, s.currentConfig().Runtime.MaxBatchSamples)
		} else {
			order, normalized, err = normalizeOrder(*request.Order, s.Resources, s.currentConfig().Runtime.MaxBatchSamples)
		}
		if err != nil {
			runtimeError(w, 422, err)
			return
		}
		request.DataID, request.EventID, request.StartTime = normalized.DataID, normalized.EventID, normalized.StartTime
		request.Models, request.SampleStarts, region = normalized.Models, normalized.SampleStarts, order.Region
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
	if !s.recoveryMu.TryLock() {
		runtimeError(w, 409, fmt.Errorf("execution or preparation is active; retry preflight when idle"))
		return
	}
	defer s.recoveryMu.Unlock()
	s.executionMu.Lock()
	busy := len(s.executions) > 0
	s.executionMu.Unlock()
	s.resourcesMu.Lock()
	busy = busy || s.preparingResources
	s.resourcesMu.Unlock()
	if busy {
		runtimeError(w, 409, fmt.Errorf("execution or preparation is active; retry preflight when idle"))
		return
	}
	s.jobsMu.Lock()
	stopping := s.stopping
	s.jobsMu.Unlock()
	if stopping {
		runtimeError(w, 409, fmt.Errorf("Handler is stopping"))
		return
	}
	selection := RuntimeRequest{DataID: dataID, EventID: request.EventID, StartTime: request.StartTime, SampleStarts: request.SampleStarts, Models: request.Models}
	if region != nil {
		selection.CenterLon, selection.CenterLat = *region.CenterLon, *region.CenterLat
	}
	ctx, cancel := context.WithTimeout(r.Context(), imagePullTimeout)
	defer cancel()
	checks, execution, err := s.executionPreflight(ctx, selection, nil)
	writeJSON(w, 200, map[string]any{"ok": err == nil, "checks": checks, "execution_context": execution})
}

func (s *Server) windowCheck(parent context.Context, dataID, data, catalog, eventID, start, id string, region *OrderRegion) Check {
	if eventID == "" || !strings.HasSuffix(start, "Z") {
		return Check{ID: id, OK: false, Message: "event id and a UTC window ending in Z are required"}
	}
	ctx, cancel := context.WithTimeout(parent, time.Minute)
	defer cancel()
	args, err := s.dataCommandArgs(dataID, data, catalog, "--check-window", "--event", eventID, "--start", start)
	if err != nil {
		return Check{ID: id, OK: false, Message: err.Error()}
	}
	if region != nil {
		args = append(args, "--center-lon", strconv.FormatFloat(*region.CenterLon, 'f', -1, 64), "--center-lat", strconv.FormatFloat(*region.CenterLat, 'f', -1, 64))
	}
	args = append([]string{"run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp"}, args...)
	code, out, err := s.docker(ctx, args...)
	if err != nil || code != 0 {
		return Check{ID: id, OK: false, Message: fmt.Sprintf("metadata check failed: %v %s", err, strings.TrimSpace(string(out)))}
	}
	var value struct {
		Ready bool `json:"ready"`
	}
	if json.Unmarshal(containerJSON(out), &value) != nil || !value.Ready {
		return Check{ID: id, OK: false, Message: "selected window metadata was not ready"}
	}
	if dataID == "cma_radar" {
		return Check{ID: id, OK: true, Message: "time-window metadata passed; regional footprint and pixel availability are not probed until ingestion"}
	}
	return Check{ID: id, OK: true, Message: "selected window exists and catalog integrity passed"}
}
