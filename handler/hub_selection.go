package handler

import (
	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler/internal/orders"

	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"regexp"

	"gopkg.in/yaml.v3"
)

type hubRef struct {
	Kind     string  `json:"kind" yaml:"kind"`
	ID       string  `json:"ref_id" yaml:"ref_id"`
	Revision string  `json:"revision" yaml:"revision"`
	SHA      string  `json:"sha256" yaml:"sha256"`
	Path     *string `json:"path" yaml:"path"`
}

func (a hubRef) matches(b hubRef) bool {
	return a.Kind == b.Kind && a.ID == b.ID && a.Revision == b.Revision && a.SHA == b.SHA
}

type hubItem struct {
	Product string `json:"product_id" yaml:"product_id"`
	Version string `json:"version_id" yaml:"version_id"`
	Config  hubRef `json:"configuration_schema_ref" yaml:"configuration_schema_ref"`
}
type hubSelection struct {
	Schema   string    `json:"schema_version" yaml:"schema_version"`
	Revision string    `json:"catalog_revision" yaml:"catalog_revision"`
	Items    []hubItem `json:"items" yaml:"items"`
}
type hubPort struct {
	Name        string
	Requirement string
	Contract    hubRef   `yaml:"contract_ref"`
	Constraints []hubRef `yaml:"constraint_refs"`
}
type hubVersion struct {
	hubItem         `yaml:",inline"`
	Inputs, Outputs []hubPort
}
type hubMapping struct {
	Product  string `json:"product_id"`
	Version  string `json:"version_id"`
	Category string `json:"category"`
	Data     string `json:"data_id"`
	Model    string `json:"model_id"`
}
type hubProduct struct {
	hubMapping
	version hubVersion
}

var hubDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)

func (s *Server) importSelection(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, orders.MaxBytes))
	if err != nil {
		var limit *http.MaxBytesError
		code := 400
		if errors.As(err, &limit) {
			code = 413
		}
		runtimeError(w, code, err)
		return
	}
	var sel hubSelection
	// JSON enforces wire types. KnownFields also rejects duplicate keys and case aliases.
	if err = json.Unmarshal(raw, &sel); err == nil {
		d := yaml.NewDecoder(bytes.NewReader(raw))
		d.KnownFields(true)
		var exact hubSelection
		err = d.Decode(&exact)
	}
	if err != nil {
		runtimeError(w, 400, fmt.Errorf("invalid Hub selection JSON: %w", err))
		return
	}
	if err = s.resourceFailure(); err != nil {
		runtimeError(w, 503, err)
		return
	}
	order, err := s.hubDraft(sel)
	if err != nil {
		runtimeError(w, 400, err)
		return
	}
	writeJSON(w, 200, map[string]any{"order": order})
}

func hubJSON(root, path string, dst any) error {
	raw, err := os.ReadFile(filepath.Join(root, path))
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, dst)
}

// All document paths passed here come from a packaged product, never from the request.
func (ref hubRef) document(root string, dst any) error {
	if ref.Path == nil || !filepath.IsLocal(filepath.FromSlash(*ref.Path)) {
		return fmt.Errorf("invalid pinned path for %s", ref.ID)
	}
	raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(*ref.Path)))
	if err != nil {
		return err
	}
	if fmt.Sprintf("%x", sha256.Sum256(raw)) != ref.SHA {
		return fmt.Errorf("pinned checksum mismatch for %s", ref.ID)
	}
	return json.Unmarshal(raw, dst)
}

