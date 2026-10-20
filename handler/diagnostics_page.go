package handler

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// The result page is one diagnostics container: Visualization and Metrics are
// served by the same process. The Handler owns its lifetime and proxies it on
// the existing result listener, so Hub's Open result URL does not change.
func (s *Server) handleDiagnosticsPage(w http.ResponseWriter, r *http.Request) {
	runID := r.PathValue("id")
	if runID == "" {
		s.diagnosticsViewerMu.Lock()
		runID = s.diagnosticsViewerRun
		s.diagnosticsViewerMu.Unlock()
	}
	if runID == "" {
		http.Error(w, "diagnostics result viewer is not started", http.StatusServiceUnavailable)
		return
	}
	if !idPattern.MatchString(runID) {
		http.Error(w, "invalid run id", http.StatusBadRequest)
		return
	}
	if err := s.ensureDiagnosticsViewer(runID); err != nil {
		http.Error(w, err.Error(), http.StatusServiceUnavailable)
		return
	}
	s.diagnosticsProxy().ServeHTTP(w, r)
}

func (s *Server) diagnosticsProxy() *httputil.ReverseProxy {
	target := &url.URL{
		Scheme: "http",
		Host:   net.JoinHostPort(s.currentConfig().Diagnostics.Host, strconv.Itoa(s.currentConfig().Diagnostics.Port)),
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, err error) {
		http.Error(w, "diagnostics result viewer is unavailable: "+err.Error(), http.StatusBadGateway)
	}
	return proxy
}

func (s *Server) ensureDiagnosticsViewer(runID string) error {
	s.diagnosticsViewerMu.Lock()
	defer s.diagnosticsViewerMu.Unlock()

	if s.diagnosticsViewerRun == runID && s.diagnosticsViewerContainer != "" && s.diagnosticsViewerHealthy() {
		return nil
	}
	s.stopDiagnosticsViewerLocked()

	cfg := s.currentConfig()
	runDir := filepath.Join(cfg.Paths.Runs, runID)
	if _, err := os.Stat(runDir); err != nil {
		return fmt.Errorf("diagnostics run unavailable: %w", err)
	}
	image := ""
	if s.Resources != nil && s.Resources.Diagnostics != nil {
		image = s.Resources.Diagnostics.Container.Image
	}
	if image == "" {
		return fmt.Errorf("diagnostics image is not available")
	}

	name := "nib-diagnostics-result-" + strings.ReplaceAll(runID, "_", "-")
	port := strconv.Itoa(cfg.Diagnostics.Port)
	args := []string{
		"run", "--rm", "-d",
		"--name", name,
		"--pull", "never",
		"-p", fmt.Sprintf("%s:%s", net.JoinHostPort(cfg.Diagnostics.Host, port), port),
		"-v", volumeSpec(runDir, "/run", false),
		"--entrypoint", "python",
		image,
		"-m", "diagnostics.diagnostics_server",
		"--run", "/run",
		"--port", port,
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	code, out, err := s.docker(ctx, args...)
	if err != nil {
		return fmt.Errorf("start diagnostics result viewer: %w", err)
	}
	if code != 0 {
		return fmt.Errorf("start diagnostics result viewer: %s", strings.TrimSpace(string(out)))
	}
	s.diagnosticsViewerRun = runID
	s.diagnosticsViewerContainer = name
	if err := s.waitDiagnosticsViewer(ctx); err != nil {
		s.stopDiagnosticsViewerLocked()
		return err
	}
	return nil
}

func (s *Server) waitDiagnosticsViewer(ctx context.Context) error {
	address := net.JoinHostPort(s.currentConfig().Diagnostics.Host, strconv.Itoa(s.currentConfig().Diagnostics.Port))
	client := &http.Client{Timeout: 2 * time.Second}
	ticker := time.NewTicker(150 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return fmt.Errorf("diagnostics result viewer did not become ready: %w", ctx.Err())
		case <-ticker.C:
			response, err := client.Get("http://" + address + "/health")
			if err == nil {
				_ = response.Body.Close()
				if response.StatusCode == http.StatusOK {
					return nil
				}
			}
		}
	}
}

func (s *Server) diagnosticsViewerHealthy() bool {
	address := net.JoinHostPort(s.currentConfig().Diagnostics.Host, strconv.Itoa(s.currentConfig().Diagnostics.Port))
	client := &http.Client{Timeout: time.Second}
	response, err := client.Get("http://" + address + "/health")
	if err != nil {
		return false
	}
	defer response.Body.Close()
	return response.StatusCode == http.StatusOK
}

func (s *Server) stopDiagnosticsViewer() {
	s.diagnosticsViewerMu.Lock()
	defer s.diagnosticsViewerMu.Unlock()
	s.stopDiagnosticsViewerLocked()
}

func (s *Server) stopDiagnosticsViewerLocked() {
	name := s.diagnosticsViewerContainer
	s.diagnosticsViewerContainer = ""
	s.diagnosticsViewerRun = ""
	if name == "" {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	_, _, _ = s.docker(ctx, "rm", "-f", name)
}
