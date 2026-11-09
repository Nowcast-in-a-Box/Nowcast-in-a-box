package handler_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

// triggerRunner answers the Docker calls a fired trigger makes. Compose "up"
// can block, which is how the busy path is observed deterministically.
type triggerRunner struct {
	mu      sync.Mutex
	samples string
	block   chan struct{}
	calls   int
}

func (runner *triggerRunner) Run(_ context.Context, args ...string) (int, []byte, error) {
	runner.mu.Lock()
	runner.calls++
	samples, block := runner.samples, runner.block
	runner.mu.Unlock()
	joined := " " + strings.Join(args, " ") + " "
	switch {
	case strings.Contains(joined, " --list-samples "):
		return 0, []byte(samples), nil
	case strings.Contains(joined, " compose ") && strings.Contains(joined, " up "):
		if block != nil {
			<-block
		}
		return 0, nil, nil
	case strings.Contains(joined, " info "):
		return 0, []byte("27.0.0\n"), nil
	}
	return 0, nil, nil
}

func (runner *triggerRunner) setSamples(samples string) {
	runner.mu.Lock()
	defer runner.mu.Unlock()
	runner.samples = samples
}

func sampleList(t *testing.T, windows ...[2]string) string {
	t.Helper()
	samples := []map[string]any{}
	for _, window := range windows {
		samples = append(samples, map[string]any{
			"event_id": window[0], "context_start_time": window[1], "ready": true,
		})
	}
	raw, err := json.Marshal(map[string]any{"data_id": "sevir", "total": len(samples), "limit": 500, "samples": samples})
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func triggerServer(t *testing.T) (handler.Config, *handler.Server, *triggerRunner) {
	t.Helper()
	cfg := testConfig(t)
	cfg.Paths.Runs = t.TempDir()
	cfg.Paths.Orders = t.TempDir()
	cfg.Paths.Artifacts = t.TempDir()
	cfg.Runtime.Token = "token"
	cfg.Runtime.AtlasRevision = strings.Repeat("b", 40)
	cfg.Source = filepath.Join(t.TempDir(), "config.yaml")
	writeSevirData(t, &cfg)
	server := handler.NewServer(cfg)
	runner := &triggerRunner{}
	server.Runner = runner
	return cfg, server, runner
}

func triggerDo(t *testing.T, server *handler.Server, method, path string, body any) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		reader = bytes.NewReader(raw)
	}
	request := httptest.NewRequest(method, "http://127.0.0.1"+path, reader)
	request.Header.Set("Authorization", "Bearer "+server.Token)
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response := httptest.NewRecorder()
	server.Routes().ServeHTTP(response, request)
	var decoded map[string]any
	_ = json.Unmarshal(response.Body.Bytes(), &decoded)
	return response, decoded
}

func saveOrder(t *testing.T, server *handler.Server) string {
	t.Helper()
	raw, hash := selectionPackage(t, "S710140", []string{"wadepre"}, "")
	response, summary := triggerDo(t, server, "POST", "/v1/orders", map[string]any{"package": raw, "sha256": hash})
	if response.Code != 201 {
		t.Fatalf("save order: %d %s", response.Code, response.Body.String())
	}
	return summary["package_id"].(string)
}

func createTrigger(t *testing.T, server *handler.Server, body map[string]any) string {
	t.Helper()
	response, decoded := triggerDo(t, server, "POST", "/v1/triggers", body)
	if response.Code != 201 {
		t.Fatalf("create trigger: %d %s", response.Code, response.Body.String())
	}
	return decoded["id"].(string)
}

func triggers(t *testing.T, server *handler.Server) []map[string]any {
	t.Helper()
	response, page := triggerDo(t, server, "GET", "/v1/triggers", nil)
	if response.Code != 200 {
		t.Fatalf("list triggers: %d %s", response.Code, response.Body.String())
	}
	raw, _ := page["triggers"].([]any)
	list := make([]map[string]any, 0, len(raw))
	for _, item := range raw {
		list = append(list, item.(map[string]any))
	}
	return list
}

func runIDs(t *testing.T, cfg handler.Config) []string {
	t.Helper()
	entries, err := os.ReadDir(cfg.Paths.Runs)
	if err != nil {
		return nil
	}
	ids := []string{}
	for _, entry := range entries {
		if entry.IsDir() {
			ids = append(ids, entry.Name())
		}
	}
	return ids
}

