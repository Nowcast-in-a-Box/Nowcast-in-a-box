package main

import (
	"context"
	"os/exec"
	"strings"
	"time"
)

type check struct {
	ID      string `json:"id"`
	OK      bool   `json:"ok"`
	Message string `json:"message"`
}

func dockerCheck() check {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	path, err := exec.LookPath("docker")
	if err != nil {
		return check{ID: "docker", OK: false, Message: "docker executable not found"}
	}
	cmd := exec.CommandContext(ctx, path, "version", "--format", "{{.Server.Version}}")
	out, err := cmd.Output()
	if err != nil {
		return check{ID: "docker", OK: false, Message: "docker daemon unavailable"}
	}
	version := strings.TrimSpace(string(out))
	if version == "" {
		return check{ID: "docker", OK: false, Message: "docker daemon unavailable"}
	}
	return check{ID: "docker", OK: true, Message: version}
}
