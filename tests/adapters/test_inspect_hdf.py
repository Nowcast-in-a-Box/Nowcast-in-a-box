import importlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import h5py

from utils.hdf_sample import MAX_CHUNK_BYTES, inspect_hdf


class TestInspectHDF(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "sample.h5"

    def test_adapters_read_stored_cell(self):
        products = (
            ("fy4b", "FY4B-_AGRI--_sample.HDF", "Data/NOMChannel07"),
            ("gk2a", "gk2a_ami_le1b_sample.nc", "image_pixel_values"),
            ("mtg", "MTG_FCI_sample.nc", "data/ir_105/measured/effective_radiance"),
            ("sevir", "sevir_vil.h5", "inputs"),
        )
        folder = self.root / "nested"
        folder.mkdir()
        for i, (source, filename, dataset) in enumerate(products):
            with self.subTest(source=source):
                with h5py.File(folder / filename, "w") as handle:
                    handle.create_dataset(dataset, data=[[37 + i]], compression="gzip")
                probe = importlib.import_module(f"adapters.{source}.source")
                result = probe.ADAPTER.inspect_sample(self.root)
                self.assertEqual(result["sample"], f"nested/{filename}")
                self.assertEqual(result["value"], 37 + i)

    def test_rejects_oversized_chunk(self):
        size = MAX_CHUNK_BYTES + 1
        with h5py.File(self.path, "w") as handle:
            handle.create_dataset("pixels", (size,), dtype="u1", chunks=(size,))
        with self.assertRaisesRegex(ValueError, "exceeds probe limit"):
            inspect_hdf(self.root, self.path, ["pixels"])

    def test_rejects_indirect_parent(self):
        for link in (h5py.SoftLink("/other"), h5py.ExternalLink("other.h5", "/")):
            with self.subTest(link=link):
                with h5py.File(self.path, "w") as handle:
                    handle["data"] = link
                with self.assertRaisesRegex(ValueError, "indirect HDF links"):
                    inspect_hdf(self.root, self.path, ["data/pixels"])

    def test_rejects_external_storage(self):
        with h5py.File(self.path, "w") as handle:
            handle.create_dataset(
                "external", (1,), dtype="u1", external=[("raw", 0, 1)]
            )
            layout = h5py.VirtualLayout((1,), dtype="u1")
            layout[:] = h5py.VirtualSource("missing.h5", "pixels", (1,))
            handle.create_virtual_dataset("virtual", layout)
        for name in ("external", "virtual"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "storage"):
                inspect_hdf(self.root, self.path, [name])
