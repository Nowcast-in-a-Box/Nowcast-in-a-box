package handler

// Triggers are standing instructions: run this saved order again when new data
// lands, or when the clock says so. The Handler is the only process that can
// answer both questions, so the decision lives here and never on the Hub. A
// trigger never edits an order; it only replaces the start window of one.

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
	"sort"
	"time"
)

const (
	triggerFileName = "nib-triggers.json"

	triggerKindNewData  = "new_data"
	triggerKindSchedule = "schedule"

	triggerWindowLatest = "latest"
	triggerWindowAll    = "all"

	triggerStartLatest = "latest_ready"
	triggerStartOrder  = "order"

	defaultTriggerPollMinutes = 10
	defaultTriggerTick        = 30 * time.Second
	// missedTriggerLimit caps the missed-fire count so a fine-grained interval
	// cannot spin the boot scan.
	missedTriggerLimit = 500
)

// Trigger is one saved order plus the condition that re-runs it.
type Trigger struct {
	ID              string `json:"id"`
	PackageID       string `json:"package_id"`
	Enabled         bool   `json:"enabled"`
	Kind            string `json:"kind"`
	PollMinutes     int    `json:"poll_minutes,omitempty"`
	IntervalMinutes int    `json:"interval_minutes,omitempty"`
	DailyAt         string `json:"daily_at,omitempty"`
	WindowMode      string `json:"window_mode"`
	StartSource     string `json:"start_source"`

	// LastDueAt is the point the schedule is counted from. A missed point
	// advances it without running anything, so a stopped Handler never catches up.
	LastDueAt    *time.Time `json:"last_due_at,omitempty"`
	LastPollAt   *time.Time `json:"last_poll_at,omitempty"`
	LastFiredAt  *time.Time `json:"last_fired_at,omitempty"`
	LastStart    string     `json:"last_start,omitempty"`
	LastRunID    string     `json:"last_run_id,omitempty"`
	LastOutcome  string     `json:"last_outcome,omitempty"`
	LastError    string     `json:"last_error,omitempty"`
	LastSkipped  int        `json:"last_skipped,omitempty"`
	MissedCount  int        `json:"missed_count,omitempty"`
	LastMissedAt *time.Time `json:"last_missed_at,omitempty"`
	CreatedAt    time.Time  `json:"created_at"`
	UpdatedAt    time.Time  `json:"updated_at"`
}

type triggerStore struct {
	Version  int        `json:"version"`
	Triggers []*Trigger `json:"triggers"`
}

func (s *Server) triggerRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /v1/triggers", s.authorize(s.handleTriggersList))
	mux.HandleFunc("POST /v1/triggers", s.authorize(s.handleTriggerCreate))
	mux.HandleFunc("PATCH /v1/triggers/{id}", s.authorize(s.handleTriggerPatch))
	mux.HandleFunc("DELETE /v1/triggers/{id}", s.authorize(s.handleTriggerDelete))
	mux.HandleFunc("POST /v1/triggers/{id}/fire", s.authorize(s.handleTriggerFire))
}

// ensureTriggers loads nib-triggers.json once per process.
func (s *Server) ensureTriggers() ([]*Trigger, error) {
	s.triggersMu.Lock()
	defer s.triggersMu.Unlock()
	return s.loadTriggersLocked()
}

func (s *Server) loadTriggersLocked() ([]*Trigger, error) {
	if s.triggersLoaded {
		return s.triggers, nil
	}
	path, err := statePath(s.currentConfig(), triggerFileName)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		s.triggers, s.triggersLoaded = []*Trigger{}, true
		return s.triggers, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", triggerFileName, err)
	}
	var store triggerStore
	if err := json.Unmarshal(raw, &store); err != nil {
		return nil, fmt.Errorf("%s is not valid JSON: %w; remove it or fix the entry", triggerFileName, err)
	}
	s.triggers, s.triggersLoaded = store.Triggers, true
	return s.triggers, nil
}

