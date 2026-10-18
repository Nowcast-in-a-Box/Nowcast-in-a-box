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
	"time"
)

// Docker image layers are treated as already cached: this build checks that
// the configured images exist and never pulls them. Weights are prepared by
// copying from runtime.weight_source (a folder laid out like artifacts/),
// which is the local equivalent of a download.
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
	writeJSON(w, http.StatusOK, s.resourcesSnapshot())
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
	for _, model := range request.Models {
		if s.modelImage(model) == "" || modelWeightName(model) == "" {
			runtimeError(w, 422, fmt.Errorf("model %q is not configured for dataset %s", model, s.dataID()))
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
	s.preparingResources = true
	s.resourcesLoaded = true
	s.executionModels = append([]string(nil), request.Models...)
	s.currentResources = resourceSnapshot{
		Diagnostics: s.checkDiagnosticsLocked(),
		Execution:   s.executionGroupLocked(request.Models),
	}
	if len(request.Models) > 0 && s.currentResources.Execution.Status != "failed" {
		s.currentResources.Execution.Status = "preparing"
		s.currentResources.Execution.Message = "preparing selected resources"
	}
	snapshot := cloneResources(s.currentResources)
	s.resourcesMu.Unlock()
	go s.prepareExecution(request.Models)
	writeJSON(w, http.StatusOK, snapshot)
}

// RefreshResources records the current image/weight state. Handler startup
// calls it so the Hub can show prepared resources as soon as it connects.
func (s *Server) RefreshResources() {
	s.resourcesMu.Lock()
	defer s.resourcesMu.Unlock()
	if s.preparingResources {
		return
	}
	s.currentResources = resourceSnapshot{
		Diagnostics: s.checkDiagnosticsLocked(),
		Execution:   s.executionGroupLocked(s.executionModels),
	}
	s.resourcesLoaded = true
}

func (s *Server) resourcesSnapshot() resourceSnapshot {
	s.resourcesMu.Lock()
	defer s.resourcesMu.Unlock()
	if s.preparingResources || s.resourcesLoaded {
		return cloneResources(s.currentResources)
	}
	s.currentResources = resourceSnapshot{
		Diagnostics: s.checkDiagnosticsLocked(),
		Execution:   s.executionGroupLocked(s.executionModels),
	}
	s.resourcesLoaded = true
	return cloneResources(s.currentResources)
}

func (s *Server) checkDiagnosticsLocked() resourceGroup {
	image := s.Config.Diagnostics.Image
	if image == "" {
		return resourceGroup{Status: "failed", Message: "diagnostics image is not configured"}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	code, _, err := s.docker(ctx, "image", "inspect", image)
	item := resourceItem{Kind: "image", ID: image}
	if err == nil && code == 0 {
		item.Status = "ready"
		item.Message = "image is present on this computer"
		return resourceGroup{Status: "ready", Items: []resourceItem{item}}
	}
	if err != nil {
		item.Status = "failed"
		item.Message = err.Error()
		return resourceGroup{Status: "failed", Message: err.Error(), Items: []resourceItem{item}}
	}
	item.Status = "missing"
	item.Message = fmt.Sprintf("image %s is not present locally; build it with docker build -f diagnostics/Dockerfile -t %s .", image, image)
	return resourceGroup{Status: "missing", Message: item.Message, Items: []resourceItem{item}}
}

func (s *Server) executionGroupLocked(models []string) resourceGroup {
	if len(models) == 0 {
		return resourceGroup{Status: "missing", Message: "select a model before preparing execution resources", Items: []resourceItem{}}
	}
	items := make([]resourceItem, 0, 1+2*len(models))
	ingestion := s.Config.Runtime.IngestionImage
	items = append(items, s.imageItem(ingestion))
	for _, model := range models {
		items = append(items, s.imageItem(s.modelImage(model)))
		items = append(items, s.weightItem(model))
	}
	return groupFromItems(items)
}

func (s *Server) imageItem(image string) resourceItem {
	item := resourceItem{Kind: "image", ID: image}
	if image == "" {
		item.Status = "failed"
		item.Message = "image is not configured"
		return item
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	code, _, err := s.docker(ctx, "image", "inspect", image)
	if err == nil && code == 0 {
		item.Status = "ready"
		item.Message = "image is present on this computer"
		return item
	}
	if err != nil {
		item.Status = "failed"
		item.Message = err.Error()
		return item
	}
	item.Status = "missing"
	item.Message = fmt.Sprintf("image %s is not present locally; build or pull it before running", image)
	return item
}

func (s *Server) weightItem(model string) resourceItem {
	item := resourceItem{Kind: "weight", ID: model}
	path := s.weightPath(model)
	if err := verifyModelWeights(path, model); err == nil {
		item.Status = "ready"
		item.Message = "weight matches the pinned SHA-256"
		return item
	}
	item.Status = "missing"
	if s.Config.Runtime.WeightSource == "" {
		item.Message = fmt.Sprintf("weight is missing at %s; place it there or set runtime.weight_source to a folder laid out like artifacts/", path)
	} else {
		item.Message = "weight is missing and will be copied from runtime.weight_source"
	}
	return item
}

func groupFromItems(items []resourceItem) resourceGroup {
	group := resourceGroup{Status: "ready", Items: items}
	for _, item := range items {
		switch item.Status {
		case "failed":
			group.Status = "failed"
			if group.Message == "" {
				group.Message = item.Message
			}
		case "missing":
			if group.Status != "failed" {
				group.Status = "missing"
			}
			if group.Message == "" {
				group.Message = item.Message
			}
		case "preparing":
			if group.Status != "failed" {
				group.Status = "preparing"
			}
		}
	}
	return group
}

func (s *Server) prepareExecution(models []string) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	for _, image := range append([]string{s.Config.Runtime.IngestionImage}, modelImages(s, models)...) {
		item := s.imageItem(image)
		s.updateResourceItem(item)
		if item.Status != "ready" {
			s.finishPreparation()
			return
		}
	}
	for _, model := range models {
		s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "preparing", Message: "checking local weight"})
		if err := verifyModelWeights(s.weightPath(model), model); err == nil {
			s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "ready", Message: "weight matches the pinned SHA-256"})
			continue
		}
		if err := s.copyWeight(ctx, model); err != nil {
			s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "failed", Message: err.Error()})
			s.finishPreparation()
			return
		}
		s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "ready", Message: "weight copied from runtime.weight_source and verified"})
	}
	s.finishPreparation()
}

