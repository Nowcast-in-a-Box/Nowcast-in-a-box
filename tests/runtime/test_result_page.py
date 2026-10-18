import json

from diagnostics.__main__ import write_report


def test_combined_report_keeps_map_and_scores_in_one_directory(tmp_path):
    run = tmp_path / "run"
    pictures = run / "diagnostics" / "visualization" / "picture"
    scores = run / "diagnostics" / "verification" / "score"
    pictures.mkdir(parents=True)
    scores.mkdir(parents=True)
    (pictures / "map.png").write_bytes(b"png")
    (scores / "mae.svg").write_text("<svg></svg>")
    report = write_report(
        run,
        [
            {
                "kind": "visualization",
                "entry": "diagnostics/visualization/picture/index.html",
            },
            {
                "kind": "verification",
                "entry": "diagnostics/verification/score/index.html",
            },
        ],
    )
    page = (run / report["entry"]).read_text()
    assert 'src="0-map.png"' in page and 'src="1-mae.svg"' in page
    assert 'name="view"' in page and "Visualization" in page and "Metrics" in page
    assert "<script" not in page and 'role="tab"' not in page
    assert 'type="radio"' in page and ":focus-visible" in page
    assert (run / report["path"] / "0-map.png").samefile(pictures / "map.png")


def test_combined_report_keeps_all_model_maps_without_name_collisions(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "model_names": {
                    "nowcastnet": "NowcastNet",
                    "exprecast": "exPreCast",
                    "wadepre": "WADEPre",
                }
            }
        )
    )
    results = []
    for model in ("nowcastnet", "exprecast", "wadepre"):
        source = tmp_path / "diagnostics" / "visualization" / model
        source.mkdir(parents=True)
        (source / "map.png").write_bytes(model.encode())
        (source / "animation.gif").write_bytes(model.encode())
        results.append(
            {
                "kind": "visualization",
                "model_id": model,
                "entry": f"diagnostics/visualization/{model}/index.html",
            }
        )
    report = write_report(tmp_path, results)
    out = tmp_path / report["path"]
    page = (tmp_path / report["entry"]).read_text()
    for index, (model, name) in enumerate(
        (
            ("nowcastnet", "NowcastNet"),
            ("exprecast", "exPreCast"),
            ("wadepre", "WADEPre"),
        )
    ):
        assert name in page
        for filename in ("map.png", "animation.gif"):
            assert (out / f"{index}-{filename}").read_bytes() == model.encode()
            assert f'src="{index}-{filename}"' in page
