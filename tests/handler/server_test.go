package handler_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

type statusBody struct {
	Service    string          `json:"service"`
	Version    string          `json:"version"`
	Host       string          `json:"host"`
	Port       string          `json:"port"`
	ConfigFile string          `json:"config_file"`
	Review     handler.Addr    `json:"review"`
	Paths      handler.Paths   `json:"paths"`
	Docker     handler.Check   `json:"docker"`
	Preflight  []handler.Check `json:"preflight"`
}

func TestHealth(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8765/health", nil)
	rec := httptest.NewRecorder()
	testServer().Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d", rec.Code)
	}
	var body map[string]string
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body["status"] != "ok" {
		t.Fatalf("body=%v", body)
	}
}

func TestStatusIncludesDockerCheck(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8765/v1/status", nil)
	rec := httptest.NewRecorder()
	testServer().Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d", rec.Code)
	}
	var body statusBody
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body.Service != "nib-handler" {
		t.Fatalf("service=%q", body.Service)
	}
	if body.Port != "8765" {
		t.Fatalf("port=%q", body.Port)
	}
	if body.ConfigFile != "/tmp/config.yaml" {
		t.Fatalf("config_file=%q", body.ConfigFile)
	}
	if body.Docker.ID != "docker" || !body.Docker.OK || body.Docker.Message != "test" {
		t.Fatalf("docker=%+v", body.Docker)
	}
	if body.Review.Port != 8766 {
		t.Fatalf("review.port=%d", body.Review.Port)
	}
	if len(body.Preflight) != 1 {
		t.Fatalf("preflight=%v", body.Preflight)
	}
	if body.Preflight[0] != body.Docker {
		t.Fatalf("preflight[0]=%+v docker=%+v", body.Preflight[0], body.Docker)
	}
}

func TestStatusDefaultsSource(t *testing.T) {
	srv := testServer()
	srv.Config.Source = ""
	req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8765/v1/status", nil)
	rec := httptest.NewRecorder()
	srv.Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d", rec.Code)
	}
	var body statusBody
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body.ConfigFile != "defaults" {
		t.Fatalf("config_file=%q", body.ConfigFile)
	}
}

func TestNonLoopbackHostRejected(t *testing.T) {
	for _, host := range []string{"example.com", "0.0.0.0:8765"} {
		req := httptest.NewRequest(http.MethodGet, "http://example.com/health", nil)
		req.Host = host
		rec := httptest.NewRecorder()
		testServer().Routes().ServeHTTP(rec, req)
		if rec.Code != http.StatusForbidden {
			t.Fatalf("host %q status=%d", host, rec.Code)
		}
	}
}
