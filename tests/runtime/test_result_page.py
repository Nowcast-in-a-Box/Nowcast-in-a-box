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


def test_the_result_page_has_both_views_in_one_document(tmp_path):
    run = make_run(tmp_path)
    with TestClient(build_app(run)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "NiB diagnostics" in page.text
        assert 'data-view="visualization"' in page.text
        assert 'data-view="metrics"' in page.text
        assert "iframe" not in page.text
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


def test_a_result_url_serves_the_same_page(tmp_path):
    run = make_run(tmp_path)
    with TestClient(build_app(run)) as client:
        page = client.get(
            "/v1/runs/run-one/files/diagnostics/report/current/index.html"
        )
        assert page.status_code == 200
        assert "NiB diagnostics" in page.text
