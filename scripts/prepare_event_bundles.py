"""Assemble measured case bundles and exact v2 replay jobs without archive access."""
import argparse
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from shutil import copy2
from subprocess import check_output
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.warp import transform, transform_bounds
import xarray as xr

from prepare_api_cases import series as measured_series
from surface_dynamics import __version__, explore
from surface_dynamics.analysis import supported_reduction
from surface_dynamics.flood_signatures import POLICY
from surface_dynamics.io import cube_from_chips
from surface_dynamics.plan_input import COLLECTION_PATHS, entries_from_scenes, series_key
from surface_dynamics.service_artifacts import checksum, native_grid, publish_files, render_layer, write_json
from surface_dynamics.service_contracts import Case, JobRequest, canonical_job
from surface_dynamics.service_workers import execute_job, published_science
from surface_dynamics.surface_features import from_db

ROOT = Path(__file__).resolve().parents[1]
NULLS = dict(forecast=None, phenomenon_classification=None, probability=None, causal_attribution=None,
             los_displacement_cm=None, observation_noise_db=None, model_uncertainty_db=None)
CREDITS = {
    "GCOV": "NASA–ISRO NISAR L-band GCOV, distributed by NASA ASF DAAC.",
    "GUNW": "NASA–ISRO NISAR L-band GUNW, distributed by NASA ASF DAAC.",
    "OPERA": "NASA/JPL OPERA DSWx-HLS and DIST-ALERT-HLS, distributed by NASA PO.DAAC and LP DAAC.",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def files(folder):
    return {p.relative_to(folder).as_posix(): checksum(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def copy_prepared(source, folder):
    assets = read(source / "assets.json")
    for name in ("manifest.json", "series.json", *(a["name"] for a in assets)):
        if Path(name).name != name:
            raise ValueError("Invalid asset name")
        copy2(source / name, folder / name)
    for asset in assets:
        path = folder / asset["name"]
        if path.stat().st_size != asset["bytes"] or checksum(path) != asset["sha256"]:
            raise ValueError(f"Prepared asset checksum mismatch: {path}")
    return read(folder / "manifest.json"), read(folder / "series.json")


def select_chips(folder, spec, full_history):
    chips = []
    for sidecar in folder.glob("*.json"):
        meta = read(sidecar)
        if "key" in spec and (series_key(meta["title"]) != spec["key"]
                              or meta["requested_pixels"] != spec["pixels"]):
            continue
        if meta["date"] <= spec.get("target_date", "9999-12-31"):
            chips.append((sidecar.with_suffix(".npz"), meta))
    chips.sort(key=lambda row: row[1]["date"])
    cutoff = spec["baseline_end_date"]
    before = [row for row in chips if row[1]["date"] <= cutoff]
    after = [row for row in chips if row[1]["date"] > cutoff]
    if not before or not after:
        raise ValueError(f"Missing measured baseline or assessment chips: {folder}")
    if not full_history:
        after = after[-2:]
        before = before[-(5 - len(after)):]
    selected = before + after
    if len(selected) > 5 or len({m["date"] for _, m in selected}) != len(selected):
        raise ValueError(f"Ambiguous or oversized scene plan: {folder}")
    first, last = selected[0][1], selected[-1][1]
    point, pixels = first["query_point"], first["requested_pixels"]
    scenes = []
    for _, meta in selected:
        if meta["query_point"] != point or meta["requested_pixels"] != pixels:
            raise ValueError("Measured chips refer to different windows")
        collection, = [k for k, v in COLLECTION_PATHS.items() if f"/{v}/" in meta["source"]]
        scenes.append({"granule_id": meta["title"], "collection": collection})
    request = dict(operation="explore", longitude=point[0], latitude=point[1],
                   start_date=first["date"], end_date=last["date"], window_pixels=pixels,
                   compare_dates=[before[-1][1]["date"], last["date"]], baseline_end_date=cutoff,
                   optical_evidence=False, lens=spec["lens"], scenes=scenes)
    JobRequest.model_validate(request)
    return selected, request


def offline_job(chips, body, out, cache, job_id, revision):
    _, entries = entries_from_scenes(body["scenes"])
    query = explore.parse_request({k: body[k] for k in ("longitude", "latitude", "start_date", "end_date")})
    query = replace(query, chip_pixels=body["window_pixels"])
    cache.mkdir()
    sources = {meta["title"]: path for path, meta in chips}
    fetch = explore.fetch_chip
    for entry in entries:
        source = sources[entry["title"]]
        path = explore._chip_path(query, entry, cache)
        copy2(source, path)
        copy2(source.with_suffix(".json"), path.with_suffix(".json"))
        fetch(entry, query.longitude, query.latitude, path, pixels=query.chip_pixels, cache_only=True)

    def cache_only(*args, **kwargs):
        return fetch(*args, **{**kwargs, "cache_only": True})

    with patch.object(explore, "fetch_chip", cache_only), patch.object(
        explore, "discover", side_effect=RuntimeError("Offline build cannot query the catalog")
    ):
        result = execute_job(body, job_id, out, cache, revision)
    if result["science_status"] not in {"complete", "partial"} or result["science"]["lens"]["status"] != "evaluated":
        raise ValueError(f"No measured evaluation for {job_id}")
    return result


def case_from_job(folder, case_id, spec, chips, result, job_dir, reference):
    science = result["science"]
    lens = science["lens"]
    cube = cube_from_chips([p for p, _ in chips], aggregate=1)
    grid = native_grid(cube["x"], cube["y"], cube["epsg"])
    before, target = lens["baseline_dates"], lens["target_date"]
    after = lens.get("assessment_dates") or [target]
    prefix = f"/v2/cases/{case_id}"
    for asset in result["artifacts"]:
        name = asset["name"]
        if Path(name).suffix in {".png", ".tif"}:
            if checksum(job_dir / name) != asset["sha256"]:
                raise ValueError(f"Worker asset changed: {name}")
            copy2(job_dir / name, folder / name)
    urls = {a["name"]: a["href"] for a in publish_files(folder, prefix + "/layers")}
    case_layers = {
        group: [{**item, **{k: urls[Path(item[k]).name] for k in ("png_url", "cog_url")}}
                for item in result[group] if item.get("cog_url")]
        for group in ("layers", "visualizations")
    }
    evidence = {"state": "radar_evaluated", "independent_reference": reference,
                "radar_support_cells": lens["radar"]["common_cells"],
                "lens_report": published_science(lens, urls)}
    case = Case(id=case_id, title=spec["title"], point=result["point"], grid=grid,
                series_key=science["series_key"], baseline_dates=before, assessment_dates=after,
                status=lens["status"], evidence=evidence, series_url=prefix + "/series",
                **case_layers).model_dump(mode="json")
    co, cross = lens["radar"]["polarizations"][:2]
    samples = measured_series(cube, grid, case["point"], {
        "baseline_dates": before, "assessment_dates": after, "sources": [m for _, m in chips],
    }, np.full(cube["power"].shape[-2:], 255), {"co_polarization": co, "cross_polarization": cross})
    samples["observed_groups"] = [g for g in samples["observed_groups"] if g["group"] == "all_common_cells"]
    samples["target_date"] = target
    return case, samples


def raster_grid(band):
    x = band.transform.c + (np.arange(band.width) + .5) * band.transform.a
    y = band.transform.f + (np.arange(band.height) + .5) * band.transform.e
    return x, y, band.crs.to_epsg()


def marker_pixel(point, band):
    x, y = transform("EPSG:4326", band.crs, [point[0]], [point[1]])
    row, col = band.index(x[0], y[0])
    if not (0 <= row < band.height and 0 <= col < band.width):
        raise ValueError("Marker falls outside the native grid")
    return row, col


def stats(values):
    values = values[np.isfinite(values)]
    return {"cells": int(values.size), "median": float(np.median(values)) if values.size else None,
            "spatial_iqr": np.percentile(values, [25, 75]).tolist() if values.size else None}


def value(x):
    return float(x) if np.isfinite(x) else None


def add_layer(folder, case, name, array, coords, units, cmap, stretch):
    case["layers"].append(render_layer(folder, f"/v2/cases/{case['id']}/layers", name, array, *coords,
                                       units=units, cmap=cmap, stretch=stretch))

def backscatter_readouts(folder, case, samples, spec):
    pilot = folder / "characterization_arrays.nc"
    observations = samples["observations"]
    dates = sorted({row["date"] for row in observations})
    pols = sorted({row["polarization"] for row in observations})
    before = case["baseline_dates"]
    target = samples.get("target_date", case["assessment_dates"][0])
    if pilot.is_file():
        with xr.open_dataset(pilot, engine="h5netcdf") as ds:
            coords = ds.x.values, ds.y.values, ds.attrs["epsg_code"]
            for day in dates:
                for pol in pols:
                    power = ds.gamma0_power.sel(time=day, polarization=pol).values
                    with np.errstate(divide="ignore", invalid="ignore"):
                        db = np.where(power > 0, 10 * np.log10(power), np.nan)
                    add_layer(folder, case, f"display_{day}_{pol}", db, coords, "dB", "gray", (-30, 0))

    arrays = {}
    for day in dates:
        for pol in pols:
            with rasterio.open(folder / f"display_{day}_{pol}.tif") as band:
                coords = raster_grid(band)
                grid = native_grid(*coords)
                if grid != {k: case["grid"][k] for k in grid}:
                    raise ValueError(f"Unaligned pass layer: {case['id']} {day} {pol}")
                pixel = marker_pixel(case["point"], band)
                arrays[day, pol] = band.read(1)
    for obs in observations:
        measured = value(arrays[obs["date"], obs["polarization"]][pixel])
        expected = obs["gamma0_db"]
        if measured != expected and (measured is None or expected is None
                                     or not np.isclose(measured, expected, atol=1e-5, rtol=0)):
            raise ValueError(f"Marker series disagrees with measured grid: {case['id']} {obs['date']}")

    common = np.isfinite(np.stack(list(arrays.values()))).all(axis=0)
    if "observed_groups" not in samples:
        samples["observed_groups"] = [
            {"date": day, "polarization": pol, "group": "all_common_cells", "cells": s["cells"],
             "median_db": s["median"], "spatial_iqr_db": s["spatial_iqr"]}
            for (day, pol), array in arrays.items() for s in [stats(array[common])]
        ]
    point, area = {}, {}
    for pol in pols:
        name = f"baseline_median_{pol.lower()}"
        path = folder / f"{name}.tif"
        if path.is_file():
            with rasterio.open(path) as band:
                baseline = band.read(1)
        else:
            baseline = supported_reduction(np.stack([arrays[d, pol] for d in before]), np.nanmedian)
            add_layer(folder, case, name, baseline, coords, "dB", "gray", (-30, 0))
        observed = arrays[target, pol]
        delta = observed - baseline
        point[pol] = {"baseline_db": value(baseline[pixel]), "observed_db": value(observed[pixel]),
                      "delta_db": value(delta[pixel])}
        area[pol] = {"baseline_db": stats(baseline[common]), "observed_db": stats(observed[common]),
                     "delta_db": stats(delta[common])}
    n = int(common.sum())
    location = dict(grid=case["grid"], baseline_dates=before, target_date=target,
                    comparator="Target pass minus cell-wise median of the listed baseline passes")
    point_deltas = ", ".join(f"{p} {v['delta_db']:+.2f} dB" for p, v in point.items() if v["delta_db"] is not None)
    medians = {pol: a["delta_db"]["median"] for pol, a in area.items()}
    area_deltas = ", ".join(f"{p} {v:+.2f} dB" for p, v in medians.items() if v is not None)
    point_text = (f"On {target} the marker shifted {point_deltas} relative to the baseline."
                  if point_deltas else f"On {target} the marker had insufficient paired radar support.")
    area_text = (f"On {target} the median shifts across {n:,} common cells were {area_deltas}."
                 if area_deltas else f"On {target} no cells had common support across the published series.")
    marker = dict(**location, point=case["point"], pixel=list(pixel), population="marker pixel",
                  denominator=int(bool(point_deltas)), channels=point, sentence=point_text + " " + spec["caveat"])
    window = dict(**location, population="cells measured in all published passes and channels",
                  denominator=n, total_cells=int(common.size), channels=area, sentence=area_text + " " + spec["caveat"])
    if pilot.is_file():
        window["diagnostic_counts"] = pilot_headlines(pilot, case, samples, arrays)
    return marker, window


def pilot_headlines(path, case, samples, arrays):
    before, after = case["baseline_dates"], case["assessment_dates"]
    data = np.asarray([[arrays[d, p] for p in ("HH", "HV")] for d in before + after])
    report = from_db(data, before + after, list(range(len(before))), list(range(len(before), len(data))))
    n = report["quality"]["fixed_common_cells"] if report["quality"]["diagnostic_available"] else 0
    passes = [{"date": day, "outside_cells": int(m.sum()), "eligible_cells": n,
               "outside_percent": 100 * int(m.sum()) / n if n else None}
              for day, m in zip(after, report["maps"]["assessment_departure_mask"], strict=True)]
    if passes != samples["diagnostic_band_timeline"]:
        raise ValueError("Per-pass out-of-band counts disagree with native measurements")
    with xr.open_dataset(path, engine="h5netcdf") as ds:
        support = ds.comparison_support.values.astype(bool)
        co = ds.target_change_db.sel(polarization="HH").values
        cross = ds.target_change_db.sel(polarization="HV").values
        flags = support & (co <= -POLICY["co_pol_change_db"]) & (cross <= -POLICY["cross_pol_darkening_db"])
        np.testing.assert_array_equal(flags, ds.candidate_darkening.values.astype(bool))
        k, n = int(flags.sum()), int(support.sum())
        evidence = case["evidence"]
        if (k, n) != (evidence["threshold_cells"], evidence["radar_support_cells"]):
            raise ValueError("Dual-darkening counts disagree with native mask")
        new = support & (ds.reference_group.values == 1)
        kn, nn = int((flags & new).sum()), int(new.sum())
        comparison = evidence["comparison"]
        if (kn, nn) != (comparison["true_positive_proxy_pixels"],
                        comparison["true_positive_proxy_pixels"] + comparison["false_negative_proxy_pixels"]):
            raise ValueError("New-water group counts disagree with native masks")
        positive = ds.matched_positive_mask.values.astype(bool)
        control = ds.matched_control_mask.values.astype(bool)
        contrast = float(co[positive].mean() - co[control].mean()) if positive.any() and control.any() else None
    return dict(dual_darkening={"cells": k, "denominator": n, "percent": 100 * k / n},
                new_water_group_dual_darkening={"cells": kn, "denominator": nn, "percent": 100 * kn / nn if nn else None},
                baseline_band_departures=passes, matched_mean_hh_contrast_db=contrast,
                matched_positive_cells=int(positive.sum()), matched_control_cells=int(control.sum()))


def insar_readouts(folder, case, samples, spec, source, legacy):
    case_id = case["id"]
    name = "correction_readiness_arrays.nc" if case_id == "choco" else "interferometric_interval_dynamics.nc"
    if case_id == "choco":
        job = read(source / "manifest.json")
        report = job["science"]
        asset = next(a for a in job["artifacts"] if a["name"] == name)
        if checksum(folder / name) != asset["sha256"]:
            raise ValueError("Chocó array does not match its original job")
    else:
        report = read(ROOT / "docs/results/kumamoto_wide_correction_readiness.json")
        readiness = legacy / "products/insar_readiness/kumamoto_wide/correction_readiness_arrays.nc"
        asset = next(row for row in report["artifacts"] if row["id"] == "correction_readiness_arrays")
        if checksum(readiness) != asset["sha256"]:
            raise ValueError("Kumamoto readiness array differs from the checked report")
        copy2(readiness, folder / readiness.name)
        case["evidence"]["catalog_network"] = next(
            n for n in read(ROOT / "docs/results/kumamoto_network.json")["networks"]
            if n["series_key"] == case["series_key"])
    quality = report["quality"]
    case["evidence"]["correction_readiness"] = {
        k: report[k] for k in ("status", "quality", "pair_quality", "required_gates", "corrections_applied")
    }
    with rasterio.open(folder / "coherence.tif") as band:
        row, col = marker_pixel(case["point"], band)
    intervals, marker = [], []
    with xr.open_dataset(folder / name, engine="h5netcdf") as ds:
        coords = ds.x.values, ds.y.values, ds.attrs["epsg_code"]
        common = ds.common_coherence_support.values.astype(bool)
        phase_support = int(ds.common_phase_support.values.sum())
        for i, item in enumerate(samples["intervals"]):
            dates = [str(d)[:10] for d in (ds.reference_time.values[i], ds.secondary_time.values[i])]
            if dates != [item["reference_date"], item["secondary_date"]]:
                raise ValueError("InSAR pair date mismatch")
            coh = ds.coherence.values[i]
            phase = ds.phase_relative_to_shared_pixel_rad.values[i]
            full, shared = stats(coh), stats(coh[common])
            observed = item.get("median_coherence", item.get("common_spatial_support_median_coherence"))
            computed = full["median"] if "median_coherence" in item else shared["median"]
            if not np.isclose(observed, computed, atol=1e-7, rtol=0):
                raise ValueError("InSAR median coherence mismatch")
            add_layer(folder, case, f"coherence_pair_{i + 1}", coh, coords, "unitless", "cividis", (0, 1))
            if np.isfinite(phase).any():
                wrapped = (phase + np.pi) % (2 * np.pi) - np.pi
                add_layer(folder, case, f"wrapped_relative_phase_pair_{i + 1}", wrapped, coords,
                          "rad", "twilight", (-np.pi, np.pi))
            intervals.append({"dates": dates, "granule_id": item["source_id"], "coherence": full,
                              "common_coherence": shared, "shared_phase_cells": phase_support})
            marker.append({"dates": dates, "coherence": value(coh[row, col]),
                           "uncorrected_relative_phase_rad": value(phase[row, col])})
        corrected = quality["common_correction_input_pixels"]
        if "common_correction_input_eligible" in ds and ds.common_correction_input_eligible.values.sum() != corrected:
            raise ValueError("Chocó correction-input mask disagrees with its report")
    if case_id == "kumamoto":
        with xr.open_dataset(folder / readiness.name, engine="h5netcdf") as ds:
            if native_grid(ds.x.values, ds.y.values, ds.attrs["epsg_code"]) != {
                k: v for k, v in case["grid"].items() if k != "north_up"
            } or int(ds.common_correction_input_eligible.values.sum()) != corrected:
                raise ValueError("Kumamoto readiness support differs from the published grid")
    if phase_support != quality["common_phase_support_pixels"]:
        raise ValueError("Common phase support differs from the readiness report")
    count = int(common.sum())
    fields = dict(grid=case["grid"], comparator="Pair coherence and phase relative to the shared reference",
                  los_displacement_cm=None)
    first = marker[0]
    point_text = (f"The first retained pair had marker coherence of {first['coherence']:.3f}."
                  if first["coherence"] is not None else "The first pair had insufficient coherence at the marker.")
    point = dict(**fields, point=case["point"], pixel=[row, col], population="marker pixel",
                 intervals=marker, sentence=point_text + " " + spec["caveat"])
    area = dict(**fields, intervals=intervals, population="common coherence cells across delivered intervals",
                denominator=count, total_cells=int(common.size), shared_phase_cells=phase_support,
                correction_input_eligible_cells=corrected, network_shape="contiguous interval chain",
                closure_test_available=False,
                sentence=f"{count:,} cells had coherence on every pair. Shared phase support was {phase_support:,} cells. "
                         f"{corrected:,} cells met the common correction-input conditions. " + spec["caveat"])
    return point, area

def sources(case, samples):
    if "intervals" in samples:
        return [{"granule_id": s["source_id"], "dates": [s["reference_date"], s["secondary_date"]],
                 "product": "GUNW", "product_version": None, "release_id": s["source_id"].split("_")[-5],
                 "polarizations": ["HH"] if case["series_key"].endswith("_SH") else None}
                for s in samples["intervals"]]
    rows = {}
    for sample in samples["observations"]:
        source = sample["source"]
        title = source["title"] if isinstance(source, dict) else source
        flags = source["flags"] if isinstance(source, dict) else {}
        if not title or not title.startswith("NISAR_"):
            raise ValueError("Sources must identify measured NISAR products")
        if title not in rows:
            rows[title] = {"granule_id": title, "catalog_id": sample.get("granule_id"),
                           "dates": [sample["date"]], "product": "GCOV", "product_version": flags.get("productVersion"),
                           "release_id": flags.get("compositeReleaseId", title.split("_")[-5]), "polarizations": []}
        rows[title]["polarizations"].append(sample["polarization"])
    return list(rows.values())


def bundle_metadata(folder, case, samples, spec, point, area, provenance):
    measured = sources(case, samples)
    track, direction, frame, *_ = case["series_key"].split("_")
    independent = case["evidence"].get("independent_reference")
    evidence_state = (independent or {}).get("status") or (independent or {}).get("label_scope")
    if case["id"] == "bug-fire":
        evidence_state = "independent_perimeter_context"
    return dict(id=case["id"], title=case["title"], point=case["point"], role=spec["role"], lens=spec["lens"],
                teaches=spec["teaches"], status=case["status"], grid=case["grid"],
                bounds_wgs84=list(transform_bounds(case["grid"]["crs"], "EPSG:4326", *case["grid"]["bounds"])),
                coverage={"state": "measured_on_sampled_support", "denominator": area["denominator"],
                          "total_cells": area["total_cells"], "population": area["population"]},
                evidence_state=evidence_state or case["evidence"]["state"], independent_reference=independent,
                dates=sorted({date for row in measured for date in row["dates"]}),
                track=int(track), frame=int(frame), direction={"A": "ascending", "D": "descending"}[direction],
                series_key=case["series_key"], sources=measured, comparators={"point": point["comparator"],
                "area": area["comparator"]}, limitations=case["evidence"]["limitations"],
                null_fields=NULLS, provenance=provenance, credits=CREDITS, files=files(folder))


def replay_job(chips, request, jobs, work, case_id, revision):
    key, body = canonical_job(JobRequest.model_validate(request), revision)
    if [body["longitude"], body["latitude"]] != chips[0][1]["query_point"]:
        raise ValueError(f"Stored chip does not match the snapped cache key: {case_id}")
    job_id = sha256(("event-replay:" + key).encode()).hexdigest()[:32]
    result_dir = jobs / job_id
    offline_job(chips, body, result_dir, work / f"cache-{job_id}", job_id, revision)
    return dict(id=case_id, cache_key=key, job_id=job_id, request=request, canonical_request=body,
                result_path=f"jobs/{job_id}/manifest.json", result_sha256=checksum(result_dir / "manifest.json"),
                revision=revision)


def build(args):
    selection = read(ROOT / "configs/event_bundle_selection.json")
    tour = selection["tour_order"]
    sha = check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    revision = args.revision
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "scratch"):
        raise ValueError("Choose a new output directory under scratch")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        work = Path(temporary)
        published = work / output.name
        published.mkdir()
        jobs = published / "jobs"
        jobs.mkdir()
        cases, catalog, replays = [], [], []
        for case_id, spec in selection["events"].items():
            print(f"Preparing {case_id}", flush=True)
            folder = published / case_id
            folder.mkdir()
            existing = args.prepared / case_id
            reference = read(args.legacy / spec["reference"]) if spec.get("reference") else None
            replay = None
            if spec.get("replay"):
                plan = {**spec["replay"], "lens": spec["lens"]}
                replay_chips, request = select_chips(args.legacy / plan["chips"], plan, False)
                replay = replay_job(replay_chips, request, jobs, work, case_id, revision)
                replays.append(replay)
            if existing.is_dir():
                case, samples = copy_prepared(existing, folder)
                provenance = {"kind": "verified_prepared_assets", "source_manifest_sha256": checksum(existing / "manifest.json"),
                              "source_code_sha": None, "source_build_revision": case["evidence"].get("source_revision")}
            else:
                if case_id not in tour:
                    chips = replay_chips
                    job_dir = published / replay["result_path"]
                    result = read(job_dir)
                    job_dir = job_dir.parent
                else:
                    chips, request = select_chips(args.legacy / spec["chips"], spec, True)
                    job_dir = work / f"worker-{case_id}"
                    offline_job(chips, request, job_dir, work / f"chips-{case_id}", "0" * 32, revision)
                    result = read(job_dir / "manifest.json")
                independent = (reference.get("lens", {}).get("evidence") or reference["case"]) if reference else None
                case, samples = case_from_job(folder, case_id, spec, chips, result, job_dir, independent)
                provenance = {"kind": "offline_worker", "source_code_sha": sha, "source_build_revision": revision,
                              "input_chip_sha256": {m["title"]: m["sha256"] for _, m in chips}}
            if spec["lens"] == "insar":
                point, area = insar_readouts(folder, case, samples, spec, args.choco_source, args.legacy)
            else:
                point, area = backscatter_readouts(folder, case, samples, spec)
            if case_id == "bug-fire":
                area["external_perimeter_comparison"] = reference["lenses"]["wildfire"]["reference_comparison"]
            if case_id == "skollersta":
                area["external_trial_bracket_comparison"] = reference["lenses"]["agriculture"]["crop_contrast"]
            samples.update({"point_readout": point, "area_readout": area})
            write_json(folder / "series.json", samples)
            case["evidence"].update(NULLS)
            case["evidence"]["limitations"] = [*case["evidence"].get("limitations", []), spec["caveat"]]
            case = Case.model_validate(case).model_dump(mode="json")
            write_json(folder / "manifest.json", case)
            assets = publish_files(folder, f"/v2/cases/{case_id}/layers")
            names = {a["name"] for a in assets}
            if any(Path(layer[k]).name not in names for layer in case["layers"] + case["visualizations"]
                   for k in ("png_url", "cog_url")):
                raise ValueError(f"Delivery budget removed a declared layer: {case_id}")
            write_json(folder / "assets.json", assets)
            provenance.update({"assembly_code_sha": sha, "build_revision": revision, "science_version": __version__,
                               "transformer_checkpoint_used": None,
                               "independent_reference_file_sha256": checksum(args.legacy / spec["reference"])
                               if reference else None})
            meta = bundle_metadata(folder, case, samples, spec, point, area, provenance)
            write_json(folder / "bundle.json", meta)
            catalog.append(dict(
                **{k: meta[k] for k in ("id", "title", "point", "role", "lens", "teaches", "grid",
                                        "bounds_wgs84", "status", "coverage", "evidence_state", "dates", "null_fields")},
                manifest_url=f"/v2/cases/{case_id}", series_url=case["series_url"],
                bundle_url=f"/v2/events/{case_id}/bundle.json", bundle_sha256=checksum(folder / "bundle.json"),
                layers=case["layers"], visualizations=case["visualizations"], readouts=["point_readout", "area_readout"],
                series_kind="interferometric_intervals" if "intervals" in samples else "measured_gamma0",
                replay=replay,
            ))
            cases.append(case)
        write_json(published / "index.json", cases)
        write_json(published / "replay.json", replays)
        write_json(published / "events.index.json", {
            "schema_version": "1.0", "science_version": __version__, "science_code_sha": sha, "build_revision": revision,
            "model": {"transformer": "Experimental. Interpolation wins on both new geographic tests.",
                      "dmd": "Experimental. Project evaluation favors simple baselines.",
                      "used_for_event_readouts": False},
            "tour_order": tour, "events": catalog,
        })
        write_json(published / "checksums.json", {"algorithm": "sha256", "files": files(published)})
        published.rename(output)
    print(f"{len(cases)} curated cases and {len(replays)} replay jobs at {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--choco-source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args())
