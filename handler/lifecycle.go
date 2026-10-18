package handler

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"time"
)

const identityProtocol = 1

var errNotRunning = errors.New("handler is not running")

type identityResponse struct {
	Service    string `json:"service"`
	Version    string `json:"version"`
	Protocol   int    `json:"protocol"`
	ConfigID   string `json:"config_id"`
	InstanceID string `json:"instance_id"`
	URL        string `json:"url"`
	ReviewURL  string `json:"review_url"`
	State      string `json:"state"`
}

type localSession struct {
	ConfigID   string `json:"config_id"`
	InstanceID string `json:"instance_id"`
	URL        string `json:"url"`
	ReviewURL  string `json:"review_url"`
	PID        int    `json:"pid"`
	Token      string `json:"token"`
}

func canonicalPath(path string) string {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return filepath.Clean(path)
	}
	ancestor := absolute
	var suffix []string
	for {
		if resolved, err := filepath.EvalSymlinks(ancestor); err == nil {
			for i := len(suffix) - 1; i >= 0; i-- {
				resolved = filepath.Join(resolved, suffix[i])
			}
			return resolved
		}
		parent := filepath.Dir(ancestor)
		if parent == ancestor {
			return absolute
		}
		suffix = append(suffix, filepath.Base(ancestor))
		ancestor = parent
	}
}

func addressURL(addr Addr) string {
	host := addr.Host
	if host == "localhost" {
		host = "127.0.0.1"
	}
	return "http://" + net.JoinHostPort(host, strconv.Itoa(addr.Port))
}

func profileID(cfg Config) string {
	source := canonicalPath(cfg.Source)
	if cfg.Source == "" {
		source = canonicalPath(cfg.Paths.Runs) + "\x00" + addressURL(cfg.Handler)
	}
	digest := sha256.Sum256([]byte(source))
	return hex.EncodeToString(digest[:])
}

func configID(cfg Config) string {
	// Only the data root and trusted origins are reloaded. Tokens stay private.
	handlerURL, reviewURL := addressURL(cfg.Handler), addressURL(cfg.Review)
	cfg.Handler, cfg.Review = Addr{}, Addr{}
	cfg.Paths.DataRoot = ""
	cfg.Paths.Runs, cfg.Paths.Artifacts = canonicalPath(cfg.Paths.Runs), canonicalPath(cfg.Paths.Artifacts)
	cfg.Runtime.AllowedOrigins = nil
	body, _ := json.Marshal(struct {
		Source, Handler, Review string
		Config                  Config
	}{canonicalPath(cfg.Source), handlerURL, reviewURL, cfg})
	digest := sha256.Sum256(body)
	return hex.EncodeToString(digest[:])
}

func sessionDirectory(cfg Config) (string, error) {
	cache, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(cache, "NiB", "handler", profileID(cfg)), nil
}

