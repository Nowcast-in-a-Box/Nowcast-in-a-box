package handler

import (
	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler/internal/orders"
	"net/http"
	"sort"
)

type (
	Order              = orders.Order
	OrderRegion        = orders.OrderRegion
	OrderVerification  = orders.OrderVerification
	OrderVisualization = orders.OrderVisualization
	RuntimeRequest     = orders.RuntimeRequest
)

var MetricFamilies = orders.MetricFamilies

func ParseOrder(raw []byte, resources *Resources, maxSamples int) (Order, RuntimeRequest, error) {
	return orders.ParseOrder(raw, resources, maxSamples)
}

func ParseRuntimePackage(raw []byte, expected string, resources *Resources) (RuntimeRequest, error) {
	return orders.ParseRuntimePackage(raw, expected, resources)
}

func (s *Server) handleOrderOptions(w http.ResponseWriter, r *http.Request) {
	if err := s.resourceFailure(); err != nil {
		runtimeError(w, 503, err)
		return
	}
	data := []any{}
	ids := []string{}
	for id := range s.Resources.Data {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		resource := s.Resources.Data[id]
		models := []any{}
		modelIDs := []string{}
		for modelID := range s.Resources.Models[id] {
			modelIDs = append(modelIDs, modelID)
		}
		sort.Strings(modelIDs)
		for _, modelID := range modelIDs {
			model := s.Resources.Models[id][modelID]
			models = append(models, map[string]any{"id": model.ID, "name": model.Name})
		}
		cadence := 5
		item := map[string]any{"id": id, "name": resource.Name, "models": models, "verification": map[string]any{"level_min": resource.Verification.LevelMin, "level_max": resource.Verification.LevelMax, "unit": resource.Verification.Unit}}
		if id == "cma_radar" {
			cadence = 6
			item["region"] = map[string]any{"center_lon": 118, "center_lat": 32, "lon_min": 76, "lon_max": 132, "lat_min": 15, "lat_max": 51}
		}
		item["fixed"] = map[string]any{"context_frames": 6, "target_frames": 20, "cadence_minutes": cadence, "width": 128, "height": 128}
		data = append(data, item)
	}
	methods := []string{}
	for method := range MetricFamilies {
		methods = append(methods, method)
	}
	sort.Strings(methods)
	limit := s.Config.Runtime.MaxBatchSamples
	if limit <= 0 || limit > 32 {
		limit = 32
	}
	writeJSON(w, 200, map[string]any{"data": data, "methods": methods, "max_batch_samples": limit})
}