func (s *Server) saveTriggersLocked() error {
	path, err := statePath(s.currentConfig(), triggerFileName)
	if err != nil {
		return err
	}
	raw, err := json.MarshalIndent(triggerStore{Version: 1, Triggers: s.triggers}, "", "  ")
	if err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), "."+triggerFileName+"-*")
	if err != nil {
		return err
	}
	name := temporary.Name()
	defer os.Remove(name)
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(append(raw, '\n')); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(name, path)
}

func (s *Server) triggersSnapshot() []*Trigger {
	triggers, err := s.ensureTriggers()
	if err != nil {
		s.note("triggers: " + err.Error())
		return nil
	}
	copies := make([]*Trigger, 0, len(triggers))
	for _, trigger := range triggers {
		copy := *trigger
		copies = append(copies, &copy)
	}
	return copies
}

func (s *Server) triggerByID(id string) *Trigger {
	for _, trigger := range s.triggersSnapshot() {
		if trigger.ID == id {
			return trigger
		}
	}
	return nil
}

// updateTrigger mutates one stored trigger and persists it.
func (s *Server) updateTrigger(id string, mutate func(*Trigger)) error {
	s.triggersMu.Lock()
	defer s.triggersMu.Unlock()
	if _, err := s.loadTriggersLocked(); err != nil {
		return err
	}
	for _, trigger := range s.triggers {
		if trigger.ID != id {
			continue
		}
		mutate(trigger)
		return s.saveTriggersLocked()
	}
	return errTriggerNotFound
}

var errTriggerNotFound = fmt.Errorf("trigger not found")

// StartTriggers runs the firing loop. Main starts it once after the listeners
// are up; tests set TriggerTick to a short interval.
func (s *Server) StartTriggers() {
	if _, err := s.ensureTriggers(); err != nil {
		s.note("triggers: " + err.Error())
	}
	s.accountMissedTriggers(time.Now())
	tick := s.TriggerTick
	if tick <= 0 {
		tick = defaultTriggerTick
	}
	ticker := time.NewTicker(tick)
	defer ticker.Stop()
	for {
		select {
		case <-s.shutdown:
			return
		case now := <-ticker.C:
			s.runDueTriggers(now)
		}
	}
}

// accountMissedTriggers records the schedule points that passed while the
// Handler was not running. It does not run them.
func (s *Server) accountMissedTriggers(now time.Time) {
	for _, trigger := range s.triggersSnapshot() {
		if !trigger.Enabled || trigger.Kind != triggerKindSchedule {
			continue
		}
		count, last := missedPoints(trigger, now)
		if count == 0 {
			continue
		}
		stamp := now.UTC()
		if err := s.updateTrigger(trigger.ID, func(stored *Trigger) {
			stored.MissedCount += count
			stored.LastMissedAt = &last
			stored.LastDueAt = &stamp
			stored.UpdatedAt = stamp
		}); err != nil {
			s.note("triggers: " + err.Error())
			continue
		}
		s.note(fmt.Sprintf("trigger %s missed %d scheduled run(s) while the Handler was stopped; last was %s", trigger.ID, count, last.Format(time.RFC3339)))
	}
}

func (s *Server) runDueTriggers(now time.Time) {
	for _, trigger := range s.triggersSnapshot() {
		if !trigger.Enabled {
			continue
		}
		switch trigger.Kind {
		case triggerKindNewData:
			minutes := trigger.PollMinutes
			if minutes <= 0 {
				minutes = defaultTriggerPollMinutes
			}
			if trigger.LastPollAt != nil && now.Sub(*trigger.LastPollAt) < time.Duration(minutes)*time.Minute {
				continue
			}
			s.fireTrigger(trigger.ID, now, time.Time{})
		case triggerKindSchedule:
			point, ok := nextDue(trigger, scheduleBase(trigger))
			if !ok || point.After(now) {
				continue
			}
			s.fireTrigger(trigger.ID, now, point)
		}
	}
}

// RunDueTriggersForTest drives the firing decision for one instant, so a test
// does not have to wait for a real schedule. Production uses StartTriggers.
func (s *Server) RunDueTriggersForTest(now time.Time) {
	s.runDueTriggers(now)
}

const (
	outcomeStarted = "started"
	outcomeBusy    = "busy"
	outcomeSkipped = "skipped"
	outcomeFailed  = "failed"
)