func waitForJobEnd(t *testing.T, server *handler.Server, id string) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		_, job := triggerDo(t, server, "GET", "/v1/jobs/"+id, nil)
		status, _ := job["status"].(string)
		if status != "queued" && status != "running" {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("job %s did not finish", id)
}

func TestTriggerLifecycle(t *testing.T) {
	_, server, _ := triggerServer(t)
	packageID := saveOrder(t, server)

	id := createTrigger(t, server, map[string]any{
		"package_id": packageID, "kind": "schedule", "interval_minutes": 60, "start_source": "order",
	})
	if !strings.HasPrefix(id, "t_") {
		t.Fatalf("unexpected id %q", id)
	}
	list := triggers(t, server)
	if len(list) != 1 || list[0]["package_id"] != packageID || list[0]["next_due"] == nil {
		t.Fatalf("stored trigger: %v", list)
	}

	response, decoded := triggerDo(t, server, "PATCH", "/v1/triggers/"+id, map[string]any{"enabled": false})
	if response.Code != 200 || decoded["enabled"] != false {
		t.Fatalf("pause: %d %s", response.Code, response.Body.String())
	}
	if response, _ := triggerDo(t, server, "PATCH", "/v1/triggers/"+id, map[string]any{"package_id": "other"}); response.Code != 422 {
		t.Fatalf("changing the order must be refused: %d", response.Code)
	}
	if response, _ := triggerDo(t, server, "DELETE", "/v1/triggers/"+id, nil); response.Code != 200 {
		t.Fatalf("delete: %d %s", response.Code, response.Body.String())
	}
	if list := triggers(t, server); len(list) != 0 {
		t.Fatalf("trigger survived delete: %v", list)
	}
	if response, _ := triggerDo(t, server, "DELETE", "/v1/triggers/"+id, nil); response.Code != 404 {
		t.Fatalf("second delete: %d", response.Code)
	}
}

func TestTriggerRejectsUnknownOrderAndBadSchedule(t *testing.T) {
	_, server, _ := triggerServer(t)
	packageID := saveOrder(t, server)
	cases := []map[string]any{
		{"package_id": "missing-order", "kind": "new_data"},
		{"package_id": packageID, "kind": "schedule"},
		{"package_id": packageID, "kind": "schedule", "interval_minutes": 1},
		{"package_id": packageID, "kind": "schedule", "daily_at": "8 o'clock"},
		{"package_id": packageID, "kind": "new_data", "poll_minutes": 0, "window_mode": "every"},
		{"package_id": packageID, "kind": "sometimes"},
	}
	for _, body := range cases {
		if response, _ := triggerDo(t, server, "POST", "/v1/triggers", body); response.Code != 422 {
			t.Fatalf("%v must be refused with 422, got %d %s", body, response.Code, response.Body.String())
		}
	}
}

func TestFireUsesTheNewestWindowOnceOnly(t *testing.T) {
	cfg, server, runner := triggerServer(t)
	packageID := saveOrder(t, server)
	runner.setSamples(sampleList(t,
		[2]string{"S710140", "2017-06-27T00:35:00Z"},
		[2]string{"S710141", "2017-06-28T00:35:00Z"},
	))
	id := createTrigger(t, server, map[string]any{"package_id": packageID, "kind": "new_data"})

	response, first := triggerDo(t, server, "POST", "/v1/triggers/"+id+"/fire", nil)
	if response.Code != 202 || first["last_outcome"] != "started" {
		t.Fatalf("first fire: %d %v", response.Code, first)
	}
	if first["last_start"] != "2017-06-28T00:35:00Z" {
		t.Fatalf("newest window not chosen: %v", first["last_start"])
	}
	if first["last_skipped"] != float64(1) {
		t.Fatalf("older window must be reported as skipped: %v", first["last_skipped"])
	}
	waitForJobEnd(t, server, first["last_run_id"].(string))

	// Same data, second fire: nothing new, and the idempotency key still holds.
	response, second := triggerDo(t, server, "POST", "/v1/triggers/"+id+"/fire", nil)
	if response.Code != 202 || second["last_outcome"] != "skipped" {
		t.Fatalf("second fire: %d %v", response.Code, second)
	}
	if ids := runIDs(t, cfg); len(ids) != 1 {
		t.Fatalf("expected one run, got %v", ids)
	}

	// A newer window is a new run.
	runner.setSamples(sampleList(t,
		[2]string{"S710140", "2017-06-27T00:35:00Z"},
		[2]string{"S710141", "2017-06-28T00:35:00Z"},
		[2]string{"S710142", "2017-06-29T00:35:00Z"},
	))
	response, third := triggerDo(t, server, "POST", "/v1/triggers/"+id+"/fire", nil)
	if response.Code != 202 || third["last_outcome"] != "started" {
		t.Fatalf("third fire: %d %v", response.Code, third)
	}
	if ids := runIDs(t, cfg); len(ids) != 2 {
		t.Fatalf("expected two runs, got %v", ids)
	}
	waitForJobEnd(t, server, third["last_run_id"].(string))
}

