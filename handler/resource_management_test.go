package handler

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func managementServer(t *testing.T) (*Server, *imageTestRunner) {
	t.Helper()
	runner := &imageTestRunner{present: map[string]bool{}}
	s := &Server{Runner: runner, Config: Config{Source: filepath.Join(t.TempDir(), "config.yaml"), Runtime: Runtime{ImageRegistry: "registry.example/nib"}}, Resources: &Resources{
		Data:        map[string]*DataResource{"data": {ID: "data", Container: ResourceContainer{Image: "data:test"}}},
		Models:      map[string]map[string]*ModelResource{"data": {"model": {ID: "model", Container: ResourceContainer{Image: "model:test"}, Weights: ModelWeights{File: "weight.bin", SHA256: fmt.Sprintf("%x", sha256.Sum256([]byte("weight")))}}}},
		Diagnostics: &DiagnosticsResource{Container: ResourceContainer{Image: "diagnostics:test"}},
	}}
	s.Config.Paths.Artifacts = t.TempDir()
	s.Config.Paths.WeightSource = t.TempDir()
	source := filepath.Join(s.Config.Paths.WeightSource, "data", "model")
	if err := os.MkdirAll(source, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "weight.bin"), []byte("weight"), 0600); err != nil {
		t.Fatal(err)
	}
	return s, runner
}

func TestResourceManagementPreparePullsAndVerifies(t *testing.T) {
	s, r := managementServer(t)
	s.executionDataID = "data"
	s.currentResources = s.pendingResources("data", []string{"model"})
	s.preparingResources = true
	s.resourcesLoaded = true
	s.prepareExecution("data", []string{"model"})
	if s.preparingResources || s.currentResources.Execution.Status != "ready" || s.currentResources.Diagnostics.Status != "ready" {
		t.Fatalf("snapshot: %+v", s.currentResources)
	}
	for _, image := range []string{"data:test", "model:test", "diagnostics:test"} {
		if !r.present[image] {
			t.Fatalf("not pulled: %s", image)
		}
	}
	if err := verifyWeight(filepath.Join(s.Config.Paths.Artifacts, "data", "model", "weight.bin"), s.Resources.Models["data"]["model"].Weights.SHA256); err != nil {
		t.Fatal(err)
	}
	calls := len(r.calls)
	if got := s.resourcesSnapshot("data"); got.Execution.Status != "prepared" {
		t.Fatalf("stale ready: %+v", got)
	}
	if len(r.calls) != calls {
		t.Fatal("ordinary snapshot used Docker")
	}
	r.present = map[string]bool{}
	if got := s.inspectResources(context.Background(), "data"); got.Diagnostics.Status != "missing" {
		t.Fatalf("stale daemon: %+v", got)
	}
}

func TestResourceManagementRetainsFailureAndFastPreparing(t *testing.T) {
	s, r := managementServer(t)
	r.failPull = map[string]bool{"registry.example/nib/data:test": true}
	s.executionDataID = "data"
	s.currentResources = s.pendingResources("data", []string{"model"})
	s.preparingResources = true
	s.resourcesLoaded = true
	s.inspectResources(context.Background(), "data")
	if len(r.calls) != 0 {
		t.Fatal("preparing refresh used Docker")
	}
	s.prepareExecution("data", []string{"model"})
	if got := s.resourcesSnapshot("data"); got.Execution.Status != "failed" || !strings.Contains(got.Execution.Message, "could not pull") {
		t.Fatalf("lost error: %+v", got)
	}
}

func TestResourceManagementSettingsAtomicAndBusy(t *testing.T) {
	s, _ := managementServer(t)
	old := map[string]string{"data": "old"}
	s.Config.DataRoots = old
	patch := func(body string) int {
		w := httptest.NewRecorder()
		s.handleSettings(w, httptest.NewRequest("PATCH", "/v1/settings", strings.NewReader(body)))
		return w.Code
	}
	if code := patch(`{"image_registry":"registry.example/new/","image_mirror":"mirror.example/nib","image_source":"mirror"}`); code != 200 {
		t.Fatal(code)
	}
	cfg := s.Config
	if err := cfg.applySettings(); err != nil {
		t.Fatal(err)
	}
	if cfg.Runtime.ImageRegistry != "registry.example/new" || cfg.Runtime.ImageMirror != "mirror.example/nib" || cfg.Runtime.ImageSource != "mirror" {
		t.Fatalf("not persisted: %+v", cfg.Runtime)
	}
	s.Config.DataRoots["data"] = "new"
	if old["data"] != "old" {
		t.Fatal("config map aliased")
	}
	if code := patch(`{"image_registry":"$(touch nope)"}`); code != 422 {
		t.Fatal(code)
	}
	for _, busy := range []string{"preparing", "stopping", "execution", "image"} {
		s.preparingResources = busy == "preparing"
		s.stopping = busy == "stopping"
		if busy == "execution" {
			s.executionMu.Lock()
		}
		if busy == "image" {
			s.imageMu.Lock()
		}
		code := patch(`{"image_registry":"registry.example/other"}`)
		if busy == "execution" {
			s.executionMu.Unlock()
		}
		if busy == "image" {
			s.imageMu.Unlock()
		}
		if code != 409 {
			t.Fatalf("%s: %d", busy, code)
		}
	}
}

func TestResourceManagementSynchronizesPulls(t *testing.T) {
	s, r := managementServer(t)
	var wg sync.WaitGroup
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := s.ensureImage(context.Background(), "data:test"); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	count := 0
	for _, call := range r.calls {
		if strings.HasPrefix(call, "pull ") {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("pull count %d", count)
	}
}

func TestResourceManagementBadCopyPreservesTarget(t *testing.T) {
	s, _ := managementServer(t)
	target := filepath.Join(s.Config.Paths.Artifacts, "data", "model", "weight.bin")
	if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	s.Resources.Models["data"]["model"].Weights.SHA256 = strings.Repeat("0", 64)
	if err := s.prepareModelWeight(context.Background(), "data", "model"); err == nil {
		t.Fatal("accepted bad checksum")
	}
	raw, err := os.ReadFile(target)
	if err != nil || string(raw) != "original" {
		t.Fatalf("target changed: %q %v", raw, err)
	}
}

func TestResourceManagementCanceledPreparation(t *testing.T) {
	s, r := managementServer(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	s.serviceCtx = ctx
	s.executionDataID = "data"
	s.currentResources = s.pendingResources("data", []string{"model"})
	s.preparingResources = true
	s.resourcesLoaded = true
	s.prepareExecution("data", []string{"model"})
	if s.preparingResources || s.currentResources.Execution.Status != "failed" {
		t.Fatalf("cancellation lost: %+v", s.currentResources)
	}
	if len(r.calls) != 0 {
		t.Fatalf("commands after shutdown: %v", r.calls)
	}
}

func TestResourceManagementImagePrefixes(t *testing.T) {
	for _, value := range []string{"", "ghcr.io/team", "localhost:5000/team", "mirror.example/team/sub-team"} {
		if _, err := normalizeImagePrefix(value); err != nil {
			t.Errorf("rejected %q: %v", value, err)
		}
	}
	for _, value := range []string{"https://ghcr.io/team", "user:password@host", "host/team:tag", "host/team;touch file", "host/../team", "-host/team", "host/team\nother"} {
		if _, err := normalizeImagePrefix(value); err == nil {
			t.Errorf("accepted %q", value)
		}
	}
}
