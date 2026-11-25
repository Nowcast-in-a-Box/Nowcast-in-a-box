package handler

import (
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestEmbeddedWeightsRequireImageNotHostFile(t *testing.T) {
	root := t.TempDir()
	runner := &imageTestRunner{present: map[string]bool{"demo:embedded": true}}
	server := &Server{Config: Config{Paths: Paths{Artifacts: root}}, Runner: runner}
	model := &ModelResource{Weights: ModelWeights{File: "model.onnx", SHA256: "pinned", Embedded: true}, Container: ResourceContainer{Image: "demo:embedded"}}
	if item := server.weightItem("demo", "model", model); item.Status != "ready" {
		t.Fatalf("embedded weights with available image: %+v", item)
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != 0 {
		t.Fatalf("host weight cache changed: %v %v", entries, err)
	}
	runner.present["demo:embedded"] = false
	if item := server.weightItem("demo", "model", model); item.Status != "missing" {
		t.Fatalf("missing image cannot supply weights: %+v", item)
	}
}

func TestExternalWeightsStillRequireMatchingHostChecksum(t *testing.T) {
	resources, err := LoadResources("..")
	if err != nil {
		t.Fatal(err)
	}
	model, _ := resources.ModelByProductID("sevir", "nowcastnet-sevir")
	copyModel := *model
	contents := []byte("pinned model bytes")
	copyModel.Weights.SHA256 = fmt.Sprintf("%x", sha256.Sum256(contents))
	root := t.TempDir()
	server := &Server{Config: Config{Paths: Paths{Artifacts: root}}, Resources: resources}
	if item := server.weightItem("sevir", "nowcastnet", &copyModel); item.Status != "missing" {
		t.Fatalf("missing external weight: %+v", item)
	}
	path := filepath.Join(root, "sevir", "nowcastnet", model.Weights.File)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, contents, 0600); err != nil {
		t.Fatal(err)
	}
	if item := server.weightItem("sevir", "nowcastnet", &copyModel); item.Status != "ready" {
		t.Fatalf("valid external weight: %+v", item)
	}
	if err := os.WriteFile(path, []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	if item := server.weightItem("sevir", "nowcastnet", &copyModel); item.Status != "missing" {
		t.Fatalf("corrupt external weight accepted: %+v", item)
	}
}

type noDockerRunner struct{ t *testing.T }

func (r noDockerRunner) Run(context.Context, ...string) (int, []byte, error) {
	r.t.Fatal("weightless model invoked Docker")
	return 1, nil, nil
}

func TestWeightlessModelSkipsImageAndHashVerification(t *testing.T) {
	resources, err := LoadResources("..")
	if err != nil {
		t.Fatal(err)
	}
	server := &Server{
		Config:    Config{Paths: Paths{Artifacts: t.TempDir()}},
		Resources: resources,
		Runner:    noDockerRunner{t},
	}
	model, ok := resources.ModelByProductID("cma_radar", "pysteps-lk-cma")
	if !ok {
		t.Fatal("pysteps-lk-cma model not found")
	}
	if item := server.weightItem("cma_radar", "pysteps_lk", model); item.Status != "ready" || item.Message != "no separate weights required" {
		t.Fatalf("weightless item: %+v", item)
	}
	if err := server.prepareModelWeight(context.Background(), "cma_radar", "pysteps_lk"); err != nil {
		t.Fatalf("weightless preparation: %v", err)
	}
}
