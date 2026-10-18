package handler

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strconv"
	"sync"
)

// Version is the handler release identifier reported on /v1/status.
const Version = "0.1.0"

// Server is the loopback HTTP API.
type Server struct {
	Config             Config
	Preflight          []func() Check
	Runner             CommandRunner
	Token              string
	PickFolder         func() (string, error)
	configMu           sync.RWMutex
	jobsMu             sync.Mutex
	jobs               map[string]*Job
	executionMu        sync.Mutex
	recoveryMu         sync.Mutex
	resourcesMu        sync.Mutex
	currentResources   resourceSnapshot
	preparingResources bool
	resourcesLoaded    bool
	executionModels    []string
	stopping           bool // guarded by jobsMu
	shutdown           chan struct{}
	instanceID         string
}

// NewServer returns a server whose preflight list starts with DockerCheck.
func NewServer(cfg Config) *Server {
	token := cfg.Runtime.Token
	if token == "" {
		token = randomToken()
	}
	return &Server{
		Config: cfg, Runner: execRunner{}, Token: token, jobs: make(map[string]*Job),
		Preflight: []func() Check{DockerCheck},
		shutdown:  make(chan struct{}), instanceID: randomToken()[:32],
	}
}

func randomToken() string {
	var value [32]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(value[:])
}

// Routes returns the handler mux, wrapped so non-loopback Host values are
// rejected.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /v1/identity", s.handleIdentity)
	mux.HandleFunc("GET /v1/status", s.handleStatus)
	mux.HandleFunc("POST /v1/control/stop", s.authorize(s.handleStop))
	s.runtimeRoutes(mux)
	return s.loopbackOnly(s.originControl(mux))
}

// ReviewRoutes serves only registered diagnostic results on the review listener.
func (s *Server) ReviewRoutes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/runs/{id}/files/{file...}", s.handleResultFile)
	return s.loopbackOnly(s.originControl(mux))
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

func (s *Server) handleIdentity(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	cfg := s.currentConfig()
	s.jobsMu.Lock()
	state := "running"
	if s.stopping {
		state = "stopping"
	}
	s.jobsMu.Unlock()
	writeJSON(w, http.StatusOK, identityResponse{
		Service: "nib-handler", Version: Version, Protocol: identityProtocol,
		ConfigID: configID(cfg), InstanceID: s.instanceID, State: state,
		URL: addressURL(cfg.Handler), ReviewURL: addressURL(cfg.Review),
	})
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

func (s *Server) currentConfig() Config {
	s.configMu.RLock()
	defer s.configMu.RUnlock()
	return s.Config
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	cfg := s.currentConfig()
	source := cfg.Source
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
		Host:       cfg.Handler.Host,
		Port:       strconv.Itoa(cfg.Handler.Port),
		ConfigFile: source,
		Review:     cfg.Review,
		Paths:      cfg.Paths,
		Docker:     docker,
		Preflight:  checks,
	})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}
