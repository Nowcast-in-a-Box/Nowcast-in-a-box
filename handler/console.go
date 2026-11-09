package handler

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
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

// File names must be safe on every platform: no separators or colons, no
// leading dot, no trailing dot/space, and no Windows-reserved stems. Hub ids
// are lowercase by contract, but the manifest grammar is not enforced here.
var packageIDPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$`)

var windowsReservedNames = map[string]bool{
	"con": true, "prn": true, "aux": true, "nul": true,
	"com1": true, "com2": true, "com3": true, "com4": true, "com5": true,
	"com6": true, "com7": true, "com8": true, "com9": true,
	"lpt1": true, "lpt2": true, "lpt3": true, "lpt4": true, "lpt5": true,
	"lpt6": true, "lpt7": true, "lpt8": true, "lpt9": true,
}

func validPackageID(id string) bool {
	if !packageIDPattern.MatchString(id) || strings.HasSuffix(id, ".") || strings.HasSuffix(id, " ") {
		return false
	}
	return !windowsReservedNames[id]
}

type orderSummary struct {
	PackageID     string   `json:"package_id"`
	OrderID       string   `json:"order_id,omitempty"`
	DataID        string   `json:"data_id,omitempty"`
	EventID       string   `json:"event_id,omitempty"`
	StartTime     string   `json:"context_start_time,omitempty"`
	Samples       int      `json:"samples,omitempty"`
	Models        []string `json:"models,omitempty"`
	Metrics       int      `json:"metrics,omitempty"`
	Visualization bool     `json:"visualization,omitempty"`
	Source        string   `json:"source"`
	RunID         string   `json:"run_id,omitempty"`
	Error         string   `json:"error,omitempty"`
}

func requestSummary(request *RuntimeRequest) orderSummary {
	return orderSummary{
		PackageID:     request.PackageID,
		OrderID:       request.OrderID,
		DataID:        request.DataID,
		EventID:       request.EventID,
		StartTime:     request.StartTime,
		Samples:       len(request.SampleStarts),
		Models:        append([]string{}, request.Models...),
		Metrics:       len(request.Metrics),
		Visualization: request.Visualization,
	}
}

// handleConsole serves the offline control page with this session's token.
// The token lives in the no-store response only; the page keeps it in memory.
func (s *Server) handleConsole(w http.ResponseWriter, r *http.Request) {
	page := strings.Replace(consolePage, `"__NIB_TOKEN__"`, strconv.Quote(s.Token), 1)
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

func handleConsoleFont(w http.ResponseWriter, r *http.Request) {
	if !IsLoopbackRequestHost(r.Host) {
		http.Error(w, "refusing non-loopback host", http.StatusForbidden)
		return
	}
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

// handleOrdersList reports saved order packages plus the packages of past
// runs, so an order started once from the Hub stays visible offline.
func (s *Server) handleOrdersList(w http.ResponseWriter, r *http.Request) {
	cfg := s.currentConfig()
	summaries := []orderSummary{}
	seen := map[string]bool{}
	if entries, err := os.ReadDir(cfg.Paths.Orders); err == nil {
		names := []string{}
		for _, entry := range entries {
			if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".zip") {
				names = append(names, entry.Name())
			}
		}
		sort.Strings(names)
		for _, name := range names {
			summary := s.summarizeSavedPackage(filepath.Join(cfg.Paths.Orders, name))
			seen[summary.PackageID] = true
			summaries = append(summaries, summary)
		}
	}
	if entries, err := os.ReadDir(cfg.Paths.Runs); err == nil {
		ids := []string{}
		for _, entry := range entries {
			if entry.IsDir() && idPattern.MatchString(entry.Name()) {
				ids = append(ids, entry.Name())
			}
		}
		sort.Sort(sort.Reverse(sort.StringSlice(ids)))
		for _, id := range ids {
			summary, ok := runPackageSummary(cfg.Paths.Runs, id)
			if !ok || seen[summary.PackageID] {
				continue
			}
			seen[summary.PackageID] = true
			summaries = append(summaries, summary)
		}
	}
	writeJSON(w, 200, map[string]any{"orders": summaries})
}

func (s *Server) summarizeSavedPackage(path string) orderSummary {
	stem := strings.TrimSuffix(filepath.Base(path), ".zip")
	raw, err := os.ReadFile(path)
	if err != nil {
		return orderSummary{PackageID: stem, Source: "orders", Error: err.Error()}
	}
	sum := sha256.Sum256(raw)
	request, err := ParseRuntimePackage(raw, hex.EncodeToString(sum[:]), s.Resources)
	if err != nil {
		return orderSummary{PackageID: stem, Source: "orders", Error: err.Error()}
	}
	summary := requestSummary(&request)
	summary.Source = "orders"
	return summary
}

func runPackageSummary(runsDir, id string) (orderSummary, bool) {
	raw, err := os.ReadFile(filepath.Join(runsDir, id, "job.json"))
	if err != nil {
		return orderSummary{}, false
	}
	var job Job
	if json.Unmarshal(raw, &job) != nil || job.ID != id || job.Request.PackageID == "" {
		return orderSummary{}, false
	}
	summary := requestSummary(&job.Request)
	summary.Source = "runs"
	summary.RunID = id
	return summary, true
}

// handleOrderSave stores a Hub-generated package for offline start. It never
// starts a run and never overwrites a different package under the same id.
func (s *Server) handleOrderSave(w http.ResponseWriter, r *http.Request) {
	var upload struct {
		Package []byte `json:"package"`
		SHA256  string `json:"sha256"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 12<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&upload); err != nil {
		runtimeError(w, 400, err)
		return
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		runtimeError(w, 400, fmt.Errorf("one request object required"))
		return
	}
	request, err := ParseRuntimePackage(upload.Package, upload.SHA256, s.Resources)
	if err != nil {
		runtimeError(w, 422, err)
		return
	}
	if !validPackageID(request.PackageID) {
		runtimeError(w, 422, fmt.Errorf("package id %q is not a valid order file name", request.PackageID))
		return
	}
	ordersDir := s.currentConfig().Paths.Orders
	target := filepath.Join(ordersDir, request.PackageID+".zip")
	if rel, relErr := filepath.Rel(ordersDir, target); relErr != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		runtimeError(w, 400, fmt.Errorf("package id escapes the orders directory"))
		return
	}
	if existing, readErr := os.ReadFile(target); readErr == nil {
		sum := sha256.Sum256(existing)
		if hex.EncodeToString(sum[:]) == request.PackageSHA256 {
			summary := requestSummary(&request)
			summary.Source = "orders"
			writeJSON(w, 200, summary)
			return
		}
		runtimeError(w, 409, fmt.Errorf("package %s is already saved with different content; delete %s first", request.PackageID, target))
		return
	}
	if err := os.MkdirAll(ordersDir, 0o755); err != nil {
		runtimeError(w, 500, err)
		return
	}
	temporary := filepath.Join(ordersDir, request.PackageID+".tmp-"+randomToken()[:8])
	if err := os.WriteFile(temporary, upload.Package, 0o600); err != nil {
		runtimeError(w, 500, err)
		return
	}
	if err := os.Rename(temporary, target); err != nil {
		_ = os.Remove(temporary)
		runtimeError(w, 500, err)
		return
	}
	summary := requestSummary(&request)
	summary.Source = "orders"
	writeJSON(w, 201, summary)
}

