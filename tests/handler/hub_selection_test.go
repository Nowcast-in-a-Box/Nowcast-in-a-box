package handler_test

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestImportHubSelection(t *testing.T) {
	cfg := consoleConfig(t)
	s := handler.NewServer(cfg)
	routes := s.Routes()
	raw, err := os.ReadFile(filepath.Join(testResourcesPath(t), "atlas", "examples", "hub-selection-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	fixture := string(raw)
	post := func(body, token string) *httptest.ResponseRecorder {
		r := httptest.NewRequest("POST", "http://127.0.0.1/v1/orders/import-selection", strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		routes.ServeHTTP(w, r)
		return w
	}
	w := post(fixture, s.Token)
	if w.Code != 200 {
		t.Fatalf("import: %d %s", w.Code, w.Body.String())
	}
	var response struct{ Order handler.Order }
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	o := response.Order
	if o.SchemaVersion != "nib.order.v1" || o.DataID != "sevir" || !reflect.DeepEqual(o.Models, []string{"nowcastnet", "exprecast"}) || len(o.SampleStarts) != 0 || o.EventID != "" || o.Verification != nil || o.Visualization != nil {
		t.Fatalf("unexpected draft: %+v", o)
	}
	cmaRaw, err := os.ReadFile(filepath.Join("fixtures", "hub-cma-selection-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	w = post(string(cmaRaw), s.Token)
	if w.Code != 200 {
		t.Fatalf("CMA import: %d %s", w.Code, w.Body.String())
	}
	var cmaResponse struct{ Order handler.Order }
	if err := json.Unmarshal(w.Body.Bytes(), &cmaResponse); err != nil {
		t.Fatal(err)
	}
	cmaOrder := cmaResponse.Order
	if cmaOrder.SchemaVersion != "nib.order.v1" || cmaOrder.DataID != "cma_radar" || !reflect.DeepEqual(cmaOrder.Models, []string{"nowcastnet"}) || cmaOrder.Verification == nil || len(cmaOrder.Verification.Methods) == 0 || !reflect.DeepEqual(cmaOrder.Verification.Models, cmaOrder.Models) || cmaOrder.Visualization == nil || !reflect.DeepEqual(cmaOrder.Visualization.Models, cmaOrder.Models) {
		t.Fatalf("unexpected CMA draft: %+v", cmaOrder)
	}
	for name, body := range map[string]string{"unknown schema": strings.Replace(fixture, "nib-hub-selection-v1", "v3", 1), "unknown version": strings.Replace(fixture, `"v2"`, `"v9"`, 1), "mixed versions": strings.Replace(fixture, `"v2"`, `"v1"`, 1), "path": strings.Replace(fixture, "catalog/configuration/sevir-vil-configuration/v1.json", "../untrusted", 1), "config identity": strings.Replace(fixture, "sevir-vil-configuration", "other", 1), "unknown field": strings.Replace(fixture, `"items":`, `"command":"run", "items":`, 1), "duplicate key": strings.Replace(fixture, `"schema_version":`, `"schema_version":"bad", "schema_version":`, 1), "trailing document": fixture + "{}"} {
		t.Run(name, func(t *testing.T) {
			if w := post(body, s.Token); w.Code != 400 {
				t.Fatalf("status %d: %s", w.Code, w.Body.String())
			}
		})
	}
	if w := post(fixture, "invalid"); w.Code == 200 {
		t.Fatal("accepted unauthorized import")
	}
	if w := post(fixture+strings.Repeat(" ", 1<<20), s.Token); w.Code != 413 {
		t.Fatalf("oversized status %d", w.Code)
	}
	entries, err := os.ReadDir(cfg.Paths.Orders)
	if err != nil || len(entries) != 0 {
		t.Fatalf("orders after import: %v, %v", entries, err)
	}
}
