package handler_test

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

type cmaSampleRunner struct{ calls *[][]string }

func (runner cmaSampleRunner) Run(_ context.Context, args ...string) (int, []byte, error) {
	*runner.calls = append(*runner.calls, args)
	return 0, []byte(`{"data_id":"cma_radar","samples":[],"total":0}`), nil
}

func TestCMASamplesUseControlledReadOnlyDirectory(t *testing.T) {
	cfg := testConfig(t)
	cfg.Runtime.Token = "test-token"
	writeCmaData(t, &cfg)
	server := handler.NewServer(cfg)
	calls := [][]string{}
	server.Runner = cmaSampleRunner{&calls}
	req := httptest.NewRequest("GET", "http://127.0.0.1/v1/datasets/cma_radar/samples?limit=20&day=20260702", nil)
	req.Header.Set("Authorization", "Bearer test-token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, req)
	if response.Code != 200 {
		t.Fatal(response.Code, response.Body.String())
	}
	if len(calls) != 1 {
		t.Fatal(calls)
	}
	args := strings.Join(calls[0], " ")
	if !strings.Contains(args, "--dataset cma_radar") || !strings.Contains(args, "--day 20260702") || !strings.Contains(args, ":/inputs/data:ro") || strings.Contains(args, "CATALOG.csv") || strings.Contains(args, "data.h5") {
		t.Fatal(args)
	}
	req = httptest.NewRequest("GET", "http://127.0.0.1/v1/datasets/sevir/samples", nil)
	req.Header.Set("Authorization", "Bearer test-token")
	response = httptest.NewRecorder()
	server.Routes().ServeHTTP(response, req)
	if response.Code != 409 || len(calls) != 1 {
		t.Fatal(response.Code, calls)
	}
}
