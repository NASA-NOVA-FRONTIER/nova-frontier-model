"""Read an immutable release of measured cases and pinned v2 jobs."""
from hashlib import sha256
import json

from .service_artifacts import checksum
from .service_contracts import Job, JobRequest, canonical_job


class EventRelease:
    def __init__(self, root, release_sha256):
        self.root = root
        manifest_path = root / "checksums.json"
        if checksum(manifest_path) != release_sha256:
            raise ValueError("Unexpected event release checksum")
        files = json.loads(manifest_path.read_bytes())["files"]

        def verified_bytes(name):
            path = root / name
            data = path.read_bytes()
            if sha256(data).hexdigest() != files[name]:
                raise ValueError(f"Event release hash mismatch: {name}")
            return data

        self.index_bytes = verified_bytes("events.index.json")
        self.index = json.loads(self.index_bytes)
        self.events = {row["id"]: row for row in self.index["events"]}
        self.revision = self.index["build_revision"]
        self.records = {}
        self.requests = {}
        if len(self.events) != len(self.index["events"]):
            raise ValueError("Duplicate event ID")
        for row in self.events.values():
            replay = row["replay"]
            if replay is None:
                continue
            job_id = replay["job_id"]
            key, normalized = canonical_job(JobRequest.model_validate(replay["request"]), self.revision)
            if (key != replay["cache_key"] or normalized != replay["canonical_request"]
                    or replay["revision"] != self.revision or key in self.requests or job_id in self.records):
                raise ValueError("Event replay identity does not match its release")
            name = f"jobs/{job_id}/manifest.json"
            if replay["result_path"] != name or replay["result_sha256"] != files[name]:
                raise ValueError("Event replay result is missing from the release manifest")
            result = json.loads(verified_bytes(name))
            if result["revision"] != self.revision or result["point"] != [normalized["longitude"], normalized["latitude"]]:
                raise ValueError("Event replay result has a different provenance or location")
            self.records[job_id] = Job(job_id=job_id, status="succeeded", status_url=f"/v2/jobs/{job_id}",
                                       cache="hit", result=result)
            self.requests[key] = job_id

    def submit(self, request):
        key, _ = canonical_job(request, self.revision)
        return self.records.get(self.requests.get(key))

    def get(self, job_id):
        return self.records.get(job_id)