func localHTTPClient() *http.Client {
	return &http.Client{
		Timeout:   2 * time.Second,
		Transport: &http.Transport{Proxy: nil},
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func probeAddress(ctx context.Context, address string) (identityResponse, error) {
	var identity identityResponse
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, address+"/v1/identity", nil)
	if err != nil {
		return identity, err
	}
	client := localHTTPClient()
	defer client.CloseIdleConnections()
	response, err := client.Do(request)
	if err != nil {
		var networkError *net.OpError
		if errors.As(err, &networkError) && networkError.Op == "dial" && !networkError.Timeout() {
			return identity, fmt.Errorf("%w at %s", errNotRunning, address)
		}
		return identity, fmt.Errorf("cannot identify service at %s: %w", address, err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || json.NewDecoder(io.LimitReader(response.Body, 16<<10)).Decode(&identity) != nil {
		return identity, fmt.Errorf("%s is occupied by an old Handler or another service; stop or update that service first", address)
	}
	if identity.Service != "nib-handler" || identity.Protocol != identityProtocol || identity.Version == "" || identity.InstanceID == "" {
		return identity, fmt.Errorf("service at %s is not a compatible nib-handler", address)
	}
	return identity, nil
}

func probeInstance(ctx context.Context, cfg Config) (identityResponse, error) {
	identity, err := probeAddress(ctx, addressURL(cfg.Handler))
	if err != nil {
		return identity, err
	}
	if identity.ConfigID != configID(cfg) || identity.URL != addressURL(cfg.Handler) || identity.ReviewURL != addressURL(cfg.Review) {
		return identity, fmt.Errorf("a Handler with a different configuration is using %s; use its configuration; if you edited this config file, run restart -config with that file", addressURL(cfg.Handler))
	}
	return identity, nil
}

func saveSession(cfg Config, session localSession) error {
	dir, err := sessionDirectory(cfg)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	file, err := os.CreateTemp(dir, ".session-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if err := json.NewEncoder(file).Encode(session); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), filepath.Join(dir, "session.json"))
}

func readSession(cfg Config) (localSession, error) {
	var session localSession
	dir, err := sessionDirectory(cfg)
	if err != nil {
		return session, err
	}
	file, err := os.Open(filepath.Join(dir, "session.json"))
	if err != nil {
		return session, fmt.Errorf("local control session is unavailable: %w", err)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || (runtime.GOOS != "windows" && info.Mode().Perm()&0o077 != 0) {
		return session, fmt.Errorf("local control session must be private to this user")
	}
	if err := json.NewDecoder(io.LimitReader(file, 16<<10)).Decode(&session); err != nil {
		return session, err
	}
	if !localServiceURL(session.URL) || !localServiceURL(session.ReviewURL) || session.PID <= 0 || session.ConfigID == "" || session.InstanceID == "" || session.Token == "" {
		return session, fmt.Errorf("local control session is invalid; refusing to send credentials")
	}
	return session, nil
}

func removeSession(cfg Config, instanceID string) {
	if session, err := readSession(cfg); err == nil && session.InstanceID == instanceID {
		if dir, err := sessionDirectory(cfg); err == nil {
			_ = os.Remove(filepath.Join(dir, "session.json"))
		}
	}
}

func localServiceURL(raw string) bool {
	address, err := url.Parse(raw)
	return err == nil && address.Scheme == "http" && address.User == nil && address.Path == "" && address.RawQuery == "" && address.Fragment == "" && address.Port() != "" && RequireLoopbackHost(address.Hostname()) == nil
}

func listenerClosed(raw string) bool {
	address, _ := url.Parse(raw)
	connection, err := net.DialTimeout("tcp", address.Host, 100*time.Millisecond)
	if err == nil {
		connection.Close()
		return false
	}
	var networkError *net.OpError
	return errors.As(err, &networkError) && networkError.Op == "dial" && !networkError.Timeout()
}

func waitBackgroundExit(cfg Config, pid int) error {
	deadline := time.Now().Add(12 * time.Second)
	for time.Now().Before(deadline) {
		if backgroundProcessExited(cfg, pid) {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("registered Handler is still completing shutdown; wait or inspect its log before restarting")
}

func stopInstance(cfg Config) error {
	session, err := readSession(cfg)
	if err != nil {
		if _, probeErr := probeInstance(context.Background(), cfg); errors.Is(probeErr, errNotRunning) && errors.Is(err, os.ErrNotExist) {
			return waitBackgroundExit(cfg, 0)
		}
		return err
	}
	identity, err := probeAddress(context.Background(), session.URL)
	if err != nil && !errors.Is(err, errNotRunning) {
		return err
	}
	if err == nil && (session.InstanceID != identity.InstanceID || session.ConfigID != identity.ConfigID || session.URL != identity.URL || session.ReviewURL != identity.ReviewURL) {
		return fmt.Errorf("local session is stale; do not stop an unidentified process")
	}
	if err == nil {
		request, err := http.NewRequest(http.MethodPost, session.URL+"/v1/control/stop", bytes.NewReader(nil))
		if err != nil {
			return err
		}
		request.Header.Set("Authorization", "Bearer "+session.Token)
		client := localHTTPClient()
		defer client.CloseIdleConnections()
		response, err := client.Do(request)
		if err != nil {
			return err
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusAccepted {
			var body struct {
				Message string `json:"message"`
			}
			_ = json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&body)
			return fmt.Errorf("Handler refused stop (HTTP %d): %s", response.StatusCode, body.Message)
		}
	}
	deadline := time.Now().Add(12 * time.Second)
	for time.Now().Before(deadline) {
		current, err := probeAddress(context.Background(), session.URL)
		if (errors.Is(err, errNotRunning) || (err == nil && current.InstanceID != session.InstanceID)) && listenerClosed(session.ReviewURL) {
			recorded, recordErr := readSession(cfg)
			cleared := errors.Is(recordErr, os.ErrNotExist) || (recordErr == nil && recorded.InstanceID != session.InstanceID)
			if !cleared && processExited(session.PID) {
				removeSession(cfg, session.InstanceID)
				cleared = true
			}
			if cleared && backgroundProcessExited(cfg, session.PID) {
				return nil
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("Handler accepted stop but has not exited; inspect its log before retrying")
}

func (s *Server) requestShutdown() error {
	// The execution lock covers diagnostics; jobsMu makes draining atomic with
	// queued-job admission. Historical records are not live process state.
	if !s.executionMu.TryLock() {
		return fmt.Errorf("wait for active inference or diagnostics before stopping Handler")
	}
	defer s.executionMu.Unlock()
	// Reconcile after holding the lock: an execution that just failed cleanup has
	// released it and must no longer be exempt from the container check.
	if err := s.reconcileExecutions(); err != nil {
		return err
	}
	s.jobsMu.Lock()
	defer s.jobsMu.Unlock()
	if s.stopping {
		return nil
	}
	for _, job := range s.jobs {
		if job.Status == "queued" || job.Status == "running" {
			return fmt.Errorf("wait for queued or running jobs before stopping Handler")
		}
	}
	s.stopping = true
	close(s.shutdown)
	return nil
}

func (s *Server) handleStop(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Origin") != "" {
		runtimeError(w, http.StatusForbidden, fmt.Errorf("stop Handler using its local CLI, not a website"))
		return
	}
	if err := s.requestShutdown(); err != nil {
		runtimeError(w, http.StatusConflict, err)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"status": "stopping"})
}
