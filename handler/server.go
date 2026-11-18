package handler

import (
	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler/internal/console"

	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// Version is stamped by the build; plain go build has no branch metadata.
var Version = "dev-unknown"

// Server is the loopback HTTP API.
type Server struct {
	Config         Config
	Preflight      []func() Check
	Runner         CommandRunner
	Token          string
	PickFolder     func() (string, error)
	configMu       sync.RWMutex
	ordersMu       sync.Mutex
	jobsMu         sync.Mutex
	jobs           map[string]*Job
	triggersMu     sync.Mutex
	triggers       []*Trigger
	triggersLoaded bool
	// TriggerTick is the firing loop interval; tests shorten it.
	TriggerTick                time.Duration
	executionMu                sync.Mutex
	executions                 map[string]*execution
	executionQueue             []*execution
	executionSlots             int
	executionDispatching       bool
	triggerDecisions           sync.Map
	recoveryMu                 sync.Mutex
	resourcesMu                sync.Mutex
	imageMu                    sync.Mutex
	currentResources           resourceSnapshot
	preparingResources         bool
	resourcesLoaded            bool
	diagnosticsViewerMu        sync.Mutex
	diagnosticsViewerRun       string
	diagnosticsViewerContainer string
	diagnosticsViewerReport    string
	executionDataID            string
	executionModels            []string
	Resources                  *Resources
	ResourceError              error
	stopping                   bool // guarded by jobsMu
	shutdown                   chan struct{}
	instanceID                 string
	startedAt                  time.Time
	serviceCtx                 context.Context
	serviceCancel              context.CancelFunc
	background                 sync.WaitGroup
	admissionMu                sync.RWMutex
	restartServer              *Server // guarded by jobsMu
}

// NewServer checks the configured Docker context in its default status probe.
func NewServer(cfg Config) *Server {
	if cfg.Runtime.MaxConcurrentJobs == 0 {
		cfg.Runtime.MaxConcurrentJobs = defaultMaxConcurrentJobs
	}
	token := cfg.Runtime.Token
	if token == "" {
		token = randomToken()
	}
	ctx, cancel := context.WithCancel(context.Background())
	server := &Server{
		Config: cfg, Runner: execRunner{}, Token: token, jobs: make(map[string]*Job),
		shutdown: make(chan struct{}), instanceID: randomToken()[:32],
		startedAt: time.Now().UTC(), serviceCtx: ctx, serviceCancel: cancel,
	}
	server.Preflight = []func() Check{server.dockerCheck}
	server.Resources, server.ResourceError = LoadResources(cfg.Paths.Resources)
	return server
}

func (s *Server) resources() *Resources {
	return s.Resources
}

func (s *Server) resourceFailure() error {
	if s.ResourceError != nil {
		return s.ResourceError
	}
	if s.Resources == nil {
		return fmt.Errorf("runtime resources are not loaded")
	}
	return nil
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
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, _ *http.Request) { console.ServePage(w, s.Token) })
	mux.HandleFunc("GET /console/fonts/{name}", console.ServeFont)
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /v1/identity", s.handleIdentity)
	mux.HandleFunc("GET /v1/status", s.handleStatus)
	mux.HandleFunc("POST /v1/control/stop", s.authorize(s.handleStop))
	mux.HandleFunc("POST /v1/control/restart", s.authorize(s.handleRestart))
	mux.HandleFunc("POST /v1/datasets/{dataset}/inspect", s.authorize(s.handleReaderInspect))
	mux.HandleFunc("GET /v1/dashboard", s.authorize(s.handleDashboard))
	s.runtimeRoutes(mux)
	return s.loopbackOnly(s.originControl(s.admitRequests(mux)))
}

// ReviewRoutes serves the same diagnostics result viewer on the review listener.
func (s *Server) ReviewRoutes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/runs/{id}/files/{file...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /api/{path...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /static/{path...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /visualization", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /visualization/{path...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /map-viewer/{path...}", s.handleDiagnosticsPage)
	mux.HandleFunc("GET /map-basemap/{path...}", s.handleDiagnosticsPage)
	return s.loopbackOnly(s.admitRequests(mux))
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
	state, _ := s.lifecycleStatus()
	writeJSON(w, http.StatusOK, identityResponse{
		Service: "nib-handler", Version: Version, Protocol: identityProtocol,
		ConfigID: configID(cfg), InstanceID: s.instanceID, State: state,
		URL: addressURL(cfg.Handler), ReviewURL: addressURL(cfg.Review),
	})
}

type statusResponse struct {
	InstanceID     string    `json:"instance_id"`
	StartedAt      time.Time `json:"started_at"`
	HandlerState   string    `json:"handler_state"`
	ActiveJobs     int       `json:"active_jobs"`
	Service        string    `json:"service"`
	Version        string    `json:"version"`
	Host           string    `json:"host"`
	Port           string    `json:"port"`
	ConfigFile     string    `json:"config_file"`
	Review         Addr      `json:"review"`
	Paths          Paths     `json:"paths"`
	Docker         Check     `json:"docker"`
	Preflight      []Check   `json:"preflight"`
	ResourcesError string    `json:"resources_error,omitempty"`
}

func (s *Server) currentConfig() Config {
	s.configMu.RLock()
	defer s.configMu.RUnlock()
	cfg := s.Config
	cfg.Runtime.AllowedOrigins = append([]string(nil), s.Config.Runtime.AllowedOrigins...)
	if s.Config.DataRoots != nil {
		cfg.DataRoots = make(map[string]string, len(s.Config.DataRoots))
		for key, value := range s.Config.DataRoots {
			cfg.DataRoots[key] = value
		}
	}
	return cfg
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
	w.Header().Set("Cache-Control", "no-store")
	state, active := s.lifecycleStatus()
	writeJSON(w, http.StatusOK, statusResponse{
		InstanceID: s.instanceID, StartedAt: s.startedAt, HandlerState: state, ActiveJobs: active,
		Service:    "nib-handler",
		Version:    Version,
		Host:       cfg.Handler.Host,
		Port:       strconv.Itoa(cfg.Handler.Port),
		ConfigFile: source,
		Review:     cfg.Review,
		Paths:      cfg.Paths,
		Docker:     docker,
		Preflight:  checks,
		ResourcesError: func() string {
			if s.ResourceError != nil {
				return s.ResourceError.Error()
			}
			return ""
		}(),
	})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}
