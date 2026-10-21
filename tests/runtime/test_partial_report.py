import json

from diagnostics.__main__ import write_report


def test_report_registers_the_operation_without_writing_a_second_page(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"id": "sample-run", "model_names": {"model-a": "Model A"}})
    )
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
    assert (tmp_path / report["path"]).is_dir()
    assert not (tmp_path / report["path"] / "index.html").exists()


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
    assert report["status"] == "partial"
    assert report["attempt_ids"] == ["bad"]
    assert not (tmp_path / report["entry"]).exists()
