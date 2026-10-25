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
// the packaged images exist and never pulls them. Missing weights are copied
// from paths.weight_source, laid out like artifacts/<data>/<model>/<file>.
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
	if dataID == "" {
		dataID = s.executionDataID
	}
	writeJSON(w, http.StatusOK, s.resourcesSnapshot(dataID))
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
	s.resourcesMu.Lock()
	if s.preparingResources {
		snapshot := cloneResources(s.currentResources)
		s.resourcesMu.Unlock()
		writeJSON(w, http.StatusOK, snapshot)
		return
	}
	s.preparingResources = true
	s.resourcesLoaded = true
	s.executionDataID = request.DataID
	s.executionModels = append([]string(nil), request.Models...)
	s.currentResources = resourceSnapshot{
		Diagnostics: s.checkDiagnosticsLocked(),
		Execution:   s.executionGroupLocked(request.DataID, request.Models),
	}
	if len(request.Models) > 0 && s.currentResources.Execution.Status != "failed" {
		s.currentResources.Execution.Status = "preparing"
		s.currentResources.Execution.Message = "preparing selected resources"
	}
	snapshot := cloneResources(s.currentResources)
	s.resourcesMu.Unlock()
	go s.prepareExecution(request.DataID, request.Models)
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
		Execution:   s.executionGroupLocked(s.executionDataID, s.executionModels),
	}
	s.resourcesLoaded = true
}

func (s *Server) resourcesSnapshot(dataID string) resourceSnapshot {
	s.resourcesMu.Lock()
	defer s.resourcesMu.Unlock()
	if s.preparingResources || s.resourcesLoaded {
		return cloneResources(s.currentResources)
	}
	s.currentResources = resourceSnapshot{
		Diagnostics: s.checkDiagnosticsLocked(),
		Execution:   s.executionGroupLocked(dataID, nil),
	}
	s.resourcesLoaded = true
	return cloneResources(s.currentResources)
}

func (s *Server) checkDiagnosticsLocked() resourceGroup {
	if s.Resources == nil || s.Resources.Diagnostics == nil {
		return resourceGroup{Status: "failed", Message: s.resourceErrorText("diagnostics resources are not loaded")}
	}
	image := s.Resources.Diagnostics.Container.Image
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
	if s.currentConfig().Runtime.ImageRegistry != "" {
		item.Message = fmt.Sprintf("image %s is not present locally; Handler will pull it from %s", image, s.currentConfig().Runtime.ImageRegistry)
	} else {
		item.Message = fmt.Sprintf("image %s is not present locally; build it with docker build -f %s .", image, buildHint(s.Resources.Diagnostics.Container.Build, "diagnostics/Dockerfile"))
	}
	return resourceGroup{Status: "missing", Message: item.Message, Items: []resourceItem{item}}
}

func (s *Server) executionGroupLocked(dataID string, models []string) resourceGroup {
	if dataID == "" || len(models) == 0 {
		return resourceGroup{Status: "missing", Message: "select a data source and model before preparing execution resources", Items: []resourceItem{}}
	}
	data, err := s.dataResource(dataID)
	if err != nil {
		return resourceGroup{Status: "failed", Message: err.Error(), Items: []resourceItem{}}
	}
	items := make([]resourceItem, 0, 1+2*len(models))
	items = append(items, s.imageItem(data.Container.Image))
	for _, model := range models {
		resource, err := s.modelResource(dataID, model)
		if err != nil {
			items = append(items, resourceItem{Kind: "weight", ID: model, Status: "failed", Message: err.Error()})
			continue
		}
		items = append(items, s.imageItem(resource.Container.Image))
		items = append(items, s.weightItem(dataID, model, resource))
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
	item.Message = fmt.Sprintf("image %s is not present locally; start Handler or Validate to pull it from the configured registry", image)
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
	if s.Config.Paths.WeightSource == "" {
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

func (s *Server) prepareExecution(dataID string, models []string) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	data, err := s.dataResource(dataID)
	if err != nil {
		s.finishPreparation()
		return
	}
	images := []string{data.Container.Image}
	for _, model := range models {
		if resource, err := s.modelResource(dataID, model); err == nil {
			images = append(images, resource.Container.Image)
		}
	}
	for _, image := range images {
		item := s.imageItem(image)
		s.updateResourceItem(item)
		if item.Status != "ready" {
			s.finishPreparation()
			return
		}
	}
	for _, model := range models {
		resource, err := s.modelResource(dataID, model)
		if err != nil {
			s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "failed", Message: err.Error()})
			s.finishPreparation()
			return
		}
		if resource.Weights.Embedded {
			s.updateResourceItem(s.weightItem(dataID, model, resource))
			continue
		}
		s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "preparing", Message: "checking local weight"})
		weight, err := s.weightPath(dataID, model)
		if err == nil {
			err = verifyWeight(weight, resource.Weights.SHA256)
		}
		if err == nil {
			s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "ready", Message: "weight matches the pinned SHA-256"})
			continue
		}
		if err := s.copyWeight(ctx, dataID, model, resource, weight); err != nil {
			s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "failed", Message: err.Error()})
			s.finishPreparation()
			return
		}
		s.updateResourceItem(resourceItem{Kind: "weight", ID: model, Status: "ready", Message: "weight copied from paths.weight_source and verified"})
	}
	s.finishPreparation()
}

func (s *Server) copyWeight(ctx context.Context, dataID, modelID string, resource *ModelResource, target string) error {
	if s.Config.Paths.WeightSource == "" {
		return fmt.Errorf("weight %s is missing; set paths.weight_source to a folder laid out like artifacts/", modelID)
	}
	source := filepath.Join(s.Config.Paths.WeightSource, dataID, modelID, resource.Weights.File)
	info, err := os.Stat(source)
	if err != nil || !info.Mode().IsRegular() {
		return fmt.Errorf("weight %s is missing; place it at %s or configure paths.weight_source", modelID, target)
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
