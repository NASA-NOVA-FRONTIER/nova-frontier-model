"""Index case evidence across the science tree and read-only predecessor workspaces."""
import argparse
import csv
import json
from pathlib import Path
from subprocess import check_output

import numpy as np
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform

from prepare_event_bundles import ROOT, read
from surface_dynamics.plan_input import GRANULE, series_key
from surface_dynamics.service_artifacts import native_grid, write_json

ALIASES = {
    "palouse_upland": "palouse", "high_plains_upland": "high-plains", "rowe_creek": "rowe-creek",
    "bug": "bug-fire", "skollersta_rye": "skollersta", "venezuela_june2026": "venezuela",
    "baja_flood_context": "baja-california-sur", "timber_fire_context": "timber-fire",
    "unprepared-point": "colombia-unprepared-point", "kumamoto_wide": "kumamoto",
}


def identity(name):
    return ALIASES.get(name, name.replace("_", "-"))


def inventory(documents, release):
    legacy = documents / "NISAC 3 - Copy"
    older = documents / "NISAC 2"
    entries = {}

    def record(name):
        return entries.setdefault(identity(name), dict(id=identity(name), title=None, point=None, role=None,
                                   workers=[], sources=[], observations=[], measured_grids=[], independent_evidence=[],
                                   source_statuses=[], reference_only=False))

    def source(row, path):
        if path.is_file():
            path = path.relative_to(documents).as_posix()
            if path not in row["sources"]:
                row["sources"].append(path)

    def append_unique(row, key, value):
        if value and value not in row[key]:
            row[key].append(value)

    def config(data, path, role, worker):
        for spec in data:
            name = spec.get("id") or spec.get("case_id")
            if not name:
                continue
            row = record(name)
            row["role"] = row["role"] or role
            row["title"] = row["title"] or spec.get("name") or spec.get("context")
            if row["point"] is None:
                row["point"] = spec.get("point")
                for lon, lat in (("longitude", "latitude"), ("lon", "lat")):
                    if lon in spec and lat in spec:
                        row["point"] = [spec[lon], spec[lat]]
            append_unique(row, "workers", worker)
            source(row, path)

    def observations(row, metadata):
        for meta in metadata:
            title = meta.get("title", meta.get("granule_id"))
            if not isinstance(title, str) or not title.startswith("NISAR_"):
                continue
            match = GRANULE.fullmatch(title)
            flags = meta.get("flags") or {}
            canonical = dict(granule_id=title, dates=meta.get("dates") or ([meta["date"]] if meta.get("date") else []),
                             product="GCOV" if "_GCOV_" in title else "GUNW",
                             series_key=series_key(title) if match else meta.get("series_key"),
                             polarizations=meta.get("pols", meta.get("polarizations")),
                             product_version=flags.get("productVersion", meta.get("product_version")))
            for previous in row["observations"]:
                if previous["granule_id"] == title:
                    for key, item in canonical.items():
                        previous[key] = previous.get(key) or item
                    break
            else:
                row["observations"].append(canonical)

    def inspect_cube(row, path):
        if not path.is_file():
            return
        with np.load(path, allow_pickle=False) as cube:
            if "dates" not in cube:
                return
            source(row, path)
            grid = native_grid(cube["x"], cube["y"], cube["epsg"])
            grid.update(dates=cube["dates"].astype(str).tolist(),
                        polarizations=cube["polarizations"].astype(str).tolist())
            append_unique(row, "measured_grids", grid)
            records = json.loads(str(cube.get("source_metadata_json", "[]")))
            observations(row, records)
            if row["point"] is None and records and records[0].get("query_point"):
                row["point"] = records[0]["query_point"]

    checked_roots = [ROOT, legacy, documents / "NISAC Preview Quota",
                     documents / "NISAC Modal Visualizations", documents / "NISAC Main"]
    config_roles = {
        "global_cases": ("development case", "global explorer"),
        "flood_pilot": ("development case", "flood pilot"),
        "characterization_v05": ("fresh evaluation", "characterization and frozen fingerprint probe"),
        "exploration_holdout_v1": ("held-out test", "exploration lenses V1"),
        "flood_evidence": ("fresh evaluation", "flood evidence"),
        "flood_evidence_followup": ("fresh evaluation", "flood follow-up"),
        "flood_similarity_holdout": ("held-out test", "flood similarity holdout"),
    }
    for root in checked_roots:
        for name, (role, worker) in config_roles.items():
            path = root / "configs" / (name + ".json")
            if path.is_file():
                data = read(path)
                config(data["cases"] if isinstance(data, dict) else data, path, role, worker)
    model = read(ROOT / "configs/global_v2.json")
    model_path = ROOT / "models/global_explorer_v2/MODEL_CARD.md"
    config(model["new_regions"], ROOT / "configs/global_v2.json", None, "global explorer")
    for region_group, role in (("training_regions", "training-region for the Transformer"),
                               ("locked_test_regions", "held-out test"),
                               ("legacy_development_regions", "development case")):
        for name in model[region_group]:
            row = record(name)
            row["role"] = role
            source(row, model_path)
            append_unique(row, "workers", "frozen Transformer reconstruction")
    for path in [legacy / "products/surface_features/cases.json", legacy / "research/worldwide/catalog/summary.json"]:
        data = read(path)
        if isinstance(data, list):
            config(data, path, "development case", "surface features / coverage inventory")
    for config_name in ("cases.json",):
        path = legacy / "scratch/exploration-holdout-v2" / config_name
        config(read(path), path, "held-out test", "exploration lenses V2")
    for name in ["pursat", "baja", "barh", "matam", "unprepared-point"]:
        path = legacy / f"scratch/flood-lens-v1-check/{name}"
        row = record("pursat_lake" if name == "pursat" else "baja_flood_context" if name == "baja" else name)
        row["role"] = row["role"] or "development case"
        for artifact in sorted(path.glob("*.json")):
            source(row, artifact)
        append_unique(row, "workers", "computed flood lens")
    for name in ["skollersta-agriculture", "barh-flood"]:
        path = legacy / f"contracts/examples/observatory/{name}/bundle.json"
        row = record("skollersta" if name.startswith("skollersta") else "barh")
        source(row, path)
        append_unique(row, "workers", "Observatory")
    for root in checked_roots[:3]:
        product = root / "products"
        for pattern in ("cubes/*.npz", "global_v2/cubes/*.npz", "*/cases/*/cube.npz"):
            for path in sorted(product.glob(pattern)):
                row = record(path.parent.name if path.name == "cube.npz" else path.stem)
                inspect_cube(row, path)
                for artifact in (path.parent / "admission.json", path.parent / "reference_pair.json",
                                 path.parent / "disturbance_reference.json", path.parent / "metadata_screen.json"):
                    if artifact.is_file():
                        data = read(artifact)
                        source(row, artifact)
                        append_unique(row, "source_statuses", data.get("status"))
                        append_unique(row, "independent_evidence", data.get("reference_epochs") or data.get("epochs"))
    for path in sorted(legacy.glob("scratch/exploration-holdout-v*/**/chips")):
        row = record(path.parent.name)
        row["role"] = row["role"] or "held-out test"
        source(row, path.parent / "summary.json")
        for sidecar in path.glob("*.json"):
            data = read(sidecar)
            source(row, sidecar)
            observations(row, [data])
            row["point"] = row["point"] or data.get("query_point")
            with np.load(sidecar.with_suffix(".npz"), allow_pickle=False) as chip:
                posting = native_grid(chip["x"], chip["y"], chip["epsg"])
                append_unique(row, "measured_grids", posting)
    for file in ("flood_evidence_a", "flood_evidence_b", "flood_similarity_holdout", "flood_evidence_2026"):
        for path in sorted((legacy / "products" / file / "cases").glob("*/admission.json")):
            d = read(path)
            row = record(path.parent.name)
            source(row, path)
            append_unique(row, "source_statuses", d.get("status"))
            append_unique(row, "independent_evidence", d.get("reference_epochs"))
    venezuela = ROOT / "docs/results/venezuela_insar_v05.json"
    v = read(venezuela)
    selection = v["selection"]
    config([selection], venezuela, "fresh evaluation", "InSAR stack and readiness")
    row = record("venezuela")
    observations(row, [{"title": title, "series_key": selection["series_key"]} for title in selection["source_ids"]])
    append_unique(row, "source_statuses", v["readiness_status"])
    source(row, venezuela)
    from surface_dynamics.network import acquisition_dates

    for obs in row["observations"]:
        obs["dates"] = list(acquisition_dates(obs["granule_id"]))
        obs["polarizations"] = ["HH"]
    for alternate in ("pair_0.json", "pair_1.json", "pair_2.json"):
        source(row, ROOT / "products/characterization_v05/insar_candidate/cache" / alternate)
    other_projects(record, source, append_unique, observations, older, documents)
    adopted = {r["id"]: r for r in read(release / "events.index.json")["events"]}
    for case_id, r in adopted.items():
        data = read(release / case_id / "bundle.json")
        row = record(case_id)
        row.update(point=r["point"], title=r["title"], role=r["role"])
        source(row, release / case_id / "bundle.json")
        append_unique(row, "workers", r["lens"])
        append_unique(row, "measured_grids", r["grid"])
        append_unique(row, "independent_evidence", data["independent_reference"])
        append_unique(row, "source_statuses", r["status"])
        observations(row, data["sources"])
    return entries, adopted

