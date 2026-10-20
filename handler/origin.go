package handler

import (
	"crypto/subtle"
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

func (s *Server) originControl(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if r.URL.Path == "/v1/pair" {
			next.ServeHTTP(w, r)
			return
		}
		if origin != "" {
			permitted := origin == "http://"+r.Host
			for _, allowed := range s.currentConfig().Runtime.AllowedOrigins {
				if origin == allowed {
					permitted = true
					break
				}
			}
			if !permitted {
				http.Error(w, "Hub origin is not authorized in local config", http.StatusForbidden)
				return
			}
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Vary", "Origin")
			if r.Method == http.MethodOptions {
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Package-SHA256, Idempotency-Key")
				if r.Header.Get("Access-Control-Request-Private-Network") == "true" {
					w.Header().Set("Access-Control-Allow-Private-Network", "true")
				}
				w.WriteHeader(http.StatusNoContent)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

func ParseConnectURI(raw string) (string, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "nib" || parsed.Host != "connect" || parsed.User != nil || parsed.Path != "" || parsed.Fragment != "" {
		return "", fmt.Errorf("use nib://connect?origin=...")
	}
	query, err := url.ParseQuery(parsed.RawQuery)
	if err != nil || len(query) != 1 || len(query["origin"]) != 1 {
		return "", fmt.Errorf("connect accepts exactly one Hub origin, not commands or callback URLs")
	}
	origin, err := url.Parse(query.Get("origin"))
	if err != nil || (origin.Scheme != "http" && origin.Scheme != "https") || origin.Hostname() == "" || origin.User != nil {
		return "", fmt.Errorf("Hub origin must be an http(s) origin")
	}
	return origin.Scheme + "://" + origin.Host, nil
}

func originAllowed(cfg Config, origin string) bool {
	for _, allowed := range cfg.Runtime.AllowedOrigins {
		if allowed == origin {
			return true
		}
	}
	return false
}

func (s *Server) handlerURL() string {
	return addressURL(s.currentConfig().Handler)
}

func (s *Server) handlePair(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	origin := r.Header.Get("Origin")
	if origin == "" || origin != r.URL.Query().Get("origin") {
		writeJSON(w, http.StatusForbidden, map[string]string{"message": "open the local handler from this Hub page"})
		return
	}
	if s.Config.Source != "" {
		loaded, err := Load(s.Config.Source)
		if err != nil {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"code": "CONFIG_UNAVAILABLE", "message": "Handler cannot read its local configuration; inspect the local log and repair the configuration"})
			return
		}
		s.configMu.Lock()
		s.Config.Runtime.AllowedOrigins = loaded.Runtime.AllowedOrigins
		s.configMu.Unlock()
	}
	for _, allowed := range s.currentConfig().Runtime.AllowedOrigins {
		if allowed == origin {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Vary", "Origin")
			w.Header().Set("Access-Control-Allow-Private-Network", "true")
			if r.Method == http.MethodOptions {
				w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
				w.WriteHeader(http.StatusNoContent)
				return
			}
			writeJSON(w, http.StatusOK, map[string]string{"url": s.handlerURL(), "token": s.Token})
			return
		}
	}
	writeJSON(w, http.StatusForbidden, map[string]string{"code": "ORIGIN_NOT_ALLOWED", "message": "This Hub is not in the Handler's trusted deployment configuration; use the configured Hub or ask the deployment maintainer to update runtime.allowed_origins"})
}

func (s *Server) authorize(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		header := r.Header.Get("Authorization")
		token := strings.TrimPrefix(header, "Bearer ")
		if !strings.HasPrefix(header, "Bearer ") || s.Token == "" || subtle.ConstantTimeCompare([]byte(token), []byte(s.Token)) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"code": "UNAUTHORIZED", "message": "Reconnect the local handler from the Hub page; the previous session is no longer valid"})
			return
		}
		next(w, r)
	}
}
