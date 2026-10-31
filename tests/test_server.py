"""Offline, deterministic tests for the data shaping, the picture preparation
and fetch()'s promise never to raise. PokeAPI is a fake urlopen that answers
from small fixtures; pictures are drawn here with Pillow; the fal.ai painter is
never called (the AI path runs against a stub collection)."""

from __future__ import annotations

import base64
import io
import json
import re
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

import server

TODAY = date(2026, 10, 31)

MONS: dict[int, dict[str, Any]] = {
    1: {
        "slug": "bulbasaur",
        "en": "Bulbasaur",
        "ja": "フシギダネ",
        "h": 7,
        "types": ["grass", "poison"],
    },
    6: {
        "slug": "charizard",
        "en": "Charizard",
        "ja": "リザードン",
        "h": 17,
        "types": ["fire", "flying"],
    },
    25: {
        "slug": "pikachu",
        "en": "Pikachu",
        "ja": "ピカチュウ",
        "h": 4,
        "types": ["electric"],
    },
    29: {
        "slug": "nidoran-f",
        "en": "Nidoran♀",
        "ja": "ニドラン♀",
        "h": 4,
        "types": ["poison"],
    },
    83: {
        "slug": "farfetchd",
        "en": "Farfetch’d",
        "ja": "カモネギ",
        "h": 8,
        "types": ["normal", "flying"],
    },
    122: {
        "slug": "mr-mime",
        "en": "Mr. Mime",
        "ja": "バリヤード",
        "h": 13,
        "types": ["psychic", "fairy"],
    },
    321: {
        "slug": "wailord",
        "en": "Wailord",
        "ja": "ホエルオー",
        "h": 145,
        "types": ["water"],
    },
    595: {
        "slug": "joltik",
        "en": "Joltik",
        "ja": "バチュル",
        "h": 1,
        "types": ["bug", "electric"],
    },
}
for n in range(2, 152):
    MONS.setdefault(
        n,
        {
            "slug": f"mon{n}",
            "en": f"Mon {n}",
            "ja": f"モン{n}",
            "h": n % 20 + 1,
            "types": ["normal"],
        },
    )
WATER = [321, 7, 8, 9]
SPRITE_HOST = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"


def _png(w: int, h: int, colour=(220, 60, 40, 255), pad: int = 4) -> bytes:
    im = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    for y in range(h):
        for x in range(w):
            edge = x in (0, w - 1) or y in (0, h - 1)
            im.putpixel((x + pad, y + pad), (10, 10, 10, 255) if edge else colour)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _mon(n: int) -> dict[str, Any]:
    m = MONS[n]
    return {
        "id": n,
        "name": m["slug"],
        "height": m["h"],
        "weight": 10,
        "species": {"url": f"https://pokeapi.co/api/v2/pokemon-species/{n}/"},
        "types": [
            {"slot": i + 1, "type": {"name": t}} for i, t in enumerate(m["types"])
        ],
        "sprites": {
            "front_default": f"{SPRITE_HOST}/{n}.png",
            "other": {
                "official-artwork": {
                    "front_default": f"{SPRITE_HOST}/other/official-artwork/{n}.png"
                }
            },
        },
        "moves": ["a long list the widget does not keep"] * 50,
    }


