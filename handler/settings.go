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
	// DataRoot is the pre-multi-dataset shape. It becomes a fallback for any
	// data source without an explicit entry; the next save writes DataRoots.
	DataRoot      string            `json:"data_root,omitempty"`
	DataRoots     map[string]string `json:"data_roots,omitempty"`
	ImageSource   string            `json:"image_source,omitempty"`
	ImageRegistry *string           `json:"image_registry,omitempty"`
	ImageMirror   *string           `json:"image_mirror,omitempty"`
}

// localStateDir is where this install keeps the small files next to config.yaml.
func localStateDir(cfg Config) (string, error) {
	if cfg.Source != "" {
		return filepath.Dir(cfg.Source), nil
	}
	executable, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("locate the Handler program directory: %w", err)
	}
	return filepath.Dir(executable), nil
}

func statePath(cfg Config, name string) (string, error) {
	dir, err := localStateDir(cfg)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, name), nil
}

func settingsPath(cfg Config) (string, error) {
	return statePath(cfg, settingsFileName)
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
		return settings, fmt.Errorf("%s is not valid JSON: %w; remove it or restore a data_roots entry", settingsFileName, err)
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

// applySettings merges saved local settings over config.yaml. Relative roots
// are resolved against the settings file.
func (c *Config) applySettings() error {
	settings, err := readSettings(*c)
	if err != nil {
		return err
	}
	path, err := settingsPath(*c)
	if err != nil {
		return err
	}
	resolve := func(value string) string {
		if value == "" || filepath.IsAbs(value) {
			return filepath.Clean(value)
		}
		return filepath.Join(filepath.Dir(path), value)
	}
	if len(settings.DataRoots) > 0 {
		c.DataRoots = map[string]string{}
		for id, value := range settings.DataRoots {
			c.DataRoots[id] = resolve(value)
		}
	}
	if settings.DataRoot != "" {
		c.DataRootFallback = resolve(settings.DataRoot)
	}
	source, err := normalizeImageSource(c.Runtime.ImageSource)
	if err != nil {
		return err
	}
	if settings.ImageSource != "" {
		source, err = normalizeImageSource(settings.ImageSource)
		if err != nil {
			return fmt.Errorf("nib-settings.json image_source: %w", err)
		}
	}
	c.Runtime.ImageSource = source
	for _, setting := range []struct {
		saved  *string
		target *string
	}{
		{settings.ImageRegistry, &c.Runtime.ImageRegistry},
		{settings.ImageMirror, &c.Runtime.ImageMirror},
	} {
		if setting.saved != nil {
			*setting.target = *setting.saved
		}
		value, err := normalizeImagePrefix(*setting.target)
		if err != nil {
			return err
		}
		*setting.target = value
	}
	return nil
}

func (s *Server) dataRoot(id string) string {
	if root := s.currentConfig().DataRoots[id]; root != "" {
		return root
	}
	return s.currentConfig().DataRootFallback
}

func (s *Server) effectiveDataRoots() map[string]string {
	roots := map[string]string{}
	if resources := s.resources(); resources != nil {
		for _, data := range resources.DataSources() {
			if root := s.dataRoot(data.ID); root != "" {
				roots[data.ID] = root
			}
		}
		return roots
	}
	for id, root := range s.currentConfig().DataRoots {
		roots[id] = root
	}
	if s.currentConfig().DataRootFallback != "" && len(roots) == 0 {
		// Without resources the fallback still applies to the single legacy
		// data source shape.
		roots[""] = s.currentConfig().DataRootFallback
	}
	return roots
}

func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		config := s.currentConfig()
		writeJSON(w, 200, map[string]any{
			"data_roots":     s.effectiveDataRoots(),
			"image_source":   config.Runtime.ImageSource,
			"image_registry": config.Runtime.ImageRegistry,
			"image_mirror":   config.Runtime.ImageMirror,
			"image_sources":  []string{"official", "mirror", "auto"},
		})
		return
	}
	var update settingsUpdate
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&update); err != nil {
		runtimeError(w, 400, err)
		return
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		runtimeError(w, 400, fmt.Errorf("one request object required"))
		return
	}
	s.saveSettings(w, update)
}

func (s *Server) handlePickDataRoot(w http.ResponseWriter, r *http.Request) {
	var update struct {
		DataID string `json:"data_id"`
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 4096))
	if err != nil {
		runtimeError(w, 400, err)
		return
	}
	if text := strings.TrimSpace(string(raw)); text != "" {
		decoder := json.NewDecoder(strings.NewReader(text))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&update); err != nil {
			runtimeError(w, 400, err)
			return
		}
		if decoder.Decode(&struct{}{}) != io.EOF {
			runtimeError(w, 400, fmt.Errorf("one request object required"))
			return
		}
	}
	if update.DataID == "" {
		update.DataID = r.URL.Query().Get("data_id")
	}
	if update.DataID == "" {
		runtimeError(w, 400, fmt.Errorf("data_id is required"))
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
	s.saveDataRoot(w, update.DataID, chosen)
}

type settingsUpdate struct {
	DataID        string  `json:"data_id"`
	DataRoot      string  `json:"data_root"`
	ImageSource   *string `json:"image_source"`
	ImageRegistry *string `json:"image_registry"`
	ImageMirror   *string `json:"image_mirror"`
}

