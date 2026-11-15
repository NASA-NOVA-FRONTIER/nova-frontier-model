"""Smoke-test measured cases and pinned replays on a deployed gateway."""
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

from modal_preview_smoke import request

ROOT = Path(__file__).resolve().parents[1]


def verify(base):
    base = base.rstrip("/")
    config = json.loads((ROOT / "data/event_release.json").read_bytes())
    with urlopen(base + "/v2/events/checksums.json", timeout=60) as response:
        checksum_bytes = response.read()
    if sha256(checksum_bytes).hexdigest() != config["checksums_sha256"]:
        raise ValueError("Gateway is serving a different event release")
    expected = json.loads(checksum_bytes)["files"]["events.index.json"]
    with urlopen(base + "/v2/events.index.json", timeout=60) as response:
        index_bytes = response.read()
    if sha256(index_bytes).hexdigest() != expected:
        raise ValueError("Deployed event index differs from the verified release")
    index = json.loads(index_bytes)
    cases = {case["id"]: case for case in request(base + "/v2/cases")}
    if set(cases) != {case["id"] for case in index["events"]}:
        raise ValueError("The gateway served a different set of case manifests")
    for row in index["events"]:
        if row["replay"] is None:
            continue
        replay = row["replay"]
        job = request(base + "/v2/jobs", replay["request"])
        if (job["status"], job["cache"], job["job_id"]) != ("succeeded", "hit", replay["job_id"]):
            raise ValueError(f"Expected an immediate pinned replay for {row['id']}")
        if request(base + job["status_url"])["result"] != job["result"]:
            raise ValueError(f"Poll result differs for {row['id']}")
    sample = cases["qadirabad"]
    series = request(base + sample["series_url"])
    if not 0.71 < series["point_readout"]["channels"]["HH"]["delta_db"] < 0.72:
        raise ValueError("Qadirabad marker regression")
    with urlopen(Request(base + sample["layers"][0]["cog_url"], headers={"Range": "bytes=0-31"}),
                 timeout=60) as response:
        if response.status != 206 or len(response.read()) != 32:
            raise ValueError("The case COG did not return the requested byte range")
    print(f"Verified {len(cases)} curated cases and every pinned job replay")


if __name__ == "__main__":
    verify(sys.argv[1])