// fireTrigger resolves one trigger into a run and records the outcome. A busy
// trigger skips this round instead of queueing: a queue would block `stop` and
// grow while one long run holds the execution lock.
func (s *Server) fireTrigger(id string, now, due time.Time) {
	trigger := s.triggerByID(id)
	if trigger == nil {
		return
	}
	outcome, start, runID, skipped, err := s.startTriggerRun(trigger, due)
	stamp := now.UTC()
	updateErr := s.updateTrigger(id, func(stored *Trigger) {
		stored.LastPollAt = &stamp
		stored.LastOutcome = outcome
		stored.LastSkipped = skipped
		stored.LastError = ""
		stored.UpdatedAt = stamp
		if err != nil {
			stored.LastError = err.Error()
		}
		if outcome == outcomeStarted {
			stored.LastFiredAt = &stamp
			stored.LastStart = start
			stored.LastRunID = runID
		}
		if stored.Kind != triggerKindSchedule {
			return
		}
		cursor := stamp
		if !due.IsZero() {
			cursor = due.UTC()
		}
		stored.LastDueAt = &cursor
	})
	if updateErr != nil {
		s.note("triggers: " + updateErr.Error())
	}
	if err != nil {
		s.note(fmt.Sprintf("trigger %s failed: %s", id, err.Error()))
	}
}

// startTriggerRun picks the windows for one fire and admits the run.
func (s *Server) startTriggerRun(trigger *Trigger, due time.Time) (outcome, start, runID string, skipped int, err error) {
	if s.triggerBusy(trigger.ID) {
		return outcomeBusy, "", "", 0, nil
	}
	pkg, err := s.findOrderPackage(trigger.PackageID)
	if err != nil {
		return outcomeFailed, "", "", 0, err
	}
	sum := sha256.Sum256(pkg)
	request, err := ParseRuntimePackage(pkg, hex.EncodeToString(sum[:]), s.Resources)
	if err != nil {
		return outcomeFailed, "", "", 0, err
	}
	limit := s.Config.Runtime.MaxBatchSamples
	if limit <= 0 {
		limit = defaultMaxBatchSamples
	}
	key := "trigger:" + trigger.ID
	if trigger.StartSource == triggerStartOrder {
		// Every scheduled point is its own run of the frozen window.
		key += ":order:" + due.UTC().Format(time.RFC3339)
	} else {
		windows, err := s.newestSamples(context.Background(), request.DataID)
		if err != nil {
			return outcomeFailed, "", "", 0, err
		}
		chosen, rest := unseenWindows(windows, trigger.LastStart, trigger.WindowMode, limit)
		if len(chosen) == 0 {
			return outcomeSkipped, "", "", rest, nil
		}
		windows = chosen
		// The window decides the event, not the package that was saved earlier.
		request.StartTime = windows[0].Start
		request.EventID = windows[0].EventID
		request.SampleStarts = make([]string, 0, len(windows))
		for _, window := range windows {
			request.SampleStarts = append(request.SampleStarts, window.Start)
		}
		key += ":" + request.StartTime
		skipped = rest
	}
	job, status, err := s.admitJob(request, pkg, key, trigger.ID)
	if err != nil {
		return outcomeFailed, "", "", skipped, err
	}
	if status == http.StatusOK {
		// The idempotency key already ran, including before a Handler restart.
		return outcomeSkipped, request.StartTime, job.ID, skipped, nil
	}
	return outcomeStarted, request.StartTime, job.ID, skipped, nil
}

func (s *Server) triggerBusy(id string) bool {
	s.jobsMu.Lock()
	defer s.jobsMu.Unlock()
	for _, job := range s.jobs {
		if job.TriggerID == id && (job.Status == "queued" || job.Status == "running") {
			return true
		}
	}
	return false
}

// sampleWindow is one runnable window as the data reader declares it.
type sampleWindow struct {
	EventID string `json:"event_id"`
	Start   string `json:"context_start_time"`
	Ready   bool   `json:"ready"`
}

