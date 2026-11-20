package handler_test

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
	"gopkg.in/yaml.v3"
)

// Keep legacy archive fixtures for parser tests; HTTP callers submit portable orders.
func orderFromPackage(t *testing.T, raw []byte, hash string) handler.Order {
	t.Helper()
	request, err := handler.ParseRuntimePackage(raw, hash, testResources(t))
	if err != nil {
		t.Fatal(err)
	}
	order := handler.Order{
		SchemaVersion: "nib.order.v1", OrderID: strings.ToLower(request.OrderID),
		DataID: request.DataID, EventID: request.EventID,
		SampleStarts: request.SampleStarts, Models: request.Models,
	}
	if len(order.SampleStarts) == 0 {
		order.SampleStarts = []string{request.StartTime}
	}
	if order.DataID == "cma_radar" {
		order.Region = &handler.OrderRegion{CenterLon: &request.CenterLon, CenterLat: &request.CenterLat}
	}
	if len(request.Metrics) > 0 {
		verification := &handler.OrderVerification{Models: request.VerificationModels}
		for _, group := range request.Metrics {
			encoded, err := json.Marshal(group)
			if err != nil {
				t.Fatal(err)
			}
			var metric struct {
				Items   []string  `json:"items"`
				Levels  []float64 `json:"levels"`
				Windows []int     `json:"half_windows"`
			}
			if err := json.Unmarshal(encoded, &metric); err != nil {
				t.Fatal(err)
			}
			verification.Methods = append(verification.Methods, metric.Items...)
			if len(metric.Levels) > 0 {
				verification.Levels = metric.Levels
			}
			if len(metric.Windows) > 0 {
				verification.HalfWindows = metric.Windows
			}
		}
		order.Verification = verification
	}
	if request.Visualization {
		order.Visualization = &handler.OrderVisualization{Models: request.VisualizationModels}
	}
	return order
}

func orderYAML(t *testing.T, order handler.Order) string {
	t.Helper()
	raw, err := yaml.Marshal(order)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func orderFixture() handler.Order {
	return handler.Order{
		SchemaVersion: "nib.order.v1", OrderID: "order-one", DataID: "sevir",
		EventID: "S696651", SampleStarts: []string{"2017-06-27T00:35:00Z"},
		Models: []string{"wadepre"},
	}
}
