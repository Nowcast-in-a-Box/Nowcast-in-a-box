package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"

	"gopkg.in/yaml.v3"
)

const (
	defaultHandlerHost = "127.0.0.1"
	defaultHandlerPort = 8765
	defaultReviewHost  = "127.0.0.1"
	defaultReviewPort  = 8766
	defaultArtifacts   = "artifacts"
	configFileName     = "config.yaml"
)

type Addr struct {
	Host string `yaml:"host" json:"host"`
	Port int    `yaml:"port" json:"port"`
}

type Paths struct {
	Artifacts string `yaml:"artifacts" json:"artifacts"`
	DataRoot  string `yaml:"data_root,omitempty" json:"data_root,omitempty"`
}

type Config struct {
	Source  string `yaml:"-" json:"-"`
	Handler Addr   `yaml:"handler"`
	Review  Addr   `yaml:"review"`
	Paths   Paths  `yaml:"paths"`
}

func defaultConfig() Config {
	return Config{
		Handler: Addr{Host: defaultHandlerHost, Port: defaultHandlerPort},
		Review:  Addr{Host: defaultReviewHost, Port: defaultReviewPort},
		Paths:   Paths{Artifacts: defaultArtifacts},
	}
}

func loadConfig(explicit string) (Config, error) {
	cfg := defaultConfig()
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
	d := defaultConfig()
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
}

func (c *Config) resolvePaths(baseDir string) {
	c.Paths.Artifacts = resolveAgainst(baseDir, c.Paths.Artifacts)
	if c.Paths.DataRoot != "" {
		c.Paths.DataRoot = resolveAgainst(baseDir, c.Paths.DataRoot)
	}
}

func (c *Config) applyDataRootEnv() {
	if env := os.Getenv("NIB_DATA_ROOT"); env != "" {
		c.Paths.DataRoot = env
	}
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

func applyOverrides(cfg Config, host, port string) (Config, error) {
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
