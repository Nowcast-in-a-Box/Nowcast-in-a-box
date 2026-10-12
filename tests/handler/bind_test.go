package handler_test

import (
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestRequireLoopbackHost(t *testing.T) {
	for _, host := range []string{"127.0.0.1", "localhost", "::1"} {
		if err := handler.RequireLoopbackHost(host); err != nil {
			t.Fatalf("%s: %v", host, err)
		}
	}
	for _, host := range []string{"0.0.0.0", "8.8.8.8"} {
		if err := handler.RequireLoopbackHost(host); err == nil {
			t.Fatalf("expected %s to be rejected", host)
		}
	}
}

func TestLoopbackRequestHost(t *testing.T) {
	loopback := []string{"127.0.0.1:8765", "localhost:8765", "[::1]:8765"}
	for _, host := range loopback {
		if !handler.IsLoopbackRequestHost(host) {
			t.Fatalf("%s should be loopback", host)
		}
	}
	if handler.IsLoopbackRequestHost("example.com:8765") {
		t.Fatal("example.com should not be loopback")
	}
	if handler.IsLoopbackRequestHost("0.0.0.0:8765") {
		t.Fatal("0.0.0.0 should not be loopback")
	}
}
