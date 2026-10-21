import json

from fastapi.testclient import TestClient

from diagnostics.diagnostics_server import build_app


def make_run(tmp_path):
    run = tmp_path / "run"
    picture = run / "diagnostics" / "visualization" / "picture"
    picture.mkdir(parents=True)
    (picture / "map.png").write_bytes(b"png")
    (picture / "animation.gif").write_bytes(b"gif")
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "nib.run.v1",
                "id": "run-one",
                "sample": {"sample_id": "S700003"},
                "model_names": {"wadepre": "WADEPre"},
                "diagnostics": [
                    {
                        "kind": "visualization",
                        "status": "succeeded",
                        "model_id": "wadepre",
                        "path": "diagnostics/visualization/picture",
                    }
                ],
            }
        )
    )
    return run


def add_verification(run):
    attempt = run / "diagnostics" / "verification" / "attempt"
    attempt.mkdir(parents=True)
    (attempt / "config.json").write_text(
        json.dumps(
            {
                "group": {"id": "run-one", "name": "20260701@2026-07-01T00:00:00Z"},
                "ruler": {
                    "ground_truth": "cma_radar",
                    "unit": "dBZ",
                    "time_step_minutes": 6,
                    "extent": [-190500, -190500, 190500, 190500],
                    "resolution": 3000,
                },
                "members": [
                    {
                        "name": "nowcastnet",
                        "variable": "CREF",
                        "unit": "dBZ",
                        "leads": [6, 12],
                    }
                ],
                "metrics": [
                    {"family": "continuous", "items": ["mae"]},
                    {"family": "yes_or_no", "items": ["ets"], "levels": [20]},
                ],
            }
        )
    )
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["diagnostics"].append(
        {
            "kind": "verification",
            "status": "succeeded",
            "path": "diagnostics/verification/attempt",
        }
    )
    manifest_path.write_text(json.dumps(manifest))
    return attempt


def test_the_result_page_has_both_views_in_one_document(tmp_path):
    run = make_run(tmp_path)
    with TestClient(build_app(run)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "NiB diagnostics" in page.text
        assert 'data-view="visualization"' in page.text
        assert 'data-view="metrics"' in page.text
        assert "iframe" not in page.text
        assert 'id="viz-sample"' in page.text
        assert "Content-Security-Policy" in page.headers
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]


def test_visualization_is_a_same_origin_artifact_list(tmp_path):
    run = make_run(tmp_path)
    with TestClient(build_app(run)) as client:
        body = client.get("/visualization").json()
        assert body["run_id"] == "run-one"
        assert body["sample_id"] == "S700003"
        assert body["models"][0]["name"] == "WADEPre"
        urls = {image["url"] for image in body["models"][0]["images"]}
        assert "/visualization/diagnostics/visualization/picture/map.png" in urls
        assert client.get(next(iter(urls))).content in (b"png", b"gif")



def test_a_batch_result_exposes_each_sample_as_a_visualization_case(tmp_path):
    run = tmp_path / "run"
    samples = []
    for index, init_time in enumerate(
        ("2026-07-01T00:00:00Z", "2026-07-01T00:06:00Z")
    ):
        sample_id = f"20260701@{init_time}"
        path = f"samples/{index:03d}"
        picture = run / path / "diagnostics" / "visualization" / "picture"
        picture.mkdir(parents=True)
        (picture / "map.png").write_bytes(b"png")
        samples.append(
            {
                "sample_id": sample_id,
                "event_id": "20260701",
                "init_time": init_time,
                "path": path,
            }
        )
    run.mkdir(exist_ok=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "nib.run.v1",
                "id": "run-batch",
                "batch": True,
                "samples": samples,
                "sample_count": 2,
                "sample": samples[0],
                "model_names": {"wadepre": "WADEPre"},
                "diagnostics": [
                    {
                        "kind": "visualization",
                        "status": "succeeded",
                        "model_id": "wadepre",
                        "sample_id": sample["sample_id"],
                        "path": f"{sample['path']}/diagnostics/visualization/picture",
                    }
                    for sample in samples
                ],
            }
        )
    )
    with TestClient(build_app(run)) as client:
        body = client.get("/visualization").json()
        assert [sample["sample_id"] for sample in body["samples"]] == [
            sample["sample_id"] for sample in samples
        ]
        assert len(body["models"][0]["cases"]) == 2
        first = body["models"][0]["cases"][0]
        second = body["models"][0]["cases"][1]
        assert first["sample_id"] == samples[0]["sample_id"]
        assert second["sample_id"] == samples[1]["sample_id"]
        assert "/visualization/samples/000/" in first["images"][0]["url"]
        assert "/visualization/samples/001/" in second["images"][0]["url"]
        image = client.get(second["images"][0]["url"])
        assert image.content == b"png"

def test_a_result_config_is_queryable_with_a_metric(tmp_path):
    run = make_run(tmp_path)
    add_verification(run)
    with TestClient(build_app(run)) as client:
        configs = client.get("/api/configs")
        assert configs.status_code == 200
        assert configs.json()["configs"][0]["group_id"] == "run-one"
        detail = client.get("/api/configs/run-one")
        assert detail.status_code == 200
        methods = {item["method"] for item in detail.json()["metrics_available"]}
        assert {"mae", "ets"} <= methods
        result = client.get(
            "/api/scores",
            params={
                "config": "run-one",
                "method": "mae",
                "init_from": "2026-07-01T00:30:00Z",
                "init_to": "2026-07-01T00:30:00Z",
            },
        )
        assert result.status_code == 200


def test_a_result_url_serves_the_same_page(tmp_path):
    run = make_run(tmp_path)
    with TestClient(build_app(run)) as client:
        page = client.get(
            "/v1/runs/run-one/files/diagnostics/report/current/index.html"
        )
        assert page.status_code == 200
        assert "NiB diagnostics" in page.text
