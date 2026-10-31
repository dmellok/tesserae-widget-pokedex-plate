"""Pokédex Plate: Pokémon cut out and packed onto one printed page like a
natural-history specimen sheet, each as large as the page allows, with a
caption under each.

The Pokémon come from PokeAPI (https://pokeapi.co, no key): a generation, a
type, a random sheet that changes once a day, or a list of names and numbers.
Each carries its national dex number, its name (in the language picked), its
types and its height. Pictures are the 96 px pixel sprites, the official
artwork, or AI paintings of the official artwork made once per style through
the host's app.artwork collection and kept.

Nothing is bundled: every picture is fetched at runtime from PokeAPI's sprite
repository and cached under the widget's data dir for good; Pokémon records for
thirty days, misses for a day. fetch() never raises: it returns
{"state": "error", "error": "..."} instead.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import random
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

try:
    from app import artwork  # Tesserae 0.463+: the pictures a widget makes and keeps
    from app import fal as _fal
except (
    ImportError
):  # an older Tesserae: sprites and artwork only, with a note saying why
    artwork = None  # type: ignore[assignment]
    _fal = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

WIDGET = "pokedex_plate"
VERSION = "0.1.0"
USER_AGENT = f"tesserae/pokedex_plate/{VERSION} (+https://github.com/dmellok/tesserae-widget-pokedex-plate)"
API = "https://pokeapi.co/api/v2"
GRAPHQL = "https://graphql.pokeapi.co/v1beta2"
SPRITES = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"
HTTP_TIMEOUT_S = 15
IMG_TIMEOUT_S = 30
IMG_MAX_BYTES = 8_000_000
JSON_MAX_BYTES = 4_000_000
RECORD_S = 30 * 86400
MISS_S = 86400
COUNT_MAX = 24
COUNT_DEFAULT = 12
DEX_FALLBACK = 1025
# New PokeAPI JSON calls one fetch may make; the rest wait for the next refresh.
CALLS_PER_FETCH = 60
WORKERS = 4
LIVE_S = 6
ASPECT = "1:1"
# Bumped when the way sprites and artwork are prepared changes, so the kept copies are made again.
PREP = "p4"
# On black and white a sprite pixel darker than this prints black.
MONO_CUT = 128
# The tallest on a sheet is at most this many times the shortest when sized by height.
TRUE_RATIO = 4.0

SOURCES = ("generation", "type", "random", "list")
ORDERS = ("daily", "dex", "largest")
PICTURES = ("sprite", "artwork", "gen")
SIZINGS = ("same", "true")
PACKINGS = ("balanced", "spiral", "voids")
PAPERS = ("cream", "white", "aged")
LEVELS = ("colour", "gray16", "gray4", "mono")
LABELS = ("number", "name", "types", "height")
LANGS = {
    "en": "en",
    "ja-Hrkt": "ja-hrkt",
    "ja-roma": "ja-roma",
    "de": "de",
    "fr": "fr",
    "es": "es",
    "it": "it",
    "ko": "ko",
    "zh-Hans": "zh-hans",
}
TYPES = (
    "normal",
    "fire",
    "water",
    "grass",
    "electric",
    "ice",
    "fighting",
    "poison",
    "ground",
    "flying",
    "psychic",
    "bug",
    "rock",
    "ghost",
    "dragon",
    "dark",
    "steel",
    "fairy",
)
# National dex ranges and home regions, so a generation needs no list call.
GENERATIONS: dict[int, tuple[int, int, str]] = {
    1: (1, 151, "Kanto"),
    2: (152, 251, "Johto"),
    3: (252, 386, "Hoenn"),
    4: (387, 493, "Sinnoh"),
    5: (494, 649, "Unova"),
    6: (650, 721, "Kalos"),
    7: (722, 809, "Alola"),
    8: (810, 905, "Galar"),
    9: (906, 1025, "Paldea"),
}
CREDIT = (
    "Data and images: PokeAPI. Pokémon and Pokémon character names are trademarks "
    "of Nintendo, Creatures Inc. and GAME FREAK inc."
)
NEEDS_HOST = "AI paintings need Tesserae 0.463 or later; this server is older, so the official artwork shows."
ERR_UNREACHABLE = "PokeAPI could not be reached; the plate shows again when it can."
ERR_EMPTY_LIST = "Add some Pokémon to the list: names or national dex numbers, one per line or separated by commas."

# Each style: its label, the words the painter gets, and whether it is black ink only.
STYLES: dict[str, tuple[str, str, bool]] = {
    "engraving": (
        "Victorian engraving, hand-coloured",
        "a hand-coloured copperplate engraving from a Victorian natural-history book: fine "
        "engraved linework, cross-hatching and stippling for the form, translucent watercolour "
        "washes laid over the engraving, the look of a lithographed plate from the 1860s",
        False,
    ),
    "watercolour": (
        "Naturalist's watercolour field study",
        "a naturalist's watercolour field study: a light graphite underdrawing, soft wet washes "
        "for the body, a few precise dry-brush details on the eyes, claws and markings, "
        "plenty of untouched white paper",
        False,
    ),
    "ukiyoe": (
        "Japanese woodblock (ukiyo-e)",
        "a Japanese ukiyo-e woodblock print: confident black keyblock outlines of even weight, "
        "flat areas of muted mineral colour, soft bokashi gradations, the grain of the block "
        "faintly visible in the flat colour",
        False,
    ),
    "sumie": (
        "Sumi-e ink painting",
        "a sumi-e ink painting: a few confident brush strokes of black sumi ink, wet and dry "
        "brush, the tones from deep black to pale grey wash, much of the form suggested "
        "rather than outlined, black ink only with no colour at all",
        True,
    ),
    "linocut": (
        "Linocut print",
        "a linocut print: bold carved black lines, gouged texture in the shading, two or three "
        "flat colours printed slightly off register, the markings simplified but true",
        False,
    ),
    "line": (
        "Scientific line drawing",
        "a scientific line drawing in black ink for a field guide: clean even outlines, the "
        "features and markings drawn carefully, light stippled shading, black ink only with "
        "no colour at all",
        True,
    ),
    "stainedglass": (
        "Stained glass",
        "a stained-glass window panel: the creature built from pieces of coloured glass held "
        "by dark lead lines, rich translucent colours with a little painted detail on the glass",
        False,
    ),
    "gouache": (
        "Children's-book gouache",
        "a gouache illustration from a classic children's picture book: opaque matt paint, soft "
        "rounded forms, visible brush texture, warm gentle colours",
        False,
    ),
}
MODELS = ("nano_banana", "nano_banana_pro", "flux_kontext", "gpt_image", "seedream")
MODEL_LABELS = {
    "nano_banana": "Nano Banana",
    "nano_banana_pro": "Nano Banana Pro",
    "flux_kontext": "FLUX.1 Kontext Pro",
    "gpt_image": "GPT Image 2",
    "seedream": "Seedream 4",
}
PROMPT = (
    "Paint the Pokémon {name} from the reference picture as {style}. Show the whole creature "
    "alone, its whole body in view, its true colours, markings and proportions kept exactly as "
    "in the reference{mono}. The creature fills the picture. On plain pure white paper: no "
    "background, no scenery, no text, no label, no border, no frame, no shadow."
)
PROMPT_MONO = ", rendered in black ink only"

# A sheet that renders with no network at all: the Kanto starters and friends, data only.
SAMPLE: list[dict[str, Any]] = [
    {"id": 1, "name": "Bulbasaur", "types": ["grass", "poison"], "height": 7},
    {"id": 4, "name": "Charmander", "types": ["fire"], "height": 6},
    {"id": 7, "name": "Squirtle", "types": ["water"], "height": 5},
    {"id": 25, "name": "Pikachu", "types": ["electric"], "height": 4},
    {"id": 39, "name": "Jigglypuff", "types": ["normal", "fairy"], "height": 5},
    {"id": 94, "name": "Gengar", "types": ["ghost", "poison"], "height": 15},
    {"id": 131, "name": "Lapras", "types": ["water", "ice"], "height": 25},
    {"id": 143, "name": "Snorlax", "types": ["normal"], "height": 21},
]

# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:60] or "x"


def roman(n: int) -> str:
    out, n = "", max(1, min(3999, int(n)))
    for value, glyph in (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ):
        while n >= value:
            out, n = out + glyph, n - value
    return out


def _write(path: Path, data: bytes) -> None:
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    with contextlib.suppress(OSError, ValueError, UnicodeDecodeError):
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _choice(value: Any, allowed: tuple[str, ...]) -> str:
    v = str(value if value is not None else "").strip()
    return v if v in allowed else allowed[0]


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return default


def _flag(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def _id_of(url: str) -> int:
    m = re.search(r"/(\d+)/?$", str(url or ""))
    return int(m.group(1)) if m else 0


def http(
    url: str,
    accept: str = "application/json",
    timeout: float = HTTP_TIMEOUT_S,
    limit: int = JSON_MAX_BYTES,
    body: bytes | None = None,
) -> tuple[bytes | None, str | None]:
    """(bytes, None) or (None, why): "unreachable", "too large" or "HTTP <code>"."""
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(limit + 1)
            return (None, "too large") if len(data) > limit else (data, None)
    except urllib.error.HTTPError as err:
        return None, f"HTTP {err.code}"
    except Exception:
        return None, "unreachable"


def get_json(url: str, body: bytes | None = None) -> tuple[Any, str | None]:
    raw, err = http(url, body=body)
    if err:
        return None, err
    try:
        return json.loads((raw or b"").decode("utf-8")), None
    except (ValueError, UnicodeDecodeError):
        return None, "bad json"


class Calls:
    """The new PokeAPI calls one fetch may still make, shared across threads."""

    def __init__(self, n: int = CALLS_PER_FETCH) -> None:
        self.left = n
        self.lock = threading.Lock()
        self.short = 0

    def take(self) -> bool:
        with self.lock:
            if self.left <= 0:
                self.short += 1
                return False
            self.left -= 1
            return True


def cached_json(
    data_dir: Path,
    name: str,
    url: str,
    calls: Calls,
    ttl: float = RECORD_S,
    body: bytes | None = None,
) -> tuple[Any, str | None]:
    """A PokeAPI answer kept for ``ttl``; a stale copy when PokeAPI is away;
    a 404 remembered for a day. ``name`` is the cache file's stem."""
    path = data_dir / "cache" / f"{name}.json"
    hit = _read_json(path, None)
    if isinstance(hit, dict) and "at" in hit:
        fresh = time.time() - float(hit.get("at") or 0) < ttl
        if fresh and "data" in hit:
            return hit["data"], None
        if fresh and hit.get("miss"):
            return None, str(hit.get("miss"))
    if not calls.take():
        if isinstance(hit, dict) and "data" in hit:
            return hit["data"], None
        return None, "later"
    got, err = get_json(url, body=body)
    if err or got is None:
        if err and err.startswith("HTTP 4"):
            _write(path, json.dumps({"at": time.time(), "miss": err}).encode())
            return None, err
        if isinstance(hit, dict) and "data" in hit:
            return hit["data"], None
        return None, err or "unreachable"
    _write(path, json.dumps({"at": time.time(), "data": got}).encode())
    return got, None