def other_projects(record, source, add, observations, older, documents):
    forest = older / "nisar-forest"
    showcase = forest / "outputs/fire-showcase-v1-final"
    catalog = showcase / "catalog.json"
    for item in read(catalog)["events"]:
        row = record(item["id"])
        row.update(title=item["title"], role="development case")
        source(row, catalog)
        path = showcase / item["manifestPath"].lstrip("/")
        source(row, path)
        add(row, "workers", "NISAC 2 fire showcase")
        add(row, "source_statuses", "measured under legacy contract")
        add(row, "measured_grids", {"crs": "EPSG:4326", "bounds": item["boundsWgs84"]})
        if path.is_file():
            data = read(path)
            affine = Affine(*data["nativeTransform"])
            height, width = data["nativeShape"]
            bounds = list(array_bounds(height, width, affine))
            crs = data["nativeCrs"]
            add(row, "measured_grids", dict(crs=crs, bounds=bounds, shape=[height, width],
                                           posting_m=[abs(affine.a), abs(affine.e)]))
            x, y = affine * (width / 2, height / 2)
            lon, lat = transform(crs, "EPSG:4326", [x], [y])
            row["point"] = [lon[0], lat[0]]
            add(row, "independent_evidence", data["reference"])
            for date in data.get("observations", []):
                observations(row, [{"title": date.get("granuleId") or date.get("granuleName"),
                                    "date": date["datetime"][:10], "polarizations": ["HH", "HV"]}])
    old_models = older / "nisar-models"
    event_doc = old_models / "docs/recent-events-round4.md"
    for name, point, status in [
        ("syapru-besi", None, "measured under legacy contract"),
        ("emsr935-spain", None, "catalog only, no event-bracketing post pass"),
        ("emsr926-latvia-lithuania", None, "independent report only"),
    ]:
        row = record(name)
        row.update(point=point, role="development case")
        source(row, event_doc)
        add(row, "source_statuses", status)
        add(row, "workers", "NISAC 2 bounded event discovery")
    for path in (old_models / "data/round4-events/observed").glob("*/manifest.json"):
        row = record("syapru-besi")
        source(row, path)
        measured = read(path)
        observations(row, [{**r["source"], "date": r["source"]["start"][:10],
                            "product_version": "1.0.11", "polarizations": ["HH", "HV"]}
                           for r in measured["sources"]])
        add(row, "measured_grids", {"crs": "EPSG:32645", "posting_m": [10, 10], "shape": [437, 253]})
        add(row, "measured_grids", {"crs": "EPSG:4326", "bounds": [85.3232, 28.149117, 85.348268, 28.188188]})
    temporal = older / "nisar-temporal-evolution"
    latest = temporal / "releases/demo-release-86235fe1d55afd36a28c6c41"
    for path in sorted(latest.glob("cases/*/manifest.json")):
        row = record(path.parent.name)
        row["role"] = "development case"
        source(row, path)
        add(row, "workers", "NISAC 2 temporal evolution")
        add(row, "source_statuses", "measured under legacy contract")
        data = read(path)
        row["title"] = data["title"]
        add(row, "measured_grids", data["native_grid"])
        if "pair" in data:
            pair = data["pair"]
            for side in ("reference", "secondary"):
                row["observations"].append(dict(product="RSLC", granule_id=pair[side + "_id"],
                                                dates=[pair[side + "_at"][:10]], track=pair["track"],
                                                frame=pair["frame"], direction=pair["pass_direction"],
                                                polarizations=[pair["polarization"]], series_key=None))
    for name in ("omodeo-v1", "powell-v1"):
        row = record(name)
        row["role"] = "development case"
        for path in sorted((temporal / "data/positive-change-development" / name).glob("*.json")):
            source(row, path)
        add(row, "workers", "NISAC 2 positive-change development")
    dmd = documents / "NISAR PROJECT/NISAC_Data_Lab"
    for folder in sorted((dmd / "resultados_dmd").iterdir()):
        row = record("dmd-" + folder.name)
        row["role"] = "development case"
        source(row, folder / "configuracion.json")
        add(row, "source_statuses", "synthetic" if folder.name.startswith("sintetica") else "legacy measured DMD")
        add(row, "workers", "Data Lab DMD")
    for name in ("everglades", "forest_river", "kilauea"):
        row = record(name)
        row["role"] = row["role"] or "development case"
        for path in sorted((documents / "NISAC 3 - Copy/scratch/availability").glob(name + "*_point.json")):
            source(row, path)
        add(row, "workers", "availability and GUNW network")
    sparse = record("sparse-polar-point")
    sparse.update(point=[0, -89.99], role="held-out test")
    source(sparse, documents / "NISAC 3 - Copy/scratch/exploration-api-rehearsal/sparse/result.json")
    add(sparse, "source_statuses", "no catalog coverage")
    for name in ("guaranda", "san-marcos"):
        row = record(name)
        row["role"] = row["role"] or "fresh evaluation"
        path = ROOT.parent / f"verify-la-mojana-{name}.json"
        source(row, path)
        if path.is_file():
            job = (read(path).get("result") or {})
            row["point"] = row["point"] or job.get("point")
            add(row, "source_statuses", job.get("science_status"))
            passes = job.get("science", {}).get("observations", [])
            observations(row, [{"title": s.get("granule_title"), "date": s.get("date"),
                                "pols": list(s.get("point_db", {})),
                                "flags": s.get("quality")} for s in passes])


