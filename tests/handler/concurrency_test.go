package handler_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

// Block at Docker up, outside runtimeRunner's bookkeeping lock.
type concurrentRunner struct {
	base        *runtimeRunner
	entered     chan string
	release     chan struct{}
	done        chan struct{}
	diagnostics bool
}

func (r *concurrentRunner) Run(ctx context.Context, args ...string) (int, []byte, error) {
	joined := " " + strings.Join(args, " ") + " "
	if strings.Contains(joined, " up ") && (strings.Contains(joined, "compose-ingestion.yaml") || r.diagnostics && strings.Contains(joined, "diagnostics-")) {
		r.entered <- joined
		select {
		case <-r.release:
		case <-r.done:
		case <-ctx.Done():
			return 1, nil, ctx.Err()
		}
	}
	return r.base.Run(ctx, args...)
}

func concurrentServer(t *testing.T, limit int, diagnostics bool) (*handler.Server, handler.Config, *concurrentRunner, func()) {
	t.Helper()
	cfg, _, _ := triggerServer(t)
	cfg.Runtime.MaxConcurrentJobs = limit
	server := handler.NewServer(cfg)
	runner := &concurrentRunner{base: &runtimeRunner{root: cfg.Paths.Runs}, entered: make(chan string, 16), release: make(chan struct{}), done: make(chan struct{}), diagnostics: diagnostics}
	server.Runner = runner
	writeExecutionWeights(t, server)
	var once sync.Once
	unblock := func() { once.Do(func() { close(runner.done) }) }
	t.Cleanup(func() { unblock(); waitExecutionIdle(t, server) })
	return server, cfg, runner, unblock
}

func waitExecutionIdle(t *testing.T, server *handler.Server) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for !server.ExecutionAvailableForTest() {
		if time.Now().After(deadline) {
			t.Error("execution did not release all reservations")
			return
		}
		time.Sleep(time.Millisecond)
	}
}

func awaitExecution(t *testing.T, runner *concurrentRunner) string {
	t.Helper()
	select {
	case call := <-runner.entered:
		return call
	case <-time.After(2 * time.Second):
		t.Fatal("independent execution did not start")
		return ""
	}
}

func noExecution(t *testing.T, runner *concurrentRunner) {
	t.Helper()
	select {
	case call := <-runner.entered:
		t.Fatalf("unexpected execution: %s", call)
	case <-time.After(20 * time.Millisecond):
	}
}

func TestIndependentJobsOverlapAndQueue(t *testing.T) {
	for _, limit := range []int{1, 2} {
		t.Run(fmt.Sprint(limit), func(t *testing.T) {
			server, _, runner, unblock := concurrentServer(t, limit, false)
			for i := 0; i < limit; i++ {
				submitSelection(t, server, fmt.Sprintf("S71014%d", i), []string{"wadepre"}, "", fmt.Sprintf("parallel-%d", i))
				awaitExecution(t, runner)
			}
			third := submitSelection(t, server, "S710149", []string{"wadepre"}, "metrics", "queued")
			waitJob(t, server, third.ID, "queued")
			noExecution(t, runner)
			if response := stopRequest(server, "", server.Token); response.Code != 409 {
				t.Fatalf("stop accepted queued/active work: %d", response.Code)
			}
			unblock()
			awaitExecution(t, runner)
			waitJob(t, server, third.ID, "succeeded") // Automatic diagnostics must reuse the job slot even at limit 1.
			waitExecutionIdle(t, server)
			if response := stopRequest(server, "", server.Token); response.Code != 202 {
				t.Fatalf("idle stop: %d %s", response.Code, response.Body.String())
			}
		})
	}
}

