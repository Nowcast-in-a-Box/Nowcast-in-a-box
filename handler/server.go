package handler

import (
	"encoding/json"
	"net/http"
	"strconv"
)

// Version is the handler release identifier reported on /v1/status.
const Version = "0.1.0"

// Server is the loopback HTTP API.
type Server struct {
	Config    Config
	Preflight []func() Check
}

// NewServer returns a server whose preflight list starts with DockerCheck.
func NewServer(cfg Config) *Server {
	return &Server{
		Config:    cfg,
		Preflight: []func() Check{DockerCheck},
	}
}

// Routes returns the handler mux, wrapped so non-loopback Host values are
// rejected.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /v1/status", s.handleStatus)
	return s.loopbackOnly(mux)
}

func (s *Server) loopbackOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !IsLoopbackRequestHost(r.Host) {
			http.Error(w, "refusing non-loopback host", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

type statusResponse struct {
	Service    string  `json:"service"`
	Version    string  `json:"version"`
	Host       string  `json:"host"`
	Port       string  `json:"port"`
	ConfigFile string  `json:"config_file"`
	Review     Addr    `json:"review"`
	Paths      Paths   `json:"paths"`
	Docker     Check   `json:"docker"`
	Preflight  []Check `json:"preflight"`
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	source := s.Config.Source
	if source == "" {
		source = "defaults"
	}
	checks := make([]Check, 0, len(s.Preflight))
	for _, fn := range s.Preflight {
		if fn == nil {
			continue
		}
		checks = append(checks, fn())
	}
	docker := Check{}
	for _, c := range checks {
		if c.ID == "docker" {
			docker = c
			break
		}
	}
	writeJSON(w, http.StatusOK, statusResponse{
		Service:    "nib-handler",
		Version:    Version,
		Host:       s.Config.Handler.Host,
		Port:       strconv.Itoa(s.Config.Handler.Port),
		ConfigFile: source,
		Review:     s.Config.Review,
		Paths:      s.Config.Paths,
		Docker:     docker,
		Preflight:  checks,
	})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}
