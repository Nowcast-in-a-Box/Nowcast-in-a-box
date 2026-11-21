package handler_test

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func TestOrderRejectsInvalidYAMLAndSelections(t *testing.T) {
	resources := testResources(t)
	valid := orderYAML(t, orderFixture())
	for name, raw := range map[string]string{
		"unknown field":         valid + "command: rm\n",
		"duplicate field":       valid + "event_id: other\n",
		"multiple documents":    valid + "---\n" + valid,
		"empty second document": valid + "---\n",
		"too large":             valid + "#" + strings.Repeat("x", 1<<20),
	} {
		t.Run(name, func(t *testing.T) {
			if _, _, err := handler.ParseOrder([]byte(raw), resources, 32); err == nil {
				t.Fatal("invalid YAML accepted")
			}
		})
	}
	lon, lat, outside := 118.0, 32.0, 133.0
	cases := map[string]func(*handler.Order){
		"schema":           func(o *handler.Order) { o.SchemaVersion = "nib.order.v2" },
		"uppercase id":     func(o *handler.Order) { o.OrderID = "UPPER" },
		"reserved id":      func(o *handler.Order) { o.OrderID = "con.txt" },
		"path id":          func(o *handler.Order) { o.OrderID = "../outside" },
		"unknown data":     func(o *handler.Order) { o.DataID = "other" },
		"invalid event":    func(o *handler.Order) { o.EventID = "../event" },
		"no samples":       func(o *handler.Order) { o.SampleStarts = nil },
		"non UTC":          func(o *handler.Order) { o.SampleStarts = []string{"2017-06-27T00:35:00+01:00"} },
		"missing timezone": func(o *handler.Order) { o.SampleStarts = []string{"2017-06-27T00:35:00"} },
		"duplicate instant": func(o *handler.Order) {
			o.SampleStarts = []string{"2017-06-27T00:35:00Z", "2017-06-27T00:35:00.000+00:00"}
		},
		"no model":           func(o *handler.Order) { o.Models = nil },
		"unknown model":      func(o *handler.Order) { o.Models = []string{"shell"} },
		"duplicate model":    func(o *handler.Order) { o.Models = []string{"wadepre", "wadepre"} },
		"region unsupported": func(o *handler.Order) { o.Region = &handler.OrderRegion{CenterLon: &lon, CenterLat: &lat} },
		"region missing latitude": func(o *handler.Order) {
			o.DataID = "cma_radar"
			o.EventID = "20170627"
			o.Models = []string{"nowcastnet"}
			o.Region = &handler.OrderRegion{CenterLon: &lon}
		},
		"region outside": func(o *handler.Order) {
			o.DataID = "cma_radar"
			o.EventID = "20170627"
			o.Models = []string{"nowcastnet"}
			o.Region = &handler.OrderRegion{CenterLon: &outside, CenterLat: &lat}
		},
		"verification model not selected": func(o *handler.Order) {
			o.Verification = &handler.OrderVerification{Models: []string{"nowcastnet"}, Methods: []string{"mae"}}
		},
		"unknown method": func(o *handler.Order) {
			o.Verification = &handler.OrderVerification{Models: o.Models, Methods: []string{"unknown"}}
		},
		"threshold required": func(o *handler.Order) {
			o.Verification = &handler.OrderVerification{Models: o.Models, Methods: []string{"ts"}}
		},
		"duplicate threshold": func(o *handler.Order) {
			o.Verification = &handler.OrderVerification{Models: o.Models, Methods: []string{"ts"}, Levels: []float64{16, 16}}
		},
		"window required": func(o *handler.Order) {
			o.Verification = &handler.OrderVerification{Models: o.Models, Methods: []string{"fss"}, Levels: []float64{16}}
		},
		"visualization model not selected": func(o *handler.Order) { o.Visualization = &handler.OrderVisualization{Models: []string{"nowcastnet"}} },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			order := orderFixture()
			change(&order)
			if _, _, err := handler.ParseOrder([]byte(orderYAML(t, order)), resources, 32); err == nil {
				t.Fatalf("invalid order accepted: %+v", order)
			}
		})
	}
	order := orderFixture()
	order.SampleStarts = append(order.SampleStarts, "2017-06-27T00:40:00Z")
	if _, _, err := handler.ParseOrder([]byte(orderYAML(t, order)), resources, 1); err == nil {
		t.Fatal("configured batch limit ignored")
	}
}

