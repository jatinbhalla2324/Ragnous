"""
Pick a curated model by inspecting the actual mesh, not the search metadata.

The first pass of CURATED_MODELS was ranked on title match and likes, because
that is all Sketchfab's search returns. It says nothing about whether a model
carries textures, and 16 of the 31 picks turned out to be flat untextured
geometry — a detailed scanned heart next to a brain that looks like clay.

This downloads only the JSON chunk at the head of each candidate GLB (a few
hundred KB, not the whole model), reads how many materials actually have a
base-colour texture, and ranks on that.

    python -m scripts.curate_models "human brain" "human lungs"

Prints the uid to paste into CURATED_MODELS for each topic.
"""

import asyncio
import json
import os
import struct
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.model3d_service import (  # noqa: E402
    MAX_MODEL_BYTES,
    _get_json,
    _tokenize,
    score_result,
)

CANDIDATES_PER_TOPIC = int(os.getenv("CURATE_CANDIDATES", "5"))
# The download endpoint really does rate-limit — with a 429, unlike the 202 the
# whole authenticated API returns to a client it does not like.
CALL_DELAY = float(os.getenv("CURATE_DELAY", "3"))


async def _download_info(uid: str, token: str) -> dict:
    """Download-endpoint JSON, backing off once if we are being throttled."""
    url = f"https://api.sketchfab.com/v3/models/{uid}/download"
    found = await _get_json(url, token)
    if found is None:
        await asyncio.sleep(25)
        found = await _get_json(url, token)
    return found or {}


def _read_gltf_header(url: str, limit: int = 6_000_000) -> dict:
    """Parse a GLB's JSON chunk by reading only the head of the file."""
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=90) as response:
        head = response.read(20)
        if len(head) < 20 or head[:4] != b"glTF":
            raise ValueError("not a binary glTF")
        json_len = struct.unpack("<I", head[12:16])[0]
        if json_len > limit:
            raise ValueError("JSON chunk implausibly large")
        body = b""
        while len(body) < json_len:
            chunk = response.read(min(65536, json_len - len(body)))
            if not chunk:
                break
            body += chunk
    return json.loads(body.decode("utf-8", "replace"))


def inspect(gltf: dict) -> dict:
    materials = gltf.get("materials", []) or []
    textured = sum(
        1 for m in materials
        if (m.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
    )
    return {
        "materials": len(materials),
        "textures": len(gltf.get("textures", []) or []),
        "textured_materials": textured,
    }


async def best_for(topic: str, token: str) -> None:
    url = (
        "https://api.sketchfab.com/v3/search?type=models"
        "&downloadable=true&count=24&sort_by=-likeCount"
        f"&q={quote(topic)}"
    )
    data = await _get_json(url, token)
    results = (data or {}).get("results") or []

    ranked = sorted(
        ((score_result(r, topic), r) for r in results), key=lambda p: p[0], reverse=True
    )
    topic_tokens = _tokenize(topic)

    def names_the_thing(model: dict) -> bool:
        return bool(topic_tokens & _tokenize(model.get("name") or ""))

    ranked = [
        (s, r) for s, r in ranked if s >= 2.0 and names_the_thing(r)
    ][:CANDIDATES_PER_TOPIC]

    print(f"\n### {topic}")
    if not ranked:
        print("  no candidate cleared the relevance bar")
        return

    scored = []
    for relevance, model in ranked:
        uid = model["uid"]
        size = ((model.get("archives") or {}).get("glb") or {}).get("size") or 0
        faces = model.get("faceCount") or 0
        if not size or size > MAX_MODEL_BYTES:
            continue

        found = await _download_info(uid, token)
        glb_url = (found.get("glb") or {}).get("url")
        if not glb_url:
            print(f"  - {model['name'][:34]:36} no GLB archive")
            continue

        try:
            info = await asyncio.to_thread(_read_gltf_header, glb_url)
            stats = inspect(info)
        except Exception as e:
            print(f"  - {model['name'][:34]:36} unreadable ({e})")
            continue

        # Textures are the whole point of this pass; everything else only
        # breaks ties. Face count is capped so a photogrammetry scan does not
        # win purely on being enormous, relevance carries real weight so a
        # 300k-face "Human Organs" bundle cannot win a query for one organ,
        # and anything over ~12MB is penalised because these load on phones.
        quality = (
            8.0 * min(stats["textured_materials"], 4) / 4
            + 2.0 * min(faces / 60_000, 1.0)
            + relevance * 0.6
            - 2.0 * max(0.0, (size / 1e6 - 12) / 12)
        )
        scored.append((quality, uid, model, stats, faces, size))
        flag = "TEXTURED" if stats["textured_materials"] else "flat    "
        print(
            f"  {flag} {model['name'][:34]:36} mats={stats['materials']:>3} "
            f"texMats={stats['textured_materials']:>2} {faces:>7}f "
            f"{size/1e6:5.1f}MB q={quality:.2f}"
        )
        await asyncio.sleep(CALL_DELAY)

    if not scored:
        print("  nothing usable")
        return

    scored.sort(key=lambda x: x[0], reverse=True)
    quality, uid, model, stats, faces, size = scored[0]
    verdict = "textured" if stats["textured_materials"] else "STILL FLAT — no textured candidate"
    print(f'  -> "{topic}": "{uid}",   # {model["name"][:40]} ({verdict})')


async def main(topics: list) -> int:
    token = os.getenv("SKETCHFAB_API_KEY")
    if not token:
        print("SKETCHFAB_API_KEY is not set.")
        return 1
    for topic in topics:
        await best_for(topic, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