def _species(n: int) -> dict[str, Any]:
    m = MONS[n]
    return {
        "id": n,
        "name": m["slug"],
        "names": [
            {"language": {"name": "en"}, "name": m["en"]},
            {"language": {"name": "ja-hrkt"}, "name": m["ja"]},
        ],
    }


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    """PokeAPI and its sprite host, offline; counts each kind of call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.calls.append(url)
        if url.startswith(SPRITE_HOST):
            n = int(re.search(r"/(\d+)\.png$", url).group(1))
            size = (
                (40 + n % 30, 30 + n % 40)
                if "official-artwork" not in url
                else (300, 280)
            )
            return _Resp(_png(*size))
        if "graphql" in url:
            ids = json.loads(req.data)["variables"]["ids"]
            rows = sorted(
                ({"id": i, "height": MONS[i]["h"]} for i in ids),
                key=lambda r: (-r["height"], r["id"]),
            )
            return _Resp(json.dumps({"data": {"pokemon": rows}}).encode())
        if "/pokemon-species/?limit=1" in url:
            return _Resp(json.dumps({"count": 1025}).encode())
        m = re.search(r"/(pokemon|pokemon-species|type)/([^/]+)/$", url)
        if m:
            kind, key = m.groups()
            if kind == "type":
                if key != "water":
                    raise urllib.error.HTTPError(url, 404, "nf", {}, None)  # type: ignore[arg-type]
                rows = [
                    {"pokemon": {"url": f"https://pokeapi.co/api/v2/pokemon/{i}/"}}
                    for i in WATER
                ]
                rows.append(
                    {"pokemon": {"url": "https://pokeapi.co/api/v2/pokemon/10089/"}}
                )
                return _Resp(json.dumps({"pokemon": rows}).encode())
            n = (
                int(key)
                if key.isdigit()
                else next((i for i, v in MONS.items() if v["slug"] == key), 0)
            )
            if not n or n not in MONS:
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)  # type: ignore[arg-type]
            return _Resp(
                json.dumps(_mon(n) if kind == "pokemon" else _species(n)).encode()
            )
        raise OSError("no network in tests")


def _fetch(tmp: Path, net: FakeNet | None = None, settings=None, ctx=None, **opts):
    with patch.object(urllib.request, "urlopen", net or FakeNet()):
        return server.fetch(
            opts, settings or {}, ctx={"data_dir": str(tmp), **(ctx or {})}
        )


# ----- list parsing ---------------------------------------------------------


def test_parse_list_names_numbers_and_mixed() -> None:
    got = server.parse_list(
        "Pikachu, #6\n143; No. 25\n  \nMr. Mime,farfetch'd, Nidoran♀, Flabébé, Type: Null, 0"
    )
    assert got == [
        ("name", "pikachu"),
        ("id", 6),
        ("id", 143),
        ("id", 25),
        ("name", "mr-mime"),
        ("name", "farfetchd"),
        ("name", "nidoran-f"),
        ("name", "flabebe"),
        ("name", "type-null"),
        ("bad", "0"),
    ]
    assert server.parse_list("") == []
    assert server.parse_list(["Eevee", "007"]) == [("name", "eevee"), ("id", 7)]


def test_list_fetch_skips_bad_entries_and_keeps_order(tmp_path: Path) -> None:
    d = _fetch(
        tmp_path,
        source="list",
        list="Pikachu, #6, mr. mime\n9999, blorp, pikachu, Nidoran♀",
    )
    assert d["state"] == "ok"
    assert [s["id"] for s in d["species"]] == [25, 6, 122, 29]
    assert any("9999" in n and "blorp" in n for n in d["notices"])
    assert d["species"][2]["name"] == "Mr. Mime"


def test_empty_list_says_so(tmp_path: Path) -> None:
    d = _fetch(tmp_path, source="list", list="  ,, \n")
    assert d["state"] == "error" and d["error"] == server.ERR_EMPTY_LIST


# ----- picking ----------------------------------------------------------------


def test_daily_random_is_the_same_all_day_and_changes_next_day() -> None:
    pool = list(range(1, 1026))
    a = server.daily_pick(pool, 12, TODAY)
    assert a == server.daily_pick(pool, 12, TODAY)
    assert a == sorted(a) and len(set(a)) == 12
    assert a != server.daily_pick(pool, 12, date(2026, 11, 1))
    assert server.daily_pick(pool, 12, TODAY, "generation:1:fire") != a


def test_random_fetch_is_identical_on_repeat(tmp_path: Path) -> None:
    class Day(date):
        @classmethod
        def today(cls):
            return TODAY

    with patch.object(server, "date", Day):
        MONS.update(
            {
                n: MONS.get(n)
                or {
                    "slug": f"m{n}",
                    "en": f"M{n}",
                    "ja": "",
                    "h": 5,
                    "types": ["normal"],
                }
                for n in range(1, 1026)
            }
        )
        first = _fetch(tmp_path, source="random", count="6")
        again = _fetch(tmp_path, source="random", count="6")
    assert first["state"] == "ok"
    assert json.dumps(first, sort_keys=True) == json.dumps(again, sort_keys=True)
    assert first["title"].startswith("Today's Pokédex · 31 October 2026")


def test_generation_dex_order_and_title(tmp_path: Path) -> None:
    d = _fetch(tmp_path, source="generation", generation="1", order="dex", count="5")
    assert [s["id"] for s in d["species"]] == [1, 2, 3, 4, 5]
    assert d["title"] == "Generation I · Kanto" and d["plate"] == "Plate I"
    assert d["species"][0]["lines"] == ["No. 001  Bulbasaur"]


def test_type_largest_first_uses_one_graphql_call(tmp_path: Path) -> None:
    net = FakeNet()
    d = _fetch(
        tmp_path, net, source="type", poke_type="water", order="largest", count="2"
    )
    assert [s["id"] for s in d["species"]] == [9, 321]  # the two tallest, in dex order
    assert sum("graphql" in u for u in net.calls) == 1
    assert d["title"] == "Water type"
    # forms past 10000 are left out of a type
    assert all(s["id"] < 10000 for s in d["species"])


def test_records_and_pictures_are_cached(tmp_path: Path) -> None:
    net = FakeNet()
    _fetch(tmp_path, net, source="list", list="1, 6, 25")
    n = len(net.calls)
    assert n > 0
    again = FakeNet()
    d = _fetch(tmp_path, again, source="list", list="1, 6, 25")
    assert again.calls == [] and d["state"] == "ok"
    # the kept record is slim: no moves
    kept = json.loads(
        (tmp_path / "cache" / "mon" / "6.json").read_text(encoding="utf-8")
    )
    assert "moves" not in json.dumps(kept)


def test_unreachable_gives_the_sample_sheet(tmp_path: Path) -> None:
    def _down(req, timeout=0):
        raise OSError("down")

    with patch.object(urllib.request, "urlopen", _down):
        d = server.fetch({"source": "generation"}, {}, ctx={"data_dir": str(tmp_path)})
    assert d["state"] == "ok" and d["demo"] is True
    assert server.ERR_UNREACHABLE in d["notices"]
    assert all(s["pic"] is None for s in d["species"])


def test_fetch_never_raises(tmp_path: Path) -> None:
    with patch.object(server, "record", side_effect=RuntimeError("boom")):
        d = _fetch(tmp_path, source="list", list="25")
    assert d["state"] == "error" and "error" in d


# ----- captions and sizes -------------------------------------------------


def test_captions_and_languages(tmp_path: Path) -> None:
    d = _fetch(
        tmp_path,
        source="list",
        list="6",
        labels=["number", "name", "types", "height"],
        names="ja-Hrkt",
    )
    s = d["species"][0]
    assert s["name"] == "リザードン"
    assert s["lines"] == ["No. 006  リザードン", "Fire / Flying · 1.7 m"]
    none = _fetch(tmp_path, source="list", list="6", labels=[])
    assert none["species"][0]["lines"] == []


def test_true_sizes_clamp_the_ratio() -> None:
    sizes = server.size_of([145, 1, 60])
    assert sizes[0] == 145 and sizes[2] == 60
    assert sizes[1] == pytest.approx(145 / server.TRUE_RATIO)
    assert max(sizes) / min(sizes) <= server.TRUE_RATIO + 1e-9


# ----- pictures and panels --------------------------------------------------


def _decode(uri: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))


@pytest.mark.parametrize("levels", server.LEVELS)
def test_sprites_per_panel(levels: str) -> None:
    raw = _png(20, 10, colour=(200, 120, 40, 255))
    data, w, h = server.sprite_image(raw, levels)
    assert (w, h) == (20, 10)  # cropped to its pixels
    im = Image.open(io.BytesIO(data))
    if levels == "colour":
        assert im.mode == "RGBA"
        return
    grey, alpha = im.convert("LA").split()
    assert set(alpha.tobytes()) <= {0, 255}
    shades = {g for g, a in zip(grey.tobytes(), alpha.tobytes(), strict=True) if a}
    allowed = {
        "mono": {0, 255},
        "gray4": {0, 85, 170, 255},
        "gray16": {round(i * 255 / 15) for i in range(16)},
    }[levels]
    assert shades <= allowed
    if levels == "mono":
        # thresholded: the dark outline prints black, the light body white
        assert grey.getpixel((0, 0)) == 0
        assert {grey.getpixel((x, 5)) for x in range(2, 18)} == {255}
        dark, _, _ = server.sprite_image(_png(20, 10, colour=(60, 40, 90, 255)), "mono")
        assert set(
            Image.open(io.BytesIO(dark)).convert("LA").getchannel("L").tobytes()
        ) == {0}


def test_artwork_keeps_alpha_and_greys(tmp_path: Path) -> None:
    raw = _png(120, 90)
    data, w, h = server.artwork_image(raw, "gray4")
    im = Image.open(io.BytesIO(data))
    assert im.format == "WEBP" and (w, h) == (120, 90)
    r, g, b, a = im.convert("RGBA").split()
    assert r.tobytes() == g.tobytes() == b.tobytes()


def test_fetch_sends_crisp_sprites(tmp_path: Path) -> None:
    d = _fetch(tmp_path, source="list", list="25", panel="mono")
    pic = d["species"][0]["pic"]
    assert pic["kind"] == "sprite" and pic["pixel"] is True
    assert pic["src"].startswith("data:image/png;base64,")
    assert _decode(pic["src"]).size == (pic["w"], pic["h"])
    art = _fetch(tmp_path, source="list", list="25", pictures="artwork")["species"][0][
        "pic"
    ]
    assert art["kind"] == "artwork" and art["pixel"] is False
    assert art["src"].startswith("data:image/webp;base64,")


# ----- AI paintings, against a stub collection -------------------------------


class StubArt:
    """One fetch's view of a collection: ``ready`` holds the keys painted."""

    def __init__(self, ready: dict[str, bytes]) -> None:
        self.ready = ready
        self.requests: list[tuple[str, dict]] = []
        self.finished = False

    def request(self, key: str, payload: dict, *, title: str = ""):
        self.requests.append((key, payload))
        if key in self.ready:
            return SimpleNamespace(
                ready=True,
                state="ready",
                meta={
                    "credit": "AI painting (Nano Banana on fal.ai) from the official artwork"
                },
                path=None,
            )
        return SimpleNamespace(ready=False, state="painting", meta={}, path=None)

    def picture(self, key: str):
        return (self.ready[key], {}) if key in self.ready else None

    def url(
        self,
        key: str,
        *,
        px: int = 600,
        levels: str = "colour",
        cut: bool = False,
        variant: str = "",
    ) -> str:
        return (
            "data:image/png;base64,"
            + base64.b64encode(self.ready.get(key, b"")).decode()
            if key in self.ready
            else ""
        )

    def live(self):
        pending = sum(1 for k, _ in self.requests if k not in self.ready)
        return {"live_refresh_s": 6 if pending else 0, "live_pending": pending}

    def notes(self):
        return []

    def status(self):
        pending = sum(1 for k, _ in self.requests if k not in self.ready)
        return (
            [f"painting {len(self.requests) - pending + 1} of {len(self.requests)}"]
            if pending
            else []
        )

    def finish(self, ctx, facet: str = "", extra=None):
        self.finished = True


