package handler

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path"
	"strings"
)

const maxPackageBytes = 8 << 20

type RuntimeRequest struct {
	OrderID             string           `json:"order_id"`
	PackageID           string           `json:"package_id"`
	PackageSHA256       string           `json:"package_sha256"`
	EventID             string           `json:"event_id"`
	StartTime           string           `json:"context_start_time"`
	Models              []string         `json:"models"`
	Metrics             []map[string]any `json:"metrics,omitempty"`
	Visualization       bool             `json:"visualization"`
	VerificationModels  []string         `json:"verification_models,omitempty"`
	VisualizationModels []string         `json:"visualization_models,omitempty"`
}

type packageItem struct {
	ID       string                     `json:"order_item_id"`
	Product  string                     `json:"product_id"`
	Version  string                     `json:"version_id"`
	Category string                     `json:"category"`
	Config   map[string]json.RawMessage `json:"resolved_configuration"`
	Bindings []struct {
		Consumer      string `json:"consumer_order_item_id"`
		Producer      string `json:"producer_order_item_id"`
		Input         string `json:"input_name"`
		Output        string `json:"output_name"`
		Compatibility string `json:"compatibility"`
	} `json:"input_bindings"`
}

func metricGroups(config map[string]json.RawMessage) ([]map[string]any, error) {
	var methods []string
	var levels []float64
	var windows []int
	if len(config) != 3 || json.Unmarshal(config["methods"], &methods) != nil || json.Unmarshal(config["levels"], &levels) != nil || json.Unmarshal(config["half_windows"], &windows) != nil || len(methods) == 0 || len(methods) > 11 || len(levels) == 0 || len(levels) > 16 || len(windows) == 0 || len(windows) > 16 {
		return nil, fmt.Errorf("invalid metric configuration")
	}
	levelSet := map[float64]bool{}
	for _, level := range levels {
		if level < 0 || level > 255 || levelSet[level] {
			return nil, fmt.Errorf("VIL thresholds must be distinct raw values 0..255")
		}
		levelSet[level] = true
	}
	windowSet := map[int]bool{}
	for _, window := range windows {
		if window < 0 || window > 16 || windowSet[window] {
			return nil, fmt.Errorf("invalid or repeated spatial window")
		}
		windowSet[window] = true
	}
	families := map[string]string{"mae": "continuous", "rmse": "continuous", "me": "continuous", "ts": "yes_or_no", "bias": "yes_or_no", "ets": "yes_or_no", "far": "yes_or_no", "pod": "yes_or_no", "dts": "yes_or_no", "fss": "spatial", "corr": "moments"}
	selected := map[string][]string{}
	seen := map[string]bool{}
	for _, method := range methods {
		family := families[method]
		if family == "" || seen[method] {
			return nil, fmt.Errorf("unsupported or repeated metric")
		}
		seen[method] = true
		selected[family] = append(selected[family], method)
	}
	groups := []map[string]any{}
	for _, family := range []string{"continuous", "yes_or_no", "spatial", "moments"} {
		if items := selected[family]; len(items) > 0 {
			group := map[string]any{"family": family, "items": items}
			if family == "yes_or_no" || family == "spatial" {
				group["levels"] = levels
			}
			if family == "spatial" {
				group["half_windows"] = windows
			}
			groups = append(groups, group)
		}
	}
	return groups, nil
}

