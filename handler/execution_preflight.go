package handler

import (
	"context"
	"fmt"
	"strings"
)

// ExecutionContext records local resolution, not a guarantee of immutable data bytes.
type ExecutionContext struct {
	DataPath    string            `json:"data_path"`
	CatalogPath string            `json:"catalog_path,omitempty"`
	Images      map[string]string `json:"images"`
	Weights     map[string]string `json:"weight_sha256"`
}

// executionPreflight is shared by explicit checks and every admitted inference.
// The caller holds executionMu so settings cannot change underneath preparation.
func (s *Server) executionPreflight(ctx context.Context, request RuntimeRequest, progress func(string, []Check)) ([]Check, ExecutionContext, error) {
	checks := []Check{}
	execution := ExecutionContext{Images: map[string]string{}, Weights: map[string]string{}}
	stage := func(id string) {
		if progress != nil {
			progress(id, checks)
		}
	}
	add := func(check Check) error {
		checks = append(checks, check)
		if progress != nil {
			progress(check.ID, checks)
		}
		if !check.OK {
			return fmt.Errorf("%s: %s", check.ID, check.Message)
		}
		return nil
	}
	fail := func(id string, err error) ([]Check, ExecutionContext, error) {
		_ = add(Check{ID: id, Message: err.Error()})
		return checks, execution, err
	}
	stage("docker")
	if err := s.requireDocker(); err != nil {
		return fail("docker", err)
	}
	_ = add(Check{ID: "docker", OK: true, Message: "Docker daemon is reachable"})
	dataResource, err := s.dataResource(request.DataID)
	if err != nil {
		return fail("data", err)
	}
	data, catalog, err := s.dataPaths(request.DataID)
	if err != nil {
		return fail("data", err)
	}
	execution.DataPath, execution.CatalogPath = data, catalog
	_ = add(Check{ID: "data", OK: true, Message: "selected local data files are present; windows are checked separately"})
	if len(request.Models) == 0 {
		return fail("weights", fmt.Errorf("select a model before checking weights"))
	}
	images := []struct{ id, image string }{{"image:data", dataResource.Container.Image}}
	if s.Resources.Diagnostics != nil {
		images = append(images, struct{ id, image string }{"image:diagnostics", s.Resources.Diagnostics.Container.Image})
	}
	for _, modelID := range request.Models {
		model, err := s.modelResource(request.DataID, modelID)
		if err != nil {
			return fail("image:model:"+modelID, err)
		}
		images = append(images, struct{ id, image string }{"image:model:" + modelID, model.Container.Image})
		execution.Weights[modelID] = model.Weights.SHA256
	}
	for _, image := range images {
		stage(image.id)
		execution.Images[image.id] = image.image
		if err := add(s.imageCheck(ctx, image.image, image.id, image.id)); err != nil {
			return checks, execution, err
		}
	}
	starts := request.SampleStarts
	if len(starts) == 0 {
		starts = []string{request.StartTime}
	}
	var region *OrderRegion
	if request.CenterLon != 0 || request.CenterLat != 0 {
		region = &OrderRegion{CenterLon: &request.CenterLon, CenterLat: &request.CenterLat}
	}
	for index, start := range starts {
		id := "window"
		if len(starts) > 1 {
			id = fmt.Sprintf("window:%d", index+1)
		}
		eventID := request.EventID
		if request.DataID == "cma_radar" {
			eventID = strings.ReplaceAll(start[:min(10, len(start))], "-", "")
		}
		stage(id)
		if err := add(s.windowCheck(ctx, request.DataID, data, catalog, eventID, start, id, region)); err != nil {
			return checks, execution, err
		}
	}
	for _, modelID := range request.Models {
		id := "weight:" + modelID
		stage(id)
		if err := s.prepareModelWeight(ctx, request.DataID, modelID); err != nil {
			return fail(id, err)
		}
		_ = add(Check{ID: id, OK: true, Message: "weight verified against the pinned SHA-256"})
	}
	return checks, execution, nil
}
