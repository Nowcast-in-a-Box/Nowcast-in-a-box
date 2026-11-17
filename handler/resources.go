package handler

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Resource inspection never hashes weights; verification belongs to preparation.
type resourceItem struct {
	Kind            string `json:"kind"`
	ID              string `json:"id"`
	Status          string `json:"status"`
	Message         string `json:"message,omitempty"`
	DownloadedBytes int64  `json:"downloaded_bytes,omitempty"`
	TotalBytes      int64  `json:"total_bytes,omitempty"`
}

type resourceGroup struct {
	Status  string         `json:"status"`
	Message string         `json:"message,omitempty"`
	Items   []resourceItem `json:"items"`
}

type resourceSnapshot struct {
	Diagnostics resourceGroup `json:"diagnostics"`
	Execution   resourceGroup `json:"execution"`
}

func (s *Server) handleResources(w http.ResponseWriter, r *http.Request) {
	dataID := r.URL.Query().Get("data_id")
	snapshot := s.resourcesSnapshot(dataID)
	if r.URL.Query().Get("refresh") == "true" {
		snapshot = s.inspectResources(r.Context(), dataID)
	}
	writeJSON(w, http.StatusOK, snapshot)
}

func cloneResources(snapshot resourceSnapshot) resourceSnapshot {
	clone := func(group resourceGroup) resourceGroup {
		group.Items = append([]resourceItem(nil), group.Items...)
		return group
	}
	return resourceSnapshot{Diagnostics: clone(snapshot.Diagnostics), Execution: clone(snapshot.Execution)}
}