class StubCollection:
    def __init__(self, art: StubArt, folder: Path) -> None:
        self.art = art
        self._folder = folder

    def open(self, ctx, settings, *, subject: str = ""):
        return self.art

    def folder(self, data_dir: Path) -> Path:
        return self._folder

    def path(self, key: str, data_dir: Path):
        return None


def _painting() -> bytes:
    """A creature on off-white paper, as a painter returns it."""
    im = Image.new("RGB", (200, 200), (246, 242, 232))
    for y in range(60, 150):
        for x in range(50, 140):
            im.putpixel(
                (x, y),
                (40, 90, 160)
                if (x - 95) ** 2 + (y - 105) ** 2 < 40**2
                else (246, 242, 232),
            )
    for y in range(95, 110):  # a white belly inside the creature stays opaque
        for x in range(85, 105):
            im.putpixel((x, y), (250, 248, 240))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=95)
    return out.getvalue()


def test_ai_pending_shows_the_artwork_meanwhile(tmp_path: Path) -> None:
    art = StubArt({})
    with patch.object(server, "ART", StubCollection(art, tmp_path / "paintings")):
        d = _fetch(
            tmp_path, source="list", list="25, 6", pictures="gen", style="ukiyoe"
        )
    assert [k for k, _ in art.requests] == [
        "0025_pikachu--ukiyoe--p1",
        "0006_charizard--ukiyoe--p1",
    ]
    payload = art.requests[0][1]
    assert (
        payload["artwork"].endswith("/official-artwork/25.png")
        and payload["style"] == "ukiyoe"
    )
    assert all(s["pic"]["kind"] == "artwork" for s in d["species"])
    assert d["live_refresh_s"] == 6 and d["live_pending"] == 2
    assert d["status"] and art.finished


