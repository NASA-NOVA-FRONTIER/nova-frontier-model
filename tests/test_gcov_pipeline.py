"""Verify bounded parallel archive transport and strict GCOV block support."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

import numpy as np

from surface_dynamics.io import cube_from_chips
from surface_dynamics.ranges import RangeReader


class GcovPipelineTests(TestCase):
    def test_strict_power_aggregation_and_equal_pixel_edges(self):
        with TemporaryDirectory() as work:
            chip = Path(work) / "chip.npz"
            x, y = np.arange(8) * 10 + 5, np.arange(8)[::-1] * 10 + 5
            power = np.arange(1, 65, dtype="float32").reshape(8, 8)
            mask = np.ones((8, 8), dtype="uint8")
            mask[7, 7] = 255
            np.savez(chip, HHHH=power, HVHV=power * 2, x=x, y=y, mask=mask, epsg=32618)
            chip.with_suffix(".json").write_text(json.dumps({
                "sha256": hashlib.sha256(chip.read_bytes()).hexdigest(),
                "date": "2026-10-15", "flags": {"radarBand": "L"},
            }))
            native = cube_from_chips([chip], aggregate=1)
            coarse = cube_from_chips([chip], aggregate=4, require_posting=10)
            self.assertEqual(coarse["x"].tolist(), [20, 60])
            self.assertEqual(coarse["y"].tolist(), [60, 20])
            np.testing.assert_array_equal(coarse["valid"][0, 0], [[True, True], [True, False]])
            np.testing.assert_allclose(coarse["power"][0, :, 0, 0], native["power"][0, :, :4, :4].mean((1, 2)))
            self.assertTrue(np.isnan(coarse["power"][0, :, 1, 1]).all())
            with self.assertRaisesRegex(ValueError, "verified 10 m"):
                cube_from_chips([chip], aggregate=4, require_posting=20)

    def test_bulk_exact_ranges_run_concurrently_with_a_shared_budget(self):
        gate = Barrier(2, timeout=5)
        url = "https://nisar.asf.earthdatacloud.nasa.gov/test.h5"

        def download(_, start, end, timeout, bulk):
            if bulk:
                gate.wait()
                return bytes([start]) * (end - start + 1), 1000
            return b"\x89HDF\r\n\x1a\n" + bytes(8), 1000

        stats = {}
        with patch.object(RangeReader, "_download", download), RangeReader(
            url, "test", budget=24, parallel=True, stats=stats
        ) as reader:
            self.assertEqual(reader.cat_ranges([url, url], [20, 30], [24, 34]), [bytes([20])*4, bytes([30])*4])
            with self.assertRaisesRegex(RuntimeError, "transfer budget"):
                reader.cat_ranges([url], [40], [41])
            with self.assertRaisesRegex(ValueError, "Invalid archive"):
                reader.cat_ranges(["another-file"], [40], [41])
        self.assertEqual((stats["http_requests"], stats["asf_bytes"], stats["peak_requests"]), (3, 24, 2))
