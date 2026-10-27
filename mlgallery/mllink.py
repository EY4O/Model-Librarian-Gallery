"""Model Librarian's library, read (never written): the model files you have, to show the ones your images never use.

Model Librarian keeps its shared data in `<models folder>/.sm_model_fetcher`. The Gallery finds that folder
(or uses the one set in Settings), takes the models folder it sits in, and lists the checkpoints, diffusion
models and LoRAs there by file name, with when Model Librarian last saw each one used in ComfyUI. Nothing
is required: without it, statistics just leave out "never used".
"""
from __future__ import annotations

import json
import os
import string
import threading
import time

DIRNAME = ".sm_model_fetcher"
KINDS = {"checkpoints": "checkpoint", "diffusion_models": "unet", "unet": "unet", "loras": "lora"}
MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")
CACHE_FOR = 600  # seconds a listing is reused (walking the models folder takes a moment on a hard drive)


def _candidates() -> list[str]:
    """Where Model Librarian's shared folder usually is: next to Comfy Desktop's or Stability Matrix's models."""
    out = []
    appdata = os.environ.get("APPDATA") or ""
    for cfg in (os.path.join(appdata, "ComfyUI", "extra_models_config.yaml"),):
        try:
            with open(cfg, encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.partition(":")
                    if k.strip() == "base_path" and v.strip():
                        base = v.strip().strip("'\"")
                        out += [os.path.join(base, DIRNAME), os.path.join(base, "models", DIRNAME)]
        except OSError:
            pass
    drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")] if os.name == "nt" else ["/"]
    for root in drives:
        out += [os.path.join(root, "StabilityMatrix", "Data", "Models", DIRNAME),
                os.path.join(root, "Comfy-Desktop", "ComfyUI-Shared", "models", DIRNAME)]
    out.append(os.path.join(os.path.expanduser("~"), "Documents", "ComfyUI", "models", DIRNAME))
    return out


def _is_ml(p: str) -> bool:
    return any(os.path.isfile(os.path.join(p, f)) for f in ("registry.json", "hash_cache.json", "usage.json"))


# False in a Gallery on a data folder of its own (--data or MLG_DATA: the tests, a throwaway): Model Librarian's folder
# is never looked for, only the one set in Settings or MLG_ML_DATA, so a test run can't sync characters into yours
SEARCH = True


def find(configured: str = "") -> str | None:
    """Model Librarian's shared data folder, or None. A folder set in Settings (or MLG_ML_DATA) is the only one
    looked at; otherwise the usual places are (unless SEARCH is off)."""
    configured = os.environ.get("MLG_ML_DATA") or configured
    if configured:
        return os.path.normpath(configured) if _is_ml(configured) else None
    if not SEARCH:
        return None
    return next((os.path.normpath(p) for p in _candidates() if _is_ml(p)), None)


def _read(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def stem(name: str) -> str:
    base = str(name).replace("\\", "/").split("/")[-1]
    low = base.lower()
    for ext in MODEL_EXTS:
        if low.endswith(ext):
            return base[: -len(ext)]
    return base


class ModelLibrary:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._cache: tuple[float, str | None, dict] | None = None

    def data_dir(self) -> str | None:
        return find(self.settings.get("ml_data"))

    def models(self) -> dict:
        """{data_dir, root, models: [{kind, name, file, size, last_used}]} (cached for a while)."""
        d = self.data_dir()
        with self._lock:
            if self._cache and self._cache[1] == d and time.time() - self._cache[0] < CACHE_FOR:
                return self._cache[2]
        out = {"data_dir": d, "root": None, "models": []}
        if d:
            root = os.path.dirname(d)
            out["root"] = root
            usage = _read(os.path.join(d, "usage.json"), {})
            last = usage.get("last_used") or {}
            seen = set()
            for folder, kind in KINDS.items():
                base = os.path.join(root, folder)
                if not os.path.isdir(base):
                    continue
                for dp, dirs, files in os.walk(base):
                    dirs[:] = [x for x in dirs if not x.startswith(".")]
                    for fn in files:
                        if not fn.lower().endswith(MODEL_EXTS):
                            continue
                        full = os.path.join(dp, fn)
                        real = os.path.normcase(os.path.realpath(full))
                        if real in seen:  # the same file reached through a linked folder
                            continue
                        seen.add(real)
                        name = stem(fn)
                        try:
                            size = os.path.getsize(full)
                        except OSError:
                            size = None
                        lu = last.get(f"{folder}:{name.lower()}") or last.get(f"*:{name.lower()}")
                        out["models"].append({"kind": kind, "name": name, "file": os.path.relpath(full, base).replace("\\", "/"),
                                              "size": size, "last_used": lu})
        with self._lock:
            self._cache = (time.time(), d, out)
        return out
