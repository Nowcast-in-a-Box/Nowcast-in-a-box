package handler

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// localSettings is the small state the Hub saves on this computer. It lives
// next to config.yaml so the download folder stays self-contained and never
// needs a privileged per-user directory. The file is gitignored.
type localSettings struct {
	DataRoot string `json:"data_root,omitempty"`
}

func settingsPath(cfg Config) (string, error) {
	dir := ""
	if cfg.Source != "" {
		dir = filepath.Dir(cfg.Source)
	} else {
		executable, err := os.Executable()
		if err != nil {
			return "", fmt.Errorf("locate the Handler program directory: %w", err)
		}
		dir = filepath.Dir(executable)
	}
	return filepath.Join(dir, settingsFileName), nil
}

func readSettings(cfg Config) (localSettings, error) {
	var settings localSettings
	path, err := settingsPath(cfg)
	if err != nil {
		return settings, err
	}
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return settings, nil
	}
	if err != nil {
		return settings, fmt.Errorf("read %s: %w", settingsFileName, err)
	}
	if err := json.Unmarshal(raw, &settings); err != nil {
		return settings, fmt.Errorf("%s is not valid JSON: %w; remove it or restore a data_root", settingsFileName, err)
	}
	return settings, nil
}

func writeSettings(cfg Config, settings localSettings) error {
	path, err := settingsPath(cfg)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".nib-settings-*")
	if err != nil {
		return err
	}
	name := file.Name()
	defer os.Remove(name)
	if err := file.Chmod(0o600); err != nil {
		file.Close()
		return err
	}
	if _, err := file.Write(append(raw, '\n')); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(name, path)
}

// applySettings merges the saved local settings over config.yaml. A saved
// data root is relative to the settings file when it is not absolute.
func (c *Config) applySettings() error {
	settings, err := readSettings(*c)
	if err != nil {
		return err
	}
	if settings.DataRoot == "" {
		return nil
	}
	if !filepath.IsAbs(settings.DataRoot) {
		path, err := settingsPath(*c)
		if err != nil {
			return err
		}
		settings.DataRoot = filepath.Join(filepath.Dir(path), settings.DataRoot)
	}
	c.Paths.DataRoot = filepath.Clean(settings.DataRoot)
	return nil
}

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
	if err != nil || strings.TrimSpace(string(raw)) != "" {
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
	// Keep every saved field: only data_root is browser-editable today.
	settings, err := readSettings(s.Config)
	if err != nil {
		runtimeError(w, 500, err)
		return
	}
	settings.DataRoot = root
	if err := writeSettings(s.Config, settings); err != nil {
		runtimeError(w, 500, fmt.Errorf("save %s next to config.yaml: %w", settingsFileName, err))
		return
	}
	s.Config.Paths.DataRoot = root
	writeJSON(w, 200, map[string]string{"data_root": root})
}
