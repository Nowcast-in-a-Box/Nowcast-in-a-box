package handler

import (
	"fmt"
	"path/filepath"
)

func (s *Server) dataID() string {
	if s.Config.Runtime.Dataset == "" {
		return "sevir"
	}
	return s.Config.Runtime.Dataset
}

func (s *Server) modelImage(model string) string {
	if s.dataID() == "cma_radar" {
		if model != "nowcastnet" {
			return ""
		}
		return s.Config.Runtime.ModelImages["cma_radar/"+model]
	}
	return s.Config.Runtime.ModelImages[model]
}

func (s *Server) weightPath(model string) string {
	return filepath.Join(s.Config.Paths.Artifacts, s.dataID(), model, modelWeightName(model))
}

// Only controlled host configuration determines mounts and container arguments.
func (s *Server) ingestionArgs(data, catalog string, extra ...string) []string {
	if s.dataID() == "cma_radar" {
		args := []string{"-v", volumeSpec(data, "/inputs/data", true), s.Config.Runtime.IngestionImage, "--dataset", "cma_radar", "--data", "/inputs/data"}
		return append(args, extra...)
	}
	args := []string{"-v", volumeSpec(data, "/inputs/data.h5", true), "-v", volumeSpec(catalog, "/inputs/CATALOG.csv", true), s.Config.Runtime.IngestionImage, "--data", "/inputs/data.h5", "--catalog", "/inputs/CATALOG.csv"}
	return append(args, extra...)
}

func (s *Server) requireDataset(id string) error {
	if id != s.dataID() {
		return fmt.Errorf("selected dataset %s differs from configured dataset %s; choose its data directory and handler configuration", id, s.dataID())
	}
	return nil
}
