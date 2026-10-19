import json

from diagnostics.__main__ import write_report


def test_report_keeps_success_beside_failure_and_binds_exact_operation(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"id": "sample-run", "model_names": {"model-a": "Model A"}})
    )
    pictures = tmp_path / "diagnostics" / "visualization" / "current"
    pictures.mkdir(parents=True)
    (pictures / "map.png").write_bytes(b"map")
    results = [
        {
            "id": "current-picture",
            "kind": "visualization",
            "model_id": "model-a",
            "status": "succeeded",
            "entry": "diagnostics/visualization/current/index.html",
        },
        {
            "id": "current-metrics",
            "kind": "verification",
            "status": "failed",
            "error": "Missing truth <unsafe>",
        },
    ]
    report = write_report(tmp_path, results, operation_id="operation-current")
    assert report["status"] == "partial"
    assert report["operation_id"] == "operation-current"
    assert report["attempt_ids"] == ["current-picture", "current-metrics"]
    page = (tmp_path / report["entry"]).read_text()
    assert "Model A" in page and 'src="0-map.png"' in page
    assert "Missing truth &lt;unsafe&gt;" in page and "<unsafe>" not in page
    assert 'type="radio" name="view"' in page
    assert "<script" not in page and "iframe" not in page


def test_report_distinguishes_not_selected_from_failed(tmp_path):
    report = write_report(
        tmp_path,
        [
            {
                "id": "bad",
                "kind": "verification",
                "status": "failed",
                "error": "Unavailable",
            }
        ],
    )
    page = (tmp_path / report["entry"]).read_text()
    assert "not selected for this request" in page
    assert 'role="alert">failed: Unavailable' in page
    assert report["status"] == "partial"
