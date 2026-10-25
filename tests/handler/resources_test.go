package handler_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

type resourceRunner struct {
	mu      sync.Mutex
	present map[string]bool
	calls   []string
}

func (r *resourceRunner) Run(_ context.Context, args ...string) (int, []byte, error) {
	r.mu.Lock()
	r.calls = append(r.calls, strings.Join(args, " "))
	key := ""
	if len(args) >= 3 && args[0] == "image" && args[1] == "inspect" {
		key = args[2]
	}
	present := r.present[key]
	r.mu.Unlock()
	if len(args) >= 2 && args[0] == "image" && args[1] == "inspect" {
		if present {
			return 0, []byte("ok"), nil
		}
		return 1, []byte("not found"), nil
	}
	if len(args) > 0 && args[0] == "pull" {
		return 1, []byte("pull is not used by this Handler"), nil
	}
	return 0, []byte("{}"), nil
}

func (r *resourceRunner) pulled() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, call := range r.calls {
		if strings.HasPrefix(call, "pull ") {
			return true
		}
	}
	return false
}

func resourceServer(t *testing.T, cfg handler.Config) *handler.Server {
	t.Helper()
	cfg.Runtime.Token = "token"
	return handler.NewServer(cfg)
}

func externalWeightServer(t *testing.T, cfg handler.Config) *handler.Server {
	t.Helper()
	server := resourceServer(t, cfg)
	model, ok := server.Resources.ModelByProductID("cma_radar", "nowcastnet-cma")
	if !ok {
		t.Fatal("missing CMA model")
	}
	model.Weights.Embedded = false
	model.Container.Image = "nib-nowcastnet:cma-demo"
	return server
}

