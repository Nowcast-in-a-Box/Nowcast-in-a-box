package handler_test

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestOrderPatchRequiresCurrentRevision(t *testing.T) {
	server := handler.NewServer(consoleConfig(t))
	server.Runner = forbiddenOrderRunner{t}
	order := orderFixture()
	out, saved := triggerDo(t, server, "POST", "/v1/orders", map[string]any{"order": order})
	if out.Code != 201 {
		t.Fatal(out.Body.String())
	}
	revision := saved["revision"].(string)
	order.Models = []string{"nowcastnet"}
	for _, revision := range []string{"", "stale"} {
		out, _ := triggerDo(t, server, "PATCH", "/v1/orders/order-one", map[string]any{"order": order, "revision": revision})
		if out.Code != 409 {
			t.Fatalf("revision %q: %d %s", revision, out.Code, out.Body.String())
		}
	}
	out, updated := triggerDo(t, server, "PATCH", "/v1/orders/order-one", map[string]any{"order": order, "revision": revision})
	if out.Code != 200 || updated["revision"] == revision {
		t.Fatalf("patch: %d %s", out.Code, out.Body.String())
	}
	out, _ = triggerDo(t, server, "PATCH", "/v1/orders/order-one", map[string]any{"order": orderFixture(), "revision": revision})
	if out.Code != 409 {
		t.Fatalf("stale save overwrote order: %d %s", out.Code, out.Body.String())
	}
	out, current := triggerDo(t, server, "GET", "/v1/orders/order-one", nil)
	if out.Code != 200 || current["revision"] != updated["revision"] || current["yaml"] != updated["yaml"] {
		t.Fatalf("GET differs from saved detail: %d %s", out.Code, out.Body.String())
	}
	order.OrderID = "other-order"
	out, _ = triggerDo(t, server, "PATCH", "/v1/orders/order-one", map[string]any{"order": order, "revision": updated["revision"]})
	if out.Code != 422 {
		t.Fatalf("rename accepted: %d", out.Code)
	}
	out, _ = triggerDo(t, server, "PATCH", "/v1/orders/other-order", map[string]any{"order": order, "revision": revision})
	if out.Code != 404 {
		t.Fatalf("PATCH created missing order: %d", out.Code)
	}
}

func TestConcurrentOrderSavesHaveOneWinner(t *testing.T) {
	for _, method := range []string{"POST", "PATCH"} {
		t.Run(method, func(t *testing.T) {
			server := handler.NewServer(consoleConfig(t))
			server.Runner = forbiddenOrderRunner{t}
			revision, path, success := "", "/v1/orders", 201
			if method == "PATCH" {
				out, detail := triggerDo(t, server, "POST", path, map[string]any{"order": orderFixture()})
				if out.Code != 201 {
					t.Fatal(out.Body.String())
				}
				revision, path, success = detail["revision"].(string), "/v1/orders/order-one", 200
			}
			first, second := orderFixture(), orderFixture()
			first.Models = []string{"nowcastnet"}
			second.Models = []string{"nowcastnet", "wadepre"}
			responses := make([]*httptest.ResponseRecorder, 2)
			start := make(chan struct{})
			var wg sync.WaitGroup
			for i, order := range []handler.Order{first, second} {
				body, err := json.Marshal(map[string]any{"order": order, "revision": revision})
				if err != nil {
					t.Fatal(err)
				}
				wg.Add(1)
				go func(i int, body []byte) {
					defer wg.Done()
					req := httptest.NewRequest(method, "http://127.0.0.1"+path, bytes.NewReader(body))
					req.Header.Set("Authorization", "Bearer "+server.Token)
					responses[i] = httptest.NewRecorder()
					<-start
					server.Routes().ServeHTTP(responses[i], req)
				}(i, body)
			}
			close(start)
			wg.Wait()
			winner := -1
			for i, out := range responses {
				if out.Code == success && winner == -1 {
					winner = i
				} else if out.Code != 409 {
					t.Fatalf("concurrent %s: %d %s", method, out.Code, out.Body.String())
				}
			}
			if winner < 0 {
				t.Fatal("neither save succeeded")
			}
			var detail map[string]any
			if err := json.Unmarshal(responses[winner].Body.Bytes(), &detail); err != nil {
				t.Fatal(err)
			}
			out, saved := triggerDo(t, server, "GET", "/v1/orders/order-one", nil)
			if out.Code != 200 || saved["revision"] != detail["revision"] || saved["yaml"] != detail["yaml"] {
				t.Fatalf("stored content is not the winner: %d %s", out.Code, out.Body.String())
			}
		})
	}
}

