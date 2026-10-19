package handler_test

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
	"gopkg.in/yaml.v3"
)

type fakeRunner struct {
	inspectCode   int
	runCode       int
	writeProducts bool
	calls         [][]string
}

func (f *fakeRunner) Run(_ context.Context, args ...string) (int, []byte, error) {
	f.calls = append(f.calls, append([]string(nil), args...))
	if len(args) == 0 {
		return -1, nil, errString("no args")
	}
	if args[0] == "image" {
		if f.inspectCode != 0 {
			return f.inspectCode, []byte("missing"), nil
		}
		return 0, []byte("ok"), nil
	}
	if args[0] == "rm" {
		return 0, nil, nil
	}
	if f.writeProducts {
		runDir := hostRunDir(args)
		if runDir == "" {
			return -1, nil, errString("no run mount")
		}
		if err := os.MkdirAll(filepath.Join(runDir, "products", "frames"), 0o755); err != nil {
			return -1, nil, err
		}
		if err := os.WriteFile(filepath.Join(runDir, "products", "metrics.json"), []byte("{\"ok\":true}\n"), 0o644); err != nil {
			return -1, nil, err
		}
	}
	return f.runCode, []byte("container log"), nil
}

type errString string

func (e errString) Error() string { return string(e) }

func hostRunDir(args []string) string {
	for i := 0; i < len(args)-1; i++ {
		if args[i] == "-v" && strings.HasSuffix(args[i+1], ":/run") {
			path := strings.TrimSuffix(args[i+1], ":/run")
			if !filepath.IsAbs(path) {
				return ""
			}
			return path
		}
	}
	return ""
}

func diagConfig(t *testing.T) handler.Config {
	t.Helper()
	return handler.Config{
		Paths: handler.Paths{Runs: filepath.Join(t.TempDir(), "runs"), Resources: testResourcesPath(t)},
	}
}

func writeRequest(t *testing.T, dir, body string) string {
	t.Helper()
	path := filepath.Join(dir, "request.yaml")
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func runDiag(t *testing.T, cfg handler.Config, request string, runner *fakeRunner, id string) (string, error) {
	t.Helper()
	opts := handler.DiagnosticsOptions{
		Config:      cfg,
		RequestPath: request,
		Runner:      runner,
		Stderr:      io.Discard,
		Timeout:     time.Minute,
		RunID:       id,
	}
	return handler.RunDiagnostics(opts)
}

func readStatus(t *testing.T, runDir string) map[string]any {
	t.Helper()
	body, err := os.ReadFile(filepath.Join(runDir, "status.json"))
	if err != nil {
		t.Fatal(err)
	}
	var st map[string]any
	if err := json.Unmarshal(body, &st); err != nil {
		t.Fatal(err)
	}
	return st
}

func TestDiagnosticsRejectsRequest(t *testing.T) {
	cases := []struct {
		name string
		yaml string
		want string
	}{
		{
			name: "continuous levels",
			yaml: `
observation: obs
forecasts:
  - id: m1
    path: fcst
metrics:
  - family: continuous
    items: [mae]
    levels: [35]
`,
			want: "forbids levels",
		},
		{
			name: "yes_or_no missing levels",
			yaml: `
observation: obs
forecasts:
  - id: m1
    path: fcst
metrics:
  - family: yes_or_no
    items: [csi]
`,
			want: "requires levels",
		},
		{
			name: "spatial missing half_windows",
			yaml: `
observation: obs
forecasts:
  - id: m1
    path: fcst
metrics:
  - family: spatial
    items: [fss]
    levels: [35]
`,
			want: "requires half_windows",
		},
		{
			name: "unknown family",
			yaml: `
observation: obs
forecasts:
  - id: m1
    path: fcst
metrics:
  - family: score
    items: [mae]
`,
			want: "unknown family",
		},
		{
			name: "empty items",
			yaml: `
observation: obs
forecasts:
  - id: m1
    path: fcst
metrics:
  - family: continuous
    items: []
`,
			want: "items",
		},
		{
			name: "escape",
			yaml: `
observation: ../secret
forecasts:
  - id: m1
    path: fcst
metrics:
  - family: continuous
    items: [mae]
`,
			want: "escapes",
		},
		{
			name: "nested escape",
			yaml: `
observation: foo/../../secret
forecasts:
  - id: m1
    path: fcst
metrics:
  - family: continuous
    items: [mae]
`,
			want: "escapes",
		},
		{
			name: "missing path",
			yaml: `
observation: missing.zarr
forecasts:
  - id: m1
    path: also-missing.zarr
metrics:
  - family: continuous
    items: [mae]
`,
			want: "missing.zarr",
		},
		{
			name: "duplicate id",
			yaml: `
observation: obs
forecasts:
  - id: m1
    path: fcst
  - id: m1
    path: fcst
metrics:
  - family: continuous
    items: [mae]
`,
			want: "duplicate",
		},
		{
			name: "illegal id",
			yaml: `
observation: obs
forecasts:
  - id: ../x
    path: fcst
metrics:
  - family: continuous
    items: [mae]
`,
			want: "invalid forecast id",
		},
		{
			name: "docker.sock",
			yaml: `
observation: docker.sock
forecasts:
  - id: m1
    path: fcst
metrics:
  - family: continuous
    items: [mae]
`,
			want: "docker.sock",
		},
		{
			name: "zero half window",
			yaml: `
observation: obs
forecasts:
  - id: m1
    path: fcst
metrics:
  - family: spatial
    items: [fss]
    levels: [35]
    half_windows: [0]
`,
			want: "positive",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := diagConfig(t)
			reqDir := t.TempDir()
			request := writeRequest(t, reqDir, tc.yaml)
			runner := &fakeRunner{}
			runDir, err := runDiag(t, cfg, request, runner, "job1")
			if err == nil {
				t.Fatal("expected error")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("error=%v want %q", err, tc.want)
			}
			if len(runner.calls) != 0 {
				t.Fatalf("docker calls=%v", runner.calls)
			}
			if runDir == "" {
				t.Fatal("expected failed run directory")
			}
			if st := readStatus(t, runDir); st["status"] != "failed" {
				t.Fatalf("status=%v", st)
			}
		})
	}
}