func TestQueuedJobsStartInAdmissionOrder(t *testing.T) {
	server, _, runner, unblock := concurrentServer(t, 1, false)
	first := submitSelection(t, server, "S710140", []string{"wadepre"}, "", "fifo-first")
	awaitExecution(t, runner)
	second := submitSelection(t, server, "S710141", []string{"wadepre"}, "", "fifo-second")
	third := submitSelection(t, server, "S710142", []string{"wadepre"}, "", "fifo-third")
	waitJob(t, server, second.ID, "queued")
	waitJob(t, server, third.ID, "queued")
	for _, next := range []handler.Job{second, third} {
		runner.release <- struct{}{}
		if call := awaitExecution(t, runner); !strings.Contains(call, next.ID) {
			t.Fatalf("queued jobs started out of order: expected %s, got %s", next.ID, call)
		}
	}
	unblock()
	for _, job := range []handler.Job{first, second, third} {
		waitJob(t, server, job.ID, "succeeded")
	}
}

func postJobPackage(t *testing.T, server *handler.Server, raw []byte, hash, key string) (*httptest.ResponseRecorder, handler.Job) {
	body, _ := json.Marshal(map[string]any{"order": orderFromPackage(t, raw, hash)})
	request := httptest.NewRequest("POST", "http://127.0.0.1/v1/jobs", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+server.Token)
	request.Header.Set("Idempotency-Key", key)
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	var job handler.Job
	_ = json.Unmarshal(response.Body.Bytes(), &job)
	return response, job
}

func TestConcurrentIdempotencyAndDistinctRequests(t *testing.T) {
	server, cfg, runner, unblock := concurrentServer(t, 2, false)
	raw, hash := selectionPackage(t, "S710140", []string{"wadepre"}, "")
	type result struct {
		status int
		job    handler.Job
	}
	replies := make(chan result, 8)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			response, job := postJobPackage(t, server, raw, hash, "same-key")
			replies <- result{response.Code, job}
		}()
	}
	wg.Wait()
	close(replies)
	firstID := ""
	for reply := range replies {
		if reply.status != 200 && reply.status != 202 {
			t.Fatalf("same key submission: %d", reply.status)
		}
		if firstID == "" {
			firstID = reply.job.ID
		}
		if firstID != reply.job.ID {
			t.Fatal("same identity created multiple runs")
		}
	}
	awaitExecution(t, runner)
	response, second := postJobPackage(t, server, raw, hash, "distinct-key")
	if response.Code != 202 || second.ID == firstID {
		t.Fatalf("independent request blocked: %d %s", response.Code, response.Body.String())
	}
	awaitExecution(t, runner)
	if ids := runIDs(t, cfg); len(ids) != 2 {
		t.Fatalf("expected two isolated runs: %v", ids)
	}
	unblock()
	waitJob(t, server, firstID, "succeeded")
	waitJob(t, server, second.ID, "succeeded")
}

func TestScheduledPointsOverlapWithoutReplayingExpiredPoints(t *testing.T) {
	server, cfg, runner, unblock := concurrentServer(t, 2, false)
	packageID := saveOrder(t, server)
	id := createTrigger(t, server, map[string]any{"package_id": packageID, "kind": "schedule", "interval_minutes": 5, "start_source": "order"})
	now := time.Now()
	var ticks sync.WaitGroup
	for i := 0; i < 8; i++ {
		ticks.Add(1)
		go func() {
			defer ticks.Done()
			server.RunDueTriggersForTest(now.Add(17 * time.Minute))
		}()
	}
	ticks.Wait()
	awaitExecution(t, runner)
	server.RunDueTriggersForTest(now.Add(18 * time.Minute))
	noExecution(t, runner)
	server.RunDueTriggersForTest(now.Add(21 * time.Minute))
	awaitExecution(t, runner)
	server.RunDueTriggersForTest(now.Add(21 * time.Minute))
	if ids := runIDs(t, cfg); len(ids) != 2 {
		t.Fatalf("distinct points for %s: %v", id, ids)
	}
	unblock()
	waitExecutionIdle(t, server)
	server.RunDueTriggersForTest(now.Add(22 * time.Minute))
	if ids := runIDs(t, cfg); len(ids) != 2 {
		t.Fatalf("finished due point repeated: %v", ids)
	}
}