func TestWindowModeSelectsOneOrAllNewWindows(t *testing.T) {
	_, server, runner := triggerServer(t)
	packageID := saveOrder(t, server)
	runner.setSamples(sampleList(t,
		[2]string{"S710140", "2017-06-27T00:35:00Z"},
		[2]string{"S710140", "2017-06-27T00:45:00Z"},
		[2]string{"S710141", "2017-06-28T00:35:00Z"},
	))

	latest := createTrigger(t, server, map[string]any{"package_id": packageID, "kind": "new_data", "window_mode": "latest"})
	_, fired := triggerDo(t, server, "POST", "/v1/triggers/"+latest+"/fire", nil)
	if fired["last_outcome"] != "started" {
		t.Fatalf("latest fire: %v", fired)
	}
	waitForJobEnd(t, server, fired["last_run_id"].(string))
	_, job := triggerDo(t, server, "GET", "/v1/jobs/"+fired["last_run_id"].(string), nil)
	request, _ := job["request"].(map[string]any)
	if starts, _ := request["sample_starts"].([]any); len(starts) != 1 {
		t.Fatalf("latest must send one window: %v", starts)
	}

	// "all" keeps the windows of the newest event id; the other event is skipped
	// because one batch run carries one event id.
	all := createTrigger(t, server, map[string]any{"package_id": packageID, "kind": "new_data", "window_mode": "all"})
	_, fired = triggerDo(t, server, "POST", "/v1/triggers/"+all+"/fire", nil)
	if fired["last_outcome"] != "started" {
		t.Fatalf("all fire: %v", fired)
	}
	if fired["last_skipped"] != float64(2) {
		t.Fatalf("other-event windows must be skipped: %v", fired["last_skipped"])
	}
	waitForJobEnd(t, server, fired["last_run_id"].(string))
	_, job = triggerDo(t, server, "GET", "/v1/jobs/"+fired["last_run_id"].(string), nil)
	request, _ = job["request"].(map[string]any)
	if starts, _ := request["sample_starts"].([]any); len(starts) != 1 || starts[0] != "2017-06-28T00:35:00Z" {
		t.Fatalf("all must send the newest event's windows: %v", starts)
	}
}

func TestScheduledOrderWindowIsIdempotentPerDuePoint(t *testing.T) {
	cfg, server, _ := triggerServer(t)
	packageID := saveOrder(t, server)
	id := createTrigger(t, server, map[string]any{
		"package_id": packageID, "kind": "schedule", "interval_minutes": 60, "start_source": "order",
	})

	_, first := triggerDo(t, server, "POST", "/v1/triggers/"+id+"/fire", nil)
	if first["last_outcome"] != "started" {
		t.Fatalf("first fire: %v", first)
	}
	waitForJobEnd(t, server, first["last_run_id"].(string))

	// A manual fire has no scheduled point, so the frozen window cannot run twice.
	_, second := triggerDo(t, server, "POST", "/v1/triggers/"+id+"/fire", nil)
	if second["last_outcome"] != "skipped" {
		t.Fatalf("second fire must be skipped: %v", second)
	}
	if ids := runIDs(t, cfg); len(ids) != 1 {
		t.Fatalf("expected one run, got %v", ids)
	}
}

