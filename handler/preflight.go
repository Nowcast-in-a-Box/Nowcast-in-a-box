package handler

import (
	"context"
	"os/exec"
	"strings"
	"time"
)

// Check is one preflight result. Later matcher, data-root, and Compose
// checks append to Server.Preflight using the same shape.
type Check struct {
	ID      string `json:"id"`
	OK      bool   `json:"ok"`
	Message string `json:"message"`
}

// DockerCheck reports whether the Docker CLI can reach a daemon.
func DockerCheck() Check {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	path, err := exec.LookPath("docker")
	if err != nil {
		return Check{ID: "docker", OK: false, Message: "docker executable not found"}
	}
	cmd := exec.CommandContext(ctx, path, "version", "--format", "{{.Server.Version}}")
	out, err := cmd.Output()
	if err != nil {
		return Check{ID: "docker", OK: false, Message: "docker daemon unavailable"}
	}
	version := strings.TrimSpace(string(out))
	if version == "" {
		return Check{ID: "docker", OK: false, Message: "docker daemon unavailable"}
	}
	return Check{ID: "docker", OK: true, Message: version}
}