func TestDiagnosticsShareCapacityAndReserveEachRun(t *testing.T) {
	server, cfg, runner, unblock := concurrentServer(t, 2, true)
	for _, id := range []string{"saved-one", "saved-two", "saved-three"} {
		dir := filepath.Join(cfg.Paths.Runs, id)
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
		order := orderFixture()
		order.DataID, order.EventID, order.Models = "cma_radar", "20170627", []string{"nowcastnet"}
		if err := os.WriteFile(filepath.Join(dir, "order.yaml"), []byte(orderYAML(t, order)), 0o600); err != nil {
			t.Fatal(err)
		}
		raw := fmt.Sprintf(`{"schema":"nib.run.v2","id":%q,"data":{"id":"cma_radar"},"forecasts":{"nowcastnet":{}},"diagnostics":[]}`, id)
		if err := os.WriteFile(filepath.Join(dir, "manifest.json"), []byte(raw), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	submit := func(id string) *httptest.ResponseRecorder {
		request := httptest.NewRequest("POST", "http://127.0.0.1/v1/runs/"+id+"/diagnostics", strings.NewReader(`{"models":["nowcastnet"],"methods":["mae"],"levels":[10],"half_windows":[0]}`))
		request.Header.Set("Authorization", "Bearer "+server.Token)
		response := httptest.NewRecorder()
		server.Routes().ServeHTTP(response, request)
		return response
	}
	for _, id := range []string{"saved-one", "saved-two"} {
		if response := submit(id); response.Code != 202 {
			t.Fatalf("diagnostics %s: %d %s", id, response.Code, response.Body.String())
		}
		awaitExecution(t, runner)
	}
	if response := submit("saved-one"); response.Code != 409 {
		t.Fatalf("same run accepted overlapping write: %d", response.Code)
	}
	response := submit("saved-three")
	if response.Code != 202 {
		t.Fatalf("queue diagnostics: %d %s", response.Code, response.Body.String())
	}
	var operation handler.DiagnosticOperation
	if err := json.Unmarshal(response.Body.Bytes(), &operation); err != nil {
		t.Fatal(err)
	}
	if operation.Status != "queued" {
		t.Fatalf("queued operation reports %s", operation.Status)
	}
	if response := submit("saved-three"); response.Code != 409 {
		t.Fatalf("queued reservation lost: %d", response.Code)
	}
	noExecution(t, runner)
	if response := stopRequest(server, "", server.Token); response.Code != 409 {
		t.Fatal("stop accepted active diagnostics")
	}
	unblock()
	awaitExecution(t, runner)
	waitExecutionIdle(t, server)
	for _, id := range []string{"saved-one", "saved-two", "saved-three"} {
		files, err := filepath.Glob(filepath.Join(cfg.Paths.Runs, id, "logs", "diagnostics-*", "status.json"))
		if err != nil || len(files) != 1 {
			t.Fatal("operation isolation", files, err)
		}
		raw, err := os.ReadFile(files[0])
		if err != nil {
			t.Fatal(err)
		}
		var state handler.DiagnosticOperation
		if err := json.Unmarshal(raw, &state); err != nil {
			t.Fatal(err)
		}
		if state.Status != "succeeded" {
			t.Fatalf("%s: %+v", id, state)
		}
	}
}

func TestConcurrencyConfig(t *testing.T) {
	for _, test := range []struct {
		value string
		want  int
		valid bool
	}{{"", 2, true}, {"1", 1, true}, {"3", 3, true}, {"0", 0, false}, {"-1", 0, false}, {"1.5", 0, false}} {
		t.Run("value="+test.value, func(t *testing.T) {
			raw := "runtime: {}\n"
			if test.value != "" {
				raw = "runtime:\n  max_concurrent_jobs: " + test.value + "\n"
			}
			cfg, err := handler.Load(writeConfig(t, t.TempDir(), raw))
			if !test.valid {
				if err == nil {
					t.Fatal("invalid limit accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if cfg.Runtime.MaxConcurrentJobs != test.want {
				t.Fatalf("limit=%d want %d", cfg.Runtime.MaxConcurrentJobs, test.want)
			}
		})
	}
}