func TestOrderRevisionUsesNormalizedContent(t *testing.T) {
	resources := testResources(t)
	first := orderFixture()
	first.Models = []string{"wadepre", "nowcastnet"}
	first.Verification = &handler.OrderVerification{Models: first.Models, Methods: []string{"ts", "mae", "fss"}, Levels: []float64{32, 16}, HalfWindows: []int{2, 0}}
	first.Visualization = &handler.OrderVisualization{Models: first.Models}
	second := first
	second.Models = []string{"nowcastnet", "wadepre"}
	second.SampleStarts = []string{"2017-06-27T00:35:00.000+00:00"}
	second.Verification = &handler.OrderVerification{Models: second.Models, Methods: []string{"fss", "mae", "ts"}, Levels: []float64{16, 32}, HalfWindows: []int{0, 2}}
	second.Visualization = &handler.OrderVisualization{Models: second.Models}
	parse := func(order handler.Order) (handler.Order, handler.RuntimeRequest) {
		t.Helper()
		normalized, request, err := handler.ParseOrder([]byte("# comment does not change identity\n"+orderYAML(t, order)), resources, 32)
		if err != nil {
			t.Fatal(err)
		}
		return normalized, request
	}
	normalized, a := parse(first)
	other, b := parse(second)
	if !reflect.DeepEqual(normalized, other) || a.OrderRevision != b.OrderRevision || len(a.OrderRevision) != 64 || a.PackageSHA256 != a.OrderRevision {
		t.Fatalf("equivalent orders differ: %+v %+v", a, b)
	}
	if a.PackageID != first.OrderID || a.OrderID != first.OrderID {
		t.Fatalf("identity aliases differ: %+v", a)
	}
	second.SampleStarts = []string{"2017-06-27T00:40:00Z"}
	_, changed := parse(second)
	if changed.OrderRevision == a.OrderRevision {
		t.Fatal("changed sample reused revision")
	}

	// Explicit default region and omitted default region have the same meaning.
	cma := orderFixture()
	cma.DataID, cma.Models = "cma_radar", []string{"nowcastnet"}
	cma.EventID = "20170627"
	_, implicit := parse(cma)
	lon, lat := 118.0, 32.0
	cma.Region = &handler.OrderRegion{CenterLon: &lon, CenterLat: &lat}
	_, explicit := parse(cma)
	if implicit.OrderRevision != explicit.OrderRevision {
		t.Fatal("default region changed revision")
	}
}

// Any Docker use in a static endpoint fails the test, even if its response succeeds.
type forbiddenOrderRunner struct{ t *testing.T }

func (r forbiddenOrderRunner) Run(context.Context, ...string) (int, []byte, error) {
	r.t.Error("static order operation invoked Docker")
	return 1, []byte("Docker unavailable"), nil
}