func (s *Server) newestSamples(ctx context.Context, dataID string) ([]sampleWindow, error) {
	const pageSize = 500
	page, err := s.listSamples(ctx, dataID, 0, pageSize, "")
	if err != nil {
		return nil, err
	}
	windows := decodeSampleWindows(page)
	total, _ := page["total"].(float64)
	if int(total) > pageSize {
		last, err := s.listSamples(ctx, dataID, int(total)-pageSize, pageSize, "")
		if err != nil {
			return nil, err
		}
		windows = decodeSampleWindows(last)
	}
	return windows, nil
}

func decodeSampleWindows(page map[string]any) []sampleWindow {
	raw, err := json.Marshal(page["samples"])
	if err != nil {
		return nil
	}
	var windows []sampleWindow
	if json.Unmarshal(raw, &windows) != nil {
		return nil
	}
	return windows
}

// unseenWindows keeps the ready windows after lastStart, in reader order. A
// batch run carries one event id, so `all` keeps only the windows that share the
// newest window's event id; the rest are reported as skipped.
func unseenWindows(windows []sampleWindow, lastStart, mode string, limit int) ([]sampleWindow, int) {
	fresh := make([]sampleWindow, 0, len(windows))
	for _, window := range windows {
		if !window.Ready || window.Start == "" {
			continue
		}
		if lastStart != "" && !windowStartAfter(window.Start, lastStart) {
			continue
		}
		fresh = append(fresh, window)
	}
	if len(fresh) == 0 {
		return nil, 0
	}
	newest := fresh[len(fresh)-1]
	if mode != triggerWindowAll {
		return []sampleWindow{newest}, len(fresh) - 1
	}
	chosen := make([]sampleWindow, 0, len(fresh))
	for _, window := range fresh {
		if window.EventID == newest.EventID && len(chosen) < limit {
			chosen = append(chosen, window)
		}
	}
	return chosen, len(fresh) - len(chosen)
}

func windowStartAfter(candidate, lastStart string) bool {
	left, leftErr := time.Parse(time.RFC3339, candidate)
	right, rightErr := time.Parse(time.RFC3339, lastStart)
	if leftErr != nil || rightErr != nil {
		return candidate > lastStart
	}
	return left.After(right)
}

// scheduleBase is the point the next due time is counted from.
func scheduleBase(trigger *Trigger) time.Time {
	if trigger.LastDueAt != nil {
		return *trigger.LastDueAt
	}
	if trigger.LastFiredAt != nil {
		return *trigger.LastFiredAt
	}
	return trigger.CreatedAt
}

// nextDue returns the first scheduled point strictly after base. A daily time is
// this machine's local time.
func nextDue(trigger *Trigger, base time.Time) (time.Time, bool) {
	switch {
	case trigger.IntervalMinutes > 0:
		return base.Add(time.Duration(trigger.IntervalMinutes) * time.Minute), true
	case trigger.DailyAt != "":
		hour, minute, err := parseDailyAt(trigger.DailyAt)
		if err != nil {
			return time.Time{}, false
		}
		local := base.In(time.Local)
		point := time.Date(local.Year(), local.Month(), local.Day(), hour, minute, 0, 0, time.Local)
		for !point.After(base) {
			point = point.AddDate(0, 0, 1)
		}
		return point, true
	}
	return time.Time{}, false
}

// missedPoints counts the scheduled points that passed before now, capped so a
// long stop with a fine interval cannot spin.
func missedPoints(trigger *Trigger, now time.Time) (int, time.Time) {
	base := scheduleBase(trigger)
	count := 0
	var last time.Time
	for count < missedTriggerLimit {
		point, ok := nextDue(trigger, base)
		if !ok || point.After(now) {
			break
		}
		count++
		last = point
		base = point
	}
	return count, last
}

func parseDailyAt(value string) (int, int, error) {
	for _, layout := range []string{"15:04", "15:04:05"} {
		if parsed, err := time.Parse(layout, value); err == nil {
			return parsed.Hour(), parsed.Minute(), nil
		}
	}
	return 0, 0, fmt.Errorf("daily time must be HH:MM")
}

