"""Extend a verified event release from retained, measured local artifacts."""
import argparse
import json
from pathlib import Path
from shutil import copy2, copytree
from subprocess import check_output
from tempfile import TemporaryDirectory

import jsonschema
import numpy as np
import rasterio
from rasterio.warp import transform
import xarray as xr

from prepare_api_cases import series as measured_series
from prepare_event_bundles import (ROOT, NULLS, add_layer, backscatter_readouts, bundle_metadata,
                                   files, marker_pixel, raster_grid, read, stats, value)
from surface_dynamics.analysis import measured_db, supported_reduction
from surface_dynamics.engine import analyze
from surface_dynamics.exploration_lenses import agriculture_history, evaluate_lens
from surface_dynamics.fingerprint_worker import process_fingerprint
from surface_dynamics.flood import GROUP_LEGEND
from surface_dynamics.io import cube_from_chips, load_cube
from surface_dynamics.network import acquisition_dates
from surface_dynamics.plan_input import series_key
from surface_dynamics.service_artifacts import checksum, native_grid, publish_files, render_layer, write_json
from surface_dynamics.service_contracts import Case
from surface_dynamics.service_workers import analysis_dashboard
from surface_dynamics.service_visualizations import Frame, render_gcov_visualizations

RADAR_NOTE = "The map shows gamma-zero change on measured support. Moisture, geometry and surface structure can affect the return."
INSAR_NOTE = "Phase is uncorrected. Reference stability and deformation are unverified."


def checked(path, expected):
    if checksum(path) != expected:
        raise ValueError(f"Source checksum mismatch: {path}")
    return path


def coords(cube):
    return cube["x"], cube["y"], cube["epsg"]


