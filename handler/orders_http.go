package handler

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler/internal/orders"
	"gopkg.in/yaml.v3"
)

type orderSummary struct {
	PackageID     string   `json:"package_id"`
	OrderID       string   `json:"order_id,omitempty"`
	DataID        string   `json:"data_id,omitempty"`
	EventID       string   `json:"event_id,omitempty"`
	StartTime     string   `json:"context_start_time,omitempty"`
	Samples       int      `json:"samples,omitempty"`
	Models        []string `json:"models,omitempty"`
	Metrics       int      `json:"metrics,omitempty"`
	Visualization bool     `json:"visualization,omitempty"`
	Source        string   `json:"source"`
	RunID         string   `json:"run_id,omitempty"`
	Error         string   `json:"error,omitempty"`
	ReadOnly      bool     `json:"read_only"`
	Revision      string   `json:"revision,omitempty"`
}

func requestSummary(request *RuntimeRequest) orderSummary {
	return orderSummary{
		PackageID:     request.PackageID,
		OrderID:       request.OrderID,
		DataID:        request.DataID,
		EventID:       request.EventID,
		StartTime:     request.StartTime,
		Samples:       len(request.SampleStarts),
		Models:        append([]string{}, request.Models...),
		Metrics:       len(request.Metrics),
		Visualization: request.Visualization,
	}
}

func (s *Server) handleOrdersList(w http.ResponseWriter, r *http.Request) {
	cfg := s.currentConfig()
	summaries := []orderSummary{}
	seen := map[string]bool{}
	entries, err := os.ReadDir(cfg.Paths.Orders)
	if err != nil && !os.IsNotExist(err) {
		runtimeError(w, 500, err)
		return
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".yaml") {
			continue
		}
		id := strings.TrimSuffix(entry.Name(), ".yaml")
		summary := orderSummary{PackageID: id, OrderID: id, Source: "saved"}
		_, request, _, err := s.loadOrder(id)
		if err != nil {
			summary.Error = err.Error()
			summary.ReadOnly = true
		} else {
			summary = requestSummary(&request)
			summary.Source = "saved"
			summary.Revision = request.OrderRevision
		}
		seen[id] = true
		summaries = append(summaries, summary)
	}
	entries, err = os.ReadDir(cfg.Paths.Runs)
	if err != nil && !os.IsNotExist(err) {
		runtimeError(w, 500, err)
		return
	}
	// ReadDir sorts names; newest historical fallback wins.
	for i := len(entries) - 1; i >= 0; i-- {
		entry := entries[i]
		if !entry.IsDir() || !idPattern.MatchString(entry.Name()) {
			continue
		}
		summary, ok := runPackageSummary(cfg.Paths.Runs, entry.Name())
		if !ok {
			continue
		}
		identity := summary.OrderID
		if identity == "" {
			identity = summary.PackageID
		}
		if seen[identity] {
			continue
		}
		seen[identity] = true
		summaries = append(summaries, summary)
	}
	sort.Slice(summaries, func(i, j int) bool { return summaries[i].OrderID < summaries[j].OrderID })
	writeJSON(w, 200, map[string]any{"orders": summaries, "total": len(summaries)})
}

func runPackageSummary(runsDir, id string) (orderSummary, bool) {
	raw, err := os.ReadFile(filepath.Join(runsDir, id, "job.json"))
	if err != nil {
		return orderSummary{}, false
	}
	var job Job
	if json.Unmarshal(raw, &job) != nil || job.ID != id || job.Request.PackageID == "" {
		return orderSummary{}, false
	}
	summary := requestSummary(&job.Request)
	summary.Source, summary.RunID, summary.ReadOnly = "runs", id, true
	return summary, true
}

func (s *Server) handleOrderSave(w http.ResponseWriter, r *http.Request) {
	order, request, revision, err := s.readOrderInput(w, r)
	if err != nil {
		runtimeError(w, 422, err)
		return
	}
	id := order.OrderID
	if r.Method == "PATCH" && id != r.PathValue("package_id") {
		runtimeError(w, 422, fmt.Errorf("order_id cannot change during an edit"))
		return
	}
	s.ordersMu.Lock()
	defer s.ordersMu.Unlock()
	_, previous, _, readErr := s.loadOrder(id)
	status := http.StatusCreated
	if readErr == nil {
		if r.Method == "PATCH" && revision != previous.OrderRevision {
			runtimeError(w, 409, fmt.Errorf("order changed; reload it before saving"))
			return
		}
		if r.Method == "POST" && request.OrderRevision != previous.OrderRevision {
			runtimeError(w, 409, fmt.Errorf("order already exists; open it to edit, or choose another order_id"))
			return
		}
		status = http.StatusOK
	} else if !os.IsNotExist(readErr) {
		runtimeError(w, 409, readErr)
		return
	} else if r.Method == "PATCH" {
		runtimeError(w, 404, fmt.Errorf("saved YAML order not found"))
		return
	}
	raw, err := yaml.Marshal(order)
	if err != nil {
		runtimeError(w, 422, err)
		return
	}
	directory := s.currentConfig().Paths.Orders
	if err := os.MkdirAll(directory, 0o755); err != nil {
		runtimeError(w, 500, err)
		return
	}
	temporary, err := os.CreateTemp(directory, ".order-*")
	if err != nil {
		runtimeError(w, 500, err)
		return
	}
	defer os.Remove(temporary.Name())
	if _, err = temporary.Write(raw); err == nil {
		err = temporary.Sync()
	}
	closeErr := temporary.Close()
	if err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(temporary.Name(), filepath.Join(directory, id+".yaml"))
	}
	if err != nil {
		runtimeError(w, 500, err)
		return
	}
	writeJSON(w, status, orderDetail(order, request, raw))
}

