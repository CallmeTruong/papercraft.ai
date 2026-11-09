"""Explicit model preparation. Generation never downloads weights or executes remote code."""

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
from pathlib import Path
from urllib.request import urlopen

MODELS = {
    "important_details": "florence-community/Florence-2-base",
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


def prepare(kinds, refresh=False):
    for kind in kinds:
        if not refresh:
            try:
                _, data = manifest(kind, verify=True)
                print(f"Already prepared {kind}: {data['revision']}", flush=True)
                continue
            except (OSError, ValueError, KeyError):
                pass
        directory = root() / kind
        directory.mkdir(parents=True, exist_ok=True)
        if kind == "face_features":
            target = directory / "face_landmarker.task"
            with urlopen(MODELS[kind], timeout=120) as response:
                target.write_bytes(response.read(30_000_000))
            revision = "float16/1"
        else:
            with urlopen(
                f"https://huggingface.co/api/models/{MODELS[kind]}", timeout=60
            ) as response:
                info = json.load(response)
            revision = info["sha"]
            for entry in info["siblings"]:
                name = entry["rfilename"]
                if not any(
                    fnmatch.fnmatch(name, pattern)
                    for pattern in ("*.json", "*.safetensors", "*.txt", "README.md", "LICENSE*")
                ):
                    continue
                target = (directory / name).resolve()
                if not target.is_relative_to(directory.resolve()):
                    raise ValueError("Invalid model repository path")
                target.parent.mkdir(parents=True, exist_ok=True)
                print(f"Downloading {kind}/{name}", flush=True)
                partial = target.with_suffix(target.suffix + ".partial")
                with (
                    urlopen(
                        f"https://huggingface.co/{MODELS[kind]}/resolve/{revision}/{name}",
                        timeout=120,
                    ) as response,
                    partial.open("wb") as stream,
                ):
                    shutil.copyfileobj(response, stream, length=1024 * 1024)
                partial.replace(target)
        files = {
            str(path.relative_to(directory)).replace("\\", "/"): file_hash(path)
            for path in directory.rglob("*")
            if path.is_file()
            and ".cache" not in path.parts
            and path.name != "manifest.json"
            and path.suffix != ".partial"
        }
        data = {"model_id": MODELS[kind], "revision": revision, "files": files}
        (directory / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf8")
        print(f"Prepared {kind}: {revision}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", choices=[*MODELS, "all"])
    parser.add_argument(
        "--refresh", action="store_true", help="Download and pin current upstream revision"
    )
    args = parser.parse_args()
    prepare(list(MODELS) if "all" in args.models else args.models, refresh=args.refresh)
