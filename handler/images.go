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

// qualifyImage adds a registry prefix unless the manifest already carries a
// fully-qualified image reference. A manifest keeps the short local tag so
// existing Compose definitions and locally built images remain compatible.
func qualifyImage(image, registry string) string {
	registry = strings.TrimSuffix(strings.TrimSpace(registry), "/")
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

func (s *Server) imageCandidates(image string) []string {
	config := s.currentConfig()
	source, err := normalizeImageSource(config.Runtime.ImageSource)
	if err != nil {
		source = "auto"
	}
	official := qualifyImage(image, config.Runtime.ImageRegistry)
	mirror := qualifyImage(image, config.Runtime.ImageMirror)
	var candidates []string
	add := func(value string) {
		if value == "" {
			return
		}
		for _, existing := range candidates {
			if existing == value {
				return
			}
		}
		candidates = append(candidates, value)
	}
	switch source {
	case "official":
		add(official)
	case "mirror":
		add(mirror)
	default:
		add(official)
		add(mirror)
	}
	return candidates
}

// remoteImage returns the first configured source for status messages.
func (s *Server) remoteImage(image string) string {
	candidates := s.imageCandidates(image)
	if len(candidates) == 0 {
		return ""
	}
	return candidates[0]
}

func (s *Server) imagePresent(ctx context.Context, image string) bool {
	if image == "" {
		return false
	}
	code, _, err := s.docker(ctx, "image", "inspect", image)
	return err == nil && code == 0
}

// ensureImage tries the configured source candidates in order and tags a
// successful pull under the local manifest name. The image mutex prevents the
// startup diagnostics pull and a simultaneous Validate from downloading the
// same image twice.
func (s *Server) ensureImage(ctx context.Context, image string) (string, error) {
	s.imageMu.Lock()
	defer s.imageMu.Unlock()

	if s.imagePresent(ctx, image) {
		return "", nil
	}
	candidates := s.imageCandidates(image)
	if len(candidates) == 0 {
		return "", fmt.Errorf("image %s is missing and neither runtime.image_registry nor runtime.image_mirror is configured", image)
	}
	var failures []string
	for _, remote := range candidates {
		code, out, err := s.docker(ctx, "pull", remote)
		if err != nil {
			failures = append(failures, fmt.Sprintf("%s: %v (%s)", remote, err, strings.TrimSpace(string(out))))
			continue
		}
		if code != 0 {
			failures = append(failures, fmt.Sprintf("%s: %s", remote, strings.TrimSpace(string(out))))
			continue
		}
		if remote != image {
			code, out, err = s.docker(ctx, "tag", remote, image)
			if err != nil || code != 0 {
				failures = append(failures, fmt.Sprintf("tag %s as %s: %v %s", remote, image, err, strings.TrimSpace(string(out))))
				continue
			}
		}
		return remote, nil
	}
	return "", fmt.Errorf("could not pull %s: %s", image, strings.Join(failures, "; "))
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
	remote, err := s.ensureImage(ctx, image)
	if err != nil {
		s.note("diagnostics image bootstrap skipped: " + err.Error())
		return
	}
	if remote == "" {
		s.note("diagnostics image ready from local cache: " + image)
	} else {
		s.note("diagnostics image ready: " + image + " (pulled from " + remote + ")")
	}
	s.RefreshResources()
}

// imageCheck is the Validate-time image gate. A missing image is pulled (and
// tagged) before the selected window or model is admitted.
func (s *Server) imageCheck(ctx context.Context, image, id string, label string) Check {
	if s.imagePresent(ctx, image) {
		return Check{ID: id, OK: true, Message: label + " image is ready"}
	}
	remote, err := s.ensureImage(ctx, image)
	if err != nil {
		return Check{ID: id, OK: false, Message: label + " image unavailable: " + err.Error()}
	}
	if remote == "" {
		return Check{ID: id, OK: true, Message: label + " image is ready"}
	}
	return Check{ID: id, OK: true, Message: label + " image was pulled from " + remote}
}
