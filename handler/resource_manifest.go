package handler

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

const resourceSchema = "nib.resource.v1"

var resourceMountNames = map[string]bool{
	"data": true, "catalog": true, "run": true, "inputs": true,
	"forecasts": true, "weights": true, "basemap": true,
}

type ResourceBuild struct {
	Context    string `yaml:"context"`
	Dockerfile string `yaml:"dockerfile"`
}

type ResourceMount struct {
	Name   string `yaml:"name"`
	Target string `yaml:"target"`
	Write  bool   `yaml:"write,omitempty"`
}

type ResourceContainer struct {
	Image      string          `yaml:"image"`
	Build      *ResourceBuild  `yaml:"build,omitempty"`
	Entrypoint []string        `yaml:"entrypoint,omitempty"`
	Command    []string        `yaml:"command,omitempty"`
	Mounts     []ResourceMount `yaml:"mounts"`
}

func (c ResourceContainer) mount(name string) (ResourceMount, bool) {
	for _, item := range c.Mounts {
		if item.Name == name {
			return item, true
		}
	}
	return ResourceMount{}, false
}

type dataPathsSpec struct {
	Path    string `yaml:"path"`
	Kind    string `yaml:"kind"`
	Catalog string `yaml:"catalog,omitempty"`
}

type VerificationSpec struct {
	LevelMin float64 `yaml:"level_min"`
	LevelMax float64 `yaml:"level_max"`
	Unit     string  `yaml:"unit,omitempty"`
}

type DataResource struct {
	Schema               string            `yaml:"schema"`
	Kind                 string            `yaml:"kind"`
	ID                   string            `yaml:"id"`
	ProductID            string            `yaml:"product_id"`
	Name                 string            `yaml:"name"`
	Data                 dataPathsSpec     `yaml:"data"`
	Verification         VerificationSpec  `yaml:"verification"`
	VisualizationBasemap bool              `yaml:"visualization_basemap"`
	Container            ResourceContainer `yaml:"container"`
}

type ModelWeights struct {
	File   string `yaml:"file"`
	SHA256 string `yaml:"sha256"`
}

type ModelResource struct {
	Schema     string            `yaml:"schema"`
	Kind       string            `yaml:"kind"`
	ID         string            `yaml:"id"`
	ProductIDs []string          `yaml:"product_ids"`
	Name       string            `yaml:"name"`
	Weights    ModelWeights      `yaml:"weights"`
	Container  ResourceContainer `yaml:"container"`
}

type DiagnosticsResource struct {
	Schema    string            `yaml:"schema"`
	Kind      string            `yaml:"kind"`
	Container ResourceContainer `yaml:"container"`
}

// Resources is the trusted, packaged definition of every supported data
// source and model. It is loaded once at server start; changing a manifest
// requires a restart.
type Resources struct {
	Root            string
	Data            map[string]*DataResource
	Models          map[string]map[string]*ModelResource
	DataByProduct   map[string]string
	ModelsByProduct map[string]map[string]string
	Diagnostics     *DiagnosticsResource
}

