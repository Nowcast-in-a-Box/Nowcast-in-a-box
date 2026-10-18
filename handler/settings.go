package handler

import (
	"encoding/json"
	"fmt"
	"gopkg.in/yaml.v3"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		writeJSON(w, 200, map[string]any{"data_root": s.currentConfig().Paths.DataRoot})
		return
	}
	var update struct {
		DataRoot string `json:"data_root"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&update); err != nil {
		runtimeError(w, 400, err)
		return
	}
	if decoder.Decode(&struct{}{}) != io.EOF || update.DataRoot == "" {
		runtimeError(w, 400, fmt.Errorf("one data_root setting is required"))
		return
	}
	s.saveDataRoot(w, update.DataRoot)
}

func (s *Server) handlePickDataRoot(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 8))
	if err != nil || len(strings.TrimSpace(string(raw))) != 0 {
		runtimeError(w, 400, fmt.Errorf("folder selection does not accept a path"))
		return
	}
	pick := s.PickFolder
	if pick == nil {
		pick = nativePickFolder
	}
	chosen, err := pick()
	if err != nil {
		runtimeError(w, 409, err)
		return
	}
	s.saveDataRoot(w, chosen)
}

func (s *Server) saveDataRoot(w http.ResponseWriter, requested string) {
	if os.Getenv("NIB_DATA_ROOT") != "" {
		runtimeError(w, 409, fmt.Errorf("NIB_DATA_ROOT overrides this setting; unset it before changing config"))
		return
	}
	root, err := filepath.Abs(requested)
	if err != nil {
		runtimeError(w, 400, err)
		return
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		runtimeError(w, 422, fmt.Errorf("data root must be an existing local directory"))
		return
	}
	s.jobsMu.Lock()
	defer s.jobsMu.Unlock()
	for _, job := range s.jobs {
		if job.Status == "running" || job.Status == "queued" {
			runtimeError(w, 409, fmt.Errorf("wait for active jobs before changing data root"))
			return
		}
	}
	s.configMu.Lock()
	defer s.configMu.Unlock()
	if s.Config.Source == "" {
		runtimeError(w, 409, fmt.Errorf("start the handler with a config file to save settings"))
		return
	}
	raw, err := os.ReadFile(s.Config.Source)
	if err != nil {
		runtimeError(w, 500, err)
		return
	}
	var document yaml.Node
	if yaml.Unmarshal(raw, &document) != nil || len(document.Content) != 1 || document.Content[0].Kind != yaml.MappingNode {
		runtimeError(w, 500, fmt.Errorf("invalid local config document"))
		return
	}
	paths := yamlMapping(document.Content[0], "paths")
	if paths.Kind != yaml.MappingNode {
		runtimeError(w, 500, fmt.Errorf("config paths must be a mapping"))
		return
	}
	var target *yaml.Node
	for i := 0; i < len(paths.Content); i += 2 {
		if paths.Content[i].Value == "data_root" {
			target = paths.Content[i+1]
		}
	}
	if target == nil {
		target = &yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str"}
		paths.Content = append(paths.Content, &yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Value: "data_root"}, target)
	}
	target.Kind = yaml.ScalarNode
	target.Tag = "!!str"
	target.Value = root
	body, err := yaml.Marshal(&document)
	if err != nil {
		runtimeError(w, 500, err)
		return
	}
	temporary := s.Config.Source + ".tmp"
	if err := os.WriteFile(temporary, body, 0o600); err != nil {
		runtimeError(w, 500, err)
		return
	}
	if err := os.Rename(temporary, s.Config.Source); err != nil {
		runtimeError(w, 500, err)
		return
	}
	s.Config.Paths.DataRoot = root
	writeJSON(w, 200, map[string]string{"data_root": root})
}

func yamlMapping(parent *yaml.Node, key string) *yaml.Node {
	for i := 0; i < len(parent.Content); i += 2 {
		if parent.Content[i].Value == key {
			return parent.Content[i+1]
		}
	}
	node := &yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
	parent.Content = append(parent.Content, &yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Value: key}, node)
	return node
}
