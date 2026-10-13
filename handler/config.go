package handler

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

const (
	defaultHandlerHost      = "127.0.0.1"
	defaultHandlerPort      = 8765
	defaultReviewHost       = "127.0.0.1"
	defaultReviewPort       = 8766
	defaultArtifacts        = "artifacts"
	defaultRuns             = "runs"
	defaultDiagnosticsImage = "nib-diagnostics:dev"
	configFileName          = "config.yaml"
)

// Addr is a loopback bind address from config.yaml.
type Addr struct {
	Host string `yaml:"host" json:"host"`
	Port int    `yaml:"port" json:"port"`
}

// Paths are host filesystem locations from config.yaml.
// Relative values resolve against the directory that contains the file.
type Paths struct {
	Artifacts string `yaml:"artifacts" json:"artifacts"`
	DataRoot  string `yaml:"data_root,omitempty" json:"data_root,omitempty"`
	Runs      string `yaml:"runs,omitempty" json:"runs,omitempty"`
}

// Diagnostics is the metrics and visualization image. The request YAML
// cannot override it.
type Diagnostics struct {
	Image string `yaml:"image,omitempty" json:"image,omitempty"`
}

// Runtime configures controlled container execution; browser requests cannot set these values.
type Runtime struct {
	DockerContext  string            `yaml:"docker_context,omitempty" json:"docker_context,omitempty"`
	DataFile       string            `yaml:"data_file" json:"data_file"`
	CatalogFile    string            `yaml:"catalog_file" json:"catalog_file"`
	IngestionImage string            `yaml:"ingestion_image" json:"ingestion_image"`
	ModelImages    map[string]string `yaml:"model_images" json:"model_images"`
	AllowedOrigins []string          `yaml:"allowed_origins" json:"allowed_origins"`
	Token          string            `yaml:"token,omitempty" json:"-"`
	AtlasRevision  string            `yaml:"atlas_revision" json:"atlas_revision"`
}

// Config is the host runtime settings loaded from config.yaml.
// Source is the file that was read; it is not a YAML field.
type Config struct {
	Source      string      `yaml:"-" json:"-"`
	Handler     Addr        `yaml:"handler"`
	Review      Addr        `yaml:"review"`
	Paths       Paths       `yaml:"paths"`
	Diagnostics Diagnostics `yaml:"diagnostics"`
	Runtime     Runtime     `yaml:"runtime"`
}

// DefaultConfig is used when a key is missing from config.yaml, or when
// no config file is found.
func DefaultConfig() Config {
	return Config{
		Handler:     Addr{Host: defaultHandlerHost, Port: defaultHandlerPort},
		Review:      Addr{Host: defaultReviewHost, Port: defaultReviewPort},
		Paths:       Paths{Artifacts: defaultArtifacts, Runs: defaultRuns},
		Diagnostics: Diagnostics{Image: defaultDiagnosticsImage},
		Runtime: Runtime{DataFile: "SEVIR/processed/sevir_vil.h5", CatalogFile: "source/CATALOG.csv",
			IngestionImage: "nib-ingestion:dev", ModelImages: map[string]string{
				"nowcastnet": "nib-nowcastnet:dev", "exprecast": "nib-exprecast:dev", "wadepre": "nib-wadepre:dev"}},
	}
}

// Load reads config.yaml. An empty path uses NIB_CONFIG if set, otherwise
// walks from the working directory and the executable toward the filesystem
// root. An explicit path that cannot be read is an error.
func Load(explicit string) (Config, error) {
	cfg := DefaultConfig()
	path := explicit
	if path == "" {
		found, ok := findConfigFile()
		if !ok {
			cwd, err := os.Getwd()
			if err != nil {
				cwd = "."
			}
			cfg.resolvePaths(cwd)
			cfg.applyDataRootEnv()
			return cfg, nil
		}
		path = found
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("read config %s: %w", path, err)
	}
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		return Config{}, fmt.Errorf("parse config %s: %w", path, err)
	}
	cfg.applyDefaults()
	cfg.Source = path
	cfg.resolvePaths(filepath.Dir(path))
	cfg.applyDataRootEnv()
	return cfg, nil
}