func LoadResources(root string) (*Resources, error) {
	if strings.TrimSpace(root) == "" {
		return nil, fmt.Errorf("paths.resources is required")
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve resources root: %w", err)
	}
	dataRoot := filepath.Join(absolute, "adapters")
	if _, err := os.Stat(dataRoot); err != nil {
		// A release bundle may keep resources/ beside config.yaml.
		if nested := filepath.Join(absolute, "resources", "adapters"); fileExists(nested) {
			absolute = filepath.Join(absolute, "resources")
			dataRoot = filepath.Join(absolute, "adapters")
		}
	}
	resources := &Resources{
		Root:            absolute,
		Data:            map[string]*DataResource{},
		Models:          map[string]map[string]*ModelResource{},
		DataByProduct:   map[string]string{},
		ModelsByProduct: map[string]map[string]string{},
	}
	entries, err := os.ReadDir(dataRoot)
	if err != nil {
		return nil, fmt.Errorf("read resources %s: %w", dataRoot, err)
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		manifest := filepath.Join(dataRoot, entry.Name(), "runtime.yaml")
		if _, err := os.Stat(manifest); err != nil {
			continue
		}
		data, err := readDataResource(manifest, entry.Name())
		if err != nil {
			return nil, err
		}
		if other, exists := resources.DataByProduct[data.ProductID]; exists {
			return nil, fmt.Errorf("%s: product_id %q is already declared by data %s", manifest, data.ProductID, other)
		}
		resources.Data[data.ID] = data
		resources.DataByProduct[data.ProductID] = data.ID
		models, err := readModelResources(filepath.Join(dataRoot, entry.Name()), data.ID)
		if err != nil {
			return nil, err
		}
		resources.Models[data.ID] = models
		if len(models) > 0 {
			resources.ModelsByProduct[data.ID] = map[string]string{}
		}
		for modelID, model := range models {
			for _, productID := range model.ProductIDs {
				if existing, exists := resources.ModelsByProduct[data.ID][productID]; exists {
					return nil, fmt.Errorf("%s: product_id %q is already declared by model %s", manifest, productID, existing)
				}
				resources.ModelsByProduct[data.ID][productID] = modelID
			}
		}
	}
	if len(resources.Data) == 0 {
		return nil, fmt.Errorf("no data runtime.yaml found under %s", dataRoot)
	}
	diagnosticsPath := filepath.Join(absolute, "diagnostics", "runtime.yaml")
	diagnostics, err := readDiagnosticsResource(diagnosticsPath)
	if err != nil {
		return nil, err
	}
	resources.Diagnostics = diagnostics
	return resources, nil
}

func readDataResource(path, directory string) (*DataResource, error) {
	var resource DataResource
	if err := decodeResource(path, &resource); err != nil {
		return nil, err
	}
	if resource.Kind != "data" {
		return nil, fmt.Errorf("%s: kind must be data", path)
	}
	if resource.ID != directory {
		return nil, fmt.Errorf("%s: id %q must match directory %q", path, resource.ID, directory)
	}
	if resource.ProductID == "" || resource.Name == "" {
		return nil, fmt.Errorf("%s: product_id and name are required", path)
	}
	if resource.Data.Kind != "file" && resource.Data.Kind != "directory" {
		return nil, fmt.Errorf("%s: data.kind must be file or directory", path)
	}
	if resource.Data.Path == "" {
		return nil, fmt.Errorf("%s: data.path is required", path)
	}
	if resource.Data.Kind == "directory" && resource.Data.Catalog != "" {
		return nil, fmt.Errorf("%s: data.catalog is only valid for file data", path)
	}
	if resource.Verification.LevelMax < resource.Verification.LevelMin {
		return nil, fmt.Errorf("%s: verification level range is invalid", path)
	}
	for label, value := range map[string]string{"data.path": resource.Data.Path, "data.catalog": resource.Data.Catalog} {
		if value == "" {
			continue
		}
		if filepath.IsAbs(value) || value == ".." || strings.HasPrefix(value, ".."+string(filepath.Separator)) {
			return nil, fmt.Errorf("%s: %s must stay relative to the data root", path, label)
		}
	}
	if err := validateContainer(path, resource.Container, "data"); err != nil {
		return nil, err
	}
	if _, ok := resource.Container.mount("data"); !ok {
		return nil, fmt.Errorf("%s: a data mount is required", path)
	}
	return &resource, nil
}

func readModelResources(dataDir, dataID string) (map[string]*ModelResource, error) {
	models := map[string]*ModelResource{}
	entries, err := os.ReadDir(dataDir)
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		path := filepath.Join(dataDir, entry.Name(), "runtime.yaml")
		if _, err := os.Stat(path); err != nil {
			continue
		}
		var resource ModelResource
		if err := decodeResource(path, &resource); err != nil {
			return nil, err
		}
		if resource.Kind != "model" {
			return nil, fmt.Errorf("%s: kind must be model", path)
		}
		if resource.ID != entry.Name() {
			return nil, fmt.Errorf("%s: id %q must match directory %q", path, resource.ID, entry.Name())
		}
		if len(resource.ProductIDs) == 0 || resource.Name == "" {
			return nil, fmt.Errorf("%s: product_ids and name are required", path)
		}
		if resource.Weights.File == "" || resource.Weights.SHA256 == "" {
			return nil, fmt.Errorf("%s: weights.file and weights.sha256 are required", path)
		}
		if filepath.IsAbs(resource.Weights.File) || strings.Contains(resource.Weights.File, string(filepath.Separator)) {
			return nil, fmt.Errorf("%s: weights.file must be one file name", path)
		}
		if err := validateContainer(path, resource.Container, "model"); err != nil {
			return nil, err
		}
		for _, name := range []string{"run", "inputs", "weights"} {
			if _, ok := resource.Container.mount(name); !ok {
				return nil, fmt.Errorf("%s: a %s mount is required", path, name)
			}
		}
		models[resource.ID] = &resource
	}
	return models, nil
}