func (s *Server) saveImageSource(w http.ResponseWriter, requested string) {
	s.saveSettings(w, settingsUpdate{ImageSource: &requested})
}

func (s *Server) saveDataRoot(w http.ResponseWriter, dataID, requested string) {
	s.saveSettings(w, settingsUpdate{DataID: dataID, DataRoot: requested})
}

// Lock order is recovery, registry (briefly), images, jobs, resources, config.
// Never wait for an active operation while holding a settings lock.
func (s *Server) lockSettings() (func(), error) {
	busy := fmt.Errorf("wait for active tasks or resource preparation before changing settings")
	if !s.recoveryMu.TryLock() {
		return nil, busy
	}
	if !s.executionMu.TryLock() {
		s.recoveryMu.Unlock()
		return nil, busy
	}
	active := len(s.executions) > 0
	s.executionMu.Unlock()
	if active || !s.imageMu.TryLock() {
		s.recoveryMu.Unlock()
		return nil, busy
	}
	s.jobsMu.Lock()
	s.resourcesMu.Lock()
	unlock := func() { s.resourcesMu.Unlock(); s.jobsMu.Unlock(); s.imageMu.Unlock(); s.recoveryMu.Unlock() }
	if s.stopping || s.preparingResources {
		unlock()
		return nil, busy
	}
	for _, job := range s.jobs {
		if job.Status == "running" || job.Status == "queued" {
			unlock()
			return nil, busy
		}
	}
	return unlock, nil
}

func (s *Server) saveSettings(w http.ResponseWriter, update settingsUpdate) {
	hasRoot := update.DataID != "" || update.DataRoot != ""
	if !hasRoot && update.ImageSource == nil && update.ImageRegistry == nil && update.ImageMirror == nil {
		runtimeError(w, 400, fmt.Errorf("data_id and data_root, or image settings, are required"))
		return
	}
	if update.ImageSource != nil {
		value, err := normalizeImageSource(*update.ImageSource)
		if err != nil {
			runtimeError(w, 422, err)
			return
		}
		update.ImageSource = &value
	}
	for _, target := range []**string{&update.ImageRegistry, &update.ImageMirror} {
		if *target == nil {
			continue
		}
		value, err := normalizeImagePrefix(**target)
		if err != nil {
			runtimeError(w, 422, err)
			return
		}
		*target = &value
	}
	if hasRoot {
		if update.DataID == "" || update.DataRoot == "" {
			runtimeError(w, 400, fmt.Errorf("data_id and data_root are required"))
			return
		}
		if os.Getenv("NIB_DATA_ROOT") != "" {
			runtimeError(w, 409, fmt.Errorf("NIB_DATA_ROOT overrides this setting; unset it before changing config"))
			return
		}
		if _, err := s.dataResource(update.DataID); err != nil {
			runtimeError(w, 422, err)
			return
		}
		root, err := filepath.Abs(update.DataRoot)
		if err != nil {
			runtimeError(w, 422, err)
			return
		}
		info, err := os.Stat(root)
		if err != nil || !info.IsDir() {
			runtimeError(w, 422, fmt.Errorf("data root must be an existing local directory"))
			return
		}
		update.DataRoot = root
	}
	unlock, err := s.lockSettings()
	if err != nil {
		runtimeError(w, 409, err)
		return
	}
	defer unlock()
	s.configMu.Lock()
	defer s.configMu.Unlock()
	settings, err := readSettings(s.Config)
	if err != nil {
		runtimeError(w, 500, err)
		return
	}
	cfg := s.Config
	cfg.DataRoots = make(map[string]string, len(s.Config.DataRoots)+1)
	for id, root := range s.Config.DataRoots {
		cfg.DataRoots[id] = root
	}
	cfg.Runtime.AllowedOrigins = append([]string(nil), cfg.Runtime.AllowedOrigins...)
	if hasRoot {
		cfg.DataRoots[update.DataID] = update.DataRoot
		settings.DataRoots = cfg.DataRoots
		settings.DataRoot = ""
	}
	if update.ImageSource != nil {
		settings.ImageSource = *update.ImageSource
		cfg.Runtime.ImageSource = *update.ImageSource
	}
	if update.ImageRegistry != nil {
		settings.ImageRegistry = update.ImageRegistry
		cfg.Runtime.ImageRegistry = *update.ImageRegistry
	}
	if update.ImageMirror != nil {
		settings.ImageMirror = update.ImageMirror
		cfg.Runtime.ImageMirror = *update.ImageMirror
	}
	if err := writeSettings(cfg, settings); err != nil {
		runtimeError(w, 500, fmt.Errorf("save %s next to config.yaml: %w", settingsFileName, err))
		return
	}
	// Keep immutable fields untouched while requests read them without configMu.
	s.Config.DataRoots = cfg.DataRoots
	s.Config.Runtime.ImageSource = cfg.Runtime.ImageSource
	s.Config.Runtime.ImageRegistry = cfg.Runtime.ImageRegistry
	s.Config.Runtime.ImageMirror = cfg.Runtime.ImageMirror
	s.resourcesLoaded = false
	writeJSON(w, 200, map[string]any{"data_roots": cfg.DataRoots, "image_source": cfg.Runtime.ImageSource, "image_registry": cfg.Runtime.ImageRegistry, "image_mirror": cfg.Runtime.ImageMirror, "image_sources": []string{"official", "mirror", "auto"}})
}
