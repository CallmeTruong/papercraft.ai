"""Explicit model preparation. Generation never downloads weights or executes remote code."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import urlopen

MODELS = {
    "segmentation": "facebook/sam2.1-hiera-tiny",
    "depth": "depth-anything/Depth-Anything-V2-Small-hf",
    "face_features": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}


def root():
    return Path(os.environ.get("PAPERCRAFT_MODEL_DIR", ".papercraft/models")).resolve()


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def manifest(kind, verify=False):
    directory = root() / kind
    data = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
    if data["model_id"] != MODELS[kind] or not data.get("files"):
        raise ValueError("Model manifest identity is invalid")
    for name, expected in data["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError("Model file missing or outside model directory")
        if verify and file_hash(path) != expected:
            raise ValueError("Model checksum mismatch")
    return directory, data


def availability():
    result = {}
    for kind in MODELS:
        try:
            _, data = manifest(kind)
            result[kind] = {"status": "prepared", "revision": data["revision"]}
        except (OSError, ValueError, KeyError):
            result[kind] = {"status": "unavailable"}
    return result


def prepare(kinds):
    for kind in kinds:
        directory = root() / kind
        directory.mkdir(parents=True, exist_ok=True)
        if kind == "face_features":
            target = directory / "face_landmarker.task"
            with urlopen(MODELS[kind], timeout=120) as response:
                target.write_bytes(response.read(30_000_000))
            revision = "float16/1"
        else:
            from huggingface_hub import HfApi, snapshot_download

            revision = HfApi().model_info(MODELS[kind]).sha
            snapshot_download(
                MODELS[kind], revision=revision, local_dir=directory,
                allow_patterns=["*.json", "*.safetensors", "README.md", "LICENSE*"],
            )
        files = {
            str(path.relative_to(directory)).replace("\\", "/"): file_hash(path)
            for path in directory.rglob("*")
            if path.is_file() and ".cache" not in path.parts and path.name != "manifest.json"
        }
        data = {"model_id": MODELS[kind], "revision": revision, "files": files}
        (directory / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf8")
        print(f"Prepared {kind}: {revision}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", choices=[*MODELS, "all"])
    args = parser.parse_args()
    prepare(list(MODELS) if "all" in args.models else args.models)
