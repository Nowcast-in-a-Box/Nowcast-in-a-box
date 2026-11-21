package console

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"io"
	"net/http"
	"strconv"
	"strings"
)

// The offline console is served by the Handler itself: orders saved on this
// machine can be listed and started without the Hub. Forecast and verification
// results stay in the diagnostics viewer behind /v1/runs/{id}/files/.

//go:embed console_page.html
var consolePage string

//go:embed console_assets/fonts
var consoleFonts embed.FS

// handleConsole serves the offline control page with this session's token.
// The token lives in the no-store response only; the page keeps it in memory.
func ServePage(w http.ResponseWriter, token string) {
	page := strings.Replace(consolePage, `"__NIB_TOKEN__"`, strconv.Quote(token), 1)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = io.WriteString(w, page)
}

// consoleFontNames are the embedded Inter glyphs (SIL OFL 1.1) the console
// page loads from its CSS; the bytes are public so no token is required,
// but the loopback host check still applies.
var consoleFontNames = map[string]bool{
	"inter-latin-400-normal.woff2": true,
	"inter-latin-500-normal.woff2": true,
	"inter-latin-600-normal.woff2": true,
}

func ServeFont(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if !consoleFontNames[name] {
		http.NotFound(w, r)
		return
	}
	body, err := consoleFonts.ReadFile("console_assets/fonts/" + name)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	etag := `"` + hex.EncodeToString(sha256Of(body)[:16]) + `"`
	w.Header().Set("ETag", etag)
	w.Header().Set("Cache-Control", "no-cache")
	if r.Header.Get("If-None-Match") == etag {
		w.WriteHeader(http.StatusNotModified)
		return
	}
	w.Header().Set("Content-Type", "font/woff2")
	_, _ = w.Write(body)
}

func sha256Of(body []byte) []byte {
	digest := sha256.Sum256(body)
	return digest[:]
}
