"""Export the original atlas grids and one display pixel per learned cell."""

import argparse
from hashlib import file_digest
import json
from pathlib import Path

import numpy as np
from PIL import Image
import xarray as xr


ATLAS_CASES = ("forest_river", "deliblato", "chenab", "niscemi", "okavango", "amazon", "murray")
ATLAS_CHECKPOINT_SHA256 = "98b93698837e12961ec64373546597cd39a183bf6de3594e6eccb4cf3f33bef1"


def sha256(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def export_atlas(products, destination):
    cases = json.loads((products / "global_examples.json").read_text(encoding="utf-8"))
    if tuple(case["case_id"] for case in cases) != ATLAS_CASES:
        raise ValueError("Expected the seven original atlas cases in figure order.")

    destination.mkdir(parents=True)
    manifest = []
    for case in cases:
        name = case["case_id"]
        source = products / "runs" / name / "surface_dynamics.nc"
        report = json.loads(source.with_name("result.json").read_text(encoding="utf-8"))
        dataset_hash = sha256(source)
        expected = next((item["sha256"] for item in report["artifacts"] if item["href"] == source.name), None)
        if dataset_hash != expected:
            raise ValueError(f"{name}: NetCDF checksum is missing or differs from its science result.")
        checkpoint = report["capabilities"]["transformer"]["metadata"]["checkpoint_sha256"]
        if checkpoint != ATLAS_CHECKPOINT_SHA256:
            raise ValueError(f"{name}: expected the original v1 atlas checkpoint.")

        grid = report["observations"]["grid"]
        with xr.open_dataset(source) as dataset:
            rgb = dataset["learned_rgb"].transpose("y", "x", "rgb").values
            x, y = dataset.x.values, dataset.y.values
        if rgb.shape != (grid["height"], grid["width"], 3):
            raise ValueError(f"{name}: learned RGB dimensions differ from the reported grid.")
        colors = np.nan_to_num(rgb, nan=0.04)
        if (colors < 0).any() or (colors > 1).any():
            raise ValueError(f"{name}: learned RGB values are outside the expected unit range.")

        stem = name.replace("_", "-")
        png = destination / f"{stem}.png"
        arrays = destination / f"{stem}.npz"
        # Match the published atlas's NaN color and Matplotlib's RGB byte conversion.
        Image.fromarray((colors * 255).astype(np.uint8)).save(png, optimize=True)
        np.savez_compressed(arrays, learned_rgb=rgb, x=x, y=y)
        manifest.append({
            "case_id": name,
            "name": case["name"],
            "date_count": len(report["observations"]["dates"]),
            "grid": grid,
            "finite_rgb_cells": int(np.isfinite(rgb).all(axis=-1).sum()),
            "source": source.relative_to(products).as_posix(),
            "source_sha256": dataset_hash,
            "checkpoint_sha256": checkpoint,
            "png": {"name": png.name, "sha256": sha256(png)},
            "arrays": {"name": arrays.name, "sha256": sha256(arrays)},
        })

    (destination / "manifest.json").write_text(json.dumps({
        "semantics": "Experimental colors from the fixed projection of multi-date radar latent states.",
        "image_encoding": "One pixel per cell in north-up order. RGB values converted to uint8; no resampling.",
        "array_dimensions": ["y", "x", "rgb"],
        "panels": manifest,
    }, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("products", type=Path, help="Original products directory with global_examples.json and runs/.")
    parser.add_argument("destination", type=Path, help="New directory for native PNGs, NPZ arrays and their manifest.")
    args = parser.parse_args()
    export_atlas(args.products, args.destination)


if __name__ == "__main__":
    main()
