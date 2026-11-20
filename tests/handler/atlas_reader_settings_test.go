package handler_test

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestAtlasReaderSettings(t *testing.T) {
	t.Setenv("NIB_DATA_ROOT", "")
	t.Setenv("NIB_IMAGE_OVERRIDE", "")
	dir := t.TempDir()
	catalog := filepath.Join(dir, "atlas", "runtime")
	if err := os.MkdirAll(catalog, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(catalog, "adapters-catalog.json"), []byte(`{"data_adapters":[{"id":"one","title":"First reader"},{"id":"two","title":"Second reader"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(source, []byte("runtime:\n  image_override: registry.example/config\n"), 0600); err != nil {
		t.Fatal(err)
	}
	load := func() *handler.Server {
		t.Helper()
		cfg, err := handler.Load(source)
		if err != nil {
			t.Fatal(err)
		}
		return handler.NewServer(cfg)
	}
	server := load()
	type response struct {
		Datasets []struct{ ID, Title string }
		Roots    map[string]string `json:"data_roots"`
		Override *string           `json:"image_override"`
	}
	request := func(method, body string, code int) response {
		t.Helper()
		req := httptest.NewRequest(method, "http://127.0.0.1/v1/settings", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+server.Token)
		w := httptest.NewRecorder()
		server.Routes().ServeHTTP(w, req)
		if w.Code != code {
			t.Fatalf("%s %s: %d %s", method, body, w.Code, w.Body.String())
		}
		var got response
		if code == 200 {
			if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
		}
		return got
	}
	initial := request("GET", "", 200)
	if len(initial.Datasets) != 2 || initial.Datasets[0].ID != "one" || initial.Datasets[1].Title != "Second reader" || initial.Override == nil || *initial.Override != "registry.example/config" {
		t.Fatalf("initial: %+v", initial)
	}
	request("PATCH", fmt.Sprintf(`{"data_id":"one","data_root":%q,"image_override":"registry.example/local"}`, dir), 200)
	request("PATCH", fmt.Sprintf(`{"data_id":"two","data_root":%q}`, catalog), 200)
	server = load()
	got := request("GET", "", 200)
	if got.Roots["one"] != dir || got.Roots["two"] != catalog || got.Override == nil || *got.Override != "registry.example/local" {
		t.Fatalf("saved: %+v", got)
	}
	request("PATCH", `{"data_id":"one"}`, 400)
	bad := filepath.Join(dir, "bad,path")
	if err := os.Mkdir(bad, 0700); err != nil {
		t.Fatal(err)
	}
	request("PATCH", fmt.Sprintf(`{"data_id":"one","data_root":%q}`, bad), 422)
	request("PATCH", `{"data_id":"one","data_root":"","image_override":""}`, 200)
	server = load()
	got = request("GET", "", 200)
	if len(got.Roots) != 1 || got.Roots["two"] != catalog || got.Override == nil || *got.Override != "" {
		t.Fatalf("cleared: %+v", got)
	}
}
