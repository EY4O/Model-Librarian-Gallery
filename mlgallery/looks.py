"""A character's look learned from your own images (no AI): the prompt tags most common in the images it's in (counted:
recognised or confirmed), sorted into appearance and outfit the way the Chrome extension sorts Danbooru's tags (its
lib/booru.js `classify`: keep the two in step), for the character's Prompt card to offer.
"""
from __future__ import annotations

import re

MIN_FILES = 3      # fewer images say too little
MIN_SHARE = 0.3    # a tag on at least this share of them
SHOWN = 30

EXPRESSION = re.compile(r"\b(holding|closed eyes|half-closed eyes|one eye closed|looking|open mouth|closed mouth|smile|smiling|grin|blush|tears|crying|frown|pout|expressionless|parted lips|tongue out|wink|sweat|surprised|angry|sad)\b")
OUTFIT = re.compile(r"\b(dress|shirt|skirt|jacket|coat|capelet|cape|cloak|hoodie|hooded|sweater|vest|uniform|kimono|yukata|apron|pants|shorts|jeans|leggings|pantyhose|thighhighs|kneehighs|socks|stockings|footwear|shoes|boots|sneakers|sandals|heels|gloves|mittens|sleeves|sleeveless|collar|necktie|tie|bowtie|ribbon|bow|hat|cap|beret|helmet|hood|headband|headwear|hairband|crown|tiara|wreath|earrings|necklace|choker|bracelet|jewelry|ring|glasses|eyewear|goggles|mask|scarf|belt|bag|armor|gauntlets|robe|swimsuit|bikini|leotard|bodysuit|suit|blazer|cardigan|camisole|tank top|crop top|serafuku|sailor collar|neckerchief|frills|lace|buttons|sash|obi|ornament|hairclip|scrunchie|brooch|wristband|armband|epaulettes|tricorne|clothes|clothing|outfit|costume|staff|sword|katana|weapon|wand)\b")
APPEARANCE = re.compile(r"\b(hair|bangs|sidelocks?|ahoge|braids?|braided|twintails|ponytail|bun|drills?|eyes|eyebrows|eyelashes|pupils|heterochromia|ears|tail|horns?|wings|halo|skin|skinned|tan|tanlines|freckles|mole|scar|fangs?|breasts|flat chest|muscular|abs|petite|elf|demon girl|cat girl|fox girl|dog girl|wolf girl|beard|stubble|mustache|facial hair|makeup|lipstick|eyeshadow|forehead|mature (female|male)|old (man|woman)|aged (up|down))\b")
COUNT = re.compile(r"^(\d|6\+)(girl|boy|other)s?$")


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "").replace("_", " ")).strip().lower()


def classify(tag) -> str:
    t = norm(tag)
    if COUNT.match(t):
        return "count"
    if EXPRESSION.search(t):
        return "skip"
    if OUTFIT.search(t):  # before appearance: "hair ribbon" is an accessory
        return "outfit"
    if APPEARANCE.search(t):
        return "appearance"
    return "skip"


def suggest(counts: dict, n: int, known=()) -> list[dict]:
    """{tag: images it's in} over n images -> [{tag, kind, share}]: appearance and outfit tags common enough, the ones the
    character has already (known) left out, most common first."""
    if n < MIN_FILES:
        return []
    have = {norm(k) for k in known if k}
    out = []
    for tag, k in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        share = k / n
        if share < MIN_SHARE:
            break
        kind = classify(tag)
        if kind == "count":
            kind = "appearance"
        if kind in ("appearance", "outfit") and norm(tag) not in have:
            out.append({"tag": tag, "kind": kind, "share": round(share, 2)})
    return out[:SHOWN]
