import bz2
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from adapters.cma_radar.source import ADAPTER as radar
from adapters.himawari9.source import ADAPTER as ahi


class TestInspectBinary(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.folder = self.root / "nested"
        self.folder.mkdir()

    def test_cref(self):
        name = "Z_RADA_C_TEST_20000101000000_P_DOR_TEST_CREF_20000101_000000.bin"
        path = self.folder / name
        (self.root / "other.bin").touch()
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                header = bytearray(256)
                struct.pack_into("<ii", header, 148, 2, 1)
                struct.pack_into("<h", header, 166, compressed)
                data = struct.pack("<hh", -135, 12)
                path.write_bytes(header + (bz2.compress(data) if compressed else data))
                self.assertEqual(
                    radar.inspect_sample(self.root),
                    {"sample": f"nested/{name}", "value": -13.5},
                )
        path.write_bytes(header + bz2.compress(b"\x00"))
        with self.assertRaisesRegex(ValueError, "No CREF cell"):
            radar.inspect_sample(self.root)

    def test_npz(self):
        path = self.root / "sample.npz"
        np.savez_compressed(path, reflectivity=np.array([[17.25, 0]], dtype=">f4"))
        self.assertEqual(
            radar.inspect_sample(self.root), {"sample": path.name, "value": 17.25}
        )

    def test_hsd(self):
        name = "HS_H09_20180205_0250_B13_FLDK_R20_S0110.DAT"
        (self.root / name.replace("H09", "H08")).touch()
        header = bytearray(400)
        struct.pack_into("<BH", header, 0, 1, 282)
        struct.pack_into("<II", header, 70, len(header), 4)
        struct.pack_into("<BHHHHB", header, 282, 2, 50, 16, 2, 1, 0)
        data = header + struct.pack("<HH", 1203, 777)
        for suffix in ("", ".bz2"):
            with self.subTest(suffix=suffix):
                path = self.folder / (name + suffix)
                path.write_bytes(bz2.compress(data) if suffix else data)
                self.assertEqual(
                    ahi.inspect_sample(self.root),
                    {"sample": f"nested/{path.name}", "value": 1203},
                )
                path.unlink()
        struct.pack_into("<I", header, 70, 2**31)
        path.write_bytes(bz2.compress(header))
        with self.assertRaisesRegex(ValueError, "probe limit"):
            ahi.inspect_sample(self.root)
