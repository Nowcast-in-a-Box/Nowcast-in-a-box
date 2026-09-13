package main

import "testing"

func TestRequireLoopbackHost(t *testing.T) {
	if err := requireLoopbackHost("127.0.0.1"); err != nil {
		t.Fatalf("127.0.0.1: %v", err)
	}
	if err := requireLoopbackHost("localhost"); err != nil {
		t.Fatalf("localhost: %v", err)
	}
	if err := requireLoopbackHost("::1"); err != nil {
		t.Fatalf("::1: %v", err)
	}
	if err := requireLoopbackHost("0.0.0.0"); err == nil {
		t.Fatal("expected 0.0.0.0 to be rejected")
	}
	if err := requireLoopbackHost("8.8.8.8"); err == nil {
		t.Fatal("expected 8.8.8.8 to be rejected")
	}
}

func TestLoopbackRequestHost(t *testing.T) {
	if !isLoopbackRequestHost("127.0.0.1:8765") {
		t.Fatal("127.0.0.1:8765 should be loopback")
	}
	if !isLoopbackRequestHost("localhost:8765") {
		t.Fatal("localhost:8765 should be loopback")
	}
	if !isLoopbackRequestHost("[::1]:8765") {
		t.Fatal("[::1]:8765 should be loopback")
	}
	if isLoopbackRequestHost("example.com:8765") {
		t.Fatal("example.com should not be loopback")
	}
}