func (s *Server) handleOrder(w http.ResponseWriter, r *http.Request) {
	order, request, raw, err := s.loadOrder(r.PathValue("package_id"))
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	if r.URL.Query().Get("download") == "1" {
		w.Header().Set("Content-Type", "application/yaml")
		w.Header().Set("Content-Disposition", "attachment; filename="+strconv.Quote(order.OrderID+".yaml"))
		_, _ = w.Write(raw)
		return
	}
	writeJSON(w, 200, orderDetail(order, request, raw))
}

func (s *Server) handleOrderValidate(w http.ResponseWriter, r *http.Request) {
	order, request, _, err := s.readOrderInput(w, r)
	if err != nil {
		runtimeError(w, 422, err)
		return
	}
	raw, err := yaml.Marshal(order)
	if err != nil {
		runtimeError(w, 422, err)
		return
	}
	writeJSON(w, 200, orderDetail(order, request, raw))
}

func orderDetail(order Order, request RuntimeRequest, raw []byte) any {
	summary := requestSummary(&request)
	summary.Source, summary.Revision = "saved", request.OrderRevision
	return struct {
		orderSummary
		Order Order  `json:"order"`
		YAML  string `json:"yaml"`
	}{summary, order, string(raw)}
}

func (s *Server) readOrderInput(w http.ResponseWriter, r *http.Request) (Order, RuntimeRequest, string, error) {
	var input struct {
		YAML     *string `json:"yaml"`
		Order    *Order  `json:"order"`
		Revision string  `json:"revision"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2*orders.MaxBytes))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		return Order{}, RuntimeRequest{}, "", err
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return Order{}, RuntimeRequest{}, "", fmt.Errorf("one request object required")
	}
	if (input.YAML == nil) == (input.Order == nil) {
		return Order{}, RuntimeRequest{}, "", fmt.Errorf("provide either yaml or order; ZIP packages are no longer accepted")
	}
	var order Order
	var request RuntimeRequest
	var err error
	if input.YAML != nil {
		order, request, err = ParseOrder([]byte(*input.YAML), s.Resources, s.Config.Runtime.MaxBatchSamples)
	} else {
		order, request, err = orders.Normalize(*input.Order, s.Resources, s.Config.Runtime.MaxBatchSamples)
	}
	return order, request, input.Revision, err
}

func (s *Server) loadOrder(id string) (Order, RuntimeRequest, []byte, error) {
	if !orders.ValidID(id) || id != strings.ToLower(id) {
		return Order{}, RuntimeRequest{}, nil, fmt.Errorf("invalid order_id")
	}
	path := filepath.Join(s.currentConfig().Paths.Orders, id+".yaml")
	info, err := os.Lstat(path)
	if err != nil {
		return Order{}, RuntimeRequest{}, nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > orders.MaxBytes {
		return Order{}, RuntimeRequest{}, nil, fmt.Errorf("order must be a regular YAML file of at most 1 MiB")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return Order{}, RuntimeRequest{}, nil, err
	}
	order, request, err := ParseOrder(raw, s.Resources, s.Config.Runtime.MaxBatchSamples)
	if err == nil && order.OrderID != id {
		err = fmt.Errorf("order_id does not match its filename")
	}
	return order, request, raw, err
}

func (s *Server) handleOrderRun(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Revision string `json:"revision"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil && err != io.EOF {
		runtimeError(w, 400, err)
		return
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		runtimeError(w, 400, fmt.Errorf("one request object required"))
		return
	}
	s.ordersMu.Lock()
	order, request, _, err := s.loadOrder(r.PathValue("package_id"))
	s.ordersMu.Unlock()
	if err != nil {
		runtimeError(w, 404, fmt.Errorf("saved YAML order unavailable (legacy history is read-only): %w", err))
		return
	}
	if input.Revision != "" && input.Revision != request.OrderRevision {
		runtimeError(w, 409, fmt.Errorf("order changed; reload before starting"))
		return
	}
	job, status, err := s.admitOrder(order, request.OrderRevision, r.Header.Get("Idempotency-Key"), "")
	if err != nil {
		runtimeError(w, status, err)
		return
	}
	writeJSON(w, status, *job)
}
