"""Smoke test a preview gateway with a real InSAR pair and an optional GCOV scene plan.

Environment: GATEWAY_URL, EXPECTED_GIT_SHA and optionally EXPECTED_JOBS_PER_DAY.
Standard library only, so the workflow needs nothing beyond the Modal client.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
import os
from pathlib import Path
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KUMAMOTO = (130.65, 32.65)
CATALOG_WINDOW_DAYS = 150
HEALTH_ATTEMPTS = 12
HEALTH_WAIT_SECONDS = 10
POLL_SECONDS = 6
JOB_BUDGET_SECONDS = 420
REQUEST_TIMEOUT_SECONDS = 60
COHERENCE_THRESHOLD = 0.35
DONE = ("succeeded", "failed")


def request(url, body=None, *, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"} if data else {}
    http_request = urllib.request.Request(url, data=data, headers=headers)
    if data is not None and token is not None:
        http_request.add_unredirected_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)


def verify_job_rejected(base, *, token=None):
    """An invalid payload prevents this probe from scheduling work even without auth."""
    try:
        request(f"{base}/v2/jobs", {}, token=token)
    except urllib.error.HTTPError as error:
        with error:
            if error.code == 401 and json.load(error).get("code") == "unauthorized":
                return
        raise SystemExit(f"Job submission returned HTTP {error.code}; expected a 401 authentication error.") from None
    raise SystemExit("Job submission accepted a request without valid credentials.")


def wait_for_health(base, expected_sha):
    """A redeploy can keep answering from the previous version for a while, so wait for the new one."""
    last = None
    for _ in range(HEALTH_ATTEMPTS):
        try:
            last = request(f"{base}/health")
            if last.get("status") == "ok" and last.get("git_sha") == expected_sha:
                return last
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
        time.sleep(HEALTH_WAIT_SECONDS)
    if not last or last.get("status") != "ok":
        raise SystemExit(f"/health did not answer ok: {last}")
    raise SystemExit(f"/health reports {last.get('git_sha')}, expected {expected_sha}")


def first_pair(coverage):
    """The first GUNW pair the catalog lists, with its two acquisition days."""
    for series in coverage.get("series", []):
        for observation in series.get("observations", []):
            days = sorted(observation.get("dates", []))
            if len(days) == 2:
                return observation["id"], days[0], days[1]
    return None


def pair_job_body(pair):
    pair_id, start, end = pair
    return {"operation": "insar-pair", "longitude": KUMAMOTO[0], "latitude": KUMAMOTO[1],
            "start_date": start, "end_date": end, "pair_id": pair_id,
            "coherence_threshold": COHERENCE_THRESHOLD}


def run_job(base, body, *, token=None):
    started = time.monotonic()
    job = request(f"{base}/v2/jobs", body, token=token)
    while job.get("status") not in DONE:
        if time.monotonic() - started > JOB_BUDGET_SECONDS:
            raise SystemExit(f"job {job.get('job_id')} did not finish in {JOB_BUDGET_SECONDS} s")
        time.sleep(POLL_SECONDS)
        job = request(f"{base}{job['status_url']}")
    result = job.get("result") or {}
    if job["status"] != "succeeded" or not result.get("layers"):
        raise SystemExit(f"job ended {job['status']} with {len(result.get('layers') or [])} layers: {job.get('error')}")
    return round(time.monotonic() - started), result


def run_pair_job(base, *, token=None):
    today = date.today()
    query = (f"longitude={KUMAMOTO[0]}&latitude={KUMAMOTO[1]}&product=GUNW"
             f"&start_date={today - timedelta(days=CATALOG_WINDOW_DAYS)}&end_date={today}")
    pair = first_pair(request(f"{base}/v2/coverage?{query}"))
    if pair is None:
        raise SystemExit("the GUNW catalog listed no pair over Kumamoto")
    seconds, result = run_job(base, pair_job_body(pair), token=token)
    print(f"insar-pair job {result['science_status']} in {seconds} s with {len(result['layers'])} layers")


def run_gcov_job(base, *, token=None):
    body = json.loads(Path(__file__).with_name("modal_preview_choco.json").read_text(encoding="utf-8"))
    seconds, result = run_job(base, body, token=token)
    science = result["science"]
    reading = science["gcov_reading"]
    if reading["reader"] != "pyfive" or reading["native_window_pixels"] != 384:
        raise SystemExit("Chocó did not use the configured pyfive reader and native window")
    grids = [item["grid"] for item in science["observations"] if item.get("grid")]
    if len(grids) != len(body["scenes"]):
        raise SystemExit(f"Chocó returned only {len(grids)} measured scenes")
    for grid in grids:
        x0, y0, x1, y1 = grid["bounds_native"]
        if (grid["width"], grid["height"], grid["pixel_spacing_m"], x1 - x0, y1 - y0) != (
                96, 96, [40, 40], 3840, 3840):
            raise SystemExit(f"Unexpected Chocó grid: {grid}")
    reads = list(reading["reads"].values())
    batches = sum(read.get("range_batches", 0) for read in reads)
    if not batches and not (len(reads) == len(grids) and all(read.get("chip_hit") for read in reads)):
        raise SystemExit("No parallel range batches or complete chip-cache reuse recorded")
    url = urllib.parse.urljoin(base + "/", result["layers"][0]["png_url"])
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        header = response.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("The Chocó layer did not serve a PNG")
    width, height = struct.unpack(">II", header[16:24])
    if not (0 < width <= 256 and 0 < height <= 256):
        raise SystemExit(f"Unexpected Chocó display PNG dimensions: {width} × {height}")
    print(f"Chocó GCOV job in {seconds} s, EPSG:{grids[0]['epsg']}, "
          f"96 × 96 at 40 m, {len(grids)} scenes, {batches} range batches, PNG {width} × {height}")


def main():
    base = os.environ["GATEWAY_URL"].rstrip("/")
    health = wait_for_health(base, os.environ["EXPECTED_GIT_SHA"])
    print(f"health ok at {health['git_sha'][:7]}")
    expected = os.environ.get("EXPECTED_JOBS_PER_DAY")
    if expected:
        limit = request(f"{base}/v2/capabilities")["daily_new_jobs_total"]
        if limit != int(expected):
            raise SystemExit(f"daily job limit is {limit}, expected {expected}")
    token = None
    if os.environ.get("NISAR_PREVIEW_JOB_AUTH") == "1":
        token = os.environ["NISAR_GATEWAY_SHARED_SECRET"]
        verify_job_rejected(base)
        verify_job_rejected(base, token="invalid-preview-token")
        print("Job submission rejected missing and incorrect credentials")
    if os.environ.get("NISAR_GCOV_READER") != "pyfive" or os.environ.get("NISAR_GCOV_POSTING_M") != "40":
        run_pair_job(base, token=token)
        return
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pair = pool.submit(run_pair_job, base, token=token)
        gcov = pool.submit(run_gcov_job, base, token=token)
        pair.result()
        gcov.result()
    print(f"Concurrent InSAR and GCOV smoke requests completed in {time.monotonic() - started:.1f} s")


if __name__ == "__main__":
    sys.exit(main())