# ----------------------------------------------------------------------
# PokeAPI
# ----------------------------------------------------------------------


def slim_record(mon: dict[str, Any], species: dict[str, Any] | None) -> dict[str, Any]:
    """What the plate keeps of a Pokémon: number, names by language, height
    in decimetres, weight, types, and the two picture URLs."""
    pid = int(mon.get("id") or 0)
    sid = _id_of((mon.get("species") or {}).get("url", "")) or pid
    names: dict[str, str] = {}
    for n in (species or {}).get("names") or []:
        lang = str(((n or {}).get("language") or {}).get("name") or "")
        if lang and n.get("name"):
            names[lang] = str(n["name"])
    if "en" not in names:
        names["en"] = str(mon.get("name") or "").replace("-", " ").title()
    sprites = mon.get("sprites") or {}
    art = ((sprites.get("other") or {}).get("official-artwork") or {}).get(
        "front_default"
    )
    types = [
        str((t.get("type") or {}).get("name") or "")
        for t in sorted(mon.get("types") or [], key=lambda t: int(t.get("slot") or 0))
    ]
    return {
        "id": sid,
        "slug": str((species or {}).get("name") or mon.get("name") or ""),
        "names": names,
        "height": int(mon.get("height") or 0),
        "weight": int(mon.get("weight") or 0),
        "types": [t for t in types if t],
        "sprite": str(sprites.get("front_default") or f"{SPRITES}/{sid}.png"),
        "artwork": str(art or f"{SPRITES}/other/official-artwork/{sid}.png"),
    }