func TestRunFreezesOrderBeforeSavedOrderIsEdited(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	writeExecutionWeights(t, server)
	server.Runner = &runtimeRunner{root: cfg.Paths.Runs}
	out, saved := triggerDo(t, server, "POST", "/v1/orders", map[string]any{"order": orderFixture()})
	if out.Code != 201 {
		t.Fatal(out.Body.String())
	}
	revision := saved["revision"].(string)
	out, _ = triggerDo(t, server, "POST", "/v1/orders/order-one/runs", map[string]any{"revision": "stale"})
	if out.Code != 409 || len(runIDs(t, cfg)) != 0 {
		t.Fatalf("stale start: %d %s", out.Code, out.Body.String())
	}
	// Direct job submission also accepts the structured form.
	out, submitted := triggerDo(t, server, "POST", "/v1/jobs", map[string]any{"order": orderFixture()})
	if out.Code != 202 {
		t.Fatalf("structured job: %d %s", out.Code, out.Body.String())
	}
	job := waitJob(t, server, submitted["id"].(string), "succeeded")
	if job.Request.OrderRevision != revision || job.Request.PackageSHA256 != revision {
		t.Fatalf("persisted identity: %+v", job.Request)
	}
	path := filepath.Join(cfg.Paths.Runs, job.ID, "order.yaml")
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	changed := orderFixture()
	changed.Models = []string{"nowcastnet"}
	out, updated := triggerDo(t, server, "PATCH", "/v1/orders/order-one", map[string]any{"order": changed, "revision": revision})
	if out.Code != 200 {
		t.Fatalf("edit: %d %s", out.Code, out.Body.String())
	}
	after, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatalf("run snapshot changed: %v", err)
	}
	frozen, request, err := handler.ParseOrder(after, server.Resources, 32)
	if err != nil || request.OrderRevision != revision || len(frozen.Models) != 1 || frozen.Models[0] != "wadepre" {
		t.Fatalf("wrong frozen order: %+v %v", frozen, err)
	}
	out, detail := triggerDo(t, server, "GET", "/v1/runs/"+job.ID, nil)
	if out.Code != 200 {
		t.Fatal(out.Body.String())
	}
	order, _ := detail["order"].(map[string]any)
	models, _ := order["models"].([]any)
	if len(models) != 1 || models[0] != "wadepre" || detail["inference_status"] != "succeeded" || detail["diagnostics_status"] != "not_requested" {
		t.Fatalf("run detail lost snapshot or status: %#v", detail)
	}
	if operations, ok := detail["operations"].([]any); !ok || len(operations) != 0 {
		t.Fatalf("operations must be an empty array: %#v", detail["operations"])
	}
	out, _ = triggerDo(t, server, "POST", "/v1/orders/order-one/runs", map[string]any{"revision": revision})
	if out.Code != 409 {
		t.Fatalf("edited order accepted stale run revision: %d", out.Code)
	}
	out, next := triggerDo(t, server, "POST", "/v1/orders/order-one/runs", map[string]any{"revision": updated["revision"]})
	if out.Code != 202 {
		t.Fatalf("current revision start: %d %s", out.Code, out.Body.String())
	}
	newJob := waitJob(t, server, next["id"].(string), "succeeded")
	if newJob.Request.OrderRevision != updated["revision"] || newJob.Request.OrderRevision == revision {
		t.Fatalf("new run used stale content: %+v", newJob.Request)
	}
}

func TestDeleteSavedOrderRemovesYAMLOnly(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	server.Runner = forbiddenOrderRunner{t}
	deleted := orderFixture()
	deleted.OrderID = "delete-order"
	out, _ := triggerDo(t, server, "POST", "/v1/orders", map[string]any{"order": deleted})
	if out.Code != 201 {
		t.Fatal(out.Body.String())
	}
	path := filepath.Join(cfg.Paths.Orders, "delete-order.yaml")
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
	out, _ = triggerDo(t, server, "DELETE", "/v1/orders/delete-order", nil)
	if out.Code != 204 {
		t.Fatalf("delete: %d %s", out.Code, out.Body.String())
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("saved YAML still exists: %v", err)
	}
	out, _ = triggerDo(t, server, "GET", "/v1/orders/delete-order", nil)
	if out.Code != 404 {
		t.Fatalf("deleted order still readable: %d", out.Code)
	}
	out, _ = triggerDo(t, server, "DELETE", "/v1/orders/delete-order", nil)
	if out.Code != 404 {
		t.Fatalf("second delete: %d", out.Code)
	}
}

