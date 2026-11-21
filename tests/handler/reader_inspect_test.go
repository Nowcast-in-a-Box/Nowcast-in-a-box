package handler_test

import (
	"context"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

type inspectRunner func(...string) (int, []byte, error)

func (f inspectRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	if _, ok := ctx.Deadline(); !ok {
		panic("missing inspect deadline")
	}
	return f(args...)
}

func TestReaderInspect(t *testing.T) {
	root := t.TempDir()
	data := t.TempDir()
	for path, content := range map[string]string{"atlas/runtime/adapters-catalog.json": `{"data_adapters":[{"id":"reader"}]}`, "adapters/reader_probe/Dockerfile": "FROM scratch", "adapters/reader_probe/Dockerfile.dockerignore": "**\n!adapters/reader_probe/Dockerfile\n"} {
		p := filepath.Join(root, path)
		if err := os.MkdirAll(filepath.Dir(p), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	var cfg handler.Config
	cfg.Paths.Resources = root
	cfg.DataRoots = map[string]string{"reader": data}
	s := handler.NewServer(cfg)
	routes := s.Routes()
	request := func(id, body, token string) *httptest.ResponseRecorder {
		r := httptest.NewRequest("POST", "http://127.0.0.1/v1/datasets/"+id+"/inspect", strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		routes.ServeHTTP(w, r)
		return w
	}
	const image = "nib-reader-inspect:local-v2"
	var calls []string
	present := false
	s.Runner = inspectRunner(func(args ...string) (int, []byte, error) {
		calls = append(calls, args[0])
		switch args[0] {
		case "image":
			if present {
				return 0, []byte("id"), nil
			}
			return 0, nil, nil
		case "build":
			if !reflect.DeepEqual(args, []string{"build", "--load", "--file", filepath.Join(root, "adapters", "reader_probe", "Dockerfile"), "--tag", image, root}) {
				t.Fatal(args)
			}
			present = true
			return 0, nil, nil
		case "run":
			if !reflect.DeepEqual(args, []string{"run", "--rm", "--pull=never", "--network", "none", "--read-only", "--tmpfs", "/tmp", "--mount", "type=bind,src=" + data + ",dst=/inputs/data,readonly", image, "--dataset", "reader", "--data", "/inputs/data"}) {
				t.Fatal(args)
			}
			return 0, []byte(`{"sample":"frame"}`), nil
		}
		panic(args)
	})
	for _, v := range [][3]string{{"unknown", "", s.Token}, {"reader", "{}", s.Token}, {"reader", "", "wrong"}} {
		if w := request(v[0], v[1], v[2]); w.Code < 400 {
			t.Fatal(w)
		}
	}
	if len(calls) != 0 {
		t.Fatal(calls)
	}
	for i := 0; i < 2; i++ {
		if w := request("reader", "", s.Token); w.Code != 200 || !strings.Contains(w.Body.String(), `"frame"`) {
			t.Fatal(w)
		}
	}
	if strings.Join(calls, " ") != "image build run image run" {
		t.Fatal(calls)
	}
	s.Runner = inspectRunner(func(args ...string) (int, []byte, error) {
		if args[0] != "image" {
			t.Fatal(args)
		}
		return -1, nil, fmt.Errorf("docker unavailable")
	})
	if w := request("reader", "", s.Token); w.Code != 503 || !strings.Contains(w.Body.String(), "docker unavailable") {
		t.Fatal(w)
	}
}
