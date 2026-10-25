package handler

import (
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