func TestFireSkipsWhileItsPreviousRunIsActive(t *testing.T) {
	cfg, server, runner := triggerServer(t)
	packageID := saveOrder(t, server)
	runner.setSamples(sampleList(t, [2]string{"S710141", "2017-06-28T00:35:00Z"}))
	release := make(chan struct{})
	var releaseOnce sync.Once
	unblock := func() { releaseOnce.Do(func() { close(release) }) }
	runner.mu.Lock()
	runner.block = release
	runner.mu.Unlock()
	t.Cleanup(unblock)

	id := createTrigger(t, server, map[string]any{"package_id": packageID, "kind": "new_data"})
	_, first := triggerDo(t, server, "POST", "/v1/triggers/"+id+"/fire", nil)
	if first["last_outcome"] != "started" {
		t.Fatalf("first fire: %v", first)
	}
	_, second := triggerDo(t, server, "POST", "/v1/triggers/"+id+"/fire", nil)
	if second["last_outcome"] != "busy" {
		t.Fatalf("a running trigger must skip, not queue: %v", second)
	}
	if ids := runIDs(t, cfg); len(ids) != 1 {
		t.Fatalf("expected one run, got %v", ids)
	}
	unblock()
	runner.mu.Lock()
	runner.block = nil
	runner.mu.Unlock()
	waitForJobEnd(t, server, first["last_run_id"].(string))
}

func writeTriggerStore(t *testing.T, cfg handler.Config, trigger map[string]any) {
	t.Helper()
	raw, err := json.MarshalIndent(map[string]any{"version": 1, "triggers": []any{trigger}}, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(filepath.Dir(cfg.Source), "nib-triggers.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestMissedScheduleIsRecordedAndNotRun(t *testing.T) {
	cfg, server, _ := triggerServer(t)
	packageID := saveOrder(t, server)
	created := time.Now().Add(-2 * time.Hour).UTC()
	writeTriggerStore(t, cfg, map[string]any{
		"id": "t_stopped1", "package_id": packageID, "enabled": true, "kind": "schedule",
		"interval_minutes": 30, "window_mode": "latest", "start_source": "order",
		"created_at": created,
	})
	server.TriggerTick = 20 * time.Millisecond
	go server.StartTriggers()
	t.Cleanup(func() { triggerDo(t, server, "POST", "/v1/control/stop", nil) })

	list := triggers(t, server)
	if len(list) != 1 {
		t.Fatalf("stored trigger not loaded: %v", list)
	}
	missed, _ := list[0]["missed_count"].(float64)
	if missed < 3 {
		t.Fatalf("missed count: %v", list[0]["missed_count"])
	}
	if list[0]["last_missed_at"] == nil {
		t.Fatalf("missed time not recorded: %v", list[0])
	}
	// Several ticks later the record must not move: a missed point is skipped.
	time.Sleep(200 * time.Millisecond)
	after := triggers(t, server)
	if after[0]["missed_count"] != list[0]["missed_count"] {
		t.Fatalf("missed count moved on its own: %v then %v", list[0]["missed_count"], after[0]["missed_count"])
	}
	if ids := runIDs(t, cfg); len(ids) != 0 {
		t.Fatalf("a missed point must not create a run: %v", ids)
	}
}

func TestScheduledTriggerFiresAtMostOncePerDuePoint(t *testing.T) {
	cfg, server, _ := triggerServer(t)
	packageID := saveOrder(t, server)
	id := createTrigger(t, server, map[string]any{
		"package_id": packageID, "kind": "schedule", "interval_minutes": 5, "start_source": "order",
	})
	now := time.Now()
	// Not due yet.
	server.RunDueTriggersForTest(now)
	if ids := runIDs(t, cfg); len(ids) != 0 {
		t.Fatalf("fired before its due time: %v", ids)
	}
	// Several intervals later one call still produces exactly one run: the
	// points that passed are not replayed.
	server.RunDueTriggersForTest(now.Add(17 * time.Minute))
	ids := runIDs(t, cfg)
	if len(ids) != 1 {
		t.Fatalf("expected exactly one run, got %v", ids)
	}
	server.RunDueTriggersForTest(now.Add(18 * time.Minute))
	if ids := runIDs(t, cfg); len(ids) != 1 {
		t.Fatalf("the same due point ran twice: %v", ids)
	}
	waitForJobEnd(t, server, ids[0])
	raw, err := os.ReadFile(filepath.Join(cfg.Paths.Runs, ids[0], "job.json"))
	if err != nil {
		t.Fatal(err)
	}
	var job map[string]any
	if err := json.Unmarshal(raw, &job); err != nil {
		t.Fatal(err)
	}
	if job["trigger_id"] != id {
		t.Fatalf("run not attributed to the trigger: %v", job["trigger_id"])
	}
	if key, _ := job["idempotency_key"].(string); !strings.HasPrefix(key, "trigger:"+id+":order:") {
		t.Fatalf("scheduled key shape: %q", key)
	}
}
