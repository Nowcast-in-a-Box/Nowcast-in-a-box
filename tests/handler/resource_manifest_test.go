package handler_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestPackagedResourcesDeclareEveryDataAndModel(t *testing.T) {
	resources := testResources(t)
	for product, dataID := range map[string]string{"sevir-vil": "sevir", "cma-radar": "cma_radar"} {
		data, ok := resources.DataByProductID(product)
		if !ok || data.ID != dataID {
			t.Fatalf("product %s -> %+v", product, data)
		}
	}
	for product, modelID := range map[string]string{"nowcastnet-sevir": "nowcastnet", "exprecast-sevir": "exprecast", "wadepre-sevir": "wadepre", "nowcastnet-cma": "nowcastnet"} {
		dataID := "sevir"
		if strings.HasSuffix(product, "-cma") {
			dataID = "cma_radar"
		}
		model, ok := resources.ModelByProductID(dataID, product)
		if !ok || model.ID != modelID {
			t.Fatalf("product %s -> %+v", product, model)
		}
	}
	if resources.Diagnostics == nil || resources.Diagnostics.Container.Image != "nib-diagnostics:automatic-flow" {
		t.Fatalf("diagnostics = %+v", resources.Diagnostics)
	}
	if sevir, _ := resources.DataByID("sevir"); sevir.Verification.LevelMax != 255 || sevir.Data.Catalog == "" {
		t.Fatalf("sevir = %+v", sevir)
	}
	if cma, _ := resources.DataByID("cma_radar"); cma.Verification.LevelMax != 75 || cma.Data.Kind != "directory" {
		t.Fatalf("cma = %+v", cma)
	}
}

func TestLoadResourcesRejectsInvalidManifest(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "adapters", "sevir")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := "schema: nib.resource.v0\nkind: data\nid: sevir\nproduct_id: sevir-vil\nname: SEVIR\n"
	if err := os.WriteFile(filepath.Join(dir, "runtime.yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := handler.LoadResources(root); err == nil || !strings.Contains(err.Error(), "schema") {
		t.Fatalf("expected schema error, got %v", err)
	}
}