func (s *Server) handlePrepareResources(w http.ResponseWriter, r *http.Request) {
	var request struct {
		DataID string   `json:"data_id"`
		Models []string `json:"models"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		runtimeError(w, 400, err)
		return
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		runtimeError(w, 400, fmt.Errorf("one request object required"))
		return
	}
	if _, err := s.dataResource(request.DataID); err != nil {
		runtimeError(w, 422, err)
		return
	}
	for _, model := range request.Models {
		if _, err := s.modelResource(request.DataID, model); err != nil {
			runtimeError(w, 422, err)
			return
		}
	}
	if !s.recoveryMu.TryLock() {
		runtimeError(w, 409, fmt.Errorf("wait for active tasks"))
		return
	}
	defer s.recoveryMu.Unlock()
	s.executionMu.Lock()
	busy := len(s.executions) > 0
	s.executionMu.Unlock()
	if busy {
		runtimeError(w, 409, fmt.Errorf("wait for active tasks"))
		return
	}
	s.jobsMu.Lock()
	defer s.jobsMu.Unlock()
	if s.stopping {
		runtimeError(w, 409, fmt.Errorf("Handler is stopping"))
		return
	}
	for _, job := range s.jobs {
		if job.Status == "queued" || job.Status == "running" {
			runtimeError(w, 409, fmt.Errorf("wait for active tasks"))
			return
		}
	}
	s.resourcesMu.Lock()
	if s.preparingResources {
		snapshot := cloneResources(s.currentResources)
		s.resourcesMu.Unlock()
		writeJSON(w, http.StatusOK, snapshot)
		return
	}
	s.executionDataID = request.DataID
	s.executionModels = append([]string(nil), request.Models...)
	s.currentResources = s.pendingResources(request.DataID, request.Models)
	s.preparingResources = true
	s.resourcesLoaded = true
	snapshot := cloneResources(s.currentResources)
	s.resourcesMu.Unlock()
	go s.prepareExecution(request.DataID, request.Models)
	writeJSON(w, http.StatusOK, snapshot)
}

// RefreshResources performs an explicit bounded inspection without hashing weights.
func (s *Server) RefreshResources() {
	if !s.recoveryMu.TryLock() {
		return
	}
	defer s.recoveryMu.Unlock()
	s.executionMu.Lock()
	busy := len(s.executions) > 0
	s.executionMu.Unlock()
	if busy {
		return
	}
	s.resourcesMu.Lock()
	if s.preparingResources {
		s.resourcesMu.Unlock()
		return
	}
	dataID := s.executionDataID
	s.resourcesMu.Unlock()
	snapshot := s.inspectResources(s.serviceContext(), dataID)
	s.resourcesMu.Lock()
	defer s.resourcesMu.Unlock()
	if !s.preparingResources && s.executionDataID == dataID {
		s.currentResources = snapshot
		s.resourcesLoaded = true
	}
}

func (s *Server) pendingResources(dataID string, models []string) resourceSnapshot {
	snapshot := resourceSnapshot{}
	if s.Resources == nil || s.Resources.Diagnostics == nil {
		snapshot.Diagnostics = resourceGroup{Status: "failed", Message: s.resourceErrorText("diagnostics resources are not loaded")}
	} else {
		snapshot.Diagnostics = groupFromItems([]resourceItem{{Kind: "image", ID: s.Resources.Diagnostics.Container.Image, Status: "preparing"}})
	}
	data, err := s.dataResource(dataID)
	if err != nil {
		snapshot.Execution = resourceGroup{Status: "missing", Message: err.Error()}
		return snapshot
	}
	items := []resourceItem{{Kind: "image", ID: data.Container.Image, Status: "preparing"}}
	for _, id := range models {
		model, err := s.modelResource(dataID, id)
		if err != nil {
			items = append(items, resourceItem{Kind: "weight", ID: id, Status: "failed", Message: err.Error()})
			continue
		}
		items = append(items, resourceItem{Kind: "image", ID: model.Container.Image, Status: "preparing"}, resourceItem{Kind: "weight", ID: id, Status: "preparing"})
	}
	snapshot.Execution = groupFromItems(items)
	return snapshot
}

func (s *Server) resourcesSnapshot(dataID string) resourceSnapshot {
	s.resourcesMu.Lock()
	defer s.resourcesMu.Unlock()
	if s.preparingResources {
		return cloneResources(s.currentResources)
	}
	if dataID == "" {
		dataID = s.executionDataID
	}
	snapshot := cloneResources(s.currentResources)
	if !s.resourcesLoaded || dataID != s.executionDataID {
		var models []string
		if s.Resources != nil {
			models = s.Resources.ModelIDs(dataID)
		}
		snapshot = s.pendingResources(dataID, models)
	}
	// Describe the last preparation without certifying the current Docker daemon.
	for _, group := range []*resourceGroup{&snapshot.Diagnostics, &snapshot.Execution} {
		for i := range group.Items {
			if group.Items[i].Status == "ready" {
				group.Items[i].Status = "prepared"
				group.Items[i].Message = "last preparation passed; execution checks again"
			} else if group.Items[i].Status != "failed" {
				group.Items[i].Status = "unknown"
				group.Items[i].Message = "refresh to inspect; prepare to verify weights"
			}
		}
		if len(group.Items) > 0 {
			*group = groupFromItems(group.Items)
		}
	}
	return snapshot
}

func (s *Server) inspectResources(parent context.Context, dataID string) resourceSnapshot {
	snapshot := s.resourcesSnapshot(dataID)
	s.resourcesMu.Lock()
	preparing := s.preparingResources
	s.resourcesMu.Unlock()
	if preparing {
		return snapshot
	}
	ctx, cancel := context.WithTimeout(parent, 8*time.Second)
	defer cancel()
	daemonErr := s.imageDaemonReady(ctx)
	for _, group := range []*resourceGroup{&snapshot.Diagnostics, &snapshot.Execution} {
		for i, item := range group.Items {
			if item.Kind != "image" {
				continue
			}
			// Preserve preparation failures until another prepare attempt succeeds.
			if item.Status == "failed" {
				continue
			}
			item.Status, item.Message = "missing", "image is missing; prepare to pull from configured sources"
			if daemonErr != nil {
				item.Status, item.Message = "failed", daemonErr.Error()
			} else if s.imagePresent(ctx, item.ID) {
				item.Status, item.Message = "ready", "image is present on the current Docker daemon"
			}
			group.Items[i] = item
		}
		if len(group.Items) > 0 {
			*group = groupFromItems(group.Items)
		}
	}
	return snapshot
}

func (s *Server) imageItem(image string) resourceItem {
	ctx, cancel := context.WithTimeout(s.serviceContext(), 5*time.Second)
	defer cancel()
	item := resourceItem{Kind: "image", ID: image, Status: "missing"}
	if s.imagePresent(ctx, image) {
		item.Status = "ready"
	}
	return item
}

func (s *Server) weightItem(dataID, modelID string, resource *ModelResource) resourceItem {
	item := resourceItem{Kind: "weight", ID: modelID}
	if resource.Weights.Embedded {
		image := s.imageItem(resource.Container.Image)
		item.Status = image.Status
		item.Message = "weights are embedded in the model image; SHA-256 is verified when inference starts"
		if image.Status != "ready" {
			item.Message = image.Message
		}
		return item
	}
	path, err := s.weightPath(dataID, modelID)
	if err != nil {
		item.Status = "failed"
		item.Message = err.Error()
		return item
	}
	if err := verifyWeight(path, resource.Weights.SHA256); err == nil {
		item.Status = "ready"
		item.Message = "weight matches the pinned SHA-256"
		return item
	}
	item.Status = "missing"
	if s.currentConfig().Paths.WeightSource == "" {
		item.Message = fmt.Sprintf("weight is missing at %s; place it there or set paths.weight_source to a folder laid out like artifacts/", path)
	} else {
		item.Message = "weight is missing and will be copied from paths.weight_source"
	}
	return item
}

func groupFromItems(items []resourceItem) resourceGroup {
	group := resourceGroup{Status: "ready", Items: items}
	for _, item := range items {
		switch item.Status {
		case "failed":
			if group.Status != "failed" || group.Message == "" {
				group.Message = item.Message
			}
			group.Status = "failed"
		case "missing":
			if group.Status != "failed" {
				group.Status = "missing"
			}
			if group.Message == "" {
				group.Message = item.Message
			}
		case "prepared":
			if group.Status == "ready" {
				group.Status = "prepared"
			}
		case "unknown":
			if group.Status == "ready" || group.Status == "prepared" {
				group.Status = "unknown"
			}
		case "preparing":
			if group.Status != "failed" {
				group.Status = "preparing"
			}
		}
	}
	return group
}

func (s *Server) prepareExecution(dataID string, models []string) {
	ctx, cancel := context.WithTimeout(s.serviceContext(), 30*time.Minute)
	defer cancel()
	defer s.finishPreparation()
	s.resourcesMu.Lock()
	snapshot := cloneResources(s.currentResources)
	s.resourcesMu.Unlock()
	for _, group := range []resourceGroup{snapshot.Diagnostics, snapshot.Execution} {
		for _, item := range group.Items {
			if item.Kind != "image" {
				continue
			}
			_, err := s.ensureImage(ctx, item.ID)
			item.Status, item.Message = "ready", "image is ready"
			if err != nil {
				item.Status, item.Message = "failed", err.Error()
			}
			s.updateResourceItem(item)
		}
	}
	for _, model := range models {
		item := resourceItem{Kind: "weight", ID: model, Status: "ready", Message: "weight matches the pinned SHA-256"}
		if err := s.prepareModelWeight(ctx, dataID, model); err != nil {
			item.Status, item.Message = "failed", err.Error()
		}
		s.updateResourceItem(item)
	}
}

// prepareModelWeight is shared by explicit preparation and execution preflight.
func (s *Server) prepareModelWeight(ctx context.Context, dataID, modelID string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	resource, err := s.modelResource(dataID, modelID)
	if err != nil {
		return err
	}
	if resource.Weights.Embedded {
		file := resource.Weights.File
		if file == "" || filepath.Base(file) != file || strings.ContainsAny(file, "/\\") || file == "." || file == ".." {
			return fmt.Errorf("invalid embedded weight filename")
		}
		if _, err := hex.DecodeString(resource.Weights.SHA256); err != nil || len(resource.Weights.SHA256) != 64 {
			return fmt.Errorf("invalid pinned weight SHA-256")
		}
		if _, err := s.ensureImage(ctx, resource.Container.Image); err != nil {
			return err
		}
		const script = `import hashlib,sys
h=hashlib.sha256()
with open(sys.argv[1],'rb') as f:
 while True:
  b=f.read(1048576)
  if not b: break
  h.update(b)
sys.exit(0 if h.hexdigest()==sys.argv[2] else 1)`
		code, out, err := s.docker(ctx, "run", "--rm", "--network", "none", "--read-only", "--pull", "never", "--entrypoint", "python", resource.Container.Image, "-c", script, "/opt/nib/weights/"+file, resource.Weights.SHA256)
		if err != nil || code != 0 {
			return fmt.Errorf("embedded weight %s verification failed: %v %s", modelID, err, strings.TrimSpace(string(out)))
		}
		return nil
	}
	target := filepath.Join(s.currentConfig().Paths.Artifacts, dataID, modelID, resource.Weights.File)
	if err := verifyWeight(target, resource.Weights.SHA256); err == nil {
		return ctx.Err()
	}
	return s.copyWeight(ctx, dataID, modelID, resource, target)
}

func (s *Server) copyWeight(ctx context.Context, dataID, modelID string, resource *ModelResource, target string) error {
	if s.currentConfig().Paths.WeightSource == "" {
		return fmt.Errorf("weight %s is missing; set paths.weight_source to a folder laid out like artifacts/", modelID)
	}
	source := filepath.Join(s.currentConfig().Paths.WeightSource, dataID, modelID, resource.Weights.File)
	info, err := os.Stat(source)
	if err != nil || !info.Mode().IsRegular() {
		return fmt.Errorf("weight %s is missing; place it at %s or configure paths.weight_source", modelID, target)
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	file, err := os.Open(source)
	if err != nil {
		return err
	}
	defer file.Close()
	output, err := os.CreateTemp(filepath.Dir(target), ".weight-*.partial")
	if err != nil {
		return err
	}
	partial := output.Name()
	remove := true
	defer func() {
		output.Close()
		if remove {
			_ = os.Remove(partial)
		}
	}()
	total := info.Size()
	hash := sha256.New()
	buffer := make([]byte, 1<<20)
	var done int64
	last := time.Time{}
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		n, readErr := file.Read(buffer)
		if n > 0 {
			if _, err := output.Write(buffer[:n]); err != nil {
				return err
			}
			_, _ = hash.Write(buffer[:n])
			done += int64(n)
			if time.Since(last) > 100*time.Millisecond || done == total {
				last = time.Now()
				s.updateResourceItem(resourceItem{Kind: "weight", ID: modelID, Status: "preparing", Message: "copying weight from paths.weight_source", DownloadedBytes: done, TotalBytes: total})
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return readErr
		}
	}
	if err := output.Close(); err != nil {
		return err
	}
	if hex.EncodeToString(hash.Sum(nil)) != resource.Weights.SHA256 {
		return fmt.Errorf("weight %s in paths.weight_source does not match the pinned SHA-256; the local file was not replaced", modelID)
	}
	if err := os.Rename(partial, target); err != nil {
		return err
	}
	remove = false
	return nil
}

func (s *Server) updateResourceItem(item resourceItem) {
	s.resourcesMu.Lock()
	defer s.resourcesMu.Unlock()
	for _, group := range []*resourceGroup{&s.currentResources.Diagnostics, &s.currentResources.Execution} {
		for index := range group.Items {
			if group.Items[index].Kind == item.Kind && group.Items[index].ID == item.ID {
				group.Items[index] = item
			}
		}
		if len(group.Items) > 0 {
			*group = groupFromItems(group.Items)
		}
	}
}

func (s *Server) finishPreparation() {
	s.resourcesMu.Lock()
	defer s.resourcesMu.Unlock()
	s.currentResources.Execution = groupFromItems(s.currentResources.Execution.Items)
	s.preparingResources = false
}

func buildHint(build *ResourceBuild, fallback string) string {
	if build == nil || build.Dockerfile == "" {
		return fallback
	}
	return build.Dockerfile
}

func (s *Server) resourceErrorText(fallback string) string {
	if s.ResourceError != nil {
		return s.ResourceError.Error()
	}
	return fallback
}
