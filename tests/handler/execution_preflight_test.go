package handler_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

// Real fixture bytes and matching pins exercise the production checksum gate.
func writeExecutionWeights(t *testing.T, server *handler.Server) {
	t.Helper()
	for dataID, models := range server.Resources.Models {
		for modelID, model := range models {
			if model.Weights.Embedded {
				continue
			}
			body := []byte("fixture weight for " + dataID + "/" + modelID)
			sum := sha256.Sum256(body)
			model.Weights.SHA256 = hex.EncodeToString(sum[:])
			target := filepath.Join(server.Config.Paths.Artifacts, dataID, modelID, model.Weights.File)
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(target, body, 0o600); err != nil {
				t.Fatal(err)
			}
		}
	}
}

type executionGateRunner struct {
	runtimeRunner
	failWindow   string
	failEmbedded bool
}

func (r *executionGateRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	joined := " " + strings.Join(args, " ") + " "
	if (r.failWindow != "" && strings.Contains(joined, " --check-window ") && strings.Contains(joined, r.failWindow)) || (r.failEmbedded && strings.Contains(joined, "/opt/nib/weights/")) {
		r.mu.Lock()
		r.calls = append(r.calls, append([]string{}, args...))
		r.mu.Unlock()
		return 1, []byte("fixture verification failure"), nil
	}
	return r.runtimeRunner.Run(ctx, args...)
}

func TestExecutionPreflightSharedGate(t *testing.T) {
	for _, entry := range []string{"manual", "order", "trigger"} {
		for _, failure := range []string{"none", "window", "weight", "batch-window"} {
			t.Run(entry+"/"+failure, func(t *testing.T) {
				cfg := consoleConfig(t)
				server := handler.NewServer(cfg)
				writeExecutionWeights(t, server)
				runner := &executionGateRunner{runtimeRunner: runtimeRunner{root: cfg.Paths.Runs}}
				server.Runner = runner
				order := orderFixture()
				wantCheck := "weight:wadepre"
				if failure == "window" {
					runner.failWindow = order.SampleStarts[0]
					wantCheck = "window"
				}
				if failure == "batch-window" {
					order.SampleStarts = append(order.SampleStarts, "2017-06-27T00:40:00Z")
					runner.failWindow = order.SampleStarts[1]
					wantCheck = "window:2"
				}
				if failure == "weight" {
					target := filepath.Join(cfg.Paths.Artifacts, "sevir", "wadepre", server.Resources.Models["sevir"]["wadepre"].Weights.File)
					if err := os.WriteFile(target, []byte("corrupt"), 0o600); err != nil {
						t.Fatal(err)
					}
				}
				path := "/v1/jobs"
				body := map[string]any{"order": order}
				if entry != "manual" {
					out, saved := triggerDo(t, server, "POST", "/v1/orders", body)
					if out.Code != 201 {
						t.Fatal(out.Body.String())
					}
					path, body = "/v1/orders/"+order.OrderID+"/runs", map[string]any{"revision": saved["revision"]}
					if entry == "trigger" {
						id := createTrigger(t, server, map[string]any{"package_id": order.OrderID, "kind": "schedule", "interval_minutes": 60, "start_source": "order"})
						path, body = "/v1/triggers/"+id+"/fire", nil
					}
				}
				out, submitted := triggerDo(t, server, "POST", path, body)
				if out.Code != 202 {
					t.Fatalf("admission: %d %s", out.Code, out.Body.String())
				}
				id, _ := submitted["id"].(string)
				if entry == "trigger" {
					id, _ = submitted["last_run_id"].(string)
				}
				wantStatus := "failed"
				if failure == "none" {
					wantStatus = "succeeded"
				}
				job := waitJob(t, server, id, wantStatus)
				if len(job.Preflight) == 0 {
					t.Fatal("job lost preflight checks")
				}
				last := job.Preflight[len(job.Preflight)-1]
				if last.ID != wantCheck || last.OK != (failure == "none") {
					t.Fatalf("checks: %+v", job.Preflight)
				}
				if job.ExecutionContext == nil || job.ExecutionContext.DataPath == "" || job.ExecutionContext.Weights["wadepre"] != server.Resources.Models["sevir"]["wadepre"].Weights.SHA256 {
					t.Fatalf("execution context: %+v", job.ExecutionContext)
				}
				raw, err := os.ReadFile(filepath.Join(cfg.Paths.Runs, id, "job.json"))
				if err != nil {
					t.Fatal(err)
				}
				var persisted handler.Job
				if err := json.Unmarshal(raw, &persisted); err != nil {
					t.Fatal(err)
				}
				if persisted.Status != wantStatus || !reflect.DeepEqual(persisted.Preflight, job.Preflight) || !reflect.DeepEqual(persisted.ExecutionContext, job.ExecutionContext) {
					t.Fatalf("job evidence not persisted: %s", raw)
				}
				runner.mu.Lock()
				defer runner.mu.Unlock()
				compose := 0
				for _, call := range runner.calls {
					if len(call) > 0 && call[0] == "compose" {
						compose++
					}
				}
				if failure != "none" && (compose != 0 || job.Stage != "preflight") {
					t.Fatalf("failed gate reached Compose: %+v, %v", job, runner.calls)
				}
				if failure == "none" && compose == 0 {
					t.Fatal("successful gate did not execute")
				}
			})
		}
	}
}

