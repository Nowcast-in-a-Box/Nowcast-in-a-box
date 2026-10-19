package handler

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func (s *Server) dataResource(id string) (*DataResource, error) {
	if err := s.resourceFailure(); err != nil {
		return nil, err
	}
	if id == "" {
		return nil, fmt.Errorf("a data source is required")
	}
	resource, ok := s.Resources.DataByID(id)
	if !ok {
		return nil, fmt.Errorf("this Handler does not package data source %q; update its resources", id)
	}
	return resource, nil
}

func (s *Server) modelResource(dataID, modelID string) (*ModelResource, error) {
	if err := s.resourceFailure(); err != nil {
		return nil, err
	}
	resource, ok := s.Resources.ModelByID(dataID, modelID)
	if !ok {
		return nil, fmt.Errorf("this Handler does not package model %q for data %q", modelID, dataID)
	}
	return resource, nil
}

func (s *Server) weightPath(dataID, modelID string) (string, error) {
	resource, err := s.modelResource(dataID, modelID)
	if err != nil {
		return "", err
	}
	return filepath.Join(s.Config.Paths.Artifacts, dataID, modelID, resource.Weights.File), nil
}

func (s *Server) weightSourcePath(dataID, modelID string) (string, error) {
	resource, err := s.modelResource(dataID, modelID)
	if err != nil {
		return "", err
	}
	return filepath.Join(s.Config.Paths.WeightSource, dataID, modelID, resource.Weights.File), nil
}

func (s *Server) dataPaths(dataID string) (string, string, error) {
	resource, err := s.dataResource(dataID)
	if err != nil {
		return "", "", err
	}
	root := s.dataRoot(dataID)
	if strings.TrimSpace(root) == "" {
		return "", "", fmt.Errorf("set the local data folder for %s before execution", resource.Name)
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return "", "", fmt.Errorf("the local data folder for %s is unavailable; choose it again", resource.Name)
	}
	resolve := func(relative, label string) (string, error) {
		if strings.TrimSpace(relative) == "" {
			return "", fmt.Errorf("%s declares no %s path", resource.ID, label)
		}
		if filepath.IsAbs(relative) {
			return "", fmt.Errorf("%s %s must be relative to the selected data folder", resource.ID, label)
		}
		path, err := ResolveDataDir(root, relative)
		if err != nil {
			return "", err
		}
		real, err := filepath.EvalSymlinks(path)
		if err != nil {
			return "", fmt.Errorf("%s %s is missing under the selected data folder", resource.Name, label)
		}
		realRoot, err := filepath.EvalSymlinks(root)
		if err != nil {
			return "", err
		}
		rel, err := filepath.Rel(realRoot, real)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return "", fmt.Errorf("%s %s escapes the data root", resource.Name, label)
		}
		return real, nil
	}
	data, err := resolve(resource.Data.Path, "data file")
	if err != nil {
		return "", "", err
	}
	info, err = os.Stat(data)
	if err != nil {
		return "", "", fmt.Errorf("%s data path is unavailable", resource.Name)
	}
	if resource.Data.Kind == "directory" {
		if !info.IsDir() {
			return "", "", fmt.Errorf("%s data path must be a directory", resource.Name)
		}
		return data, "", nil
	}
	if !info.Mode().IsRegular() {
		return "", "", fmt.Errorf("%s data path must be a regular file", resource.Name)
	}
	catalog := ""
	if resource.Data.Catalog != "" {
		catalog, err = resolve(resource.Data.Catalog, "catalog file")
		if err != nil {
			return "", "", err
		}
		catalogInfo, statErr := os.Stat(catalog)
		if statErr != nil || !catalogInfo.Mode().IsRegular() {
			return "", "", fmt.Errorf("%s catalog file is unavailable", resource.Name)
		}
	}
	return data, catalog, nil
}

func (s *Server) requireDataSupported(dataID string) error {
	_, err := s.dataResource(dataID)
	return err
}

func (s *Server) runContainerArgs(dataID, modelID, directory, data, catalog string, container ResourceContainer, extra ...string) ([]string, error) {
	args := []string{}
	for _, mount := range container.Mounts {
		host, err := s.mountHost(mount.Name, dataID, modelID, directory, data, catalog)
		if err != nil {
			return nil, err
		}
		args = append(args, "-v", volumeSpec(host, mount.Target, !mount.Write))
	}
	args = append(args, container.Image)
	command := container.Entrypoint
	if len(command) == 0 {
		command = container.Command
	}
	args = append(args, command...)
	args = append(args, extra...)
	return args, nil
}

func (s *Server) mountHost(name, dataID, modelID, directory, data, catalog string) (string, error) {
	switch name {
	case "data":
		return data, nil
	case "catalog":
		if catalog == "" {
			return "", fmt.Errorf("data source has no catalog file to mount")
		}
		return catalog, nil
	case "run":
		return directory, nil
	case "inputs":
		return filepath.Join(directory, "inputs"), nil
	case "forecasts":
		return filepath.Join(directory, "forecasts"), nil
	case "weights":
		return s.weightPath(dataID, modelID)
	case "basemap":
		return filepath.Join(s.Config.Paths.Artifacts, "basemap", "wmo"), nil
	}
	return "", fmt.Errorf("unknown runtime mount %q", name)
}

func (s *Server) dataCommandArgs(dataID, data, catalog string, extra ...string) ([]string, error) {
	resource, err := s.dataResource(dataID)
	if err != nil {
		return nil, err
	}
	return s.runContainerArgs(dataID, "", "", data, catalog, resource.Container, extra...)
}

func (s *Server) modelCommandArgs(dataID, modelID, directory string) ([]string, error) {
	resource, err := s.modelResource(dataID, modelID)
	if err != nil {
		return nil, err
	}
	return s.runContainerArgs(dataID, modelID, directory, "", "", resource.Container)
}

func (s *Server) basemapReady() bool {
	info, err := os.Stat(filepath.Join(s.Config.Paths.Artifacts, "basemap", "wmo", "acquisition.json"))
	return err == nil && info.Mode().IsRegular()
}