def center(grid):
    left, _, _, top = grid["bounds"]
    rows, cols = grid["shape"]
    dx, dy = grid["posting_m"]
    lon, lat = transform(grid["crs"], "EPSG:4326", [left + (cols // 2 + .5) * dx],
                         [top - (rows // 2 + .5) * dy])
    return [lon[0], lat[0]]


def normalized_sources(cube):
    sources = json.loads(str(cube["source_metadata_json"]))
    for row in sources:
        row["title"] = row.get("title") or row["granule"]
        row.setdefault("flags", {})
    if [row["date"] for row in sources] != cube["dates"].astype(str).tolist():
        raise ValueError("Measured dates differ from source metadata")
    if len({series_key(row["title"]) for row in sources}) != 1:
        raise ValueError("A curated cube must use a compatible series")
    return sources


def case_info(case_id, title, point, grid, key, before, after, state, reference):
    return Case(id=case_id, title=title, point=point, grid=grid, series_key=key, baseline_dates=before,
                assessment_dates=after, status=state, evidence={"state": state, "independent_reference": reference},
                layers=[], series_url=f"/v2/cases/{case_id}/series").model_dump(mode="json")


def render_gcov(folder, case_id, title, lens_id, cube, point, cutoff, reference, optical=None):
    dates = cube["dates"].astype(str).tolist()
    sources = normalized_sources(cube)
    grid = native_grid(*coords(cube))
    point = point or center(grid)
    cutoff = cutoff or dates[(len(dates) - 1) // 2]
    before, after = [d for d in dates if d <= cutoff], [d for d in dates if d > cutoff]
    target = after[-1]
    case = case_info(case_id, title, point, grid, series_key(sources[0]["title"]), before, after,
                     "measured", reference)
    pols = cube["polarizations"].astype(str).tolist()
    db = measured_db(cube)
    common = np.isfinite(db).all(axis=(0, 1))
    if not common.any():
        raise ValueError(f"No common observed support: {case_id}")
    frames = []
    for day, bands in zip(dates, db, strict=True):
        frame = dict(zip(pols, bands, strict=True))
        frames.append(Frame(day, frame, *coords(cube)))
        for pol, values in frame.items():
            add_layer(folder, case, f"display_{day}_{pol}", values, coords(cube), "dB", "gray", (-30, 0))
    baseline = supported_reduction(db[:len(before)], np.nanmedian)
    for i, pol in enumerate(pols):
        add_layer(folder, case, f"display_baseline_change_{pol}", db[-1, i] - baseline[i], coords(cube),
                  "dB", "RdBu_r", (-8, 8))
    case["visualizations"] = render_gcov_visualizations(folder, f"/v2/cases/{case_id}/layers",
                                                        frames, target, [before[-1], target])
    group_codes = np.full(common.shape, 255)
    evidence = case["evidence"]
    if lens_id != "observed_dynamics":
        report, arrays = evaluate_lens(cube, target, lens_id, baseline_end=cutoff, reference=optical, point=point)
        evidence["lens_report"] = report
        if report["status"] == "evaluated":
            case["status"] = report["status"]
            spatial = report["radar"]
            evidence["radar_support_cells"] = spatial["common_cells"]
            if lens_id == "agriculture":
                report["agriculture"] = agriculture_history(cube, report, common, *point)
            if "flood_signature_flags" in arrays:
                for index, label in enumerate(("dual_darkening", "co_pol_brightening")):
                    add_layer(folder, case, f"display_flood_signature_flags_{label}",
                              arrays["flood_signature_flags"][index], coords(cube), "diagnostic mask", "Blues", (0, 1))
            if "optical_groups" in arrays:
                group_codes = arrays["optical_groups"]
                prefix = f"/v2/cases/{case_id}/layers"
                case["layers"].append(render_layer(folder, prefix, "reference_groups", np.where(
                    group_codes == 255, np.nan, group_codes), *coords(cube), units="optical group code",
                    cmap="viridis", stretch=(0, 4), legend=GROUP_LEGEND))
            if "cross_pol_pattern" in arrays:
                add_layer(folder, case, "display_cross_pol_pattern_cross_pol_pattern", arrays["cross_pol_pattern"],
                          coords(cube), "pattern code", "viridis", (0, 3))
    if optical is not None and reference.get("status") != "admitted":
        case["status"] = "partial"
    if report_status := (evidence.get("lens_report") or {}).get("status"):
        evidence["state"] = report_status
    series = measured_series(cube, grid, point, {"baseline_dates": before, "assessment_dates": after, "sources": sources},
                             group_codes, {"co_polarization": pols[0], "cross_polarization": pols[1]})
    series["target_date"] = target
    return case, series

def flood_inputs(legacy, cohort):
    for path in sorted((legacy / "products" / cohort / "cases").glob("*/admission.json")):
        admission = read(path)
        if "cube.npz" not in admission.get("input_sha256", {}):
            continue
        cube_path = checked(path.with_name("cube.npz"), admission["input_sha256"]["cube.npz"])
        optical_path = checked(path.with_name("reference.npz"), admission["input_sha256"]["reference.npz"])
        cube = load_cube(cube_path)
        with np.load(optical_path, allow_pickle=False) as data:
            optical = {key: data[key] for key in data.files}
        report_path = path.parent / "report/result.json"
        original = read(report_path) if report_path.is_file() else {}
        original_obs = original.get("observations", {})
        dates = cube["dates"].astype(str).tolist()
        cutoff = (original_obs.get("baseline_dates") or [dates[-2]])[-1]
        if original_obs and original_obs["target_date"] != dates[-1]:
            raise ValueError("Flood report target differs from the delivered cube")
        reference = {key: admission.get(key) for key in (
            "status", "joint_reference_cells", "reference_group_cells", "reference_epochs")}
        if original:
            reference["original_comparison"] = {key: original.get(key) for key in (
                "optical_dates", "event_source", "comparison", "matched_contrasts")}
        case_id = path.parent.name.replace("_", "-")
        if case_id == "matam":
            case_id = "matam-optical"
        role = "held-out test" if cohort == "flood_similarity_holdout" else "fresh evaluation"
        teaches = "Dated optical comparisons have their own admission gate and population."
        if admission["status"] != "admitted":
            teaches = "Radar measurements are available. The original optical comparison did not pass admission."
        spec = {"role": role, "lens": "flood_wetland", "teaches": teaches, "caveat": RADAR_NOTE}
        evidence = {"input_sha256": admission["input_sha256"], "admission_sha256": checksum(path)}
        yield case_id, original.get("place") or path.parent.name.replace("_", " ").title(), spec, cube, None, cutoff, reference, optical, evidence


def holdout_input(legacy, entry):
    case_id = entry["id"]
    lens = entry["lens"]
    path = legacy / entry["path"]
    summary = read(path / "summary.json")
    paths = sorted(path.glob("chips/*.npz"))
    cube = cube_from_chips(paths, aggregate=1)
    if cube["dates"].astype(str).tolist() != summary["selected_dates"]:
        raise ValueError(f"Holdout chips differ from the frozen date plan: {case_id}")
    info = summary["case"]
    reference = {k: info[k] for k in ("source", "event_time", "reference_time", "source_sensor", "label_scope", "context_source")
                 if k in info}
    reference["status"] = "retained_holdout_comparison"
    for key in ("crop_contrast", "reference_comparison"):
        if key in summary["lenses"][lens]:
            reference[key] = summary["lenses"][lens][key]
    saved_path = path / lens / "result.json"
    if saved_path.is_file():
        archived = read(saved_path)["lens"].get("evidence") or {}
        if archived.get("status") == "optical_comparison_available":
            reference["optical"] = {key: val for key, val in archived.items() if key != "layer"}
    prov = {"holdout_report_sha256": checksum(path / "summary.json"),
            "chip_sha256": {chip.name: checksum(chip) for chip in paths}}
    spec = {"role": "held-out test", "lens": lens, "teaches": entry["teaches"], "caveat": RADAR_NOTE}
    return case_id, entry["title"], spec, cube, info["point"], entry["baseline_end_date"], reference, None, prov


def raster(generated, band=None):
    with rasterio.open(generated) as dataset:
        values = dataset.read(band, masked=True) if band else dataset.read(masked=True)
        return values.astype("float32").filled(np.nan), raster_grid(dataset)


def raster_cube(rows):
    dates, power, all_sources = [], [], []
    previous = None
    for meta, bands, grid in sorted(rows, key=lambda row: row[0]["date"]):
        if previous is not None and native_grid(*grid) != native_grid(*previous):
            raise ValueError("Stored radar observations do not share a native grid")
        dates.append(meta["date"])
        power.append(bands)
        all_sources.append(meta)
        previous = grid
    x, y, epsg = previous
    power = np.stack(power)
    return dict(power=power, valid=np.isfinite(power) & (power > 0), dates=np.array(dates),
                polarizations=np.array(["HH", "HV"]), x=x, y=y, epsg=np.array(epsg),
                radar_band=np.array("L"), source_metadata_json=np.array(json.dumps(all_sources)))


def land_inputs(older, entries):
    root = older / "nisar-land-change/data/temporal-training-v1"
    splits = read(older / "nisar-land-change/manifests/temporal-generalization-v3.json")["cases"]
    splits = {row["id"]: row for row in splits}
    for entry in entries:
        case_path = root / entry["path"]
        prior = splits[entry["path"]]
        rows, source_hashes = [], {}
        for path in sorted(case_path.rglob("observation.json")):
            meta = read(path)
            if meta["data_origin"] != "observed" or meta["units"] != "gamma0_power" or meta["radar_band"] != "L":
                raise ValueError("Legacy land-change input is outside measured L-band GCOV")
            supplied = meta["assets"]
            hh, hv, mask = (checked(path.with_name(Path(supplied[k]["reference"]).name), supplied[k]["sha256"])
                            for k in ("HHHH", "HVHV", "mask"))
            hi, grid = raster(hh, 1)
            vi, vg = raster(hv, 1)
            valid, mg = raster(mask, 1)
            if native_grid(*grid) != native_grid(*vg) or native_grid(*grid) != native_grid(*mg):
                raise ValueError("Polarizations and support mask are misaligned")
            db_power = np.where(np.isin(valid, meta["mask_valid_values"]), np.stack([hi, vi]), np.nan)
            source = dict(date=meta["acquired_at"][:10], title=meta["granule_id"],
                          flags={"productVersion": meta["processing_version"], "compositeReleaseId": meta["crid"]})
            rows.append((source, db_power, grid))
            source_hashes[source["date"]] = {k: supplied[k]["sha256"] for k in ("HHHH", "HVHV", "mask")}
        cube = raster_cube(rows)
        spec = {"lens": "vegetation_disturbance", "role": entry["role"],
                "teaches": "Measured vegetation-disturbance context from legacy land-change experiments.",
                "caveat": RADAR_NOTE + " Permanent forest conversion and its cause remain unverified."}
        reference = {"status": "weak_legacy_reference", "historical_experiment_split": prior["split"],
                     "reported_onset_days": prior["source_onset_days"],
                     "scope": "The curated result uses radar-only diagnostics. Legacy class scores are excluded."}
        yield (entry["id"], entry["title"], spec, cube, prior["centre"], entry["baseline_end_date"],
               reference, None, {"input_sha256": source_hashes})


def fire_inputs(older):
    root = older / "nisar-forest/outputs/fire-showcase-v1-final"
    for entry in read(root / "catalog.json")["events"]:
        manifest_path = checked(root / entry["manifestPath"], entry["sha256"])
        manifest = read(manifest_path)
        assets = {asset["path"]: asset["sha256"] for asset in manifest["assetInventory"]}
        rows = []
        for item in manifest["observations"]:
            if item["isDemo"]:
                raise ValueError("A synthesized observation cannot enter the gallery")
            relative = item["rawPowerPath"]
            power, grid = raster(checked(root / relative, assets[relative]))
            rows.append(({"date": item["datetime"][:10], "title": item["granuleId"]}, power, grid))
        cube = raster_cube(rows)
        pre_dates = [item["datetime"][:10] for item in manifest["observations"] if item["phase"] == "pre"]
        reference = {key: manifest["reference"][key] for key in (
            "sourceUrl", "sourceImageryTimesAsPublished", "sourceTimeZone", "perimeterTemporalType")}
        reference.update(status="independent_mapped_fire_context", event_date=manifest["eventTimeAsPublished"])
        spec = {"lens": "wildfire", "role": "development case",
                "teaches": "Dated wildfire context and the measured co-/cross-pol response across the available dates.",
                "caveat": RADAR_NOTE + " The external perimeter can include unburned cells."}
        prov = {"original_manifest_sha256": entry["sha256"],
                "input_sha256": {item["rawPowerPath"]: assets[item["rawPowerPath"]] for item in manifest["observations"]}}
        yield f"fire-{entry['id'].lower()}", entry["title"], spec, cube, None, max(pre_dates), reference, None, prov

def insar_inputs(legacy, older):
    path = legacy / "scratch/gunw"
    m = read(path / "sources.json")
    source = checked(path / "kilauea_gunw_20261010_20261022.npz", m["sha256"])
    with np.load(source, allow_pickle=False) as a:
        grid = (a["x_20m"], a["y_20m"], a["epsg"])
        yield ("kilauea-insar", "Kīlauea volcano vicinity, Hawaiʻi", [-155.29, 19.41], "151_A_011_4000_SH",
               grid, [(m["title"], a["coherenceMagnitude_20m"], np.angle(a["wrappedInterferogram_20m"]))],
               {"input_sha256": m["sha256"]}, {"status": "single_measured_pair", "phase": "Native wrapped interferogram"},
               "Native 20 m wrapped phase and coherence from one pair. Independent corrections and attribution remain unavailable.")

    path = older / "nisar-temporal-evolution/research/insar-pilot-2026-11-03-v1"
    m = read(path / "insar-pilot-reviewed.json")
    pair = read(path / "pair-selection.json")["selected"]
    data = older / "nisar-temporal-evolution/data/insar-pilot-2026-11-03-v1/scientific-cogs"
    sha_coh = m["scientific_assets"]["coherenceMagnitude"]["sha256"]
    sha_phase = m["scientific_assets"]["unwrappedPhase"]["sha256"]
    coherence, grid = raster(checked(data / "coherenceMagnitude.tif", sha_coh), 1)
    phase, grid_phase = raster(checked(data / "unwrappedPhase.tif", sha_phase), 1)
    if native_grid(*grid) != native_grid(*grid_phase):
        raise ValueError("Mexico City phase and coherence grids differ")
    yield ("mexico-city-insar", "Mexico City subsidence-context pair", None, "113_D_079_4000_SH", grid,
           [(pair["granule_id"], coherence, (phase + np.pi) % (2 * np.pi) - np.pi)],
           {"input_sha256": [sha_coh, sha_phase], "product_version": m["product_version"]},
           {"status": "single_measured_pair", "phase": "Native unwrapped phase reduced modulo 2π",
            "independently_stable_reference": False},
           "A single measured pair illustrates phase in an urban subsidence context. Corrected motion and a vertical rate remain unavailable.")

    path = legacy / "products/characterization_v05/insar_candidate"
    report = read(path / "intervals/result.json")
    readiness = read(path / "readiness/result.json")
    source = path / "intervals/interferometric_interval_dynamics.nc"
    artifact = next(a for a in report["artifacts"] if a["href"] == source.name)
    checked(source, artifact["sha256"])
    with xr.open_dataset(source, engine="h5netcdf") as a:
        grid = (a.x.values, a.y.values, a.attrs["epsg_code"])
        phase = (a.phase_relative_to_shared_pixel_rad.values + np.pi) % (2 * np.pi) - np.pi
        pairs = [(m["source_id"], a.coherence.values[i], phase[i]) for i, m in enumerate(report["observations"])]
        evidence = {"status": readiness["status"], "phase": "Wrapped phase relative to the shared reference pixel",
                    "quality": readiness["quality"], "required_gates": readiness["required_gates"]}
        yield ("venezuela-insar", "June 2026 Venezuela earthquake context", [-68.32, 10.4],
               a.attrs["series_key"], grid, pairs, {"input_sha256": artifact["sha256"]}, evidence,
               "Three measured intervals bracket the June 24 earthquake context. Only two common cells met correction-input eligibility.")


def render_insar(folder, entry):
    case_id, title, point, key, grid_coords, pairs, provenance, support, teaches = entry
    grid = native_grid(*grid_coords)
    point = point or center(grid)
    start, end = zip(*(acquisition_dates(identifier) for identifier, *_ in pairs), strict=True)
    if any(not 0 <= coherence[np.isfinite(coherence)].min() <= coherence[np.isfinite(coherence)].max() <= 1
           for _, coherence, _ in pairs):
        raise ValueError("Coherence values outside physical range")
    case = case_info(case_id, title, point, grid, key, list(start[:1]), list(end),
                     support["status"], None)
    case["evidence"]["phase_comparison"] = support
    coh_support = np.isfinite(np.stack([coh for _, coh, _ in pairs])).all(axis=0)
    phase_support = np.isfinite(np.stack([phase for *_, phase in pairs])).all(axis=0)
    if "quality" in support:
        if int(phase_support.sum()) != support["quality"]["common_phase_support_pixels"]:
            raise ValueError("Retained shared phase support differs from the phase array")
    records, spatial, markers = [], [], []
    for i, (identifier, coherence, phase) in enumerate(pairs, 1):
        dates = [start[i - 1], end[i - 1]]
        add_layer(folder, case, f"coherence_pair_{i}", coherence, grid_coords, "unitless", "cividis", (0, 1))
        add_layer(folder, case, f"wrapped_phase_pair_{i}", phase, grid_coords, "rad", "twilight", (-np.pi, np.pi))
        with rasterio.open(folder / f"coherence_pair_{i}.tif") as band:
            pixel = marker_pixel(point, band)
        marker = value(coherence[pixel])
        markers.append({"dates": dates, "coherence": marker, "wrapped_phase_rad": value(phase[pixel])})
        spatial.append({"dates": dates, "coherence": stats(coherence), "common_coherence": stats(coherence[coh_support])})
        records.append({"source_id": identifier, "reference_date": dates[0], "secondary_date": dates[1],
                        "median_coherence": spatial[-1]["coherence"]["median"]})
    n = int(coh_support.sum())
    comparable = dict(grid=case["grid"], los_displacement_cm=None, comparator="Coherence and uncorrected phase by pair")
    first = markers[0]["coherence"]
    sentence = f"The first pair has marker coherence of {first:.3f}." if first is not None else "Marker coherence is masked."
    marker = dict(**comparable, population="marker pixel", point=point, pixel=list(pixel), intervals=markers,
                  sentence=f"{sentence} {INSAR_NOTE}")
    support_cells = int(phase_support.sum())
    area = dict(**comparable, population="cells with finite coherence on every listed pair", denominator=n,
                total_cells=coh_support.size, intervals=spatial, shared_phase_cells=support_cells if len(pairs) > 1 else None,
                correction_input_eligible_cells=support.get("quality", {}).get("common_correction_input_pixels"),
                sentence=f"Coherence is available across {n:,} common cells. {INSAR_NOTE}")
    series = dict(api_version="2.0", intervals=records, point_readout=marker, area_readout=area)
    spec = dict(lens="insar", role="fresh evaluation" if case_id == "venezuela-insar" else "development case",
                teaches=teaches, caveat=INSAR_NOTE)
    return case, series, spec, provenance


def model_features(case_id, cube, series, case, folder, work, choices):
    source = work / f"{case_id}.npz"
    np.savez_compressed(source, **cube)
    out = work / f"features-{case_id}"
    request = {"contract_version": "1.0", "request_id": case_id,
               "source": {"mode": "local_cube", "cube_path": str(source)}}
    if case_id in choices["analysis"]:
        checkpoint = ROOT / "models/global_explorer_v2/masked_temporal_transformer.pt"
        report = analyze({**request, "analyses": ["physical", "spectral", "stochastic", "transformer"],
                          "transformer_checkpoint": str(checkpoint)}, out)
        dashboard = analysis_dashboard(out, cube["dates"].astype(str).tolist())
        benchmark = read(ROOT / "models/global_explorer_v2/evaluation_report.json")
        published = {"capabilities": report["capabilities"], "dashboard": dashboard,
                     "limitations": report["limitations"], "checkpoint_sha256": checksum(checkpoint),
                     "benchmark_report_sha256": checksum(ROOT / "models/global_explorer_v2/evaluation_report.json")}
        # Use the measured history and the frozen comparison published with the checkpoint.
        published["frozen_comparison"] = next(
            v for v in benchmark["learned_and_baseline_results"]["v2"]["cases"] if v["case_id"] == case_id)
    else:
        dates = cube["dates"].astype(str).tolist()
        cutoff = "2026-08-31"
        report = process_fingerprint({**request, "baseline": {"start_date": dates[0], "end_date": cutoff},
                                      "assessment": {"start_date": "2026-09-01", "end_date": dates[-1]},
                                      "maximum_points": 32}, out)
        published = {k: report[k] for k in ("status", "features", "observations", "points", "quality", "limitations")}
    retained = []
    for artifact in report["artifacts"]:
        name = Path(artifact["href"]).name
        if Path(name).suffix in {".png", ".gif", ".csv", ".tif"}:
            copy2(checked(out / name, artifact["sha256"]), folder / name)
            retained.append({**artifact, "href": f"/v2/cases/{case_id}/layers/{name}"})
    published["artifacts"] = retained
    series["model_features"] = published
    case["evidence"]["model_features_available_in_series"] = True

def historic_features(case_id, legacy, folder, samples, case):
    if case_id == "forest-river":
        dmd = legacy / "research/forest_temporal_metrics.json"
        metrics = read(dmd)
        fields = ("date", "pooled_dmd_median_rmse_db", "pooled_persistence_rmse_db")
        samples["model_features"] = {"dmd": {
            "status": "historical_experiment",
            "scope": "Forest River DMD experiment on its own separately supported crop. Persistence had lower RMSE.",
            "benchmark": [{field: row[field] for field in fields} for row in metrics["heldout"]],
            "source_sha256": checksum(dmd)}}
    if case_id == "pantanal":
        path = legacy / "products/representation_umap"
        summary, qa = read(path / "summary.json"), read(path / "validation.json")
        samples["model_features"]["umap"] = {
            "status": "historical_experiment",
            "case_count": summary["case_count"], "scope": summary["scope"], "validation_scope": qa["scope"],
            "source_sha256": checksum(path / "summary.json")}
    if case_id == "pursat-west":
        path = legacy / "products/flood_similarity/pursat_west"
        report = read(path / "result.json")
        if report["radar_dates"] != sorted({row["date"] for row in samples["observations"]}):
            raise ValueError("Pursat similarity report belongs to a different series")
        source = checked(path / "similarity.tif", report["artifacts"]["similarity.tif"])
        image, grid = raster(source, 1)
        # The saved map uses the history recipe; its values describe relative similarity.
        add_layer(folder, case, "experimental_flood_similarity", image, grid, "similarity score", "viridis", (0, 100))
        samples["model_features"] = {"analogs": {
            "status": "experimental", "scope": "Relative resemblance to the frozen optical-group library.",
            "optical_dates": report["optical_dates"], "metrics": report["metrics"],
            "source_sha256": checksum(path / "result.json")}}
    if "model_features" in samples:
        case["evidence"]["model_features_available_in_series"] = True


def publish(folder, case, samples, spec, provenance):
    case_id = case["id"]
    prefix = f"/v2/cases/{case_id}"
    case["evidence"].update(NULLS, limitations=[spec["caveat"]], lens_id=spec["lens"])
    if "intervals" not in samples:
        samples["point_readout"], samples["area_readout"] = backscatter_readouts(folder, case, samples, spec)
    case = Case.model_validate(case).model_dump(mode="json")
    assets = publish_files(folder, prefix + "/layers")
    urls = {asset["href"] for asset in assets}
    declared = [layer[key] for layer in case["layers"] + case["visualizations"] for key in ("png_url", "cog_url")]
    declared.extend(item["href"] for item in samples.get("model_features", {}).get("artifacts", []))
    if not set(declared) <= urls:
        raise ValueError(f"Case exceeds the published asset budget: {case_id}")
    for name, content in (("manifest.json", case), ("series.json", samples), ("assets.json", assets)):
        write_json(folder / name, content)
    meta = bundle_metadata(folder, case, samples, spec, samples["point_readout"], samples["area_readout"], provenance)
    write_json(folder / "bundle.json", meta)
    index = {k: meta[k] for k in ("id", "title", "point", "role", "lens", "teaches", "grid",
                                  "bounds_wgs84", "status", "coverage", "evidence_state", "dates", "null_fields")}
    index.update(manifest_url=prefix, series_url=case["series_url"], bundle_url=f"/v2/events/{case_id}/bundle.json",
                 bundle_sha256=checksum(folder / "bundle.json"), layers=case["layers"], visualizations=case["visualizations"],
                 readouts=["point_readout", "area_readout"], replay=None,
                 series_kind="interferometric_intervals" if "intervals" in samples else "measured_gamma0")
    return case, index


def inputs(legacy, older, config):
    for entry in config["cubes"]:
        path = legacy / entry["cube"]
        spec = {key: entry[key] for key in ("lens", "role", "teaches")}
        spec["caveat"] = entry["teaches"]
        yield (entry["id"], entry["title"], spec, load_cube(path), entry["point"], entry["baseline_end_date"],
               entry.get("reference"), None, {"input_sha256": checksum(path)})
    for row in config["holdouts"]:
        yield holdout_input(legacy, row)
    for cohort in config["flood_cohorts"]:
        yield from flood_inputs(legacy, cohort)
    yield from land_inputs(older, config["land_change"])
    yield from fire_inputs(older)


def validate_coverage(index, config):
    by_id = {row["id"]: row for row in index["events"]}
    known = set(by_id)
    if len(known) != len(index["events"]):
        raise ValueError("Duplicate event case IDs")
    library = config["coverage"]
    for group in library.values():
        if not set(group["case_ids"]) <= known:
            raise ValueError("A phenomenon links to an absent case")
    tour = config["tour_order"]
    five = ["earthquakes", "wildfires", "deforestation", "floods", "landslides"]
    if len(tour) != len(set(tour)) or len(tour) != len(five) or any(
            case_id not in library[theme]["case_ids"] for theme, case_id in zip(five, tour, strict=True)):
        raise ValueError("Tour must contain a case for each of the five featured phenomena")


def build(base, documents, output):
    recipe = ROOT / "configs/phenomena_examples.json"
    config = read(recipe)
    base_checksums = config["base_checksums_sha256"]
    checked(base / "checksums.json", base_checksums)
    expected = read(base / "checksums.json")["files"]
    if {k: v for k, v in files(base).items() if k != "checksums.json"} != expected:
        raise ValueError("Base release failed integrity verification")
    extension = {
        "source_code_sha": check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "builder_sha256": checksum(Path(__file__)), "recipe_sha256": checksum(recipe),
        "base_checksums_sha256": base_checksums,
    }
    legacy = documents / "NISAC 3 - Copy"
    older = documents / "NISAC 2"
    if output.exists() or not output.is_relative_to(ROOT / "scratch"):
        raise ValueError("Choose a new output directory in Git-ignored scratch")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        work = Path(temporary)
        out = copytree(base, work / output.name)
        cases = read(out / "index.json")
        index = read(out / "events.index.json")
        known = {case["id"] for case in cases}

        def save(folder, case, samples, spec, source):
            manifest, row = publish(folder, case, samples, spec, {**source, **extension})
            cases.append(manifest)
            index["events"].append(row)

        for case_id, title, spec, cube, point, cutoff, reference, optical, source in inputs(legacy, older, config):
            print(f"Building {case_id}", flush=True)
            if case_id in known:
                raise ValueError(f"Case already exists: {case_id}")
            folder = out / case_id
            folder.mkdir()
            case, samples = render_gcov(folder, case_id, title, spec["lens"], cube, point, cutoff, reference, optical)
            choices = config["feature_cases"]
            if case_id in choices["analysis"] or case_id == choices["fingerprint"]:
                model_features(case_id, cube, samples, case, folder, work, choices)
            historic_features(case_id, legacy, folder, samples, case)
            save(folder, case, samples, spec, source)
            if optical is not None and case_id not in config["coverage"]["floods"]["case_ids"]:
                config["coverage"]["floods"]["case_ids"].append(case_id)

        for entry in insar_inputs(legacy, older):
            print(f"Building {entry[0]}", flush=True)
            folder = out / entry[0]
            folder.mkdir()
            case, samples, spec, source = render_insar(folder, entry)
            save(folder, case, samples, spec, source)

        index.update(tour_order=config["tour_order"], phenomena=config["coverage"],
                     feature_examples=config["feature_cases"], extension=extension)
        validate_coverage(index, config)
        schema = read(ROOT / "docs/schemas/events.index.schema.json")
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(index)
        write_json(out / "index.json", cases)
        write_json(out / "events.index.json", index)
        checksums = files(out)
        del checksums["checksums.json"]
        write_json(out / "checksums.json", {"algorithm": "sha256", "files": checksums})
        out.rename(output)
    print(f"Built {len(cases)} cases with {len(config['tour_order'])} tour stops at {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path, help="Unpacked, verified previous event release")
    parser.add_argument("--documents", required=True, type=Path, help="Parent of the supplied local project folders")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.base.resolve(), args.documents.resolve(), args.output.resolve())