func (c *Config) applyDefaults() {
	d := DefaultConfig()
	if c.Handler.Host == "" {
		c.Handler.Host = d.Handler.Host
	}
	if c.Handler.Port == 0 {
		c.Handler.Port = d.Handler.Port
	}
	if c.Review.Host == "" {
		c.Review.Host = d.Review.Host
	}
	if c.Review.Port == 0 {
		c.Review.Port = d.Review.Port
	}
	if c.Paths.Artifacts == "" {
		c.Paths.Artifacts = d.Paths.Artifacts
	}
	if c.Paths.Runs == "" {
		c.Paths.Runs = d.Paths.Runs
	}
	if c.Diagnostics.Image == "" {
		c.Diagnostics.Image = d.Diagnostics.Image
	}
	if c.Runtime.DataFile == "" {
		c.Runtime.DataFile = d.Runtime.DataFile
	}
	if c.Runtime.CatalogFile == "" {
		c.Runtime.CatalogFile = d.Runtime.CatalogFile
	}
	if c.Runtime.IngestionImage == "" {
		c.Runtime.IngestionImage = d.Runtime.IngestionImage
	}
	if c.Runtime.ModelImages == nil {
		c.Runtime.ModelImages = d.Runtime.ModelImages
	}
}

func (c *Config) resolvePaths(baseDir string) {
	c.Paths.Artifacts = resolveAgainst(baseDir, c.Paths.Artifacts)
	c.Paths.Runs = resolveAgainst(baseDir, c.Paths.Runs)
	if c.Paths.DataRoot != "" {
		c.Paths.DataRoot = resolveAgainst(baseDir, c.Paths.DataRoot)
	}
}

func (c *Config) applyDataRootEnv() {
	if env := os.Getenv("NIB_DATA_ROOT"); env != "" {
		c.Paths.DataRoot = env
	}
}

// ResolveDataDir joins a request data_dir onto the host data root.
// It does not create directories and has no production caller yet.
// A later job slice must use this instead of a second path rule.
func ResolveDataDir(dataRoot, dataDir string) (string, error) {
	dataDir = strings.TrimSpace(dataDir)
	if dataDir == "" {
		return "", fmt.Errorf("data_dir is empty")
	}
	if runtime.GOOS != "windows" && foreignAbsolute(dataDir) {
		return "", fmt.Errorf("data_dir %q is an absolute path for another operating system; pass a path relative to the data root", dataDir)
	}
	if filepath.IsAbs(dataDir) {
		return filepath.Clean(dataDir), nil
	}
	if strings.TrimSpace(dataRoot) == "" {
		return "", fmt.Errorf("relative data_dir requires a data root (NIB_DATA_ROOT or config.yaml paths.data_root)")
	}
	cleaned := filepath.Clean(dataDir)
	if cleaned == ".." || strings.HasPrefix(cleaned, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("data_dir %q escapes the data root", dataDir)
	}
	root := filepath.Clean(dataRoot)
	joined := filepath.Join(root, cleaned)
	rel, err := filepath.Rel(root, joined)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("data_dir %q escapes the data root", dataDir)
	}
	return joined, nil
}

func foreignAbsolute(path string) bool {
	if strings.HasPrefix(path, `\\`) {
		return true
	}
	if len(path) < 3 || path[1] != ':' {
		return false
	}
	if path[2] != '/' && path[2] != '\\' {
		return false
	}
	letter := path[0]
	return (letter >= 'A' && letter <= 'Z') || (letter >= 'a' && letter <= 'z')
}

func resolveAgainst(baseDir, path string) string {
	if path == "" {
		return path
	}
	if !filepath.IsAbs(path) {
		path = filepath.Join(baseDir, path)
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return filepath.Clean(path)
	}
	return abs
}

// ApplyOverrides applies -host and -port flag values onto cfg.
func ApplyOverrides(cfg Config, host, port string) (Config, error) {
	if host != "" {
		cfg.Handler.Host = host
	}
	if port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n <= 0 || n > 65535 {
			return cfg, fmt.Errorf("invalid port %q", port)
		}
		cfg.Handler.Port = n
	}
	return cfg, nil
}

func findConfigFile() (string, bool) {
	if env := os.Getenv("NIB_CONFIG"); env != "" {
		return env, true
	}
	var starts []string
	if cwd, err := os.Getwd(); err == nil {
		starts = append(starts, cwd)
	}
	if exe, err := os.Executable(); err == nil {
		starts = append(starts, filepath.Dir(exe))
	}
	seen := map[string]bool{}
	for _, start := range starts {
		dir := start
		for {
			abs, err := filepath.Abs(dir)
			if err != nil {
				break
			}
			if seen[abs] {
				break
			}
			seen[abs] = true
			candidate := filepath.Join(abs, configFileName)
			if st, err := os.Stat(candidate); err == nil && !st.IsDir() {
				return candidate, true
			}
			parent := filepath.Dir(abs)
			if parent == abs {
				break
			}
			dir = parent
		}
	}
	return "", false
}