// ParseRuntimePackage checks the immutable package before selecting any execution.
// Templates and deployment material remain inert bytes.
func ParseRuntimePackage(raw []byte, expected string) (RuntimeRequest, error) {
	var request RuntimeRequest
	digest := sha256.Sum256(raw)
	if len(raw) > maxPackageBytes || hex.EncodeToString(digest[:]) != expected {
		return request, fmt.Errorf("package size or SHA256 mismatch")
	}
	archive, err := zip.NewReader(bytes.NewReader(raw), int64(len(raw)))
	if err != nil {
		return request, err
	}
	if len(archive.File) > 128 {
		return request, fmt.Errorf("too many package entries")
	}
	files := map[string][]byte{}
	var expanded uint64
	for _, entry := range archive.File {
		name := entry.Name
		if name == "" || strings.Contains(name, "\\") || strings.HasPrefix(name, "/") || path.Clean(name) != name || strings.Contains(name, ":") || name == ".." || strings.HasPrefix(name, "../") || entry.Mode()&os.ModeSymlink != 0 {
			return request, fmt.Errorf("unsafe archive entry")
		}
		if _, found := files[name]; found {
			return request, fmt.Errorf("duplicate archive entry")
		}
		if entry.UncompressedSize64 > maxPackageBytes-expanded {
			return request, fmt.Errorf("expanded package too large")
		}
		expanded += entry.UncompressedSize64
		stream, err := entry.Open()
		if err != nil {
			return request, err
		}
		body, readErr := io.ReadAll(io.LimitReader(stream, maxPackageBytes+1))
		closeErr := stream.Close()
		if readErr != nil {
			return request, readErr
		}
		if closeErr != nil {
			return request, closeErr
		}
		if len(body) > maxPackageBytes {
			return request, fmt.Errorf("archive entry too large")
		}
		files[name] = body
	}
	var manifest struct {
		Schema    string        `json:"schema_version"`
		OrderID   string        `json:"order_id"`
		PackageID string        `json:"package_id"`
		Items     []packageItem `json:"items"`
		Entries   []struct {
			Path string `json:"path"`
			SHA  string `json:"sha256"`
		} `json:"entries"`
	}
	if err := json.Unmarshal(files["manifest.json"], &manifest); err != nil {
		return request, err
	}
	if manifest.Schema != "nib-package-v0" || manifest.OrderID == "" || manifest.PackageID == "" {
		return request, fmt.Errorf("unsupported package manifest")
	}
	if len(manifest.Items) > 6 {
		return request, fmt.Errorf("too many runtime selections")
	}
	listed := map[string]bool{}
	for _, entry := range manifest.Entries {
		body, found := files[entry.Path]
		if !found || listed[entry.Path] || entry.Path == "manifest.json" || entry.Path == "checksums.sha256" {
			return request, fmt.Errorf("invalid manifest entry")
		}
		hash := sha256.Sum256(body)
		if hex.EncodeToString(hash[:]) != entry.SHA {
			return request, fmt.Errorf("manifest entry checksum/size mismatch")
		}
		listed[entry.Path] = true
	}
	for name := range files {
		if name != "manifest.json" && name != "checksums.sha256" && !listed[name] {
			return request, fmt.Errorf("unregistered archive entry")
		}
	}
	lines := strings.Split(strings.TrimSpace(string(files["checksums.sha256"])), "\n")
	checks := map[string]bool{}
	for _, line := range lines {
		parts := strings.SplitN(line, "  ", 2)
		if len(parts) != 2 || checks[parts[1]] {
			return request, fmt.Errorf("invalid checksum list")
		}
		body, found := files[parts[1]]
		if !found || parts[1] == "checksums.sha256" {
			return request, fmt.Errorf("invalid checksum target")
		}
		sum := sha256.Sum256(body)
		if hex.EncodeToString(sum[:]) != parts[0] {
			return request, fmt.Errorf("package entry checksum mismatch")
		}
		checks[parts[1]] = true
	}
	if len(checks) != len(files)-1 {
		return request, fmt.Errorf("incomplete checksum list")
	}
	var order struct {
		OrderID string        `json:"order_id"`
		Items   []packageItem `json:"items"`
	}
	if json.Unmarshal(files["order.json"], &order) != nil || order.OrderID != manifest.OrderID {
		return request, fmt.Errorf("order identity mismatch")
	}
	left, _ := json.Marshal(order.Items)
	right, _ := json.Marshal(manifest.Items)
	if !bytes.Equal(left, right) {
		return request, fmt.Errorf("order and manifest selections disagree")
	}
	request.OrderID = manifest.OrderID
	request.PackageID = manifest.PackageID
	request.PackageSHA256 = expected
	itemCategories := map[string]string{}
	modelItems := map[string]bool{}
	modelIDs := map[string]string{}
	dataItem := ""
	for _, item := range manifest.Items {
		if item.Version != "v1" || item.ID == "" || itemCategories[item.ID] != "" {
			return request, fmt.Errorf("unsupported product version or repeated item")
		}
		itemCategories[item.ID] = item.Category
		switch item.Product {
		case "sevir-vil":
			if item.Category != "data" || dataItem != "" {
				return request, fmt.Errorf("one SEVIR data product required")
			}
			dataItem = item.ID
			if len(item.Config) != 2 || json.Unmarshal(item.Config["event_id"], &request.EventID) != nil || json.Unmarshal(item.Config["context_start_time"], &request.StartTime) != nil {
				return request, fmt.Errorf("invalid SEVIR configuration")
			}
		case "nowcastnet-sevir", "exprecast-sevir", "wadepre-sevir":
			if item.Category != "model" || len(item.Config) != 0 {
				return request, fmt.Errorf("invalid model configuration")
			}
			model := strings.TrimSuffix(item.Product, "-sevir")
			for _, existing := range request.Models {
				if existing == model {
					return request, fmt.Errorf("duplicate model")
				}
			}
			request.Models = append(request.Models, model)
			modelItems[item.ID] = true
			modelIDs[item.ID] = model
		case "vil-verification":
			if item.Category != "evaluation" || request.Metrics != nil {
				return request, fmt.Errorf("invalid evaluation product")
			}
			groups, err := metricGroups(item.Config)
			if err != nil {
				return request, err
			}
			request.Metrics = groups
		case "vil-visualization":
			if item.Category != "visualization" || request.Visualization || len(item.Config) != 0 {
				return request, fmt.Errorf("invalid visualization configuration")
			}
			request.Visualization = true
		default:
			return request, fmt.Errorf("product is not supported by this local runtime")
		}
	}
	if dataItem == "" || len(request.Models) == 0 || !idPattern.MatchString(request.EventID) {
		return request, fmt.Errorf("data/sample/model selection is incomplete")
	}
	for _, item := range manifest.Items {
		if item.Category == "data" && len(item.Bindings) != 0 {
			return request, fmt.Errorf("data product has unexpected bindings")
		}
		for _, binding := range item.Bindings {
			if binding.Consumer != item.ID || binding.Compatibility != "compatible" || itemCategories[binding.Producer] == "" {
				return request, fmt.Errorf("unresolved or incompatible binding")
			}
		}
		if modelItems[item.ID] {
			if len(item.Bindings) != 1 || item.Bindings[0].Producer != dataItem || item.Bindings[0].Input != "observations" || item.Bindings[0].Output != "observations" {
				return request, fmt.Errorf("model is not bound to selected SEVIR observations")
			}
		}
		if item.Category == "evaluation" || item.Category == "visualization" {
			expected := 1
			if item.Category == "evaluation" {
				expected = 2
			}
			if len(item.Bindings) != expected {
				return request, fmt.Errorf("incomplete diagnostic bindings")
			}
			forecastModel := ""
			observation := false
			for _, binding := range item.Bindings {
				if binding.Input == "forecast" && binding.Output == "forecast" {
					forecastModel = modelIDs[binding.Producer]
				}
				if binding.Input == "observations" && binding.Output == "observations" && binding.Producer == dataItem {
					observation = true
				}
			}
			if forecastModel == "" || (item.Category == "evaluation" && !observation) {
				return request, fmt.Errorf("diagnostic is not bound to selected fields")
			}
			if item.Category == "evaluation" {
				request.VerificationModels = []string{forecastModel}
			} else {
				request.VisualizationModels = []string{forecastModel}
			}
		}
	}
	return request, nil
}
