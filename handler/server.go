package main

import (
	"encoding/json"
	"net/http"
	"strconv"
)

const version = "0.1.0"

type server struct {
	cfg Config
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /v1/status", s.handleStatus)
	return s.loopbackOnly(mux)
}

func (s *server) loopbackOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !isLoopbackRequestHost(r.Host) {
			http.Error(w, "refusing non-loopback host", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *server) handleStatus(w http.ResponseWriter, r *http.Request) {
	source := s.cfg.Source
	if source == "" {
		source = "defaults"
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"service":     "nib-handler",
		"version":     version,
		"host":        s.cfg.Handler.Host,
		"port":        strconv.Itoa(s.cfg.Handler.Port),
		"config_file": source,
		"review":      s.cfg.Review,
		"paths":       s.cfg.Paths,
		"docker":      dockerCheck(),
	})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}