def disposition(row, selected):
    state = row["source_statuses"]
    if selected:
        return "measured and bundled", "Selected in measured event release"
    if "synthetic" in state:
        return "synthetic", "Synthetic simulation"
    if row["id"] in ("omalos", "sparse-polar-point", "emsr935-spain", "emsr926-latvia-lithuania"):
        return "insufficient support", "Original geometry, coverage or event-bracketing gate failed"
    if row["id"] == "venezuela":
        return "measured", "Redundant InSAR teaching case with blocked displacement readiness"
    if row["role"] == "training-region for the Transformer":
        return "measured", "Training geography retained for provenance and excluded from validation"
    if row["id"] in ("hokkaido", "pantanal", "amazon", "murray"):
        return "measured", "Reconstruction benchmark or development cube, outside the release selection"
    if any("legacy" in s for s in state):
        return "legacy measured artifacts", "Different delivery contract or legacy inference policy requires a separate audit"
    if row["measured_grids"] or row["observations"]:
        return "measured or granule-linked", "Retained as a candidate outside the selected coverage, teaching and exact-replay set"
    return "reference or enrollment only", "Full measured inputs and georeferencing were unconfirmed in the inspected evidence"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, adopted = inventory(args.documents.resolve(), args.release.resolve())
    for key, row in sorted(rows.items()):
        row["role"] = row["role"] or "development case"
        row["data_status"], row["selection_reason"] = disposition(row, key in adopted)
        row["sources"].sort()
        for observation in row["observations"]:
            parts = (observation["series_key"] or "").split("_")
            if len(parts) == 5:
                observation.update(track=int(parts[0]), direction=parts[1], frame=int(parts[2]))
    refs = check_output(["git", "for-each-ref", "--format=%(objectname) %(refname:short)",
                         "refs/heads", "refs/remotes/origin"], cwd=ROOT, text=True).splitlines()
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "event_candidates.json", {"science_refs": refs, "cases": list(rows.values())})
    with (args.output / "event_candidates.csv").open("w", encoding="utf-8", newline="") as stream:
        keys = list(next(iter(rows.values())))
        table = csv.DictWriter(stream, fieldnames=keys)
        table.writeheader()
        for row in sorted(rows.values(), key=lambda r: r["id"]):
            table.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                            for k, v in row.items()})
    print(f"Indexed {len(rows)} candidate records")