func readDiagnosticsResource(path string) (*DiagnosticsResource, error) {
	var resource DiagnosticsResource
	if err := decodeResource(path, &resource); err != nil {
		return nil, err
	}
	if resource.Kind != "diagnostics" {
		return nil, fmt.Errorf("%s: kind must be diagnostics", path)
	}
	if err := validateContainer(path, resource.Container, "diagnostics"); err != nil {
		return nil, err
	}
	if _, ok := resource.Container.mount("run"); !ok {
		return nil, fmt.Errorf("%s: a run mount is required", path)
	}
	return &resource, nil
}

func validateContainer(path string, container ResourceContainer, kind string) error {
	if container.Image == "" {
		return fmt.Errorf("%s: container.image is required", path)
	}
	if container.Build != nil && (container.Build.Context == "" || container.Build.Dockerfile == "") {
		return fmt.Errorf("%s: container.build needs context and dockerfile", path)
	}
	seen := map[string]bool{}
	for _, mount := range container.Mounts {
		if !resourceMountNames[mount.Name] {
			return fmt.Errorf("%s: unknown mount %q", path, mount.Name)
		}
		if seen[mount.Name] {
			return fmt.Errorf("%s: repeated mount %q", path, mount.Name)
		}
		seen[mount.Name] = true
		if !strings.HasPrefix(mount.Target, "/") {
			return fmt.Errorf("%s: mount %q target must be an absolute container path", path, mount.Name)
		}
	}
	return nil
}

func decodeResource(path string, target any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read %s: %w", path, err)
	}
	decoder := yaml.NewDecoder(strings.NewReader(string(raw)))
	decoder.KnownFields(true)
	if err := decoder.Decode(target); err != nil {
		return fmt.Errorf("parse %s: %w", path, err)
	}
	var resource struct {
		Schema string `yaml:"schema"`
	}
	if err := yaml.Unmarshal(raw, &resource); err != nil {
		return fmt.Errorf("parse %s: %w", path, err)
	}
	if resource.Schema != resourceSchema {
		return fmt.Errorf("%s: schema must be %s", path, resourceSchema)
	}
	return nil
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func (r *Resources) DataByID(id string) (*DataResource, bool) {
	if r == nil {
		return nil, false
	}
	resource, ok := r.Data[id]
	return resource, ok
}

func (r *Resources) DataByProductID(productID string) (*DataResource, bool) {
	if r == nil {
		return nil, false
	}
	id, ok := r.DataByProduct[productID]
	if !ok {
		return nil, false
	}
	return r.DataByID(id)
}

func (r *Resources) ModelByID(dataID, modelID string) (*ModelResource, bool) {
	if r == nil {
		return nil, false
	}
	models, ok := r.Models[dataID]
	if !ok {
		return nil, false
	}
	resource, ok := models[modelID]
	return resource, ok
}

func (r *Resources) ModelByProductID(dataID, productID string) (*ModelResource, bool) {
	if r == nil {
		return nil, false
	}
	modelID, ok := r.ModelsByProduct[dataID][productID]
	if !ok {
		return nil, false
	}
	return r.ModelByID(dataID, modelID)
}

func (r *Resources) DataSources() []*DataResource {
	if r == nil {
		return nil
	}
	sources := make([]*DataResource, 0, len(r.Data))
	for _, resource := range r.Data {
		sources = append(sources, resource)
	}
	sort.Slice(sources, func(i, j int) bool { return sources[i].ID < sources[j].ID })
	return sources
}

func (r *Resources) ModelIDs(dataID string) []string {
	models := r.Models[dataID]
	ids := make([]string, 0, len(models))
	for id := range models {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}