func normalizeTrigger(trigger *Trigger) error {
	switch trigger.Kind {
	case triggerKindNewData:
		trigger.StartSource = triggerStartLatest
		trigger.IntervalMinutes, trigger.DailyAt = 0, ""
		if trigger.PollMinutes == 0 {
			trigger.PollMinutes = defaultTriggerPollMinutes
		}
		if trigger.PollMinutes < 1 || trigger.PollMinutes > 1440 {
			return fmt.Errorf("poll minutes must be 1..1440")
		}
	case triggerKindSchedule:
		trigger.PollMinutes = 0
		if (trigger.IntervalMinutes == 0) == (trigger.DailyAt == "") {
			return fmt.Errorf("choose either interval minutes or a daily time")
		}
		if trigger.IntervalMinutes != 0 {
			if trigger.IntervalMinutes < 5 || trigger.IntervalMinutes > 1440 {
				return fmt.Errorf("interval minutes must be 5..1440")
			}
		} else if _, _, err := parseDailyAt(trigger.DailyAt); err != nil {
			return err
		}
		if trigger.StartSource == "" {
			trigger.StartSource = triggerStartLatest
		}
		if trigger.StartSource != triggerStartLatest && trigger.StartSource != triggerStartOrder {
			return fmt.Errorf("start source must be %s or %s", triggerStartLatest, triggerStartOrder)
		}
	default:
		return fmt.Errorf("trigger kind must be %s or %s", triggerKindNewData, triggerKindSchedule)
	}
	switch trigger.WindowMode {
	case "":
		trigger.WindowMode = triggerWindowLatest
	case triggerWindowLatest, triggerWindowAll:
	default:
		return fmt.Errorf("window mode must be %s or %s", triggerWindowLatest, triggerWindowAll)
	}
	return nil
}

// triggerInput is the writable shape shared by create and patch. A nil field is
// left alone.
type triggerInput struct {
	PackageID       *string `json:"package_id"`
	Kind            *string `json:"kind"`
	PollMinutes     *int    `json:"poll_minutes"`
	IntervalMinutes *int    `json:"interval_minutes"`
	DailyAt         *string `json:"daily_at"`
	WindowMode      *string `json:"window_mode"`
	StartSource     *string `json:"start_source"`
	Enabled         *bool   `json:"enabled"`
}

func (in triggerInput) apply(trigger *Trigger) {
	if in.Kind != nil {
		trigger.Kind = *in.Kind
	}
	if in.PollMinutes != nil {
		trigger.PollMinutes = *in.PollMinutes
	}
	if in.IntervalMinutes != nil {
		trigger.IntervalMinutes = *in.IntervalMinutes
	}
	if in.DailyAt != nil {
		trigger.DailyAt = *in.DailyAt
	}
	if in.WindowMode != nil {
		trigger.WindowMode = *in.WindowMode
	}
	if in.StartSource != nil {
		trigger.StartSource = *in.StartSource
	}
	if in.Enabled != nil {
		trigger.Enabled = *in.Enabled
	}
}

func decodeTriggerInput(w http.ResponseWriter, r *http.Request) (triggerInput, bool) {
	var input triggerInput
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		runtimeError(w, 400, err)
		return input, false
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		runtimeError(w, 400, fmt.Errorf("one request object required"))
		return input, false
	}
	return input, true
}

type triggerResponse struct {
	Trigger
	NextDue string `json:"next_due,omitempty"`
}

func (s *Server) triggerResponses() []triggerResponse {
	triggerList := s.triggersSnapshot()
	sortByCreated := make([]*Trigger, len(triggerList))
	copy(sortByCreated, triggerList)
	sort.Slice(sortByCreated, func(i, j int) bool { return sortByCreated[i].CreatedAt.Before(sortByCreated[j].CreatedAt) })
	responses := make([]triggerResponse, 0, len(sortByCreated))
	for _, trigger := range sortByCreated {
		response := triggerResponse{Trigger: *trigger}
		if trigger.Kind == triggerKindSchedule {
			if point, ok := nextDue(trigger, scheduleBase(trigger)); ok {
				response.NextDue = point.UTC().Format(time.RFC3339)
			}
		}
		responses = append(responses, response)
	}
	return responses
}