def record(data_dir: Path, pid: int, calls: Calls) -> dict[str, Any] | None:
    """One Pokémon's slim record, kept for thirty days. The full answers are
    large (every move the Pokémon learns), so only the slim record is kept."""
    path = data_dir / "cache" / "mon" / f"{int(pid)}.json"
    hit = _read_json(path, None)
    if isinstance(hit, dict) and time.time() - float(hit.get("at") or 0) < RECORD_S:
        return hit.get("rec")
    if not calls.take():
        return hit.get("rec") if isinstance(hit, dict) else None
    mon, err = get_json(f"{API}/pokemon/{int(pid)}/")
    if err or not isinstance(mon, dict):
        return hit.get("rec") if isinstance(hit, dict) else None
    species = None
    if calls.take():
        sp, err = get_json(f"{API}/pokemon-species/{int(pid)}/")
        species = sp if isinstance(sp, dict) and not err else None
    rec = slim_record(mon, species)
    if species is not None:  # a record without its names is fetched again next time
        _write(path, json.dumps({"at": time.time(), "rec": rec}).encode())
    return rec


def dex_size(data_dir: Path, calls: Calls) -> int:
    got, _ = cached_json(data_dir, "dex_size", f"{API}/pokemon-species/?limit=1", calls)
    n = int(got.get("count") or 0) if isinstance(got, dict) else 0
    return n if 151 <= n <= 5000 else DEX_FALLBACK


def type_members(
    data_dir: Path, name: str, calls: Calls
) -> tuple[list[int] | None, str | None]:
    """The national dex numbers of a type's Pokémon (default forms only)."""
    got, err = cached_json(data_dir, f"type_{name}", f"{API}/type/{name}/", calls)
    if not isinstance(got, dict):
        return None, err or "unreachable"
    ids = sorted(
        {
            _id_of((p.get("pokemon") or {}).get("url", ""))
            for p in got.get("pokemon") or []
        }
        - {0}
    )
    return [i for i in ids if i < 10000], None


