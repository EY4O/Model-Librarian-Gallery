"""Which model family (Illustrious, Qwen, Flux, ...) a checkpoint or diffusion model belongs to, and its colour.

The families, their name rules, folder aliases and colours are Model Librarian's (smf/basemodel.py,
smf/organize.py, js/lib/util.js), so a model is filed and coloured the same in both. Evidence, best first:

  1. the folder Model Librarian's organizer sorted it into (Illustrious/, Qwen/, or a folder of your own)
  2. the CivitAI base model in the sidecar file next to it (Stability Matrix, LoRA Manager, CivitAI info)
  3. a folder in the name the workflow used ("Illustrious\\model.safetensors")
  4. words in its name
"""
from __future__ import annotations

import json
import os
import re

FAMILIES = [
    "Illustrious", "NoobAI", "Pony", "SDXL", "SD1.5", "SD2", "SD3", "Flux", "Flux Kontext", "Flux.2",
    "Chroma", "Krea 2", "Qwen", "Wan", "HunyuanVideo", "LTXV", "Lumina", "Z-Image", "Anima", "HiDream",
    "AuraFlow", "PixArt", "Kolors", "Cosmos", "SVD", "Stable Cascade", "Mochi", "CogVideoX",
]
TEXT_RULES = [
    (r"illustrious|\bilxl\b|illu(?:strious)?[-_ ]?xl", "Illustrious"), (r"noob", "NoobAI"), (r"pony|pdxl", "Pony"),
    (r"kontext", "Flux Kontext"), (r"flux[\s._-]*2", "Flux.2"), (r"chroma", "Chroma"), (r"krea[\s._-]*2|\bk2\b", "Krea 2"),
    (r"flux|krea", "Flux"), (r"qwen", "Qwen"), (r"\bwan\b|wan[\s._-]*2|wan2[12]|wan[\s_-]?video", "Wan"),
    (r"hunyuan", "HunyuanVideo"), (r"\bltx", "LTXV"), (r"z[\s._-]?image", "Z-Image"), (r"lumina", "Lumina"),
    (r"\banima\b|anima[\s._-]", "Anima"), (r"hidream", "HiDream"), (r"aura[\s_-]?flow", "AuraFlow"), (r"pixart", "PixArt"),
    (r"kolors", "Kolors"), (r"cosmos", "Cosmos"), (r"stable[\s_-]?cascade|wuerstchen", "Stable Cascade"),
    (r"\bsvd\b|stable[\s_-]?video", "SVD"), (r"mochi", "Mochi"), (r"cogvideo", "CogVideoX"),
    (r"sd[\s_-]?3|stable[\s_-]?diffusion[\s_-]?3", "SD3"), (r"sd[\s_-]?xl|sdxl|\bxl\b|_xl|xl_", "SDXL"),
    (r"sd[\s_-]?2(\.\d)?\b|sd_v2|v2-1|768-v", "SD2"), (r"sd[\s_-]?1(\.[45])?\b|sd_v1|sd15|v1-5", "SD1.5"),
]
_RULES = [(re.compile(p, re.I), f) for p, f in TEXT_RULES]
ALIASES = {
    "Illustrious": ["il", "illustriousxl", "illu", "ilxl"], "NoobAI": ["noob", "noobxl", "nai"], "Pony": ["pdxl", "ponyxl", "ponydiffusion"],
    "SDXL": ["xl", "sdxl10", "sdxl1.0", "sd xl"], "SD1.5": ["sd15", "sd1", "sd 1.5", "15", "sd1x"], "SD2": ["sd21", "sd20", "sd2x"],
    "Flux": ["flux1", "flux.1", "fluxdev", "flux1dev"], "Flux.2": ["flux2"], "Wan": ["wan21", "wan22", "wan2.1", "wan2.2", "wanvideo"],
    "Z-Image": ["zimage", "z image", "zimageturbo"], "Qwen": ["qwenimage", "qwen-image", "qwen image", "qwenimageedit"],
    "HunyuanVideo": ["hunyuan", "hyvideo"], "LTXV": ["ltx", "ltxvideo"],
}
COLORS = {
    "Illustrious": "#c084fc", "NoobAI": "#a78bfa", "Pony": "#f472b6", "SDXL": "#60a5fa", "SD1.5": "#34d399",
    "SD2": "#2dd4bf", "SD3": "#38bdf8", "Flux": "#fbbf24", "Flux Kontext": "#f59e0b", "Flux.2": "#fb923c",
    "Chroma": "#facc15", "Krea 2": "#fde047", "Qwen": "#818cf8", "Wan": "#f87171", "HunyuanVideo": "#fb7185",
    "LTXV": "#e879f9", "Lumina": "#a3e635", "Z-Image": "#4ade80", "Anima": "#f0abfc", "HiDream": "#5eead4",
}
OTHER = "#9ca3af"  # Model Librarian's colour for a family it has none for
GENERIC_DIRS = {"checkpoints", "diffusion_models", "unet", "models", "stablediffusion", "diffusionmodels", "sdxl_models", "new", "misc", "other", "old", "_archive"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def from_text(s: str | None) -> str | None:
    if not s:
        return None
    for rx, fam in _RULES:
        if rx.search(s):
            return fam
    return None


def known_folders(folder_names: dict | None = None) -> dict[str, str]:
    """normalized folder name -> family (Model Librarian's names, your renames, and the usual aliases)."""
    out = {}
    for fam in FAMILIES:
        for n in [fam, (folder_names or {}).get(fam) or fam] + ALIASES.get(fam, []):
            out[_norm(n)] = fam
    return out


def from_folder(folder: str, known: dict[str, str]) -> str | None:
    """A family folder (known by name or alias), or a folder of your own used as one (e.g. Moss/)."""
    n = _norm(folder)
    if not n or n in GENERIC_DIRS:
        return None
    return known.get(n) or folder.strip()


def color(family: str | None) -> str:
    return COLORS.get(family or "", OTHER)


class Resolver:
    """Families for model names, using Model Librarian's library when it's there (one listing per call)."""

    def __init__(self, models=None):
        self.cache: dict[str, str | None] = {}
        self.by_stem: dict[str, dict] = {}
        self.root = None
        self.known = known_folders()
        if models is not None:
            have = models.models()
            self.root = have.get("root")
            for m in have.get("models") or []:
                if m["kind"] in ("checkpoint", "unet"):
                    self.by_stem.setdefault(m["name"].lower(), m)
            if have.get("data_dir"):
                try:
                    with open(os.path.join(have["data_dir"], "config.json"), encoding="utf-8") as f:
                        self.known = known_folders(json.load(f).get("folder_names") or {})
                except (OSError, ValueError, AttributeError):
                    pass

    def family(self, name: str, file: str | None = None) -> str | None:
        key = (file or name or "").lower()
        if key in self.cache:
            return self.cache[key]
        fam = self._find(name, file)
        self.cache[key] = fam
        return fam

    def _find(self, name: str, file: str | None):
        from .mllink import stem
        m = self.by_stem.get(stem(file or name).lower())
        if m:
            parts = m["file"].split("/")
            if len(parts) > 1:
                fam = from_folder(parts[0], self.known)
                if fam:
                    return fam
            base = os.path.join(self.root, "checkpoints" if m["kind"] == "checkpoint" else "diffusion_models", *parts)
            base = base[: -len(os.path.splitext(base)[1])]
            for side, keys in ((".cm-info.json", ("BaseModel",)), (".metadata.json", ("base_model",)), (".civitai.info", ("baseModel",))):
                try:
                    with open(base + side, encoding="utf-8") as f:
                        d = json.load(f)
                    for k in keys:
                        d = d.get(k) if isinstance(d, dict) else None
                    fam = from_text(d) if isinstance(d, str) else None
                    if fam:
                        return fam
                except (OSError, ValueError):
                    continue
        if file:
            parts = str(file).replace("\\", "/").split("/")
            for folder in parts[:-1]:
                fam = from_folder(folder, self.known)
                if fam:
                    return fam
        return from_text(stem(file or name))
