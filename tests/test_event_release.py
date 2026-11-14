"""The pinned replay cache coexists with the normal live worker."""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from surface_dynamics.event_release import EventRelease
from surface_dynamics.service_api import create_app
from surface_dynamics.service_contracts import JobRequest, canonical_job
from surface_dynamics.service_state import JobStore, LocalDict


REQUEST = {"operation": "explore", "longitude": 73.713, "latitude": 32.329,
           "start_date": "2026-06-20", "end_date": "2026-07-26"}
PIN = "recorded-worker-revision"
JOB_ID = "a" * 32


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256(data).hexdigest()


class EventReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.release = self.root / "release"
        asset = b"numeric-layer-test-fixture"
        asset_name = f"jobs/{JOB_ID}/change.tif"
        digest = write(self.release / asset_name, asset)
        result = {"revision": PIN, "point": [73.713, 32.329], "artifacts": [
            {"name": "change.tif", "sha256": digest, "media_type": "image/tiff"}
        ]}
        key, normalized = canonical_job(JobRequest.model_validate(REQUEST), PIN)
        result_path = f"jobs/{JOB_ID}/manifest.json"
        result_hash = write(self.release / result_path, json.dumps(result).encode())
        index = {"build_revision": PIN, "events": [{"id": "qadirabad", "replay": {
            "request": REQUEST, "canonical_request": normalized, "cache_key": key,
            "revision": PIN, "job_id": JOB_ID, "result_path": result_path, "result_sha256": result_hash,
        }}]}
        index_hash = write(self.release / "events.index.json", json.dumps(index).encode())
        self.expected_sha = write(self.release / "checksums.json", json.dumps({"files": {
            "events.index.json": index_hash, result_path: result_hash,
        }}).encode())
        self.result_path = self.release / result_path
        self.recorded = result
        self.asset = asset

    def test_pinned_hit_and_range_use_release_and_misses_spawn_live_work(self):
        calls = []
        jobs = JobStore(LocalDict(), lambda *args: calls.append(args), "new-live-revision")
        events = EventRelease(self.release, self.expected_sha)
        with TestClient(create_app(self.root / "live", jobs, events=events)) as client:
            # An input that canonicalizes to the same point returns the frozen measurement.
            hit = client.post("/v2/jobs", json={**REQUEST, "longitude": 73.7132})
            self.assertEqual((hit.status_code, hit.json()["cache"], hit.json()["job_id"]), (200, "hit", JOB_ID))
            self.assertEqual(hit.json()["result"], self.recorded)
            self.assertEqual(client.get(hit.json()["status_url"]).json()["result"], self.recorded)
            layer = client.get(hit.json()["status_url"] + "/layers/change.tif", headers={"Range": "bytes=0-4"})
            self.assertEqual((layer.status_code, layer.content), (206, self.asset[:5]))
            self.assertEqual(calls, [])
            miss = client.post("/v2/jobs", json={**REQUEST, "longitude": 73.714})
            self.assertEqual((miss.status_code, miss.json()["cache"]), (202, "miss"))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0]["longitude"], 73.714)

    def test_corrupt_result_fails_to_load(self):
        self.result_path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "Event release hash mismatch"):
            EventRelease(self.release, self.expected_sha)
