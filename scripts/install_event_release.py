"""Install and verify the immutable event release in the NOVA Modal workspace."""
from hashlib import file_digest, sha256
import json
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

import modal

ROOT = Path(__file__).resolve().parents[1]
STORAGE = Path("/event-cases")
app = modal.App("nova-frontier-event-installer")
volume = modal.Volume.from_name("nova-frontier-event-cases", create_if_missing=True)


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def install(source, destination, manifest_sha):
    with ZipFile(source) as archive:
        manifest_bytes = archive.read("checksums.json")
        if sha256(manifest_bytes).hexdigest() != manifest_sha:
            raise ValueError("Unexpected checksum manifest")
        files = json.loads(manifest_bytes)["files"]
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != {*files, "checksums.json"}:
            raise ValueError("Archive members differ from the checksum manifest")
        for name in sorted(files):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("Unexpected archive path")
            extracted = Path(archive.extract(name, destination))
            if digest(extracted) != files[name]:
                raise ValueError(f"Unexpected SHA-256 for {name}")
        (destination / "checksums.json").write_bytes(manifest_bytes)


@app.function(volumes={str(STORAGE): volume}, timeout=600)
def provision(config):
    release = config["release"]
    if not re.fullmatch(r"cases-event-catalog-[a-z0-9-]{1,43}", release):
        raise ValueError("Invalid event release name")
    destination = STORAGE / release
    expected = config["checksums_sha256"]
    if destination.is_dir():
        if digest(destination / "checksums.json") != expected:
            raise ValueError("Installed release name refers to different bytes")
        print(f"{release} is already installed")
        return
    with TemporaryDirectory() as local:
        archive = Path(local) / "event-release.zip"
        with urlopen(config["asset_url"], timeout=90) as response, archive.open("wb") as stream:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > config["archive_bytes"]:
                    raise ValueError("Event release exceeds pinned archive size")
                stream.write(block)
        if total != config["archive_bytes"] or digest(archive) != config["archive_sha256"]:
            raise ValueError("Event release archive size or SHA-256 mismatch")
        with TemporaryDirectory(dir=STORAGE) as work:
            pending = Path(work) / release
            pending.mkdir()
            install(archive, pending, expected)
            pending.rename(destination)
    volume.commit()
    print(f"Verified and installed {release}")


@app.local_entrypoint()
def main():
    provision.remote(json.loads((ROOT / "data/event_release.json").read_bytes()))