func modelImages(s *Server, models []string) []string {
	images := make([]string, 0, len(models))
	for _, model := range models {
		images = append(images, s.modelImage(model))
	}
	return images
}

func (s *Server) copyWeight(ctx context.Context, model string) error {
	source := filepath.Join(s.Config.Runtime.WeightSource, s.dataID(), model, modelWeightName(model))
	if s.Config.Runtime.WeightSource == "" {
		return fmt.Errorf("weight %s is missing; set runtime.weight_source to a folder laid out like artifacts/", model)
	}
	target := s.weightPath(model)
	info, err := os.Stat(source)
	if err != nil || !info.Mode().IsRegular() {
		return fmt.Errorf("weight %s is missing; place it at %s or configure runtime.weight_source", model, target)
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	partial := target + ".partial"
	_ = os.Remove(partial)
	file, err := os.Open(source)
	if err != nil {
		return err
	}
	defer file.Close()
	output, err := os.OpenFile(partial, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
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
				s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "preparing", Message: "copying weight from runtime.weight_source", DownloadedBytes: done, TotalBytes: total})
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
	expected, ok := weightPins[model]
	if !ok || hex.EncodeToString(hash.Sum(nil)) != expected {
		return fmt.Errorf("weight %s in runtime.weight_source does not match the pinned SHA-256; the local file was not replaced", model)
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
	for index := range s.currentResources.Execution.Items {
		if s.currentResources.Execution.Items[index].Kind == item.Kind && s.currentResources.Execution.Items[index].ID == item.ID {
			s.currentResources.Execution.Items[index] = item
			break
		}
	}
	group := groupFromItems(s.currentResources.Execution.Items)
	if s.preparingResources {
		group.Status = "preparing"
		group.Message = "preparing selected resources"
	}
	s.currentResources.Execution = group
}

func (s *Server) finishPreparation() {
	s.resourcesMu.Lock()
	defer s.resourcesMu.Unlock()
	s.currentResources.Execution = groupFromItems(s.currentResources.Execution.Items)
	s.preparingResources = false
}
