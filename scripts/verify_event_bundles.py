"""Verify release hashes, native measurements and real v2 HTTP responses."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

import jsonschema
import numpy as np
import rasterio
from rasterio.warp import transform_bounds
import requests

from prepare_event_bundles import NULLS, files, pilot_headlines, raster_grid, read
from surface_dynamics.io import local_neighborhood, point_location
from surface_dynamics.service_artifacts import checksum, write_json
from surface_dynamics.service_contracts import Case, JobRequest, canonical_job

ROOT = Path(__file__).resolve().parents[1]
PILOT_GOLD = {
    "qadirabad": (1911, 16384, 332, 516, [1458, 1027, 1123, 1003, 1101], -4.9343244),
    "chokwe": (2880, 16384, 164, 440, [4667], -4.3442746),
    "richmond": (50, 40390, 0, 0, [8208, 6062, 4564, 12078], None),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def near(actual, expected):
    if expected is None:
        require(actual is None, "Expected null")
    else:
        np.testing.assert_allclose(actual, expected, atol=1e-6, rtol=0)


def verify_disk(folder, legacy=None):
    inventory = files(folder)
    del inventory["checksums.json"]
    require(inventory == read(folder / "checksums.json")["files"], "Release checksum manifest differs")
    index = read(folder / "events.index.json")
    schema = read(ROOT / "docs/schemas/events.index.schema.json")
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(index)
    ids = [r["id"] for r in index["events"]]
    require(len(ids) == len(set(ids)) and set(index["tour_order"]) <= set(ids), "Invalid tour order")
    require([r["id"] for r in read(folder / "index.json")] == ids, "v2 case order differs")
    for row in index["events"]:
        case_id = row["id"]
        case = folder / case_id
        meta = read(case / "bundle.json")
        manifest = read(case / "manifest.json")
        Case.model_validate(manifest)
        series = read(case / "series.json")
        file_map = files(case)
        require(file_map.pop("bundle.json") == row["bundle_sha256"] and file_map == meta["files"],
                f"Case checksum mismatch: {case_id}")
        require(row["null_fields"] == meta["null_fields"] == NULLS and
                all(k in manifest["evidence"] and manifest["evidence"][k] is None for k in NULLS),
                f"Scientific null policy differs: {case_id}")
        assets = {a["name"]: a for a in read(case / "assets.json")}
        for group in ("layers", "visualizations"):
            require(manifest[group] == row[group], f"Layer descriptors differ: {case_id}")
            for layer in row[group]:
                for field in ("cog_url", "png_url"):
                    name = Path(layer[field]).name
                    require(assets[name]["sha256"] == file_map[name], f"Unverified layer: {case_id}/{name}")
                with rasterio.open(case / Path(layer["cog_url"]).name) as raster:
                    require(raster.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG", "Raster is not a COG")
                    near(transform_bounds(raster.crs, "EPSG:4326", *raster.bounds), layer["bounds"])
        point, area = series["point_readout"], series["area_readout"]
        if row["series_kind"] == "measured_gamma0":
            verify_gcov(case, manifest, series, point, area, legacy)
        else:
            verify_insar(case, point, area)
        if row["replay"]:
            item = row["replay"]
            key, body = canonical_job(JobRequest.model_validate(item["request"]), index["build_revision"])
            require((key, body) == (item["cache_key"], item["canonical_request"]), f"Replay identity: {case_id}")
            result_path = folder / item["result_path"]
            require(checksum(result_path) == item["result_sha256"], f"Replay result: {case_id}")
            job = read(result_path)
            assets = {a["name"]: a for a in job["artifacts"]}
            for name, asset in assets.items():
                path = result_path.parent / name
                file_key = path.relative_to(folder).as_posix()
                require(asset["sha256"] == inventory[file_key] and asset["bytes"] == path.stat().st_size,
                        f"Replay artifact mismatch: {case_id}/{name}")
            for scene, row in zip(sorted(body["scenes"], key=lambda s: s["granule_id"]),
                                  sorted(job["science"]["observations"], key=lambda r: r["granule_title"]),
                                  strict=True):
                require(scene["granule_id"] == row["granule_title"], f"Replay granule mismatch: {case_id}")
                require(row["grid"]["width"] == row["grid"]["height"] == body["window_pixels"],
                        f"Replay chip size mismatch: {case_id}")
    return {"case_count": len(ids), "checksummed_files": len(inventory), "schema": "pass", "goldens": "pass"}


def verify_gcov(folder, case, series, point, area, legacy=None):
    arrays = {}
    pixel = tuple(point["pixel"])
    for row in series["observations"]:
        date, pol = row["date"], row["polarization"]
        with rasterio.open(folder / f"display_{date}_{pol}.tif") as raster:
            arrays[date, pol] = raster.read(1)
        raw = arrays[date, pol][pixel]
        near(row["gamma0_db"], float(raw) if np.isfinite(raw) else None)
    common = np.isfinite(np.stack(list(arrays.values()))).all(0)
    require(int(common.sum()) == area["denominator"] and common.size == area["total_cells"], "GCOV population mismatch")
    for pol, stats in area["channels"].items():
        with rasterio.open(folder / f"baseline_median_{pol.lower()}.tif") as raster:
            delta = arrays[area["target_date"], pol] - raster.read(1)
        near(point["channels"][pol]["delta_db"], float(delta[pixel]) if np.isfinite(delta[pixel]) else None)
        near(stats["delta_db"]["median"], float(np.median(delta[common])) if common.any() else None)
    case_id = case["id"]
    if case_id in PILOT_GOLD:
        recomputed = pilot_headlines(folder / "characterization_arrays.nc", case, series, arrays)
        require(recomputed == area["diagnostic_counts"], "Flood readout differs from native diagnostics")
        k, n, kw, nw, counts, contrast = PILOT_GOLD[case_id]
        dual, new = recomputed["dual_darkening"], recomputed["new_water_group_dual_darkening"]
        require((dual["cells"], dual["denominator"], new["cells"], new["denominator"]) == (k, n, kw, nw),
                "Pilot dual-darkening regression")
        timeline = recomputed["baseline_band_departures"]
        require([item["outside_cells"] for item in timeline] == counts, "Pilot band-regression counts")
        near(recomputed["matched_mean_hh_contrast_db"], contrast)
    if case_id == "qadirabad":
        near(point["channels"]["HH"]["delta_db"], 0.7151384)
        near(point["channels"]["HV"]["delta_db"], 1.2594604)
    if case_id == "chokwe":
        near(recomputed["dual_darkening"]["percent"], 17.578125)
        near(timeline[0]["outside_percent"], 28.485107421875)
    if case_id == "richmond":
        require(case["grid"]["shape"] == [256, 256], "Richmond shape")
        w, s, e, n = case["grid"]["bounds"]
        require((e - w, n - s) == (10240, 10240), "Richmond extent")
    if case_id == "skollersta":
        published = area["external_trial_bracket_comparison"]
        with rasterio.open(folder / "display_2026-08-10_HH.tif") as raster:
            x, y, epsg = raster_grid(raster)
        cube = dict(x=x, y=y, epsg=epsg)
        _, focal, annulus = local_neighborhood(cube, point_location(cube, case["point"]), common)
        require((focal.sum(), annulus.sum()) == (29, 593), "Sköllersta population regression")
        require(published["harvest_bracket"] == ["2026-08-10", "2026-08-22"], "Sköllersta bracket changed")
        for pol, expected in (("HH", 1.7763109), ("HV", 1.0470276)):
            before, after = (arrays[date, pol] for date in published["harvest_bracket"])
            step = (np.median(after[focal]) - np.median(after[annulus])
                    - (np.median(before[focal]) - np.median(before[annulus])))
            near(step, expected)
            near(step, published["by_pol"][pol]["harvest_bracketing_step_db"])
    if case_id in ("high-plains", "bug-fire"):
        with rasterio.open(folder / "cross_pol_pattern.tif") as raster:
            flag = raster.read(1) == 2
            require(int(flag.sum()) == {"high-plains": 79, "bug-fire": 155}[case_id], "Repeated HV decline regression")
            if case_id == "bug-fire" and legacy:
                verify_fire_reference(raster, flag, common, area, legacy)
        if case_id == "high-plains":
            optical = case["evidence"]["independent_reference"]
            require((optical["confirmed_loss_cells"], optical["usable_cells"]) == (0, 256), "Optical control regression")


def verify_fire_reference(raster, flag, common, area, legacy):
    from rasterio.features import rasterize
    from rasterio.warp import transform_geom
    from scipy.ndimage import binary_dilation, distance_transform_edt

    labels = read(legacy / "scratch/exploration-holdout-v2/references/bug_labels.json")
    require(not labels["excluded"] and not labels["footprint"], "Bug Fire reference geometry changed")
    footprints = [transform_geom("EPSG:4326", raster.crs, geom) for geom in labels["positive"]]
    observed = rasterize(footprints, out_shape=raster.shape, transform=raster.transform).astype(bool)
    spacing = abs(raster.transform.a)
    n = int(40 / spacing)
    offset = np.arange(-n, n + 1)
    disk = np.hypot(offset[:, None], offset[None, :]) <= n
    inside = distance_transform_edt(observed) * spacing > 40
    outside = ~binary_dilation(observed, structure=disk)
    for name, mask, expected in (("inside", inside, (127, 1737)), ("outside", outside, (18, 1813))):
        support = mask & common
        reported = area["external_perimeter_comparison"][name]
        require((int((flag & support).sum()), int(support.sum())) == expected, "Bug Fire reference regression")
        require((reported["counts"]["cross_pol_decline_both_dates"], reported["common_radar_cells"]) == expected,
                "Bug Fire readout differs from the retained geometry")



def verify_insar(folder, point, area):
    import xarray as xr

    expected = {"kumamoto": (35344, 1614, 3), "choco": (3844, 0, 0)}[folder.name]
    with xr.open_dataset(folder / "correction_readiness_arrays.nc", engine="h5netcdf") as ds:
        common = ds.common_coherence_support.values.astype(bool)
        phase = ds.common_phase_support.values.astype(bool)
        eligible = ds.common_correction_input_eligible.values.astype(bool)
        require((common.sum(), phase.sum(), eligible.sum()) == expected, "GUNW support regression")
        require((area["denominator"], area["shared_phase_cells"], area["correction_input_eligible_cells"]) == expected,
                "GUNW readout support differs")
        for i, item in enumerate(area["intervals"]):
            with rasterio.open(folder / f"coherence_pair_{i + 1}.tif") as raster:
                data = raster.read(1)
            dates = [str(ds.reference_time.values[i])[:10], str(ds.secondary_time.values[i])[:10]]
            require(dates == item["dates"], "GUNW pair dates differ")
            np.testing.assert_allclose(data, ds.coherence.values[i], atol=1e-7, rtol=0, equal_nan=True)
            near(item["common_coherence"]["median"], float(np.median(data[common])))
            cell = data[tuple(point["pixel"])]
            near(point["intervals"][i]["coherence"], float(cell) if np.isfinite(cell) else None)
            phase_value = ds.phase_relative_to_shared_pixel_rad.values[i]
            marker_phase = phase_value[tuple(point["pixel"])]
            near(point["intervals"][i]["uncorrected_relative_phase_rad"],
                 float(marker_phase) if np.isfinite(marker_phase) else None)
            phase_file = folder / f"wrapped_relative_phase_pair_{i + 1}.tif"
            require(phase_file.exists() == np.isfinite(phase_value).any(), "Phase layer availability differs")
            if phase_file.exists():
                with rasterio.open(phase_file) as raster:
                    expected_phase = (phase_value + np.pi) % (2 * np.pi) - np.pi
                    np.testing.assert_allclose(raster.read(1), expected_phase, atol=1e-6, rtol=0, equal_nan=True)
    require(point["los_displacement_cm"] is None and area["los_displacement_cm"] is None, "LOS must stay null")


def verify_http(client, folder, *, replay_only=False):
    catalog = read(folder / "events.index.json")
    timings = []

    def request(method, url, expected=200, **kwargs):
        start = perf_counter()
        response = client.request(method, url, timeout=45, **kwargs)
        duration = (perf_counter() - start) * 1000
        require(response.status_code == expected, f"{method} {url} returned {response.status_code}")
        timings.append({"method": method, "path": url, "ms": round(duration, 2), "status": expected})
        return response

    request("GET", "/health")
    require(request("GET", "/v2/events.index.json").content == (folder / "events.index.json").read_bytes(),
            "Hosted catalog differs")
    require(request("GET", "/v2/events/checksums.json").content == (folder / "checksums.json").read_bytes(),
            "Hosted checksum manifest differs")
    require(request("GET", "/v2/cases").json() == read(folder / "index.json"), "Cases endpoint differs")

    def layer_pair(png, cog, directory):
        image = request("GET", png)
        require(sha256(image.content).hexdigest() == checksum(directory / Path(png).name), "Hosted PNG differs")
        path = directory / Path(cog).name
        response = request("GET", cog, expected=206, headers={"Range": "bytes=0-4095"})
        with path.open("rb") as stream:
            require(response.content == stream.read(4096), "COG range differs")
        require(response.headers["Content-Range"] == f"bytes 0-{len(response.content) - 1}/{path.stat().st_size}"
                and response.headers["Accept-Ranges"] == "bytes"
                and response.headers["ETag"] == f'"{checksum(path)}"', "Missing COG range or integrity headers")

    for row in catalog["events"]:
        case = folder / row["id"]
        require(request("GET", row["manifest_url"]).json() == read(case / "manifest.json"), "Manifest mismatch")
        require(request("GET", row["series_url"]).json() == read(case / "series.json"), "Series mismatch")
        require(sha256(request("GET", row["bundle_url"]).content).hexdigest() == row["bundle_sha256"],
                "Bundle metadata hash mismatch")
        layer = row["layers"][0]
        layer_pair(layer["png_url"], layer["cog_url"], case)
        if item := row["replay"]:
            result = read(folder / item["result_path"])
            for _ in range(2):
                response = request("POST", "/v2/jobs", json=item["request"]).json()
                require(response["status"] == "succeeded" and response["cache"] == "hit"
                        and response["job_id"] == item["job_id"] and response["result"] == result,
                        "Expected an identical v2 cache hit")
            require(request("GET", response["status_url"]).json()["result"] == result, "Poll result differs")
            layer = next(layer for layer in result["layers"] if layer["cog_url"])
            layer_pair(layer["png_url"], layer["cog_url"], (folder / item["result_path"]).parent)
    if replay_only:
        missed = next(row["replay"]["request"] for row in catalog["events"] if row["replay"])
        miss = request("POST", "/v2/jobs", expected=429, json={**missed, "longitude": 0})
        require(miss.json()["code"] == "daily_budget", "Cache miss unexpectedly admitted new work")
    return timings


class Remote:
    def __init__(self, url):
        self.base = url.rstrip("/")
        self.session = requests.Session()

    def request(self, method, path, **kwargs):
        return self.session.request(method, self.base + path, **kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--replay-only", action="store_true",
                        help="Confirm a miss is rejected on the isolated archive-disabled staging gateway.")
    parser.add_argument("--timings", type=Path)
    args = parser.parse_args()
    report = verify_disk(args.release, args.legacy)
    if args.url:
        timings = verify_http(Remote(args.url), args.release, replay_only=args.replay_only)
        if args.timings:
            write_json(args.timings, timings)
        report["requests"] = len(timings)
        for group, subset in (
            ("replay", [r for r in timings if r["method"] == "POST" and r["status"] == 200]),
            ("warm_reads", [r for r in timings[1:] if r["method"] == "GET"]),
        ):
            report[group + "_ms"] = {"min": min(r["ms"] for r in subset), "max": max(r["ms"] for r in subset)}
        report["first_health_ms"] = timings[0]["ms"]
    print(json.dumps(report))
