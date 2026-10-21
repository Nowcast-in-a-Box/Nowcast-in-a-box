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
	DataRoot    string            `json:"data_root,omitempty"`
	DataRoots   map[string]string `json:"data_roots,omitempty"`
	ImageSource string            `json:"image_source,omitempty"`
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
			"data_roots":    s.effectiveDataRoots(),
			"image_source":  config.Runtime.ImageSource,
			"image_mirror":  config.Runtime.ImageMirror,
			"image_sources": []string{"official", "mirror", "auto"},
		})
		return
	}
	var update struct {
		DataID      string `json:"data_id"`
		DataRoot    string `json:"data_root"`
		ImageSource string `json:"image_source"`
	}
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
	if update.ImageSource != "" {
		s.saveImageSource(w, update.ImageSource)
		return
	}
	if update.DataID == "" || update.DataRoot == "" {
		runtimeError(w, 400, fmt.Errorf("data_id and data_root, or image_source, are required"))
		return
	}
	s.saveDataRoot(w, update.DataID, update.DataRoot)
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

func (s *Server) saveImageSource(w http.ResponseWriter, requested string) {
	source, err := normalizeImageSource(requested)
	if err != nil {
		runtimeError(w, 422, err)
		return
	}
	s.configMu.Lock()
	defer s.configMu.Unlock()
	settings, err := readSettings(s.Config)
	if err != nil {
		runtimeError(w, 500, err)
		return
	}
	settings.ImageSource = source
	if err := writeSettings(s.Config, settings); err != nil {
		runtimeError(w, 500, fmt.Errorf("save %s next to config.yaml: %w", settingsFileName, err))
		return
	}
	s.Config.Runtime.ImageSource = source
	writeJSON(w, 200, map[string]any{
		"image_source":  source,
		"image_sources": []string{"official", "mirror", "auto"},
	})
}

func (s *Server) saveDataRoot(w http.ResponseWriter, dataID, requested string) {
	if os.Getenv("NIB_DATA_ROOT") != "" {
		runtimeError(w, 409, fmt.Errorf("NIB_DATA_ROOT overrides this setting; unset it before changing config"))
		return
	}
	resources := s.resources()
	if resources == nil {
		runtimeError(w, 503, s.resourceFailure())
		return
	}
	if _, ok := resources.DataByID(dataID); !ok {
		runtimeError(w, 422, fmt.Errorf("data source %q is not packaged by this Handler; update its resources", dataID))
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
			runtimeError(w, 409, fmt.Errorf("wait for active jobs before changing a data root"))
			return
		}
	}
	var saveErr error
	func() {
		s.configMu.Lock()
		defer s.configMu.Unlock()
		settings, err := readSettings(s.Config)
		if err != nil {
			saveErr = err
			return
		}
		if settings.DataRoots == nil {
			settings.DataRoots = map[string]string{}
		}
		settings.DataRoots[dataID] = root
		// The legacy single-root field is superseded by the map.
		settings.DataRoot = ""
		if err := writeSettings(s.Config, settings); err != nil {
			saveErr = fmt.Errorf("save %s next to config.yaml: %w", settingsFileName, err)
			return
		}
		if s.Config.DataRoots == nil {
			s.Config.DataRoots = map[string]string{}
		}
		s.Config.DataRoots[dataID] = root
	}()
	if saveErr != nil {
		runtimeError(w, 500, saveErr)
		return
	}
	writeJSON(w, 200, map[string]any{"data_roots": s.effectiveDataRoots()})
}