def tallest(
    data_dir: Path, ids: list[int], n: int, calls: Calls, key: str
) -> list[int] | None:
    """The ``n`` tallest of ``ids``, by PokeAPI's GraphQL endpoint (one call
    for the whole group, kept thirty days); None when it cannot say."""
    q = (
        "query($ids:[Int!]){pokemon(where:{id:{_in:$ids}},"
        "order_by:[{height:desc},{id:asc}]){id height}}"
    )
    body = json.dumps({"query": q, "variables": {"ids": ids}}).encode()
    got, _ = cached_json(data_dir, f"tallest_{key}", GRAPHQL, calls, body=body)
    rows = (
        ((got or {}).get("data") or {}).get("pokemon")
        if isinstance(got, dict)
        else None
    )
    if not isinstance(rows, list) or not rows:
        return None
    return [int(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")][:n]


def daily_seed(today: date, salt: str = "") -> int:
    return zlib.crc32(f"pokedex-plate:{today.isoformat()}:{salt}".encode())


def daily_pick(pool: list[int], n: int, today: date, salt: str = "") -> list[int]:
    """``n`` of ``pool`` picked at random, the same all day."""
    rng = random.Random(daily_seed(today, salt))
    return sorted(rng.sample(list(pool), min(n, len(pool))))


_NAME_FIX = {"♀": "-f", "♂": "-m", "é": "e", "’": "", "'": "", ".": "", ":": ""}


def name_slug(text: str) -> str:
    """A typed name as PokeAPI spells it: Mr. Mime -> mr-mime, Farfetch'd ->
    farfetchd, Nidoran♀ -> nidoran-f, Flabébé -> flabebe, Type: Null -> type-null."""
    t = str(text).strip().lower()
    for a, b in _NAME_FIX.items():
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def parse_list(text: Any) -> list[tuple[str, Any]]:
    """The entries of a typed list, in order, each ("id", number) or
    ("name", slug); blanks dropped. Lines, commas and semicolons all separate."""
    if isinstance(text, list | tuple):
        text = "\n".join(str(t) for t in text)
    out: list[tuple[str, Any]] = []
    for raw in re.split(r"[\n,;]+", str(text or "")):
        t = raw.strip()
        if not t:
            continue
        m = re.fullmatch(r"(?:no\.?\s*|#)?0*(\d{1,4})", t, flags=re.I)
        if m:
            n = int(m.group(1))
            out.append(("id", n) if n > 0 else ("bad", t))
            continue
        s = name_slug(t)
        out.append(("name", s) if s else ("bad", t))
    return out


def resolve_list(
    data_dir: Path, entries: list[tuple[str, Any]], dex_n: int, calls: Calls
) -> tuple[list[int], list[str]]:
    """The dex numbers of a typed list, in order, duplicates dropped, and the
    entries that are not Pokémon."""
    ids: list[int] = []
    bad: list[str] = []
    for kind, v in entries:
        pid = 0
        if kind == "id":
            pid = int(v) if 1 <= int(v) <= dex_n else 0
        elif kind == "name":
            got, _ = cached_json(
                data_dir, f"name_{v}", f"{API}/pokemon-species/{v}/", calls
            )
            pid = int(got.get("id") or 0) if isinstance(got, dict) else 0
            if isinstance(got, dict) and "names" in got:
                # keep only the number: the full species answer is large
                _write(
                    data_dir / "cache" / f"name_{v}.json",
                    json.dumps({"at": time.time(), "data": {"id": pid}}).encode(),
                )
        if pid and pid not in ids:
            ids.append(pid)
        elif not pid:
            bad.append(str(v))
    return ids[:COUNT_MAX], bad


# ----------------------------------------------------------------------
# Pictures
# ----------------------------------------------------------------------


def cached_image(data_dir: Path, url: str) -> bytes | None:
    """A picture's bytes, fetched once and kept; a miss is remembered for a day."""
    folder = data_dir / "img"
    key = hashlib.sha1(url.encode()).hexdigest()[:20]
    path, miss = folder / f"{key}.bin", folder / f"{key}.miss"
    if path.exists():
        with contextlib.suppress(OSError):
            return path.read_bytes()
    with contextlib.suppress(OSError):
        if miss.exists() and time.time() - miss.stat().st_mtime < MISS_S:
            return None
    body, err = http(url, accept="image/*", timeout=IMG_TIMEOUT_S, limit=IMG_MAX_BYTES)
    if err or not body:
        with contextlib.suppress(OSError):
            folder.mkdir(parents=True, exist_ok=True)
            miss.touch()
        return None
    _write(path, body)
    return body


def _uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def sprite_image(raw: bytes, levels: str) -> tuple[bytes, int, int] | None:
    """A pixel sprite cropped to its pixels, as a PNG with hard alpha, with its
    width and height. Greys snap to the panel's own levels so nothing is
    dithered; on black and white each sprite pixel is thresholded, so the
    outlines and the dark parts print solid black and the rest white, like
    line art, with nothing for the panel to dither."""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None
    alpha = im.getchannel("A").point(lambda a: 255 if a > 127 else 0)
    box = alpha.getbbox()
    if not box:
        return None
    im, alpha = im.crop(box), alpha.crop(box)
    if levels != "colour":
        grey = im.convert("RGB").convert("L")
        if levels == "mono":
            grey = grey.point(lambda v: 0 if v < MONO_CUT else 255)
        else:
            steps = 15 if levels == "gray16" else 3
            grey = grey.point(
                [round(round(v * steps / 255) * 255 / steps) for v in range(256)]
            )
        im = Image.merge("LA", (grey, alpha))
    else:
        im.putalpha(alpha)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue(), im.size[0], im.size[1]


def artwork_image(
    raw: bytes, levels: str, px: int = 640
) -> tuple[bytes, int, int] | None:
    """Official artwork cropped to the creature, fitted to ``px``, converted and
    contrast-stretched for a greyscale panel, as a WebP with its alpha."""
    from PIL import Image, ImageOps

    try:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None
    box = im.getchannel("A").point(lambda a: 255 if a > 24 else 0).getbbox()
    if not box:
        return None
    im = im.crop(box)
    im.thumbnail((px, px), Image.Resampling.LANCZOS)
    if levels != "colour":
        alpha = im.getchannel("A")
        cutoff = {"gray16": 0.5, "gray4": 1.0, "mono": 1.5}.get(levels, 1.0)
        grey = ImageOps.autocontrast(im.convert("RGB").convert("L"), cutoff=cutoff)
        if levels == "mono":
            grey = grey.point([round(255 * (i / 255) ** 0.8) for i in range(256)])
        im = Image.merge("RGBA", (grey, grey, grey, alpha))
    out = io.BytesIO()
    im.save(out, "WEBP", quality=88, method=4)
    return out.getvalue(), im.size[0], im.size[1]


def picture(
    rec: dict[str, Any], kind: str, data_dir: Path, levels: str
) -> dict[str, Any] | None:
    """A sprite or the official artwork, prepared for the panel and cached."""
    url = rec["sprite"] if kind == "sprite" else rec["artwork"]
    key = hashlib.sha1(url.encode()).hexdigest()[:20]
    ext, mime = ("png", "image/png") if kind == "sprite" else ("webp", "image/webp")
    done = data_dir / "img" / f"{key}_{kind}_{levels}_{PREP}.{ext}"
    meta = done.with_suffix(".json")
    if done.exists() and meta.exists():
        info = _read_json(meta, {})
        with contextlib.suppress(OSError):
            return {
                "src": _uri(done.read_bytes(), mime),
                "w": int(info.get("w") or 0),
                "h": int(info.get("h") or 0),
                "kind": kind,
                "pixel": kind == "sprite",
            }
    raw = cached_image(data_dir, url)
    if not raw:
        return None
    try:
        got = (
            sprite_image(raw, levels)
            if kind == "sprite"
            else artwork_image(raw, levels)
        )
    except ImportError:  # pragma: no cover - Pillow ships with Tesserae
        return None
    if not got:
        return None
    data, w, h = got
    _write(done, data)
    _write(meta, json.dumps({"w": w, "h": h}).encode())
    return {
        "src": _uri(data, mime),
        "w": w,
        "h": h,
        "kind": kind,
        "pixel": kind == "sprite",
    }


# ----------------------------------------------------------------------
# AI paintings, through the host's collection
# ----------------------------------------------------------------------


def prompt_for(payload: dict[str, Any]) -> str:
    style = STYLES.get(str(payload.get("style") or ""), STYLES["engraving"])
    return PROMPT.format(
        name=payload.get("name") or "creature",
        style=style[1],
        mono=PROMPT_MONO if style[2] else "",
    )


def paint(job: Any) -> tuple[bytes, dict[str, Any]]:
    """One painting on a worker thread: the official artwork as the reference,
    the style's words, the picture from fal.ai through the collection's budget."""
    p = dict(job.payload)
    ref = str(p.get("artwork") or "")
    if not ref:
        raise artwork.ArtworkError("no official artwork to paint from")
    model = str(p.get("model") or "") if p.get("model") in MODELS else "nano_banana"
    raw = job.fal.generate(model, prompt_for(p), refs=[ref], aspect=ASPECT)
    label = MODEL_LABELS.get(model, model)
    return raw, {
        "credit": f"AI painting ({label} on fal.ai) from the official artwork",
        "style": str(p.get("style") or "engraving"),
        "name": str(p.get("name") or ""),
        "id": int(p.get("id") or 0),
        "model": model,
    }


def cut_to_creature(raw: bytes) -> bytes | None:
    """The painting cropped to the creature, as a PNG with alpha. The painter
    leaves a margin of off-white paper: its colour (the median of the border)
    is scaled to pure white, the paper reached from the edges without crossing
    ink becomes transparent (white inside the creature stays), specks apart
    from the creature go too, and the picture is cropped to what is left with
    a small border."""
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
    except ImportError:  # pragma: no cover - Pillow ships with Tesserae
        return None
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None
    w, h = im.size
    if w < 32 or h < 32:
        return None
    px: Any = im.load()
    border = [px[x, y] for x in range(0, w, 5) for y in (1, h - 2)]
    border += [px[x, y] for y in range(0, h, 5) for x in (1, w - 2)]
    paper = tuple(sorted(c[i] for c in border)[len(border) // 2] for i in range(3))
    if min(paper) < 160:  # not on paper at all: leave the picture as it is
        return None
    white = Image.merge(
        "RGB",
        [
            b.point([min(255, round(v * 255 / max(1, paper[i]))) for v in range(256)])
            for i, b in enumerate(im.split())
        ],
    )
    # ink: anything that differs from white by more than a little, closed a touch
    ink = white.convert("L").point(lambda v: 255 if v < 232 else 0)
    ink = ink.filter(ImageFilter.MaxFilter(5))
    # the paper: flood from the border through everything that is not ink
    k = 4
    small = ink.resize((w // k, h // k), Image.Resampling.BOX).point(
        lambda v: 255 if v > 40 else 0
    )
    sw, sh = small.size
    field = small.copy()
    for x in range(sw):
        for y in (0, sh - 1):
            if field.getpixel((x, y)) == 0:
                ImageDraw.floodfill(field, (x, y), 128)
    for y in range(sh):
        for x in (0, sw - 1):
            if field.getpixel((x, y)) == 0:
                ImageDraw.floodfill(field, (x, y), 128)
    _drop_specks(field)
    body = field.point(lambda v: 0 if v == 128 else 255)
    body = body.resize((w, h), Image.Resampling.BILINEAR).filter(
        ImageFilter.GaussianBlur(1.2)
    )
    box = body.point(lambda v: 255 if v > 127 else 0).getbbox()
    if not box:
        return None
    x0, y0, x1, y1 = box
    if x1 - x0 < w * 0.1 or y1 - y0 < h * 0.1:
        return None
    if (x1 - x0) * (y1 - y0) > 0.985 * w * h and ImageChops.difference(
        body, Image.new("L", (w, h), 255)
    ).getbbox() is None:
        return None  # the painter filled the frame: nothing to cut
    pad = max(4, int(0.02 * max(w, h)))
    crop = (max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad))
    out_im = white.convert("RGBA")
    out_im.putalpha(body)
    out = io.BytesIO()
    out_im.crop(crop).save(out, "PNG", optimize=True)
    return out.getvalue()


def _drop_specks(field: Any, share: float = 0.02) -> None:
    """Islands of ink apart from the creature (a fleck of paint, a stray
    signature) become paper: every piece under ``share`` of the largest. The
    field is the small cut grid, 128 where it is paper."""
    w, h = field.size
    px: Any = field.load()
    seen = bytearray(w * h)
    pieces: list[list[int]] = []
    for start in range(w * h):
        if seen[start] or px[start % w, start // w] == 128:
            continue
        seen[start] = 1
        stack, piece = [start], []
        while stack:
            i = stack.pop()
            piece.append(i)
            x, y = i % w, i // w
            for j, ok in (
                (i - 1, x > 0),
                (i + 1, x < w - 1),
                (i - w, y > 0),
                (i + w, y < h - 1),
            ):
                if ok and not seen[j] and px[j % w, j // w] != 128:
                    seen[j] = 1
                    stack.append(j)
        pieces.append(piece)
    if len(pieces) < 2:
        return
    top = max(len(p) for p in pieces)
    for piece in pieces:
        if len(piece) < share * top:
            for i in piece:
                px[i % w, i // w] = 128


def _size_of(data_uri: str) -> tuple[int, int]:
    try:
        from PIL import Image

        raw = base64.b64decode(data_uri.split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as im:
            return im.size
    except Exception:
        return 0, 0


def painting_src(
    art: Any, key: str, data_dir: Path, px: int, levels: str
) -> tuple[str, bool]:
    """The kept painting as a data URI, cut to the creature once and then
    derived per size and panel by the collection; (src, cut). The whole
    picture, printed by multiply, when the cut fails."""
    folder = ART.folder(data_dir)
    cut = folder / f"{key}__cut.png"
    kept = ART.path(key, data_dir) if hasattr(ART, "path") else None
    stale = False
    with contextlib.suppress(OSError):
        if kept is not None and cut.exists():
            stale = kept.stat().st_mtime > cut.stat().st_mtime
    if stale or not cut.exists():
        got = art.picture(key)
        data = cut_to_creature(got[0]) if got else None
        if data:
            _write(cut, data)
    if cut.exists():
        src = art.url(key, px=px, levels=levels, cut=True, variant="cut")
        if src:
            return src, True
    return art.url(key, px=px, levels=levels), False


class _NoArt:
    """What the collection opens to on a Tesserae without app.artwork: every
    request answers no_host, nothing starts, the note says why."""

    def __init__(self) -> None:
        self.asked = 0

    def request(self, key: str, payload: dict[str, Any], *, title: str = "") -> Any:
        from types import SimpleNamespace

        self.asked += 1
        return SimpleNamespace(
            key=key, state="no_host", ready=False, meta={}, why=NEEDS_HOST
        )

    def url(self, *a: Any, **k: Any) -> str:
        return ""

    def picture(self, *a: Any, **k: Any) -> None:
        return None

    def live(self) -> dict[str, int]:
        return {"live_refresh_s": 0, "live_pending": 0}

    def notes(self) -> list[str]:
        return [NEEDS_HOST] if self.asked else []

    def status(self) -> list[str]:
        return []

    def finish(self, *a: Any, **k: Any) -> None:
        return None


class _NoCollection:
    def folder(self, data_dir: Path) -> Path:
        return Path(data_dir) / "paintings"

    def open(
        self, ctx: dict[str, Any], settings: dict[str, Any] | None, *, subject: str = ""
    ) -> _NoArt:
        return _NoArt()


ART: Any = (
    artwork.Collection(
        WIDGET,
        "paintings",
        make=paint,
        label="Pokédex Plate",
        title="Painting Pokémon",
        facet="style",
        facet_label=lambda s: STYLES.get(s, ("AI painting",))[0],
        default_cents=200,
    )
    if artwork is not None
    else _NoCollection()
)


def painting_key(pid: int, name_slug_: str, style: str) -> str:
    return f"{int(pid):04d}_{slug(name_slug_)}--{style}--p1"


# ----------------------------------------------------------------------
# The sheet
# ----------------------------------------------------------------------


def height_words(dm: int) -> str:
    if dm <= 0:
        return ""
    m = dm / 10
    return f"{m:.1f} m" if m < 10 else f"{m:.0f} m" if m >= 100 else f"{m:.1f} m"


def caption_lines(rec: dict[str, Any], name: str, labels: list[str]) -> list[str]:
    """The caption under a Pokémon: the number and the name on the first line,
    the types and the height on the second."""
    first = []
    if "number" in labels:
        first.append(f"No. {rec['id']:03d}")
    if "name" in labels:
        first.append(name)
    second = []
    if "types" in labels and rec.get("types"):
        second.append(" / ".join(t.title() for t in rec["types"]))
    if "height" in labels and rec.get("height"):
        second.append(height_words(int(rec["height"])))
    lines = []
    if first:
        lines.append("  ".join(first))
    if second:
        lines.append(" · ".join(second))
    return lines


def name_in(rec: dict[str, Any], lang: str) -> str:
    names = rec.get("names") or {}
    return str(
        names.get(LANGS.get(lang, "en")) or names.get("en") or rec.get("slug") or "?"
    )


def size_of(heights: list[int]) -> list[float]:
    """Each Pokémon's size on a sheet sized by height: its height, with the
    shortest held to at least a quarter of the tallest so it stays visible."""
    known = [h for h in heights if h > 0]
    top = max(known) if known else 10
    floor = top / TRUE_RATIO
    return [round(max(h if h > 0 else top / 2, floor), 2) for h in heights]


def labels_of(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",")]
    if not isinstance(value, list | tuple):
        return ["number", "name"]
    return [v for v in LABELS if v in {str(x) for x in value}]


def title_and_plate(
    source: str, gen: int, ptype: str, today: date, entries_key: str
) -> tuple[str, str]:
    if source == "generation":
        return f"Generation {roman(gen)} · {GENERATIONS[gen][2]}", roman(gen)
    if source == "type":
        return f"{ptype.title()} type", roman(TYPES.index(ptype) + 1)
    if source == "random":
        return f"Today's Pokédex · {today.day} {today.strftime('%B %Y')}", roman(
            today.timetuple().tm_yday
        )
    return "Pokédex", roman(zlib.crc32(entries_key.encode()) % 99 + 1)


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    try:
        return _fetch(options or {}, settings or {}, ctx or {})
    except Exception as err:
        logger.exception("pokedex_plate: fetch failed")
        return {
            "state": "error",
            "error": "The plate could not be drawn right now.",
            "detail": str(err)[:200],
        }


def sample_data(options: dict[str, Any] | None = None) -> dict[str, Any]:
    """A sheet with no network: data only, no pictures (each shows as an empty mount)."""
    options = options or {}
    labels = labels_of(options.get("labels", ["number", "name"]))
    sizes = size_of([s["height"] for s in SAMPLE])
    species = []
    for s, size in zip(SAMPLE, sizes, strict=True):
        rec = {**s, "names": {"en": s["name"]}}
        species.append(
            {
                "id": s["id"],
                "name": s["name"],
                "types": s["types"],
                "height": s["height"],
                "size": size,
                "lines": caption_lines(rec, s["name"], labels),
                "pic": None,
            }
        )
    return {
        "state": "ok",
        "demo": True,
        "title": "Pokédex · sample",
        "plate": "Plate I",
        "pictures": "sprite",
        "sizing": _choice(options.get("sizing"), SIZINGS),
        "packing": _choice(options.get("packing"), PACKINGS),
        "paper": _choice(options.get("paper"), PAPERS),
        "panel": _choice(options.get("panel"), LEVELS),
        "credits": [CREDIT],
        "notices": [
            "A sample sheet: PokeAPI could not be reached, so no pictures yet."
        ],
        "status": [],
        "species": species,
        "live_refresh_s": 0,
        "live_pending": 0,
    }


def _fetch(
    options: dict[str, Any], settings: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    data_dir = Path(ctx.get("data_dir") or ".")
    today = date.today()
    calls = Calls()
    source = _choice(options.get("source"), SOURCES)
    gen = _int(options.get("generation"), 1, 1, 9)
    ptype = _choice(options.get("poke_type"), TYPES)
    count = _int(options.get("count"), COUNT_DEFAULT, 1, COUNT_MAX)
    order = _choice(options.get("order"), ORDERS)
    pictures = _choice(options.get("pictures"), PICTURES)
    style = _choice(options.get("style"), tuple(STYLES))
    model = _choice(options.get("model"), MODELS)
    sizing = _choice(options.get("sizing"), SIZINGS)
    packing = _choice(options.get("packing"), PACKINGS)
    labels = labels_of(options.get("labels", ["number", "name"]))
    lang = _choice(options.get("names"), tuple(LANGS))
    paper = _choice(options.get("paper"), PAPERS)
    levels = _choice(options.get("panel"), LEVELS)
    credits_on = _flag(options.get("credits"), True)
    entries = parse_list(options.get("list")) if source == "list" else []
    entries_key = json.dumps(entries)
    title, plate = title_and_plate(source, gen, ptype, today, entries_key)
    custom = str(options.get("title") or "").strip()
    base: dict[str, Any] = {
        "title": custom or title,
        "plate": f"Plate {plate}",
        "source": source,
        "pictures": pictures,
        "style": style,
        "sizing": sizing,
        "packing": packing,
        "labels": labels,
        "paper": paper,
        "panel": levels,
        "lang": lang,
        "credits": [CREDIT] if credits_on else [],
        "species": [],
        "notices": [],
        "status": [],
        "live_refresh_s": 0,
        "live_pending": 0,
    }

    # Which Pokémon
    notes: list[str] = []
    if source == "list":
        if not entries:
            return {**base, "state": "error", "error": ERR_EMPTY_LIST}
        ids, bad = resolve_list(data_dir, entries, dex_size(data_dir, calls), calls)
        if bad:
            notes.append(
                f"Not found on PokeAPI: {', '.join(bad[:4])}{'…' if len(bad) > 4 else ''}"
            )
    else:
        if source == "generation":
            a, b, _ = GENERATIONS[gen]
            pool: list[int] | None = list(range(a, b + 1))
        elif source == "type":
            pool, _err = type_members(data_dir, ptype, calls)
            if pool is None:
                return _offline(base, options)
        else:
            pool = list(range(1, dex_size(data_dir, calls) + 1))
        assert pool is not None
        if source == "random" or order == "daily":
            ids = daily_pick(
                pool,
                count,
                today,
                salt="" if source == "random" else f"{source}:{gen}:{ptype}",
            )
        elif order == "largest":
            key = f"gen{gen}" if source == "generation" else ptype
            got = tallest(data_dir, pool, count, calls, key)
            if got is None:
                notes.append(
                    "Heights could not be fetched for Largest first; dex order shows instead."
                )
            ids = got or pool[:count]
        else:
            ids = pool[:count]
        ids = sorted(ids)

    # Their records, a few at once
    with ThreadPoolExecutor(max_workers=WORKERS) as pool_ex:
        recs = list(pool_ex.map(lambda i: record(data_dir, i, calls), ids))
    rows = [r for r in recs if r]
    if not rows:
        return (
            _offline(base, options)
            if ids
            else {**base, "state": "error", "error": ERR_EMPTY_LIST}
        )
    if calls.short:
        notes.append("More Pokémon arrive on the next refresh.")
        base["live_refresh_s"] = 60

    # Pictures: the sprite or the artwork; a painting when it is ready, the artwork meanwhile.
    shown = "artwork" if pictures == "gen" else pictures
    with ThreadPoolExecutor(max_workers=WORKERS) as pool_ex:
        pics = list(pool_ex.map(lambda r: picture(r, shown, data_dir, levels), rows))
    gen_on = pictures == "gen"
    art = (
        ART.open(
            ctx,
            settings,
            subject=f"{source}-{slug(entries_key if source == 'list' else str(gen) + ptype)}",
        )
        if gen_on
        else None
    )
    painted: set[str] = set()
    px = 900 if len(rows) <= 4 else 640
    for i, r in enumerate(rows):
        if art is None:
            continue
        key = painting_key(r["id"], r["slug"], style)
        payload = {
            "style": style,
            "artwork": r["artwork"],
            "name": r["names"].get("en") or r["slug"],
            "id": r["id"],
            "model": model,
        }
        got = art.request(key, payload, title=f"{payload['name']}, {STYLES[style][0]}")
        if got.ready:
            src, was_cut = painting_src(art, key, data_dir, px, levels)
            if src:
                w, h = _size_of(src)
                pics[i] = {
                    "src": src,
                    "w": w,
                    "h": h,
                    "kind": "painting",
                    "pixel": False,
                    "multiply": True,
                    "cut": was_cut,
                }
                painted.add(str((got.meta or {}).get("credit") or "AI painting"))
    if art is not None:
        art.finish(ctx, facet=style)
        notes.extend(art.notes())
        base["status"] = art.status()
        base.update(art.live())

    sizes = size_of([int(r.get("height") or 0) for r in rows])
    base["species"] = [
        {
            "id": r["id"],
            "name": name_in(r, lang),
            "types": r["types"],
            "height": r["height"],
            "size": size,
            "lines": caption_lines(r, name_in(r, lang), labels),
            "pic": pic,
        }
        for r, size, pic in zip(rows, sizes, pics, strict=True)
    ]
    if credits_on and painted:
        base["credits"] = [*sorted(painted), CREDIT]
    base["notices"] = notes
    return {**base, "state": "ok"}


def _offline(base: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """PokeAPI is away and nothing is cached: the sample sheet, saying so."""
    demo = sample_data(options)
    return {
        **demo,
        "title": base["title"],
        "plate": base["plate"],
        "credits": base["credits"],
        "notices": [
            ERR_UNREACHABLE,
            "A sample sheet without pictures shows meanwhile.",
        ],
    }