func (s *Server) handleTriggersList(w http.ResponseWriter, r *http.Request) {
	if _, err := s.ensureTriggers(); err != nil {
		runtimeError(w, 500, err)
		return
	}
	tick := s.TriggerTick
	if tick <= 0 {
		tick = defaultTriggerTick
	}
	writeJSON(w, 200, map[string]any{"triggers": s.triggerResponses(), "tick_seconds": int(tick.Seconds())})
}

func (s *Server) handleTriggerCreate(w http.ResponseWriter, r *http.Request) {
	input, ok := decodeTriggerInput(w, r)
	if !ok {
		return
	}
	if input.PackageID == nil || !validPackageID(*input.PackageID) {
		runtimeError(w, 422, fmt.Errorf("choose a saved order"))
		return
	}
	trigger := &Trigger{PackageID: *input.PackageID, Enabled: true, CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	input.apply(trigger)
	if err := normalizeTrigger(trigger); err != nil {
		runtimeError(w, 422, err)
		return
	}
	if _, err := s.findOrderPackage(trigger.PackageID); err != nil {
		runtimeError(w, 422, err)
		return
	}
	trigger.ID = "t_" + randomToken()[:8]
	s.triggersMu.Lock()
	if _, err := s.loadTriggersLocked(); err != nil {
		s.triggersMu.Unlock()
		runtimeError(w, 500, err)
		return
	}
	s.triggers = append(s.triggers, trigger)
	err := s.saveTriggersLocked()
	s.triggersMu.Unlock()
	if err != nil {
		runtimeError(w, 500, err)
		return
	}
	writeJSON(w, 201, triggerResponse{Trigger: *trigger})
}

func (s *Server) handleTriggerPatch(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	input, ok := decodeTriggerInput(w, r)
	if !ok {
		return
	}
	if input.PackageID != nil {
		runtimeError(w, 422, fmt.Errorf("delete this trigger and create another to change its order"))
		return
	}
	if _, err := s.ensureTriggers(); err != nil {
		runtimeError(w, 500, err)
		return
	}
	existing := s.triggerByID(id)
	if existing == nil {
		runtimeError(w, 404, errTriggerNotFound)
		return
	}
	updated := *existing
	input.apply(&updated)
	if err := normalizeTrigger(&updated); err != nil {
		runtimeError(w, 422, err)
		return
	}
	updated.UpdatedAt = time.Now().UTC()
	if err := s.updateTrigger(id, func(stored *Trigger) { *stored = updated }); err != nil {
		if err == errTriggerNotFound {
			runtimeError(w, 404, err)
			return
		}
		runtimeError(w, 500, err)
		return
	}
	writeJSON(w, 200, triggerResponse{Trigger: updated})
}

func (s *Server) handleTriggerDelete(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if _, err := s.ensureTriggers(); err != nil {
		runtimeError(w, 500, err)
		return
	}
	s.triggersMu.Lock()
	defer s.triggersMu.Unlock()
	if _, err := s.loadTriggersLocked(); err != nil {
		runtimeError(w, 500, err)
		return
	}
	kept := make([]*Trigger, 0, len(s.triggers))
	for _, trigger := range s.triggers {
		if trigger.ID != id {
			kept = append(kept, trigger)
		}
	}
	if len(kept) == len(s.triggers) {
		runtimeError(w, 404, errTriggerNotFound)
		return
	}
	s.triggers = kept
	if err := s.saveTriggersLocked(); err != nil {
		runtimeError(w, 500, err)
		return
	}
	writeJSON(w, 200, map[string]string{"status": "deleted", "id": id})
}

// handleTriggerFire runs one trigger immediately, which is how a skipped window
// is caught up by hand.
func (s *Server) handleTriggerFire(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if _, err := s.ensureTriggers(); err != nil {
		runtimeError(w, 500, err)
		return
	}
	if s.triggerByID(id) == nil {
		runtimeError(w, 404, errTriggerNotFound)
		return
	}
	s.fireTrigger(id, time.Now(), time.Time{})
	trigger := s.triggerByID(id)
	if trigger == nil {
		runtimeError(w, 404, errTriggerNotFound)
		return
	}
	// The outcome is in the record; the console reads last_outcome and
	// last_error instead of parsing an error from this response.
	writeJSON(w, http.StatusAccepted, triggerResponse{Trigger: *trigger})
}
