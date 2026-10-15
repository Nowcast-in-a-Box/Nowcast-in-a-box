package handler

import (
	"crypto/subtle"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
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
			permitted := false
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
	if err != nil || parsed.Scheme != "nib" || parsed.Host != "connect" {
		return "", fmt.Errorf("use nib://connect?origin=...")
	}
	origin, err := url.Parse(parsed.Query().Get("origin"))
	if err != nil || (origin.Scheme != "http" && origin.Scheme != "https") || origin.Host == "" || origin.User != nil {
		return "", fmt.Errorf("Hub origin must be an http(s) origin")
	}
	return origin.Scheme + "://" + origin.Host, nil
}

func (s *Server) rememberOrigin(origin string) error {
	s.configMu.Lock()
	defer s.configMu.Unlock()
	for _, allowed := range s.Config.Runtime.AllowedOrigins {
		if allowed == origin {
			return nil
		}
	}
	if err := appendYAMLList(s.Config.Source, "runtime", "allowed_origins", origin); err != nil {
		return err
	}
	s.Config.Runtime.AllowedOrigins = append(s.Config.Runtime.AllowedOrigins, origin)
	return nil
}

func (s *Server) handlerURL() string {
	port := s.currentConfig().Handler.Port
	if port == 0 {
		port = 8765
	}
	return "http://127.0.0.1:" + strconv.Itoa(port)
}

func (s *Server) handlePair(w http.ResponseWriter, r *http.Request) {
	origin := r.Header.Get("Origin")
	if origin == "" || origin != r.URL.Query().Get("origin") {
		writeJSON(w, http.StatusForbidden, map[string]string{"message": "open the local handler from this Hub page"})
		return
	}
	if s.Config.Source != "" {
		if loaded, err := Load(s.Config.Source); err == nil {
			s.configMu.Lock()
			s.Config.Runtime.AllowedOrigins = loaded.Runtime.AllowedOrigins
			s.configMu.Unlock()
		}
	}
	for _, allowed := range s.currentConfig().Runtime.AllowedOrigins {
		if allowed == origin {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Private-Network", "true")
			writeJSON(w, http.StatusOK, map[string]string{"url": s.handlerURL(), "token": s.Token})
			return
		}
	}
	writeJSON(w, http.StatusForbidden, map[string]string{"message": "start the local handler once so this Hub can connect"})
}

func (s *Server) authorize(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		header := r.Header.Get("Authorization")
		token := strings.TrimPrefix(header, "Bearer ")
		if !strings.HasPrefix(header, "Bearer ") || s.Token == "" || subtle.ConstantTimeCompare([]byte(token), []byte(s.Token)) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"code": "UNAUTHORIZED", "message": "connect using the token shown by your local handler"})
			return
		}
		next(w, r)
	}
}