func (s *Server) hubDraft(sel hubSelection) (Order, error) {
	var order Order
	root := filepath.Join(s.Resources.Root, "atlas")
	var schema struct {
		Properties map[string]struct{ Const string }
	}
	if err := hubJSON(root, "contracts/hub/selection-v1.schema.json", &schema); err != nil {
		return order, err
	}
	if sel.Schema == "" || sel.Schema != schema.Properties["schema_version"].Const {
		return order, fmt.Errorf("unsupported selection schema_version")
	}
	if !hubDigest.MatchString(sel.Revision) {
		return order, fmt.Errorf("invalid catalog_revision")
	}
	paths, err := filepath.Glob(filepath.Join(root, "runtime", "products-v*.json"))
	if err != nil {
		return order, err
	}
	mapped := map[[2]string]hubMapping{}
	for _, path := range paths {
		var index struct{ Products []hubMapping }
		if err := hubJSON(filepath.Dir(path), filepath.Base(path), &index); err != nil {
			return order, err
		}
		for _, m := range index.Products {
			mapped[[2]string{m.Product, m.Version}] = m
		}
	}
	var selected []hubProduct
	seen := map[string]bool{}
	order.SchemaVersion = orders.Schema
	order.SampleStarts = []string{}
	for _, item := range sel.Items {
		m, ok := mapped[[2]string{item.Product, item.Version}]
		if !ok {
			return order, fmt.Errorf("no local order mapping for %s@%s", item.Product, item.Version)
		}
		if seen[item.Product] {
			return order, fmt.Errorf("duplicate product %s", item.Product)
		}
		seen[item.Product] = true
		var catalog struct {
			Product  string `yaml:"product_id"`
			Category string
			Versions []hubVersion
		}
		raw, err := os.ReadFile(filepath.Join(root, "catalog", "products", m.Product+".yaml"))
		if err != nil {
			return order, err
		}
		if err = yaml.Unmarshal(raw, &catalog); err != nil {
			return order, err
		}
		var v *hubVersion
		for i := range catalog.Versions {
			entry := &catalog.Versions[i]
			if entry.Product == m.Product && entry.Version == m.Version {
				v = entry
				break
			}
		}
		if catalog.Product != m.Product || catalog.Category != m.Category || v == nil || !item.Config.matches(v.Config) || v.Config.Kind != "configuration_schema" || (item.Config.Path != nil && (v.Config.Path == nil || *item.Config.Path != *v.Config.Path)) {
			return order, fmt.Errorf("invalid version or configuration reference for %s", m.Product)
		}
		var config json.RawMessage
		if err = v.Config.document(root, &config); err != nil {
			return order, err
		}
		selected = append(selected, hubProduct{m, *v})
		switch m.Category {
		case "data":
			if order.DataID != "" || s.Resources.Data[m.Data] == nil {
				return order, fmt.Errorf("select exactly one packaged dataset")
			}
			order.DataID = m.Data
		case "model":
			if _, ok := s.Resources.ModelByID(m.Data, m.Model); !ok {
				return order, fmt.Errorf("model %s is not packaged", m.Model)
			}
			order.Models = append(order.Models, m.Model)
		case "evaluation":
			if order.Verification != nil {
				return order, fmt.Errorf("cannot map multiple evaluations")
			}
			var defaults struct {
				Properties struct {
					Methods     struct{ Default []string }
					Levels      struct{ Default []float64 }
					HalfWindows struct{ Default []int } `json:"half_windows"`
				}
			}
			if err := json.Unmarshal(config, &defaults); err != nil {
				return order, err
			}
			p := defaults.Properties
			if len(p.Methods.Default) == 0 {
				return order, fmt.Errorf("no local verification methods for %s", m.Product)
			}
			order.Verification = &OrderVerification{Methods: p.Methods.Default, Levels: p.Levels.Default, HalfWindows: p.HalfWindows.Default}
		case "visualization":
			if order.Visualization != nil {
				return order, fmt.Errorf("cannot map multiple visualizations")
			}
			order.Visualization = &OrderVisualization{}
		default:
			return order, fmt.Errorf("no local order mapping for %s", m.Category)
		}
	}
	if order.DataID == "" || len(order.Models) == 0 {
		return order, fmt.Errorf("select one dataset and at least one model")
	}
	for _, consumer := range selected {
		if consumer.Data != order.DataID {
			return order, fmt.Errorf("%s is not for dataset %s", consumer.Product, order.DataID)
		}
		for _, in := range consumer.version.Inputs {
			if in.Requirement != "required" {
				continue
			}
			ok := false
			for _, producer := range selected {
				if producer.Category != "data" && (consumer.Category == "model" || producer.Category != "model") {
					continue
				}
				for _, out := range producer.version.Outputs {
					match, err := hubPortsMatch(root, in, out)
					if err != nil {
						return order, err
					}
					if match {
						ok = true
						break
					}
				}
				if ok {
					break
				}
			}
			if !ok {
				return order, fmt.Errorf("%s@%s: no compatible output for %s", consumer.Product, consumer.Version, in.Name)
			}
		}
	}
	if order.Verification != nil {
		order.Verification.Models = order.Models
	}
	if order.Visualization != nil {
		order.Visualization.Models = order.Models
	}
	return order, nil
}

func hubPortsMatch(root string, in, out hubPort) (bool, error) {
	accepts := in.Contract.matches(out.Contract)
	load := func(refs []hubRef, consumer bool) (map[string]any, error) {
		fields := map[string]any{}
		for _, ref := range refs {
			var doc map[string]any
			if err := ref.document(root, &doc); err != nil {
				return nil, err
			}
			for k, v := range doc {
				if _, ok := fields[k]; !ok {
					fields[k] = v
				}
			}
			if consumer {
				alts, _ := doc["accepted_contracts"].(map[string]any)
				entries, _ := alts[in.Name].([]any)
				for _, entry := range entries {
					m, _ := entry.(map[string]any)
					if m["ref_id"] == out.Contract.ID && m["revision"] == out.Contract.Revision && m["sha256"] == out.Contract.SHA {
						accepts = true
					}
				}
			}
		}
		return fields, nil
	}
	consumer, err := load(in.Constraints, true)
	if err != nil || !accepts {
		return false, err
	}
	producer, err := load(append(append([]hubRef{}, out.Constraints...), out.Contract), false)
	if err != nil {
		return false, err
	}
	fields, _ := consumer["requires_matching"].([]any)
	for _, f := range fields {
		k, ok := f.(string)
		if ok && consumer[k] != nil && !reflect.DeepEqual(consumer[k], producer[k]) {
			return false, nil
		}
	}
	return true, nil
}