func TestExecutionPreflightEmbeddedWeightFailureStopsInference(t *testing.T) {
	cfg := consoleConfig(t)
	writeCmaData(t, &cfg)
	server := handler.NewServer(cfg)
	runner := &executionGateRunner{runtimeRunner: runtimeRunner{root: cfg.Paths.Runs}, failEmbedded: true}
	server.Runner = runner
	order := orderFixture()
	order.DataID, order.EventID, order.Models = "cma_radar", "20170627", []string{"nowcastnet"}
	out, submitted := triggerDo(t, server, "POST", "/v1/jobs", map[string]any{"order": order})
	if out.Code != 202 {
		t.Fatal(out.Body.String())
	}
	job := waitJob(t, server, submitted["id"].(string), "failed")
	if len(job.Preflight) == 0 || job.Preflight[len(job.Preflight)-1].ID != "weight:nowcastnet" || !strings.Contains(job.Error, "verification failed") {
		t.Fatalf("wrong failure: %+v", job)
	}
	runner.mu.Lock()
	defer runner.mu.Unlock()
	verified := false
	for _, call := range runner.calls {
		if len(call) > 0 && call[0] == "compose" {
			t.Fatal("inference reached Compose", call)
		}
		if strings.Contains(strings.Join(call, " "), "/opt/nib/weights/") {
			verified = true
			if call[len(call)-1] != server.Resources.Models["cma_radar"]["nowcastnet"].Weights.SHA256 {
				t.Fatal("embedded check omitted pinned checksum", call)
			}
		}
	}
	if !verified {
		t.Fatal("embedded weight was never checked")
	}
}

func TestRunRerunUsesFrozenOrderAndCurrentDataPath(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	writeExecutionWeights(t, server)
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	order := orderFixture()
	out, saved := triggerDo(t, server, "POST", "/v1/orders", map[string]any{"order": order})
	if out.Code != 201 {
		t.Fatal(out.Body.String())
	}
	out, submitted := triggerDo(t, server, "POST", "/v1/orders/"+order.OrderID+"/runs", map[string]any{"revision": saved["revision"]})
	if out.Code != 202 {
		t.Fatal(out.Body.String())
	}
	first := waitJob(t, server, submitted["id"].(string), "succeeded")
	frozenPath := filepath.Join(cfg.Paths.Runs, first.ID, "order.yaml")
	before, err := os.ReadFile(frozenPath)
	if err != nil {
		t.Fatal(err)
	}
	order.Models = []string{"nowcastnet"}
	out, _ = triggerDo(t, server, "PATCH", "/v1/orders/"+order.OrderID, map[string]any{"order": order, "revision": saved["revision"]})
	if out.Code != 200 {
		t.Fatal(out.Body.String())
	}
	newRoot := writeSevirData(t, &cfg)
	server.Config = cfg
	out, submitted = triggerDo(t, server, "POST", "/v1/runs/"+first.ID+"/rerun", nil)
	if out.Code != 202 {
		t.Fatalf("rerun: %d %s", out.Code, out.Body.String())
	}
	second := waitJob(t, server, submitted["id"].(string), "succeeded")
	if second.ID == first.ID || second.Request.OrderRevision != first.Request.OrderRevision || !reflect.DeepEqual(second.Request.Models, first.Request.Models) {
		t.Errorf("rerun lost frozen selection: %+v", second)
	}
	wantPath, err := filepath.EvalSymlinks(filepath.Join(newRoot, "SEVIR", "processed", "sevir_vil.h5"))
	if err != nil {
		t.Fatal(err)
	}
	if second.ExecutionContext == nil || second.ExecutionContext.DataPath != wantPath || second.ExecutionContext.DataPath == first.ExecutionContext.DataPath {
		t.Fatalf("rerun did not resolve current local path: %+v", second.ExecutionContext)
	}
	after, err := os.ReadFile(frozenPath)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("rerun changed original frozen order", err)
	}
	repeated, err := os.ReadFile(filepath.Join(cfg.Paths.Runs, second.ID, "order.yaml"))
	if err != nil || !bytes.Equal(before, repeated) {
		t.Fatal("rerun changed frozen YAML", err)
	}
}

func TestRunRerunLegacyWithoutFrozenOrderReturnsConflict(t *testing.T) {
	cfg := consoleConfig(t)
	directory := filepath.Join(cfg.Paths.Runs, "legacy-run")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), []byte(`{"schema":"nib.run.v1","id":"legacy-run","forecasts":{}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	server := handler.NewServer(cfg)
	server.Runner = forbiddenOrderRunner{t}
	out, _ := triggerDo(t, server, "POST", "/v1/runs/legacy-run/rerun", nil)
	if out.Code != 409 || !strings.Contains(out.Body.String(), "frozen YAML") {
		t.Fatalf("legacy rerun: %d %s", out.Code, out.Body.String())
	}
	if ids := runIDs(t, cfg); len(ids) != 1 {
		t.Fatalf("legacy rerun created a job: %v", ids)
	}
}