func TestOrderStaticValidationAndOptionsDoNotNeedDockerOrData(t *testing.T) {
	cfg := consoleConfig(t)
	cfg.DataRoots = map[string]string{}
	cfg.Runtime.MaxBatchSamples = 7
	server := handler.NewServer(cfg)
	server.Runner = forbiddenOrderRunner{t}
	out, detail := triggerDo(t, server, "POST", "/v1/orders/validate", map[string]any{"yaml": orderYAML(t, orderFixture())})
	if out.Code != 200 {
		t.Fatalf("validate: %d %s", out.Code, out.Body.String())
	}
	if detail["package_id"] != "order-one" || detail["order_id"] != "order-one" || detail["order"] == nil {
		t.Fatalf("validation detail: %#v", detail)
	}
	if revision, _ := detail["revision"].(string); len(revision) != 64 {
		t.Fatalf("missing revision: %#v", detail)
	}
	if yaml, _ := detail["yaml"].(string); yaml == "" {
		t.Fatal("missing normalized YAML")
	}
	if orders := consoleList(t, server); len(orders) != 0 {
		t.Fatalf("validation persisted an order: %#v", orders)
	}
	out, options := triggerDo(t, server, "GET", "/v1/order-options", nil)
	if out.Code != 200 || options["max_batch_samples"] != float64(7) {
		t.Fatalf("options: %d %s", out.Code, out.Body.String())
	}
	data, _ := options["data"].([]any)
	if len(data) != len(server.Resources.Data) {
		t.Fatalf("missing data options: %#v", options)
	}
	for _, value := range data {
		entry := value.(map[string]any)
		id := entry["id"].(string)
		models, _ := entry["models"].([]any)
		if len(models) != len(server.Resources.Models[id]) {
			t.Fatalf("model options for %s: %#v", id, entry)
		}
		for _, value := range models {
			model := value.(map[string]any)
			resource, ok := server.Resources.ModelByID(id, model["id"].(string))
			if !ok || model["name"] != resource.Name {
				t.Fatalf("untrusted model option: %#v", model)
			}
		}
		fixed, _ := entry["fixed"].(map[string]any)
		if fixed["context_frames"] != float64(6) || fixed["target_frames"] != float64(20) || fixed["width"] != float64(128) || fixed["height"] != float64(128) {
			t.Fatalf("missing fixed profile: %#v", entry)
		}
		verification, _ := entry["verification"].(map[string]any)
		resource := server.Resources.Data[id]
		if verification["level_min"] != resource.Verification.LevelMin || verification["level_max"] != resource.Verification.LevelMax || verification["unit"] != resource.Verification.Unit {
			t.Fatalf("threshold metadata: %#v", entry)
		}
		if id == "cma_radar" {
			if entry["region"] == nil || fixed["cadence_minutes"] != float64(6) {
				t.Fatalf("missing radar profile: %#v", entry)
			}
		} else if entry["region"] != nil || fixed["cadence_minutes"] != float64(5) {
			t.Fatalf("unexpected data profile: %#v", entry)
		}
	}
	methods, _ := options["methods"].([]any)
	if len(methods) != len(handler.MetricFamilies) {
		t.Fatalf("method options: %#v", methods)
	}
	for _, method := range methods {
		if handler.MetricFamilies[method.(string)] == "" {
			t.Fatalf("unknown method: %v", method)
		}
	}
	if out := consoleSave(t, server, orderFixture()); out.Code != 201 {
		t.Fatalf("offline save: %d %s", out.Code, out.Body.String())
	}
}

func TestOrderEndpointsRejectZIPAndAmbiguousInput(t *testing.T) {
	server := handler.NewServer(consoleConfig(t))
	server.Runner = forbiddenOrderRunner{t}
	raw, hash := packageFixture(t, "wadepre-sevir", nil)
	valid := orderYAML(t, orderFixture())
	for _, endpoint := range []string{"/v1/jobs", "/v1/orders", "/v1/orders/validate"} {
		for name, body := range map[string]any{
			"legacy ZIP":              map[string]any{"package": raw, "sha256": hash},
			"unknown YAML":            map[string]any{"yaml": valid + "docker_args: bad\n"},
			"duplicate YAML":          map[string]any{"yaml": valid + "event_id: other\n"},
			"multiple YAML documents": map[string]any{"yaml": valid + "---\n" + valid},
			"two forms":               map[string]any{"yaml": valid, "order": orderFixture()},
			"neither form":            map[string]any{},
			"unknown object field":    map[string]any{"order": map[string]any{"schema_version": "nib.order.v1", "command": "bad"}},
		} {
			t.Run(endpoint+"/"+name, func(t *testing.T) {
				out, _ := triggerDo(t, server, "POST", endpoint, body)
				if out.Code != 422 {
					t.Fatalf("invalid input: %d %s", out.Code, out.Body.String())
				}
			})
		}
	}
	if ids := runIDs(t, server.Config); len(ids) != 0 {
		t.Fatalf("invalid requests created runs: %v", ids)
	}
	if orders := consoleList(t, server); len(orders) != 0 {
		t.Fatalf("invalid requests created orders: %#v", orders)
	}
}
