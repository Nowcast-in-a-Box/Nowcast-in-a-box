package orders

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler/internal/resources"
	"gopkg.in/yaml.v3"
)

const Schema = "nib.order.v1"
const MaxBytes = 1 << 20

// Order is portable user configuration. Host paths and executable definitions
// are resolved exclusively from the Handler's trusted resources and settings.
type Order struct {
	SchemaVersion string              `json:"schema_version" yaml:"schema_version"`
	OrderID       string              `json:"order_id" yaml:"order_id"`
	DataID        string              `json:"data_id" yaml:"data_id"`
	EventID       string              `json:"event_id" yaml:"event_id"`
	SampleStarts  []string            `json:"sample_starts" yaml:"sample_starts"`
	Models        []string            `json:"models" yaml:"models"`
	Region        *OrderRegion        `json:"region,omitempty" yaml:"region,omitempty"`
	Verification  *OrderVerification  `json:"verification,omitempty" yaml:"verification,omitempty"`
	Visualization *OrderVisualization `json:"visualization,omitempty" yaml:"visualization,omitempty"`
}

type OrderRegion struct {
	CenterLon *float64 `json:"center_lon" yaml:"center_lon"`
	CenterLat *float64 `json:"center_lat" yaml:"center_lat"`
}

type OrderVerification struct {
	Models      []string     `json:"models" yaml:"models"`
	Methods     []string     `json:"methods" yaml:"methods"`
	Levels      []float64    `json:"levels,omitempty" yaml:"levels,omitempty"`
	HalfWindows orderWindows `json:"half_windows,omitempty" yaml:"half_windows,omitempty"`
}

// YAML's default integer decoder truncates floats; spatial windows must be integers.
type orderWindows []int

func (windows *orderWindows) UnmarshalYAML(node *yaml.Node) error {
	if node.Kind != yaml.SequenceNode {
		return fmt.Errorf("half_windows must be a list of integers")
	}
	for _, value := range node.Content {
		if value.Kind != yaml.ScalarNode || value.Tag != "!!int" {
			return fmt.Errorf("half_windows must contain integers")
		}
	}
	var values []int
	if err := node.Decode(&values); err != nil {
		return err
	}
	*windows = values
	return nil
}

type OrderVisualization struct {
	Models []string `json:"models" yaml:"models"`
}

func Decode(raw []byte) (Order, error) {
	var order Order
	if len(raw) > MaxBytes {
		return order, fmt.Errorf("order exceeds 1 MiB")
	}
	decoder := yaml.NewDecoder(bytes.NewReader(raw))
	decoder.KnownFields(true)
	if err := decoder.Decode(&order); err != nil {
		return order, fmt.Errorf("invalid order YAML: %w", err)
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return order, fmt.Errorf("one YAML document required")
	}
	return order, nil
}

// ParseOrder validates a YAML document without accessing Docker or local data.
func ParseOrder(raw []byte, resources *resources.Resources, maxSamples int) (Order, RuntimeRequest, error) {
	order, err := Decode(raw)
	if err != nil {
		return order, RuntimeRequest{}, err
	}
	return Normalize(order, resources, maxSamples)
}

