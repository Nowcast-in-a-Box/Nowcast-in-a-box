package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func testServer() *server {
	return &server{cfg: Config{
		Handler: Addr{Host: "127.0.0.1", Port: 8765},
		Review:  Addr{Host: "127.0.0.1", Port: 8766},
		Paths:   Paths{Artifacts: "/tmp/artifacts"},
		Source:  "/tmp/config.yaml",
	}}
}

func TestHealth(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8765/health", nil)
	rec := httptest.NewRecorder()
	testServer().routes().ServeHTTP(rec, req)
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
	testServer().routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d", rec.Code)
	}
	var body map[string]any
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body["service"] != "nib-handler" {
		t.Fatalf("service=%v", body["service"])
	}
	if body["port"] != "8765" {
		t.Fatalf("port=%v", body["port"])
	}
	if body["config_file"] != "/tmp/config.yaml" {
		t.Fatalf("config_file=%v", body["config_file"])
	}
	docker, ok := body["docker"].(map[string]any)
	if !ok {
		t.Fatalf("docker field missing: %v", body)
	}
	if _, ok := docker["ok"]; !ok {
		t.Fatalf("docker.ok missing: %v", docker)
	}
	review, ok := body["review"].(map[string]any)
	if !ok {
		t.Fatalf("review field missing: %v", body)
	}
	if review["port"] != float64(8766) {
		t.Fatalf("review.port=%v", review["port"])
	}
}

func TestNonLoopbackHostRejected(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "http://example.com/health", nil)
	req.Host = "example.com"
	rec := httptest.NewRecorder()
	testServer().routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("status=%d", rec.Code)
	}
}
