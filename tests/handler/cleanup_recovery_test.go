package handler_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

type cleanupRunner struct {
	runtimeRunner
	lock    sync.Mutex
	fail    string
	project string
	dirty   bool
}

func (f *cleanupRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	if len(args) > 0 && args[0] == "ps" {
		f.lock.Lock()
		defer f.lock.Unlock()
		if f.dirty {
			return 0, []byte(f.project + " running\n"), nil
		}
		return 0, nil, nil
	}
	joined := strings.Join(args, " ")
	if strings.Contains(joined, " down ") && ((f.fail == "inference" && strings.Contains(joined, "compose-ingestion.yaml")) || (f.fail == "diagnostics" && strings.Contains(joined, "/compose.yaml"))) {
		f.lock.Lock()
		defer f.lock.Unlock()
		for i, arg := range args {
			if arg == "-p" {
				f.project = args[i+1]
			}
		}
		f.dirty = true
		return 1, []byte("cleanup not confirmed"), nil
	}
	return f.runtimeRunner.Run(ctx, args...)
}
func TestSameRunRequeueCannotHideFailedCleanup(t *testing.T) {
	server, cfg := automaticServer(t)
	runner := &cleanupRunner{runtimeRunner: runtimeRunner{root: cfg.Paths.Runs}, fail: "inference"}
	server.Runner = runner
	job := waitJob(t, server, submitSelection(t, server, "S700006", []string{"wadepre"}, "metrics", "first").ID, "failed")
	if job.ID == "" {
		t.Fatal("missing job")
	}
	// Same sample and models: admission would reuse this run if reconciliation
	// treated the old record as exempt.
	raw, hash := selectionPackage(t, "S700006", []string{"wadepre"}, "metrics")
	body, _ := json.Marshal(map[string]any{"package": raw, "sha256": hash})
	request := httptest.NewRequest("POST", "http://127.0.0.1/v1/jobs", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer token")
	request.Header.Set("Idempotency-Key", "second")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	if response.Code != 409 || !strings.Contains(response.Body.String(), "prior Docker project") {
		t.Fatal("same-run requeue bypassed cleanup recovery", response.Code, response.Body.String())
	}
}

func TestFailedCleanupBlocksNewExecutionAndStopAcrossRestart(t *testing.T) {
	for _, stage := range []string{"inference", "diagnostics"} {
		t.Run(stage, func(t *testing.T) {
			server, cfg := automaticServer(t)
			runner := &cleanupRunner{runtimeRunner: runtimeRunner{root: cfg.Paths.Runs}, fail: stage}
			server.Runner = runner
			expected := "partial"
			if stage == "inference" {
				expected = "failed"
			}
			job := waitJob(t, server, submitSelection(t, server, "S700004", []string{"wadepre"}, "metrics", stage).ID, expected)
			if !strings.Contains(job.Error, "cleanup") {
				t.Fatal("cleanup exit ignored", job)
			}
			for _, instance := range []*handler.Server{server, handler.NewServer(cfg)} {
				instance.Runner = runner
				raw, hash := selectionPackage(t, "S700005", []string{"wadepre"}, "")
				body, _ := json.Marshal(map[string]any{"package": raw, "sha256": hash})
				req := httptest.NewRequest("POST", "http://127.0.0.1/v1/jobs", bytes.NewReader(body))
				req.Header.Set("Authorization", "Bearer token")
				response := httptest.NewRecorder()
				instance.Routes().ServeHTTP(response, req)
				if response.Code != 409 || !strings.Contains(response.Body.String(), "prior Docker project") {
					t.Fatal(response.Code, response.Body.String())
				}
				if stopped := stopRequest(instance, "", "token"); stopped.Code != 409 {
					t.Fatal("stop bypassed uncertain cleanup", stopped.Code)
				}
			}
			runner.lock.Lock()
			runner.dirty = false
			runner.lock.Unlock()
			if stopped := stopRequest(server, "", "token"); stopped.Code != 202 {
				t.Fatal(stopped.Code, stopped.Body.String())
			}
		})
	}
}
func TestHistoricalJobPollCannotHidePriorInferenceContainer(t *testing.T) {
	server, cfg := automaticServer(t)
	directory := filepath.Join(cfg.Paths.Runs, "historical-run")
	if err := os.Mkdir(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(handler.Job{ID: "historical-run", Status: "running", Stage: "wadepre"})
	if err := os.WriteFile(filepath.Join(directory, "job.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "compose-wadepre.yaml"), []byte("fixture"), 0o600); err != nil {
		t.Fatal(err)
	}
	server.Runner = &recoveryRunner{state: "nib-historical-run-wadepre running\n"}
	poll := httptest.NewRequest("GET", "http://127.0.0.1/v1/jobs/historical-run", nil)
	poll.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, poll)
	if response.Code != 200 || !strings.Contains(response.Body.String(), "interrupted") {
		t.Fatal(response.Code, response.Body.String())
	}
	if stop := stopRequest(server, "", "token"); stop.Code != 409 {
		t.Fatal("polled history bypassed recovery", stop.Code, stop.Body.String())
	}
}
