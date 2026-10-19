package handler_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
	"gopkg.in/yaml.v3"
)

type runtimeRunner struct {
	root  string
	mu    sync.Mutex
	calls [][]string
}

func (f *runtimeRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, append([]string{}, args...))
	var id string
	for i, arg := range args {
		if arg == "-f" && i+1 < len(args) && args[len(args)-1] == "task" {
			raw, err := os.ReadFile(args[i+1])
			if err != nil {
				return 1, nil, err
			}
			var compose struct {
				Services map[string]struct {
					Command []string `yaml:"command"`
				} `yaml:"services"`
			}
			if err := yaml.Unmarshal(raw, &compose); err != nil {
				return 1, nil, err
			}
			for index, value := range compose.Services["task"].Command {
				if value == "--id" {
					id = compose.Services["task"].Command[index+1]
				}
			}
		}
	}
	if id != "" {
		child := filepath.Join(f.root, id, id)
		if err := os.Mkdir(child, 0o755); err != nil {
			return 1, nil, err
		}
		body, _ := json.Marshal(map[string]any{"schema": "nib.run.v1", "id": id, "forecasts": map[string]any{}, "diagnostics": []any{}})
		if err := os.WriteFile(filepath.Join(child, "manifest.json"), body, 0o644); err != nil {
			return 1, nil, err
		}
	}
	return 0, []byte("ok"), nil
}

func TestAsyncJobUsesControlledOfflineArgumentsAndIdempotency(t *testing.T) {
	cfg := handler.DefaultConfig()
	cfg.Paths.Runs = t.TempDir()
	cfg.Paths.DataRoot = t.TempDir()
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Runtime.Token = "token"
	cfg.Runtime.AtlasRevision = strings.Repeat("a", 40)
	for _, name := range []string{cfg.Runtime.DataFile, cfg.Runtime.CatalogFile} {
		target := filepath.Join(cfg.Paths.DataRoot, name)
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(target, []byte("fixture"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	server := handler.NewServer(cfg)
	runner := &runtimeRunner{root: cfg.Paths.Runs}
	server.Runner = runner
	raw, hash := packageFixture(t, "wadepre-sevir", nil)
	body, _ := json.Marshal(map[string]any{"package": raw, "sha256": hash})
	submit := func(key string) handler.Job {
		req := httptest.NewRequest("POST", "http://127.0.0.1/v1/jobs", strings.NewReader(string(body)))
		req.Header.Set("Authorization", "Bearer token")
		req.Header.Set("Idempotency-Key", key)
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, req)
		if response.Code != 200 && response.Code != 202 {
			t.Fatalf("submit: %d %s", response.Code, response.Body.String())
		}
		var job handler.Job
		if err := json.Unmarshal(response.Body.Bytes(), &job); err != nil {
			t.Fatal(err)
		}
		return job
	}
	first := submit("first")
	duplicate := submit("first")
	if first.ID != duplicate.ID {
		t.Fatal("duplicate created another job")
	}
	deadline := time.Now().Add(time.Second)
	for {
		req := httptest.NewRequest("GET", "http://127.0.0.1/v1/jobs/"+first.ID, nil)
		req.Header.Set("Authorization", "Bearer token")
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, req)
		var job handler.Job
		_ = json.Unmarshal(response.Body.Bytes(), &job)
		if job.Status == "ready" {
			break
		}
		if job.Status == "failed" || time.Now().After(deadline) {
			t.Fatalf("job not completed: %+v", job)
		}
		time.Sleep(time.Millisecond)
	}
	server = handler.NewServer(cfg)
	server.Runner = runner
	if repeated := submit("first"); repeated.ID != first.ID {
		t.Fatal("restart lost idempotency")
	}
	runner.mu.Lock()
	defer runner.mu.Unlock()
	joined := ""
	for _, call := range runner.calls {
		joined += strings.Join(call, " ")
	}
	if len(runner.calls) != 4 || strings.Contains(joined, "diagnostics") {
		t.Fatalf("calls=%v", runner.calls)
	}
	for _, stage := range []string{"ingestion", "wadepre"} {
		raw, err := os.ReadFile(filepath.Join(cfg.Paths.Runs, first.ID, "compose-"+stage+".yaml"))
		if err != nil {
			t.Fatal(err)
		}
		var compose struct {
			Services map[string]struct {
				Network  string   `yaml:"network_mode"`
				ReadOnly bool     `yaml:"read_only"`
				User     string   `yaml:"user"`
				Volumes  []string `yaml:"volumes"`
			} `yaml:"services"`
		}
		if err := yaml.Unmarshal(raw, &compose); err != nil {
			t.Fatal(err)
		}
		service := compose.Services["task"]
		if service.Network != "none" || !service.ReadOnly || (runtime.GOOS != "windows" && service.User == "") {
			t.Fatalf("unsafe compose: %s", raw)
		}
		if stage == "wadepre" && !strings.Contains(strings.Join(service.Volumes, " "), "/weights/model.onnx:ro") {
			t.Fatal("weights not read-only")
		}
	}
}