func resourceGet(t *testing.T, server *handler.Server, path string) map[string]any {
	t.Helper()
	request := httptest.NewRequest("GET", "http://127.0.0.1"+path, nil)
	request.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	if response.Code != 200 {
		t.Fatalf("status %d %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	return payload
}

func resourcePrepare(t *testing.T, server *handler.Server, body string) map[string]any {
	t.Helper()
	request := httptest.NewRequest("POST", "http://127.0.0.1/v1/resources/prepare", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	if response.Code != 200 {
		t.Fatalf("status %d %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	return payload
}

func executionGroup(snapshot map[string]any) map[string]any {
	group, _ := snapshot["execution"].(map[string]any)
	return group
}

func waitForExecution(t *testing.T, server *handler.Server, status string) map[string]any {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		snapshot := resourceGet(t, server, "/v1/resources")
		group := executionGroup(snapshot)
		if group["status"] == status {
			return snapshot
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("execution did not reach %s: %s", status, mustJSON(t, resourceGet(t, server, "/v1/resources")))
	return nil
}

func TestResourcesReportMissingImagesWithoutPulling(t *testing.T) {
	server := resourceServer(t, testConfig(t))
	server.Runner = &resourceRunner{}
	server.RefreshResources()
	snapshot := resourceGet(t, server, "/v1/resources")
	diagnostics := snapshot["diagnostics"].(map[string]any)
	if diagnostics["status"] != "missing" {
		t.Fatalf("diagnostics status = %v", diagnostics["status"])
	}
	if !strings.Contains(diagnostics["message"].(string), "is not present locally") {
		t.Fatalf("diagnostics message = %v", diagnostics["message"])
	}
	if server.Runner.(*resourceRunner).pulled() {
		t.Fatal("resources must not pull images")
	}
}

func TestResourcesPrepareCopiesMissingWeightFromLocalSource(t *testing.T) {
	source := filepath.Join("..", "..", "artifacts")
	if _, err := os.Stat(filepath.Join(source, "cma_radar", "nowcastnet", "NowcastNet.onnx")); err != nil {
		t.Skipf("real pinned weight is not available: %v", err)
	}
	cfg := testConfig(t)
	writeCmaData(t, &cfg)
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Paths.WeightSource, _ = filepath.Abs(source)
	server := externalWeightServer(t, cfg)
	server.Runner = &resourceRunner{present: map[string]bool{server.Resources.Diagnostics.Container.Image: true, "nib-ingestion:cma-demo": true, "nib-nowcastnet:cma-demo": true}}

	resourcePrepare(t, server, `{"data_id":"cma_radar","models":["nowcastnet"]}`)
	snapshot := waitForExecution(t, server, "ready")
	group := executionGroup(snapshot)
	if group["status"] != "ready" {
		t.Fatalf("execution = %s", mustJSON(t, group))
	}
	weight := filepath.Join(cfg.Paths.Artifacts, "cma_radar", "nowcastnet", "NowcastNet.onnx")
	if _, err := os.Stat(weight); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(weight + ".partial"); !os.IsNotExist(err) {
		t.Fatal("partial weight was left behind")
	}
	if server.Runner.(*resourceRunner).pulled() {
		t.Fatal("resources must copy weights instead of pulling images")
	}
}

func TestResourcesSkipWeightCopyWhenSHAAlreadyMatches(t *testing.T) {
	artifacts, err := filepath.Abs(filepath.Join("..", "..", "artifacts"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(artifacts, "cma_radar", "nowcastnet", "NowcastNet.onnx")); err != nil {
		t.Skipf("real pinned weight is not available: %v", err)
	}
	cfg := testConfig(t)
	writeCmaData(t, &cfg)
	cfg.Paths.Artifacts = artifacts
	cfg.Paths.WeightSource = t.TempDir()
	server := externalWeightServer(t, cfg)
	server.Runner = &resourceRunner{present: map[string]bool{server.Resources.Diagnostics.Container.Image: true, "nib-ingestion:cma-demo": true, "nib-nowcastnet:cma-demo": true}}

	resourcePrepare(t, server, `{"data_id":"cma_radar","models":["nowcastnet"]}`)
	waitForExecution(t, server, "ready")
}

func TestResourcesRejectWrongSourceWeightWithoutReplacingTarget(t *testing.T) {
	cfg := testConfig(t)
	writeCmaData(t, &cfg)
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Paths.WeightSource = t.TempDir()
	source := filepath.Join(cfg.Paths.WeightSource, "cma_radar", "nowcastnet")
	if err := os.MkdirAll(source, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "NowcastNet.onnx"), []byte("not the real weight"), 0o600); err != nil {
		t.Fatal(err)
	}
	server := externalWeightServer(t, cfg)
	server.Runner = &resourceRunner{present: map[string]bool{server.Resources.Diagnostics.Container.Image: true, "nib-ingestion:cma-demo": true, "nib-nowcastnet:cma-demo": true}}

	resourcePrepare(t, server, `{"data_id":"cma_radar","models":["nowcastnet"]}`)
	snapshot := waitForExecution(t, server, "failed")
	group := executionGroup(snapshot)
	if !strings.Contains(group["message"].(string), "does not match the pinned SHA-256") {
		t.Fatalf("execution = %s", mustJSON(t, group))
	}
	target := filepath.Join(cfg.Paths.Artifacts, "cma_radar", "nowcastnet", "NowcastNet.onnx")
	if _, err := os.Stat(target); !os.IsNotExist(err) {
		t.Fatal("an unverified weight was published")
	}
	if _, err := os.Stat(target + ".partial"); !os.IsNotExist(err) {
		t.Fatal("partial weight was left behind")
	}
}

func TestResourcesExplainMissingWeightSource(t *testing.T) {
	cfg := testConfig(t)
	writeCmaData(t, &cfg)
	cfg.Paths.Artifacts = t.TempDir()
	server := externalWeightServer(t, cfg)
	server.Runner = &resourceRunner{present: map[string]bool{server.Resources.Diagnostics.Container.Image: true, "nib-ingestion:cma-demo": true, "nib-nowcastnet:cma-demo": true}}

	resourcePrepare(t, server, `{"data_id":"cma_radar","models":["nowcastnet"]}`)
	snapshot := waitForExecution(t, server, "failed")
	if !strings.Contains(mustJSON(t, snapshot), "paths.weight_source") {
		t.Fatalf("snapshot = %s", mustJSON(t, snapshot))
	}
}

func TestResourcesRejectUnconfiguredModel(t *testing.T) {
	server := resourceServer(t, testConfig(t))
	request := httptest.NewRequest("POST", "http://127.0.0.1/v1/resources/prepare", strings.NewReader(`{"data_id":"cma_radar","models":["unknown"]}`))
	request.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	if response.Code != 422 {
		t.Fatalf("status %d %s", response.Code, response.Body.String())
	}
}

func TestResourcesPrepareEmbeddedModelWithoutHostWeights(t *testing.T) {
	cfg := testConfig(t)
	writeCmaData(t, &cfg)
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Paths.WeightSource = ""
	server := resourceServer(t, cfg)
	server.Runner = &resourceRunner{present: map[string]bool{server.Resources.Diagnostics.Container.Image: true, "nib-ingestion:cma-demo": true, "nib-nowcastnet:cma-demo-embedded": true}}
	resourcePrepare(t, server, `{"data_id":"cma_radar","models":["nowcastnet"]}`)
	waitForExecution(t, server, "ready")
	if _, err := os.Stat(filepath.Join(cfg.Paths.Artifacts, "cma_radar", "nowcastnet", "NowcastNet.onnx")); !os.IsNotExist(err) {
		t.Fatalf("embedded model must not create a host weight: %v", err)
	}
}