func TestSavedOrdersSortNewestAndKeepCreationTime(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	server.Runner = forbiddenOrderRunner{t}
	for id, created := range map[string]string{
		"older-order": "2025-01-01T00:00:00Z",
		"newer-order": "2025-01-02T00:00:00Z",
	} {
		order := orderFixture()
		order.OrderID = id
		out, _ := triggerDo(t, server, "POST", "/v1/orders", map[string]any{"order": order})
		if out.Code != 201 {
			t.Fatalf("save %s: %d %s", id, out.Code, out.Body.String())
		}
		path := filepath.Join(cfg.Paths.Orders, id+".yaml")
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, append([]byte("# nib-handler-created-at: "+created+"\n"), raw...), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	out, list := triggerDo(t, server, "GET", "/v1/orders", nil)
	orders := list["orders"].([]any)
	if out.Code != 200 || len(orders) != 2 || orders[0].(map[string]any)["order_id"] != "newer-order" {
		t.Fatalf("saved orders not newest-first: %d %#v", out.Code, orders)
	}
	newest := orders[0].(map[string]any)
	created := newest["created_at"].(string)
	order := orderFixture()
	order.OrderID = "newer-order"
	out, _ = triggerDo(t, server, "PATCH", "/v1/orders/newer-order", map[string]any{"order": order, "revision": newest["revision"]})
	if out.Code != 200 {
		t.Fatalf("patch saved order: %d %s", out.Code, out.Body.String())
	}
	raw, err := os.ReadFile(filepath.Join(cfg.Paths.Orders, "newer-order.yaml"))
	if err != nil || !bytes.Contains(raw, []byte("nib-handler-created-at: "+created)) {
		t.Fatalf("patch changed creation time %s: %v", created, err)
	}
	out, _ = triggerDo(t, server, "GET", "/v1/orders/newer-order?download=1", nil)
	if out.Code != 200 || bytes.Contains(out.Body.Bytes(), []byte("nib-handler-created-at")) {
		t.Fatalf("download includes local metadata: %d %s", out.Code, out.Body.String())
	}
}

func TestLegacyOrderKeepsFileTimeWhenFirstEdited(t *testing.T) {
	cfg := consoleConfig(t)
	server := handler.NewServer(cfg)
	server.Runner = forbiddenOrderRunner{t}
	order := orderFixture()
	out, _ := triggerDo(t, server, "POST", "/v1/orders", map[string]any{"order": order})
	if out.Code != 201 {
		t.Fatal(out.Body.String())
	}
	path := filepath.Join(cfg.Paths.Orders, order.OrderID+".yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, bytes.SplitN(raw, []byte("\n"), 2)[1], 0o600); err != nil {
		t.Fatal(err)
	}
	created := time.Date(2024, 1, 2, 3, 4, 5, 0, time.UTC)
	if err := os.Chtimes(path, created, created); err != nil {
		t.Fatal(err)
	}
	out, listing := triggerDo(t, server, "GET", "/v1/orders", nil)
	if out.Code != 200 {
		t.Fatal(out.Body.String())
	}
	items := listing["orders"].([]any)
	original := items[0].(map[string]any)
	if original["created_at"] != created.Format(time.RFC3339Nano) {
		t.Fatalf("legacy order creation time: %#v", original["created_at"])
	}
	out, _ = triggerDo(t, server, "PATCH", "/v1/orders/"+order.OrderID, map[string]any{"order": order, "revision": original["revision"]})
	if out.Code != 200 {
		t.Fatalf("patch legacy order: %d %s", out.Code, out.Body.String())
	}
	raw, err = os.ReadFile(path)
	if err != nil || !bytes.Contains(raw, []byte("nib-handler-created-at: "+created.Format(time.RFC3339Nano))) {
		t.Fatalf("first edit did not preserve file time: %v", err)
	}
}