func Normalize(order Order, resources *resources.Resources, maxSamples int) (Order, RuntimeRequest, error) {
	request := RuntimeRequest{}
	fail := func(message string) (Order, RuntimeRequest, error) { return order, request, fmt.Errorf("%s", message) }
	if order.SchemaVersion != Schema {
		return fail("schema_version must be nib.order.v1")
	}
	if !ValidID(order.OrderID) || order.OrderID != strings.ToLower(order.OrderID) {
		return fail("order_id must be a lowercase portable filename (1..128 characters)")
	}
	if resources == nil || resources.Data[order.DataID] == nil {
		return fail("data_id is not packaged by this Handler")
	}
	if !idPattern.MatchString(order.EventID) {
		return fail("event_id must contain letters, digits, underscores or hyphens")
	}
	if maxSamples <= 0 || maxSamples > 32 {
		maxSamples = 32
	}
	if len(order.SampleStarts) == 0 || len(order.SampleStarts) > maxSamples {
		return fail(fmt.Sprintf("sample_starts requires 1..%d UTC timestamps", maxSamples))
	}
	seenTimes := map[string]bool{}
	order.SampleStarts = append([]string{}, order.SampleStarts...)
	for i, value := range order.SampleStarts {
		stamp, err := time.Parse(time.RFC3339, value)
		_, offset := stamp.Zone()
		if err != nil || offset != 0 {
			return fail("sample_starts must be explicit UTC RFC3339 timestamps")
		}
		value = stamp.UTC().Format(time.RFC3339Nano)
		if seenTimes[value] {
			return fail("sample_starts must contain distinct instants")
		}
		seenTimes[value] = true
		order.SampleStarts[i] = value
	}
	if len(order.Models) == 0 {
		return fail("select at least one model")
	}
	selected := map[string]bool{}
	names := map[string]string{}
	for _, id := range order.Models {
		model, ok := resources.ModelByID(order.DataID, id)
		if !ok || selected[id] {
			return fail("models must be distinct and packaged for the selected data source")
		}
		selected[id], names[id] = true, model.Name
	}
	order.Models = append([]string{}, order.Models...)
	sort.Strings(order.Models)
	if order.Region != nil && order.DataID != "cma_radar" {
		return fail("region is only supported for cma_radar")
	}
	if order.DataID == "cma_radar" {
		if order.EventID != strings.ReplaceAll(order.SampleStarts[0][:10], "-", "") {
			return fail("CMA event_id must match the first sample date (YYYYMMDD)")
		}
		lon, lat := 118.0, 32.0
		if order.Region != nil {
			if order.Region.CenterLon == nil || order.Region.CenterLat == nil {
				return fail("region requires both center_lon and center_lat")
			}
			lon, lat = *order.Region.CenterLon, *order.Region.CenterLat
		}
		if math.IsNaN(lon) || math.IsNaN(lat) || lon < 76 || lon > 132 || lat < 15 || lat > 51 {
			return fail("region center must be within longitude 76..132 and latitude 15..51")
		}
		order.Region = &OrderRegion{CenterLon: &lon, CenterLat: &lat}
		request.CenterLon, request.CenterLat = lon, lat
	}
	if order.Verification != nil {
		v := *order.Verification
		if err := ValidateSubset(v.Models, selected); err != nil {
			return order, request, fmt.Errorf("verification: %w", err)
		}
		v.Models = sortedStrings(v.Models)
		v.Methods = sortedStrings(v.Methods)
		v.Levels = append([]float64{}, v.Levels...)
		v.HalfWindows = append([]int{}, v.HalfWindows...)
		sort.Float64s(v.Levels)
		sort.Ints(v.HalfWindows)
		needsLevels, needsWindows := false, false
		for _, method := range v.Methods {
			family := MetricFamilies[method]
			needsLevels = needsLevels || family == "yes_or_no" || family == "spatial"
			needsWindows = needsWindows || family == "spatial"
		}
		if !needsLevels {
			v.Levels = nil
		}
		if !needsWindows {
			v.HalfWindows = nil
		}
		config := map[string]json.RawMessage{}
		for key, value := range map[string]any{"methods": v.Methods, "levels": v.Levels, "half_windows": v.HalfWindows} {
			raw, err := json.Marshal(value)
			if err != nil {
				return order, request, err
			}
			config[key] = raw
		}
		groups, err := MetricGroups(config, resources.Data[order.DataID])
		if err != nil {
			return order, request, fmt.Errorf("verification: %w", err)
		}
		order.Verification = &v
		request.Metrics, request.VerificationModels = groups, v.Models
	}
	if order.Visualization != nil {
		if err := ValidateSubset(order.Visualization.Models, selected); err != nil {
			return order, request, fmt.Errorf("visualization: %w", err)
		}
		order.Visualization = &OrderVisualization{Models: sortedStrings(order.Visualization.Models)}
		request.Visualization, request.VisualizationModels = true, order.Visualization.Models
	}
	snapshot, err := yaml.Marshal(order)
	if err != nil {
		return order, request, err
	}
	if len(snapshot) > MaxBytes {
		return fail("order exceeds 1 MiB")
	}
	raw, err := json.Marshal(order)
	if err != nil {
		return order, request, err
	}
	sum := sha256.Sum256(raw)
	request.OrderID, request.PackageID = order.OrderID, order.OrderID
	request.DataID, request.EventID = order.DataID, order.EventID
	request.StartTime, request.SampleStarts = order.SampleStarts[0], order.SampleStarts
	request.Models, request.ModelNames = order.Models, names
	request.OrderRevision = hex.EncodeToString(sum[:])
	request.PackageSHA256 = request.OrderRevision
	return order, request, nil
}

func ValidateSubset(models []string, available map[string]bool) error {
	if len(models) == 0 {
		return fmt.Errorf("select at least one model")
	}
	seen := map[string]bool{}
	for _, model := range models {
		if !available[model] || seen[model] {
			return fmt.Errorf("models must be distinct selected forecasts")
		}
		seen[model] = true
	}
	return nil
}

func sortedStrings(values []string) []string {
	result := append([]string{}, values...)
	sort.Strings(result)
	return result
}