func TestDiagnosticsSuccess(t *testing.T) {
	cfg := diagConfig(t)
	reqDir := t.TempDir()
	obs := filepath.Join(reqDir, "observation")
	fcstA := filepath.Join(reqDir, "wadepre")
	fcstB := filepath.Join(reqDir, "nowcastnet")
	for _, dir := range []string{obs, fcstA, fcstB} {
		if err := os.Mkdir(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	request := writeRequest(t, reqDir, `
observation: observation
forecasts:
  - id: wadepre
    path: wadepre
  - id: nowcastnet
    path: nowcastnet
metrics:
  - family: continuous
    items: [mae, rmse]
  - family: yes_or_no
    items: [csi]
    levels: [35]
  - family: yes_or_no
    items: [ets]
    levels: [40]
  - family: spatial
    items: [fss]
    levels: [35]
    half_windows: [2, 4]
colormap: radar_reflectivity
image: evil:latest
`)
	runner := &fakeRunner{writeProducts: true}
	opts := handler.DiagnosticsOptions{
		Config:      cfg,
		RequestPath: request,
		Image:       "from-flag:latest",
		Runner:      runner,
		Stderr:      io.Discard,
		Timeout:     time.Minute,
		RunID:       "job1",
	}
	runDir, err := handler.RunDiagnostics(opts)
	if err != nil {
		t.Fatal(err)
	}
	st := readStatus(t, runDir)
	if st["status"] != "succeeded" {
		t.Fatalf("status=%v", st)
	}
	if st["exit_code"].(float64) != 0 {
		t.Fatalf("exit_code=%v", st["exit_code"])
	}

	body, err := os.ReadFile(filepath.Join(runDir, "request.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(body), reqDir) {
		t.Fatalf("host path leaked into request.yaml:\n%s", body)
	}
	var doc map[string]any
	if err := yaml.Unmarshal(body, &doc); err != nil {
		t.Fatal(err)
	}
	if doc["observation"] != "/run/inputs/observation" {
		t.Fatalf("observation=%v", doc["observation"])
	}
	if doc["colormap"] != "radar_reflectivity" {
		t.Fatalf("colormap=%v", doc["colormap"])
	}
	forecasts := doc["forecasts"].([]any)
	if len(forecasts) != 2 {
		t.Fatalf("forecasts=%v", forecasts)
	}
	if forecasts[0].(map[string]any)["path"] != "/run/inputs/forecasts/wadepre" {
		t.Fatalf("forecast=%v", forecasts[0])
	}

	source, err := os.ReadFile(filepath.Join(runDir, "request.source.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(source), "observation: observation") {
		t.Fatalf("source=%s", source)
	}
	logBody, err := os.ReadFile(filepath.Join(runDir, "logs", "container.log"))
	if err != nil {
		t.Fatal(err)
	}
	if string(logBody) != "container log" {
		t.Fatalf("log=%q", logBody)
	}

	if len(runner.calls) != 2 {
		t.Fatalf("calls=%v", runner.calls)
	}
	if runner.calls[0][0] != "image" || runner.calls[0][2] != "from-flag:latest" {
		t.Fatalf("inspect=%v", runner.calls[0])
	}
	assertRunArgs(t, runner.calls[1], "from-flag:latest")
	joined := strings.Join(runner.calls[1], "\n")
	if strings.Contains(joined, "evil:latest") {
		t.Fatalf("request image used: %v", runner.calls[1])
	}
	if !hasInputMount(runner.calls[1], "/run/inputs/observation") {
		t.Fatalf("observation mount missing: %v", runner.calls[1])
	}
	if !hasInputMount(runner.calls[1], "/run/inputs/forecasts/nowcastnet") {
		t.Fatalf("forecast mount missing: %v", runner.calls[1])
	}
}

func TestDiagnosticsRejectsAliasedDockerSocket(t *testing.T) {
	cfg, request := validRequest(t)
	dir := filepath.Dir(request)
	socket := filepath.Join(dir, "docker.sock")
	if err := os.WriteFile(socket, []byte("socket-placeholder"), 0o644); err != nil {
		t.Fatal(err)
	}
	input := filepath.Join(dir, "observation")
	if err := os.Remove(input); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(socket, input); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	runner := &fakeRunner{}
	_, err := runDiag(t, cfg, request, runner, "socket-alias")
	if err == nil || !strings.Contains(err.Error(), "docker.sock") {
		t.Fatalf("error=%v", err)
	}
	if len(runner.calls) != 0 {
		t.Fatalf("docker invoked: %v", runner.calls)
	}
}

func TestDiagnosticsRejectsSpecialInputFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix socket fixture")
	}
	cfg, request := validRequest(t)
	// macOS limits Unix socket paths to 104 bytes; t.TempDir can exceed that.
	dir, err := os.MkdirTemp("/tmp", "nib-sock-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	input := filepath.Join(dir, "input.sock")
	listener, err := net.Listen("unix", input)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	body, err := os.ReadFile(request)
	if err != nil {
		t.Fatal(err)
	}
	body = []byte(strings.Replace(string(body), "observation: observation", "observation: "+input, 1))
	if err := os.WriteFile(request, body, 0o644); err != nil {
		t.Fatal(err)
	}
	runner := &fakeRunner{}
	_, err = runDiag(t, cfg, request, runner, "special-input")
	if err == nil || !strings.Contains(err.Error(), "regular file or directory") {
		t.Fatalf("error=%v", err)
	}
	if len(runner.calls) != 0 {
		t.Fatalf("docker invoked: %v", runner.calls)
	}
}

func TestDiagnosticsAbsoluteInput(t *testing.T) {
	cfg := diagConfig(t)
	reqDir := t.TempDir()
	obs := filepath.Join(reqDir, "observation")
	fcst := filepath.Join(reqDir, "forecast")
	if err := os.Mkdir(obs, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(fcst, 0o755); err != nil {
		t.Fatal(err)
	}
	request := writeRequest(t, reqDir, `
observation: `+obs+`
forecasts:
  - id: m1
    path: `+fcst+`
metrics:
  - family: continuous
    items: [mae]
`)
	runner := &fakeRunner{writeProducts: true}
	runDir, err := runDiag(t, cfg, request, runner, "abs1")
	if err != nil {
		t.Fatal(err)
	}
	if st := readStatus(t, runDir); st["status"] != "succeeded" {
		t.Fatalf("status=%v", st)
	}
	if !hasInputMount(runner.calls[1], "/run/inputs/observation") {
		t.Fatalf("absolute mount missing: %v", runner.calls[1])
	}
}

func TestDiagnosticsExitZeroMissingProducts(t *testing.T) {
	cfg, request := validRequest(t)
	runner := &fakeRunner{}
	runDir, err := runDiag(t, cfg, request, runner, "job1")
	if err == nil {
		t.Fatal("expected error")
	}
	if !strings.Contains(err.Error(), "metrics.json") {
		t.Fatalf("error=%v", err)
	}
	if st := readStatus(t, runDir); st["status"] != "failed" {
		t.Fatalf("status=%v", st)
	}
	if _, err := os.Stat(runDir); err != nil {
		t.Fatal(err)
	}
}

func TestDiagnosticsNonZeroExit(t *testing.T) {
	cfg, request := validRequest(t)
	runner := &fakeRunner{runCode: 3, writeProducts: true}
	runDir, err := runDiag(t, cfg, request, runner, "job1")
	if err == nil || !strings.Contains(err.Error(), "exited 3") {
		t.Fatalf("error=%v", err)
	}
	st := readStatus(t, runDir)
	if st["status"] != "failed" || st["exit_code"].(float64) != 3 {
		t.Fatalf("status=%v", st)
	}
	if _, err := os.Stat(runDir); err != nil {
		t.Fatal(err)
	}
}

func TestDiagnosticsImageMissing(t *testing.T) {
	cfg, request := validRequest(t)
	runner := &fakeRunner{inspectCode: 1}
	runDir, err := runDiag(t, cfg, request, runner, "job1")
	if err == nil || !strings.Contains(err.Error(), "not available") && !strings.Contains(err.Error(), "missing") {
		t.Fatalf("error=%v", err)
	}
	if len(runner.calls) != 1 || runner.calls[0][0] != "image" {
		t.Fatalf("calls=%v", runner.calls)
	}
	if st := readStatus(t, runDir); st["status"] != "failed" {
		t.Fatalf("status=%v", st)
	}
}

func TestDiagnosticsInvalidRunID(t *testing.T) {
	cfg := diagConfig(t)
	runner := &fakeRunner{}
	runDir, err := runDiag(t, cfg, "nope", runner, "../x")
	if err == nil || !strings.Contains(err.Error(), "invalid run id") {
		t.Fatalf("error=%v", err)
	}
	if runDir != "" || len(runner.calls) != 0 {
		t.Fatalf("runDir=%q calls=%v", runDir, runner.calls)
	}
}

func TestDiagnosticsExistingRun(t *testing.T) {
	cfg := diagConfig(t)
	taken := filepath.Join(cfg.Paths.Runs, "taken")
	if err := os.MkdirAll(taken, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(taken, "status.json"), []byte("keep"), 0o644); err != nil {
		t.Fatal(err)
	}
	runner := &fakeRunner{}
	_, err := runDiag(t, cfg, "nope", runner, "taken")
	if err == nil || !strings.Contains(err.Error(), "already exists") {
		t.Fatalf("error=%v", err)
	}
	body, err := os.ReadFile(filepath.Join(taken, "status.json"))
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != "keep" {
		t.Fatalf("status overwritten: %s", body)
	}
	if len(runner.calls) != 0 {
		t.Fatalf("calls=%v", runner.calls)
	}
}

func TestDiagnosticsGeneratedIDsAreUnique(t *testing.T) {
	cfg, request := validRequest(t)
	var previous string
	for i := 0; i < 2; i++ {
		runDir, err := handler.RunDiagnostics(handler.DiagnosticsOptions{
			Config:      cfg,
			RequestPath: request,
			Runner:      &fakeRunner{writeProducts: true},
			Stderr:      io.Discard,
			Now:         time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC),
		})
		if err != nil {
			t.Fatal(err)
		}
		if runDir == previous {
			t.Fatal("two runs reused the same directory")
		}
		id := filepath.Base(runDir)
		if !strings.HasPrefix(id, "20261012T000000Z-") || len(id) != len("20261012T000000Z-")+16 {
			t.Fatalf("id=%q", id)
		}
		previous = runDir
	}
}

func TestDiagnosticsUsage(t *testing.T) {
	if code := handler.Main([]string{"diagnostics"}); code != 2 {
		t.Fatalf("code=%d", code)
	}
}

func TestDiagnosticsCLIRejectsWithoutDocker(t *testing.T) {
	dir := t.TempDir()
	cfgPath := writeConfig(t, dir, "paths:\n  runs: runs\n  resources: "+testResourcesPath(t)+"\n")
	req := writeRequest(t, dir, "metrics: []\n")
	code := handler.DiagnosticsCLI([]string{"-config", cfgPath, "-request", req, "-id", "job1"})
	if code != 1 {
		t.Fatalf("code=%d", code)
	}
	if st := readStatus(t, filepath.Join(dir, "runs", "job1")); st["status"] != "failed" {
		t.Fatalf("status=%v", st)
	}
}

type cancelRunner struct {
	calls [][]string
}

func (c *cancelRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	c.calls = append(c.calls, append([]string(nil), args...))
	if len(args) > 0 && args[0] == "image" {
		return 0, nil, nil
	}
	if len(args) > 0 && args[0] == "rm" {
		return 0, nil, nil
	}
	<-ctx.Done()
	// A killed docker CLI may report an exit code without a runner error.
	return 137, []byte("timed out"), nil
}

func TestDiagnosticsTimeoutRemovesContainer(t *testing.T) {
	cfg, request := validRequest(t)
	runner := &cancelRunner{}
	runDir, err := handler.RunDiagnostics(handler.DiagnosticsOptions{
		Config:      cfg,
		RequestPath: request,
		Runner:      runner,
		Stderr:      io.Discard,
		Timeout:     30 * time.Millisecond,
		RunID:       "job1",
	})
	if err == nil || !strings.Contains(err.Error(), "deadline exceeded") {
		t.Fatalf("expected timeout, got %v", err)
	}
	st := readStatus(t, runDir)
	if st["status"] != "failed" || !strings.Contains(st["error"].(string), "deadline exceeded") {
		t.Fatalf("status=%v", st)
	}
	var sawRemove bool
	for _, call := range runner.calls {
		if len(call) >= 3 && call[0] == "rm" && call[1] == "-f" && call[2] == "nib-diagnostics-job1" {
			sawRemove = true
		}
	}
	if !sawRemove {
		t.Fatalf("calls=%v", runner.calls)
	}
}

func TestLoadDiagnosticsConfig(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	dir := t.TempDir()
	resources := testResourcesPath(t)
	path := writeConfig(t, dir, "paths:\n  runs: out\n  resources: "+resources+"\n")
	cfg, err := handler.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	want, err := filepath.Abs(filepath.Join(dir, "out"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Paths.Runs != want {
		t.Fatalf("runs=%q want %q", cfg.Paths.Runs, want)
	}
	if cfg.Paths.Resources != resources {
		t.Fatalf("resources=%q want %q", cfg.Paths.Resources, resources)
	}

	bare := writeConfig(t, dir, "handler:\n  port: 9100\n")
	cfg, err = handler.Load(bare)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(cfg.Paths.Runs, string(filepath.Separator)+"runs") {
		t.Fatalf("default runs=%q", cfg.Paths.Runs)
	}
}

func validRequest(t *testing.T) (handler.Config, string) {
	t.Helper()
	cfg := diagConfig(t)
	reqDir := t.TempDir()
	obs := filepath.Join(reqDir, "observation")
	fcst := filepath.Join(reqDir, "forecast")
	if err := os.Mkdir(obs, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(fcst, 0o755); err != nil {
		t.Fatal(err)
	}
	request := writeRequest(t, reqDir, `
observation: observation
forecasts:
  - id: m1
    path: forecast
metrics:
  - family: continuous
    items: [mae]
`)
	return cfg, request
}

func assertRunArgs(t *testing.T, args []string, image string) {
	t.Helper()
	if !hasPair(args, "--network", "none") {
		t.Fatalf("network: %v", args)
	}
	if !hasPair(args, "--name", "nib-diagnostics-job1") {
		t.Fatalf("name: %v", args)
	}
	if !contains(args, "--read-only") {
		t.Fatalf("read-only: %v", args)
	}
	foundRun := false
	foundRO := false
	for i := 0; i < len(args)-1; i++ {
		if args[i] != "-v" {
			continue
		}
		spec := args[i+1]
		if strings.HasSuffix(spec, ":/run") {
			foundRun = true
		}
		if strings.Contains(spec, "/run/inputs/") && strings.HasSuffix(spec, ":ro") {
			foundRO = true
		}
		if strings.Contains(spec, "docker.sock") {
			t.Fatalf("docker.sock mount: %s", spec)
		}
	}
	if !foundRun || !foundRO {
		t.Fatalf("mounts run=%v ro=%v args=%v", foundRun, foundRO, args)
	}
	for _, bad := range []string{"-p", "--privileged", "privileged"} {
		if contains(args, bad) {
			t.Fatalf("forbidden %s in %v", bad, args)
		}
	}
	if runtime.GOOS != "windows" && !contains(args, "--user") {
		t.Fatalf("missing --user: %v", args)
	}
	if len(args) < 3 || args[len(args)-3] != image || args[len(args)-2] != "--config" || args[len(args)-1] != "/run/request.yaml" {
		t.Fatalf("tail=%v", args)
	}
}

func hasPair(args []string, a, b string) bool {
	for i := 0; i < len(args)-1; i++ {
		if args[i] == a && args[i+1] == b {
			return true
		}
	}
	return false
}

func hasInputMount(args []string, container string) bool {
	suffix := ":" + container + ":ro"
	for _, arg := range args {
		if strings.HasSuffix(arg, suffix) && len(arg) > len(suffix) {
			return true
		}
	}
	return false
}

func contains(args []string, want string) bool {
	for _, arg := range args {
		if arg == want {
			return true
		}
	}
	return false
}