def test_ai_ready_uses_the_painting_cut_to_the_creature(tmp_path: Path) -> None:
    key = server.painting_key(25, "pikachu", "engraving")
    art = StubArt({key: _painting()})
    with patch.object(server, "ART", StubCollection(art, tmp_path / "paintings")):
        d = _fetch(tmp_path, source="list", list="25, 6", pictures="gen")
    pika, zard = d["species"]
    assert pika["pic"]["kind"] == "painting" and pika["pic"]["multiply"] is True
    assert pika["pic"]["cut"] is True
    assert zard["pic"]["kind"] == "artwork"
    assert (
        d["credits"][0]
        == "AI painting (Nano Banana on fal.ai) from the official artwork"
    )
    assert d["credits"][-1] == server.CREDIT
    cut = Image.open(tmp_path / "paintings" / f"{key}__cut.png")
    assert cut.mode == "RGBA"
    assert cut.size[0] < 140 and cut.size[1] < 140  # cropped to the creature
    a = cut.getchannel("A")
    assert a.getpixel((0, 0)) == 0  # the paper is gone
    cx, cy = cut.size[0] // 2, cut.size[1] // 2
    assert a.getpixel((cx, cy)) == 255  # the white belly is kept


def test_cut_leaves_a_full_frame_picture_alone() -> None:
    im = Image.new("RGB", (100, 100), (30, 120, 60))
    out = io.BytesIO()
    im.save(out, "PNG")
    assert server.cut_to_creature(out.getvalue()) is None


