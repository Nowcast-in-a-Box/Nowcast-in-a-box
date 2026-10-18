package handler

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

const diagnosticsTimeout = 30 * time.Minute

var idPattern = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

// CommandRunner runs one docker CLI invocation. args do not include the
// docker binary.
type CommandRunner interface {
	Run(ctx context.Context, args ...string) (exitCode int, output []byte, err error)
}

// DiagnosticsOptions is one diagnostics invocation. Image overrides the
// image in Config when set. The request YAML cannot set the image.
type DiagnosticsOptions struct {
	Config      Config
	RequestPath string
	Image       string
	Timeout     time.Duration
	RunID       string
	Runner      CommandRunner
	Now         time.Time
	Stderr      io.Writer
}

type inputMount struct {
	Host      string
	Container string
}

type runProducts struct {
	Metrics string `json:"metrics"`
	Frames  string `json:"frames"`
}

type runStatus struct {
	ID         string      `json:"id"`
	Status     string      `json:"status"`
	StartedAt  time.Time   `json:"started_at"`
	FinishedAt *time.Time  `json:"finished_at,omitempty"`
	ExitCode   *int        `json:"exit_code,omitempty"`
	Error      string      `json:"error,omitempty"`
	Products   runProducts `json:"products"`
}

// DiagnosticsCLI is `nib-handler diagnostics`.
func DiagnosticsCLI(args []string) int {
	fs := flag.NewFlagSet("diagnostics", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	configPath := fs.String("config", "", "path to config.yaml")
	request := fs.String("request", "", "diagnostics request YAML")
	image := fs.String("image", "", "diagnostics image (overrides config)")
	timeout := fs.Duration("timeout", diagnosticsTimeout, "container timeout")
	id := fs.String("id", "", "run id")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if *request == "" {
		fmt.Fprintln(os.Stderr, "error: -request is required")
		return exitUsage
	}
	cfg, err := Load(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		return 1
	}
	runDir, err := RunDiagnostics(DiagnosticsOptions{
		Config:      cfg,
		RequestPath: *request,
		Image:       *image,
		Timeout:     *timeout,
		RunID:       *id,
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		if runDir != "" {
			fmt.Fprintf(os.Stderr, "run: %s\n", runDir)
		}
		return 1
	}
	fmt.Println(runDir)
	return 0
}

// RunDiagnostics validates a request, writes a run directory, and starts
// the diagnostics image. The returned directory remains after a failure.
func RunDiagnostics(opts DiagnosticsOptions) (string, error) {
	if opts.Runner == nil {
		opts.Runner = execRunner{}
	}
	if opts.Stderr == nil {
		opts.Stderr = os.Stderr
	}
	now := opts.Now
	if now.IsZero() {
		now = time.Now()
	}
	now = now.UTC()

	image := opts.Config.Diagnostics.Image
	if opts.Image != "" {
		image = opts.Image
	}
	if image == "" {
		return "", fmt.Errorf("diagnostics image is empty")
	}
	runsRoot := opts.Config.Paths.Runs
	if runsRoot == "" {
		return "", fmt.Errorf("paths.runs is empty")
	}

	id := opts.RunID
	if id == "" {
		var err error
		id, err = newRunID(now)
		if err != nil {
			return "", err
		}
	}
	if !idPattern.MatchString(id) {
		return "", fmt.Errorf("invalid run id %q", id)
	}
	runDir := filepath.Join(runsRoot, id)
	if _, err := os.Stat(runDir); err == nil {
		return "", fmt.Errorf("run %q already exists", id)
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	if err := os.MkdirAll(runsRoot, 0o755); err != nil {
		return "", err
	}
	if err := os.Mkdir(runDir, 0o755); err != nil {
		if errors.Is(err, os.ErrExist) {
			return "", fmt.Errorf("run %q already exists", id)
		}
		return "", err
	}

	st := runStatus{
		ID:        id,
		Status:    "running",
		StartedAt: now,
		Products: runProducts{
			Metrics: "products/metrics.json",
			Frames:  "products/frames",
		},
	}
	fail := func(err error) (string, error) {
		finished := time.Now().UTC()
		st.Status = "failed"
		st.FinishedAt = &finished
		st.Error = err.Error()
		_ = writeStatus(runDir, st)
		return runDir, err
	}

	raw, err := os.ReadFile(opts.RequestPath)
	if err != nil {
		return fail(fmt.Errorf("read request: %w", err))
	}
	if err := os.WriteFile(filepath.Join(runDir, "request.source.yaml"), raw, 0o644); err != nil {
		return fail(err)
	}
	doc, mounts, err := prepareRequest(opts.RequestPath, raw)
	if err != nil {
		return fail(err)
	}
	rewritten, err := yaml.Marshal(doc)
	if err != nil {
		return fail(err)
	}
	if err := os.WriteFile(filepath.Join(runDir, "request.yaml"), rewritten, 0o644); err != nil {
		return fail(err)
	}
	if err := writeStatus(runDir, st); err != nil {
		return fail(err)
	}

	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = diagnosticsTimeout
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	code, out, err := opts.Runner.Run(ctx, "image", "inspect", image)
	if err != nil {
		return fail(fmt.Errorf("image %q: %w", image, err))
	}
	if code != 0 {
		msg := strings.TrimSpace(string(out))
		if msg == "" {
			msg = "not available"
		}
		return fail(fmt.Errorf("image %q: %s", image, msg))
	}

	name := "nib-diagnostics-" + id
	runArgs := dockerRunArgs(image, name, runDir, mounts)
	code, out, runErr := opts.Runner.Run(ctx, runArgs...)
	if ctx.Err() != nil {
		rmCtx, rmCancel := context.WithTimeout(context.Background(), 15*time.Second)
		_, _, _ = opts.Runner.Run(rmCtx, "rm", "-f", name)
		rmCancel()
		runErr = ctx.Err()
	}
	if err := os.MkdirAll(filepath.Join(runDir, "logs"), 0o755); err == nil {
		_ = os.WriteFile(filepath.Join(runDir, "logs", "container.log"), out, 0o644)
	}
	if len(out) > 0 {
		_, _ = opts.Stderr.Write(out)
		if out[len(out)-1] != '\n' {
			_, _ = io.WriteString(opts.Stderr, "\n")
		}
	}

	finished := time.Now().UTC()
	st.FinishedAt = &finished
	if runErr != nil {
		st.Status = "failed"
		st.Error = runErr.Error()
		_ = writeStatus(runDir, st)
		return runDir, fmt.Errorf("%s: %w", runDir, runErr)
	}
	st.ExitCode = &code
	if code != 0 {
		st.Status = "failed"
		st.Error = fmt.Sprintf("container exited %d", code)
		_ = writeStatus(runDir, st)
		return runDir, fmt.Errorf("%s: container exited %d", runDir, code)
	}
	if err := checkProducts(runDir); err != nil {
		st.Status = "failed"
		st.Error = err.Error()
		_ = writeStatus(runDir, st)
		return runDir, fmt.Errorf("%s: %w", runDir, err)
	}
	st.Status = "succeeded"
	st.Error = ""
	if err := writeStatus(runDir, st); err != nil {
		return runDir, err
	}
	return runDir, nil
}

func prepareRequest(requestPath string, raw []byte) (map[string]any, []inputMount, error) {
	var doc map[string]any
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		return nil, nil, fmt.Errorf("parse request: %w", err)
	}
	if doc == nil {
		return nil, nil, fmt.Errorf("request is empty")
	}
	obsRaw, err := requiredString(doc, "observation")
	if err != nil {
		return nil, nil, err
	}
	forecasts, err := forecastSpecs(doc["forecasts"])
	if err != nil {
		return nil, nil, err
	}
	if err := validateMetrics(doc["metrics"]); err != nil {
		return nil, nil, err
	}

	obsHost, err := resolveInput(requestPath, obsRaw)
	if err != nil {
		return nil, nil, fmt.Errorf("observation: %w", err)
	}
	mounts := []inputMount{{
		Host:      obsHost,
		Container: "/run/inputs/observation",
	}}
	doc["observation"] = "/run/inputs/observation"

	rawForecasts, ok := doc["forecasts"].([]any)
	if !ok || len(rawForecasts) != len(forecasts) {
		return nil, nil, fmt.Errorf("forecasts must be a non-empty list")
	}
	for i, spec := range forecasts {
		host, err := resolveInput(requestPath, spec.path)
		if err != nil {
			return nil, nil, fmt.Errorf("forecasts[%d]: %w", i, err)
		}
		container := "/run/inputs/forecasts/" + spec.id
		item, ok := rawForecasts[i].(map[string]any)
		if !ok {
			return nil, nil, fmt.Errorf("forecasts[%d]: expected a map", i)
		}
		item["path"] = container
		mounts = append(mounts, inputMount{Host: host, Container: container})
	}
	return doc, mounts, nil
}

type forecastSpec struct {
	id   string
	path string
}

func forecastSpecs(raw any) ([]forecastSpec, error) {
	items, ok := raw.([]any)
	if !ok || len(items) == 0 {
		return nil, fmt.Errorf("forecasts must be a non-empty list")
	}
	seen := map[string]bool{}
	out := make([]forecastSpec, 0, len(items))
	for i, item := range items {
		m, ok := item.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("forecasts[%d]: expected a map", i)
		}
		id, _ := m["id"].(string)
		path, _ := m["path"].(string)
		if !idPattern.MatchString(id) {
			return nil, fmt.Errorf("forecasts[%d]: invalid forecast id %q", i, id)
		}
		if seen[id] {
			return nil, fmt.Errorf("forecasts[%d]: duplicate forecast id %q", i, id)
		}
		seen[id] = true
		if path == "" {
			return nil, fmt.Errorf("forecasts[%d]: path is empty", i)
		}
		out = append(out, forecastSpec{id: id, path: path})
	}
	return out, nil
}

func validateMetrics(raw any) error {
	items, ok := raw.([]any)
	if !ok || len(items) == 0 {
		return fmt.Errorf("metrics must be a non-empty list")
	}
	for i, item := range items {
		m, ok := item.(map[string]any)
		if !ok {
			return fmt.Errorf("metrics[%d]: expected a map", i)
		}
		family, _ := m["family"].(string)
		switch family {
		case "continuous":
			if err := allowKeys(m, "family", "items"); err != nil {
				return fmt.Errorf("metrics[%d]: continuous %w", i, err)
			}
			if err := nonEmptyStrings(m["items"], "items"); err != nil {
				return fmt.Errorf("metrics[%d]: %w", i, err)
			}
		case "yes_or_no":
			if err := allowKeys(m, "family", "items", "levels"); err != nil {
				return fmt.Errorf("metrics[%d]: yes_or_no %w", i, err)
			}
			if _, ok := m["levels"]; !ok {
				return fmt.Errorf("metrics[%d]: yes_or_no requires levels", i)
			}
			if err := nonEmptyStrings(m["items"], "items"); err != nil {
				return fmt.Errorf("metrics[%d]: %w", i, err)
			}
			if err := nonEmptyNumbers(m["levels"], "levels"); err != nil {
				return fmt.Errorf("metrics[%d]: %w", i, err)
			}
		case "spatial":
			if err := allowKeys(m, "family", "items", "levels", "half_windows"); err != nil {
				return fmt.Errorf("metrics[%d]: spatial %w", i, err)
			}
			if _, ok := m["levels"]; !ok {
				return fmt.Errorf("metrics[%d]: spatial requires levels", i)
			}
			if _, ok := m["half_windows"]; !ok {
				return fmt.Errorf("metrics[%d]: spatial requires half_windows", i)
			}
			if err := nonEmptyStrings(m["items"], "items"); err != nil {
				return fmt.Errorf("metrics[%d]: %w", i, err)
			}
			if err := nonEmptyNumbers(m["levels"], "levels"); err != nil {
				return fmt.Errorf("metrics[%d]: %w", i, err)
			}
			if err := positiveInts(m["half_windows"], "half_windows"); err != nil {
				return fmt.Errorf("metrics[%d]: %w", i, err)
			}
		default:
			return fmt.Errorf("metrics[%d]: unknown family %q", i, family)
		}
	}
	return nil
}

func allowKeys(m map[string]any, keys ...string) error {
	allowed := make(map[string]bool, len(keys))
	for _, key := range keys {
		allowed[key] = true
	}
	var extra []string
	for key := range m {
		if !allowed[key] {
			extra = append(extra, key)
		}
	}
	if len(extra) == 0 {
		return nil
	}
	sort.Strings(extra)
	return fmt.Errorf("forbids %s", strings.Join(extra, ", "))
}

func nonEmptyStrings(v any, name string) error {
	items, ok := v.([]any)
	if !ok || len(items) == 0 {
		return fmt.Errorf("%s must be a non-empty list", name)
	}
	for _, item := range items {
		s, ok := item.(string)
		if !ok || s == "" {
			return fmt.Errorf("%s must be non-empty strings", name)
		}
	}
	return nil
}

func nonEmptyNumbers(v any, name string) error {
	items, ok := v.([]any)
	if !ok || len(items) == 0 {
		return fmt.Errorf("%s must be a non-empty list", name)
	}
	for _, item := range items {
		if _, _, ok := numberKind(item); !ok {
			return fmt.Errorf("%s must be numbers", name)
		}
	}
	return nil
}

func positiveInts(v any, name string) error {
	items, ok := v.([]any)
	if !ok || len(items) == 0 {
		return fmt.Errorf("%s must be a non-empty list", name)
	}
	for _, item := range items {
		n, whole, ok := numberKind(item)
		if !ok || !whole || n <= 0 {
			return fmt.Errorf("%s must be positive integers", name)
		}
	}
	return nil
}

func numberKind(v any) (float64, bool, bool) {
	switch n := v.(type) {
	case int:
		return float64(n), true, true
	case int64:
		return float64(n), true, true
	case uint64:
		return float64(n), true, true
	case float64:
		if math.IsNaN(n) || math.IsInf(n, 0) {
			return 0, false, false
		}
		return n, n == math.Trunc(n), true
	default:
		return 0, false, false
	}
}

func requiredString(doc map[string]any, key string) (string, error) {
	v, ok := doc[key]
	if !ok {
		return "", fmt.Errorf("%s is required", key)
	}
	s, ok := v.(string)
	if !ok || strings.TrimSpace(s) == "" {
		return "", fmt.Errorf("%s must be a non-empty string", key)
	}
	return s, nil
}

func resolveInput(requestPath, raw string) (string, error) {
	if strings.TrimSpace(raw) == "" {
		return "", fmt.Errorf("path is empty")
	}
	if isDockerSock(raw) {
		return "", fmt.Errorf("refusing docker.sock")
	}
	var abs string
	if filepath.IsAbs(raw) {
		abs = filepath.Clean(raw)
	} else {
		cleaned := filepath.Clean(raw)
		if cleaned == ".." || strings.HasPrefix(cleaned, ".."+string(filepath.Separator)) {
			return "", fmt.Errorf("path %q escapes the request directory", raw)
		}
		abs = filepath.Join(filepath.Dir(requestPath), cleaned)
	}
	abs, err := filepath.Abs(abs)
	if err != nil {
		return "", err
	}
	abs, err = filepath.EvalSymlinks(abs)
	if err != nil {
		return "", err
	}
	if isDockerSock(abs) {
		return "", fmt.Errorf("refusing docker.sock")
	}
	if runtime.GOOS != "windows" && strings.Contains(abs, ":") {
		return "", fmt.Errorf("refusing path containing ':'")
	}
	info, err := os.Stat(abs)
	if err != nil {
		return "", err
	}
	if !info.IsDir() && !info.Mode().IsRegular() {
		return "", fmt.Errorf("input must be a regular file or directory")
	}
	return abs, nil
}

func isDockerSock(path string) bool {
	return filepath.Base(path) == "docker.sock"
}

func dockerRunArgs(image, name, runDir string, mounts []inputMount) []string {
	args := []string{
		"run", "--rm",
		"--pull", "never",
		"--name", name,
		"--network", "none",
		"--read-only",
		"--tmpfs", "/tmp",
		"-v", volumeSpec(runDir, "/run", false),
	}
	for _, mount := range mounts {
		args = append(args, "-v", volumeSpec(mount.Host, mount.Container, true))
	}
	if user := containerUser(); user != "" {
		args = append(args, "--user", user)
	}
	args = append(args, image, "--config", "/run/request.yaml")
	return args
}

func volumeSpec(host, container string, readOnly bool) string {
	spec := filepath.Clean(host) + ":" + container
	if readOnly {
		spec += ":ro"
	}
	return spec
}

func checkProducts(runDir string) error {
	metricsPath := filepath.Join(runDir, "products", "metrics.json")
	body, err := os.ReadFile(metricsPath)
	if err != nil {
		return fmt.Errorf("products/metrics.json: %w", err)
	}
	if len(bytes.TrimSpace(body)) == 0 {
		return fmt.Errorf("products/metrics.json is empty")
	}
	var value any
	if err := json.Unmarshal(body, &value); err != nil {
		return fmt.Errorf("products/metrics.json: %w", err)
	}
	if value == nil {
		return fmt.Errorf("products/metrics.json is null")
	}
	frames := filepath.Join(runDir, "products", "frames")
	info, err := os.Stat(frames)
	if err != nil {
		return fmt.Errorf("products/frames: %w", err)
	}
	if !info.IsDir() {
		return fmt.Errorf("products/frames is not a directory")
	}
	return nil
}

func writeStatus(runDir string, st runStatus) error {
	body, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	body = append(body, '\n')
	return os.WriteFile(filepath.Join(runDir, "status.json"), body, 0o644)
}

func newRunID(now time.Time) (string, error) {
	var buf [8]byte
	if _, err := rand.Read(buf[:]); err != nil {
		return "", err
	}
	return now.UTC().Format("20060102T150405Z") + "-" + hex.EncodeToString(buf[:]), nil
}

type execRunner struct{}

func (execRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	path, err := exec.LookPath("docker")
	if err != nil {
		return -1, nil, fmt.Errorf("docker executable not found")
	}
	cmd := exec.CommandContext(ctx, path, args...)
	out, err := cmd.CombinedOutput()
	if err == nil {
		return 0, out, nil
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		return exitErr.ExitCode(), out, nil
	}
	return -1, out, err
}