// handleOrderRun starts a saved order package through the same admission path
// as a Hub upload.
func (s *Server) handleOrderRun(w http.ResponseWriter, r *http.Request) {
	packageID := r.PathValue("package_id")
	if !validPackageID(packageID) {
		runtimeError(w, 400, fmt.Errorf("invalid package id"))
		return
	}
	key := r.Header.Get("Idempotency-Key")
	if len(key) > 128 {
		runtimeError(w, 400, fmt.Errorf("idempotency key is too long"))
		return
	}
	pkg, err := s.findOrderPackage(packageID)
	if err != nil {
		runtimeError(w, 404, err)
		return
	}
	sum := sha256.Sum256(pkg)
	job, status, err := s.createJobFromPackage(pkg, hex.EncodeToString(sum[:]), key)
	if err != nil {
		runtimeError(w, status, err)
		return
	}
	writeJSON(w, status, *job)
}

// findOrderPackage returns the package bytes: the saved orders copy first,
// else the newest run that carried this package.
func (s *Server) findOrderPackage(packageID string) ([]byte, error) {
	cfg := s.currentConfig()
	if raw, err := os.ReadFile(filepath.Join(cfg.Paths.Orders, packageID+".zip")); err == nil {
		return raw, nil
	}
	entries, err := os.ReadDir(cfg.Paths.Runs)
	if err != nil {
		return nil, fmt.Errorf("no saved order %q on this machine", packageID)
	}
	ids := []string{}
	for _, entry := range entries {
		if entry.IsDir() && idPattern.MatchString(entry.Name()) {
			ids = append(ids, entry.Name())
		}
	}
	sort.Sort(sort.Reverse(sort.StringSlice(ids)))
	for _, id := range ids {
		raw, readErr := os.ReadFile(filepath.Join(cfg.Paths.Runs, id, "job.json"))
		if readErr != nil {
			continue
		}
		var job Job
		if json.Unmarshal(raw, &job) != nil || job.ID != id || job.Request.PackageID != packageID {
			continue
		}
		if pkg, pkgErr := os.ReadFile(filepath.Join(cfg.Paths.Runs, id, "package.zip")); pkgErr == nil {
			return pkg, nil
		}
	}
	return nil, fmt.Errorf("no saved order %q on this machine", packageID)
}