def test_prompt_and_paint() -> None:
    text = server.prompt_for({"style": "engraving", "name": "Pikachu"})
    for words in (
        "Pikachu",
        "whole creature",
        "alone",
        "true colours",
        "proportions",
        "plain pure white paper",
        "no background",
        "no text",
        "no border",
        "no shadow",
        "Victorian",
    ):
        assert words in text
    assert "black ink only" in server.prompt_for({"style": "sumie", "name": "x"})
    assert "black ink only" in server.prompt_for({"style": "line", "name": "x"})
    assert (
        "black ink only"
        not in server.prompt_for({"style": "linocut", "name": "x"}).split("as a")[0]
    )
    assert len(server.STYLES) == 8

    seen = {}

    class Fal:
        def generate(self, model, prompt, refs=(), aspect=""):
            seen.update(model=model, prompt=prompt, refs=list(refs), aspect=aspect)
            return b"picture"

    job = SimpleNamespace(
        payload={
            "style": "watercolour",
            "name": "Eevee",
            "artwork": "https://x/133.png",
            "id": 133,
            "model": "nano_banana",
        },
        fal=Fal(),
    )
    raw, meta = server.paint(job)
    assert (
        raw == b"picture"
        and seen["refs"] == ["https://x/133.png"]
        and seen["model"] == "nano_banana"
    )
    assert (
        meta["credit"]
        == "AI painting (Nano Banana on fal.ai) from the official artwork"
    )


def test_old_host_says_why(tmp_path: Path) -> None:
    with patch.object(server, "ART", server._NoCollection()):
        d = _fetch(tmp_path, source="list", list="25", pictures="gen")
    assert d["state"] == "ok" and server.NEEDS_HOST in d["notices"]
    assert d["species"][0]["pic"]["kind"] == "artwork"


def test_sample_data_has_no_pictures() -> None:
    d = server.sample_data()
    assert (
        d["state"] == "ok"
        and d["species"]
        and all(s["pic"] is None for s in d["species"])
    )
    assert server.CREDIT in d["credits"]
