package handler

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// imagePullTimeout is deliberately longer than a normal request: the first
// diagnostics pull can take several minutes on a cold Docker cache.
const imagePullTimeout = 15 * time.Minute

// remoteImage returns the registry reference for a local manifest image. A
// manifest keeps the short local tag so existing Compose definitions and
// locally built images remain compatible; the registry prefix is only used for
// pull/tag bootstrap.
func (s *Server) remoteImage(image string) string {
	registry := strings.TrimSuffix(strings.TrimSpace(s.currentConfig().Runtime.ImageRegistry), "/")
	if registry == "" {
		return ""
	}
	if strings.Contains(image, "/") {
		first := strings.SplitN(image, "/", 2)[0]
		if strings.ContainsAny(first, ".:") || first == "localhost" {
			return image
		}
	}
	return registry + "/" + image
}

func (s *Server) imagePresent(ctx context.Context, image string) bool {
	if image == "" {
		return false
	}
	code, _, err := s.docker(ctx, "image", "inspect", image)
	return err == nil && code == 0
}

// ensureImage pulls a missing image from the configured registry and tags it
// under the local manifest name. The image mutex prevents the startup
// diagnostics pull and a simultaneous Validate from downloading it twice.
func (s *Server) ensureImage(ctx context.Context, image string) error {
	s.imageMu.Lock()
	defer s.imageMu.Unlock()

	if s.imagePresent(ctx, image) {
		return nil
	}
	remote := s.remoteImage(image)
	if remote == "" {
		return fmt.Errorf("image %s is missing and runtime.image_registry is not configured", image)
	}
	code, out, err := s.docker(ctx, "pull", remote)
	if err != nil {
		return fmt.Errorf("pull %s: %w (%s)", remote, err, strings.TrimSpace(string(out)))
	}
	if code != 0 {
		return fmt.Errorf("pull %s failed: %s", remote, strings.TrimSpace(string(out)))
	}
	if remote == image {
		return nil
	}
	code, out, err = s.docker(ctx, "tag", remote, image)
	if err != nil {
		return fmt.Errorf("tag %s as %s: %w (%s)", remote, image, err, strings.TrimSpace(string(out)))
	}
	if code != 0 {
		return fmt.Errorf("tag %s as %s failed: %s", remote, image, strings.TrimSpace(string(out)))
	}
	return nil
}

// ensureDiagnosticsImage runs once in the background after the Handler starts.
// It makes the first diagnostics launch self-contained without blocking the
// local HTTP connection or the resource panel.
func (s *Server) ensureDiagnosticsImage() {
	if s.Resources == nil || s.Resources.Diagnostics == nil {
		return
	}
	image := s.Resources.Diagnostics.Container.Image
	ctx, cancel := context.WithTimeout(context.Background(), imagePullTimeout)
	defer cancel()
	if err := s.ensureImage(ctx, image); err != nil {
		s.note("diagnostics image bootstrap skipped: " + err.Error())
		return
	}
	s.note("diagnostics image ready: " + image)
	s.RefreshResources()
}

// imageCheck is the Validate-time image gate. A missing image is pulled (and
// tagged) before the selected window or model is admitted.
func (s *Server) imageCheck(ctx context.Context, image, id string, label string) Check {
	if s.imagePresent(ctx, image) {
		return Check{ID: id, OK: true, Message: label + " image is ready"}
	}
	if err := s.ensureImage(ctx, image); err != nil {
		return Check{ID: id, OK: false, Message: label + " image unavailable: " + err.Error()}
	}
	return Check{ID: id, OK: true, Message: label + " image was pulled from " + s.remoteImage(image)}
}
