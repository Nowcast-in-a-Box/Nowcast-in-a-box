package handler

import (
	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler/internal/orders"

	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const readerInspectImage = "nib-reader-inspect:local-v2"

func (s *Server) handleReaderInspect(w http.ResponseWriter, r *http.Request) {
	if r.URL.RawQuery != "" || r.ContentLength != 0 || len(r.TransferEncoding) != 0 {
		runtimeError(w, 400, fmt.Errorf("inspect accepts no parameters or body"))
		return
	}
	id := r.PathValue("dataset")
	if !orders.ValidID(id) || !s.knownReader(id) {
		runtimeError(w, 404, fmt.Errorf("unknown data reader"))
		return
	}
	if _, err := s.lockSettings(); err != nil {
		runtimeError(w, 409, err)
		return
	}
	// Keep settings and image preparation serialized, without blocking status reads.
	s.resourcesMu.Unlock()
	s.jobsMu.Unlock()
	defer s.recoveryMu.Unlock()
	defer s.imageMu.Unlock()
	root := s.dataRoot(id)
	if !validReaderRoot(root) {
		runtimeError(w, 422, fmt.Errorf("save an absolute local data directory for this reader first"))
		return
	}
	if st, err := os.Stat(root); err != nil || !st.IsDir() {
		runtimeError(w, 422, fmt.Errorf("saved data directory does not exist or is inaccessible"))
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Minute)
	defer cancel()
	docker := func(args ...string) ([]byte, error) {
		code, out, err := s.docker(ctx, args...)
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if err != nil || code != 0 {
			if len(out) > 2048 {
				out = out[len(out)-2048:]
			}
			detail := strings.TrimSpace(string(out))
			if detail == "" && err != nil {
				detail = err.Error()
			}
			return nil, fmt.Errorf("docker %s failed (%d): %.2048s", args[0], code, detail)
		}
		return out, nil
	}
	local, err := docker("image", "ls", "--quiet", readerInspectImage)
	if err != nil {
		runtimeError(w, 503, err)
		return
	}
	if strings.TrimSpace(string(local)) == "" {
		resources := s.Config.Paths.Resources
		if s.Resources != nil && s.Resources.Root != "" {
			resources = s.Resources.Root
		}
		file, err := inspectDockerfile(resources)
		if err != nil {
			runtimeError(w, 503, err)
			return
		}
		if _, err = docker("build", "--load", "--file", file, "--tag", readerInspectImage, resources); err != nil {
			runtimeError(w, 502, err)
			return
		}
	}
	out, err := docker("run", "--rm", "--pull=never", "--network", "none", "--read-only", "--tmpfs", "/tmp", "--mount", "type=bind,src="+root+",dst=/inputs/data,readonly", readerInspectImage, "--dataset", id, "--data", "/inputs/data")
	if err != nil {
		runtimeError(w, 502, err)
		return
	}
	var sample struct {
		Sample string `json:"sample"`
	}
	if len(out) > 64<<10 || json.Unmarshal(out, &sample) != nil || strings.TrimSpace(sample.Sample) == "" {
		runtimeError(w, 502, fmt.Errorf("reader did not return a JSON object with a sample"))
		return
	}
	writeJSON(w, 200, json.RawMessage(out))
}

func validReaderRoot(root string) bool {
	return filepath.IsAbs(root) && !strings.ContainsAny(root, ",\"\x00\r\n") && !strings.Contains(strings.TrimPrefix(root, filepath.VolumeName(root)), ":")
}

func inspectDockerfile(root string) (string, error) {
	if !filepath.IsAbs(root) {
		return "", fmt.Errorf("inspect resources root must be absolute")
	}
	file := filepath.Join(root, "adapters", "reader_probe", "Dockerfile")
	base, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	for _, path := range []string{file, file + ".dockerignore"} {
		resolved, err := filepath.EvalSymlinks(path)
		if err != nil {
			return "", err
		}
		rel, err := filepath.Rel(base, resolved)
		if err != nil || !filepath.IsLocal(rel) {
			return "", fmt.Errorf("inspect build files must be inside the resources root")
		}
		info, err := os.Stat(resolved)
		if err != nil || !info.Mode().IsRegular() {
			return "", fmt.Errorf("inspect build file missing: %s", path)
		}
	}
	f, err := os.Open(file + ".dockerignore")
	if err != nil {
		return "", err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, 16385))
	if err != nil || len(raw) > 16384 {
		return "", fmt.Errorf("invalid inspect Dockerfile.dockerignore")
	}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if line == "**" || line == "*" {
			return file, nil
		}
		break
	}
	return "", fmt.Errorf("inspect Dockerfile.dockerignore must exclude the resources tree by default")
}
