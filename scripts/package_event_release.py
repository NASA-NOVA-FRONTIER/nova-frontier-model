"""Package a verified event directory as a GitHub release asset and produce its deployment pin."""
import argparse
from hashlib import file_digest
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile


def sha256(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def package(folder, archive):
    folder, archive = folder.resolve(), archive.resolve()
    pin = archive.with_suffix(".release.json")
    if archive.is_relative_to(folder) or archive.exists() or pin.exists():
        raise ValueError("Choose a new archive outside the event release folder")
    checksums = folder / "checksums.json"
    manifest = json.loads(checksums.read_bytes())["files"]
    declared = {*manifest, "checksums.json"}
    actual = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}
    if actual != declared:
        raise ValueError("The release directory does not match its checksum manifest")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=archive.parent) as temp:
        pending = Path(temp) / archive.name
        with ZipFile(pending, "x", ZIP_DEFLATED, compresslevel=1) as zipped:
            for name in sorted(declared):
                rel = PurePosixPath(name)
                path = folder / rel
                if rel.is_absolute() or ".." in rel.parts or not path.resolve().is_relative_to(folder):
                    raise ValueError(f"Invalid asset path: {name}")
                if name in manifest and sha256(path) != manifest[name]:
                    raise ValueError(f"Unexpected asset SHA-256: {name}")
                zipped.write(path, name)
        config = {"release": folder.name, "asset_url": None, "archive_bytes": pending.stat().st_size,
                  "archive_sha256": sha256(pending), "checksums_sha256": sha256(checksums)}
        pending.rename(archive)
        pin.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Packed {len(manifest)} verified files. Deployment pin: {pin}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_folder", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    package(args.release_folder, args.archive)
