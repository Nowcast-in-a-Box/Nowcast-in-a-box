import importlib
import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from numcodecs import Blosc, Zstd

from utils.zarr_sample import inspect_zarr


class TestInspectMLCast(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def chunk(self, variable, value, codec=None, chunks=(1, 1, 1)):
        directory = self.root / "sample.zarr" / variable
        directory.mkdir(parents=True, exist_ok=True)
        meta = {
            "zarr_format": 2,
            "shape": [2, 1, 1],
            "chunks": chunks,
            "dtype": ">f4",
            "compressor": codec.get_config() if codec else None,
            "filters": None,
            "fill_value": 0,
            "order": "C",
        }
        (directory / ".zarray").write_text(json.dumps(meta))
        data = struct.pack(">f", value)
        (directory / "1.0.0").write_bytes(codec.encode(data) if codec else data)

    def test_all_adapters_read_stored_cell(self):
        catalog = (
            Path(__file__).resolve().parents[2] / "atlas/runtime/mlcast-sources.json"
        )
        for i, entry in enumerate(json.loads(catalog.read_text()).values()):
            source, variable = entry["source"], entry["variable"]
            with self.subTest(source=source):
                self.chunk(variable, i + 0.25, (None, Blosc(), Zstd())[i % 3])
                probe = importlib.import_module(f"adapters.{source}.source")
                result = probe.ADAPTER.inspect_sample(self.root)
                self.assertEqual(result["sample"], f"sample.zarr/{variable}/1.0.0")
                self.assertEqual(result["index"], [1, 0, 0])
                self.assertEqual(result["value"], i + 0.25)
                json.dumps(result, allow_nan=False)

    def test_nonfinite_value_is_json_null(self):
        self.chunk("RR", float("nan"))
        self.assertIsNone(inspect_zarr(self.root / "sample.zarr", "RR")["value"])

    def test_oversized_decoded_chunk(self):
        self.chunk("RR", 42, chunks=(1, 4096, 4096))
        with self.assertRaisesRegex(ValueError, "exceeds probe limit"):
            inspect_zarr(self.root, "RR")
