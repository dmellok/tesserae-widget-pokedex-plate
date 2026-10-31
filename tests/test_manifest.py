"""The options panel stays short: a handful of options up front, the rest
nested under the choice they depend on or under More options."""

from __future__ import annotations

import json
from pathlib import Path

import server

MANIFEST = json.loads(
    (Path(__file__).resolve().parent.parent / "plugin.json").read_text(encoding="utf-8")
)
OPTIONS = MANIFEST["cell_options"]

# Saved cells key on these names; the order is how the panel reads.
ORDER = [
    "source",
    "generation",
    "poke_type",
    "list",
    "count",
    "order",
    "pictures",
    "style",
    "sizing",
    "labels",
    "packing",
    "names",
    "paper",
    "panel",
    "model",
    "title",
    "credits",
]

UP_FRONT = ["source", "pictures", "sizing", "labels"]


def _up_front(o: dict) -> bool:
    return not o.get("show_if") and not o.get("advanced")


def _opt(name: str) -> dict:
    return next(o for o in OPTIONS if o["name"] == name)


def _values(name: str) -> list[str]:
    return [c["value"] for c in _opt(name)["choices"]]


def test_manifest_basics() -> None:
    assert MANIFEST["name"] == "Pokédex Plate"
    assert MANIFEST["kind"] == "widget"
    assert MANIFEST["version"] == server.VERSION == "0.2.0"
    assert MANIFEST["tesserae_compat"] == "1.x"
    assert MANIFEST["tesserae_min"] == "0.463.0"
    assert MANIFEST["icon"].startswith("ph-")
    assert MANIFEST["requires"] == ["network:*"]
    assert MANIFEST["render"]["needs_network"] is True
    assert MANIFEST["render"]["warm_ahead_s"] == 150
    assert MANIFEST["supports"]["sizes"] == ["sm", "md", "lg"]
    assert len(MANIFEST["description"]) <= 280


def test_option_names_and_order() -> None:
    assert [o["name"] for o in OPTIONS] == ORDER


def test_few_options_up_front() -> None:
    assert [o["name"] for o in OPTIONS if _up_front(o)] == UP_FRONT
    assert len(UP_FRONT) <= 5


def test_no_option_named_label() -> None:
    assert "label" not in {o["name"] for o in OPTIONS}


def test_show_if_refers_to_real_options() -> None:
    names = {o["name"] for o in OPTIONS}
    for o in OPTIONS:
        for key in o.get("show_if") or {}:
            assert key in names, (o["name"], key)


def test_advanced_options_come_last() -> None:
    seen = False
    for o in OPTIONS:
        if o.get("advanced"):
            seen = True
        else:
            assert not seen, f"{o['name']} follows an advanced option"


def test_choices_match_the_server() -> None:
    assert _values("source") == list(server.SOURCES)
    assert _values("order") == list(server.ORDERS)
    assert _values("pictures") == list(server.PICTURES)
    assert _values("sizing") == list(server.SIZINGS)
    assert _values("packing") == list(server.PACKINGS)
    assert _values("paper") == list(server.PAPERS)
    assert _values("panel") == list(server.LEVELS)
    assert _values("model") == list(server.MODELS)
    assert _values("style") == list(server.STYLES)
    assert _values("poke_type") == list(server.TYPES)
    assert _values("names") == list(server.LANGS)
    assert _values("labels") == list(server.LABELS)
    assert _values("generation") == [str(n) for n in range(1, 10)]
    # every default is one of its choices, and the server's first choice
    for o in OPTIONS:
        if o["type"] == "select":
            assert o["default"] in _values(o["name"]), o["name"]
    for name in ("source", "order", "pictures", "sizing", "packing", "paper", "panel"):
        assert _opt(name)["default"] == _values(name)[0], name
    assert _opt("pictures")["default"] == "sprite"
    assert _opt("count")["max"] == server.COUNT_MAX
    assert _opt("count")["default"] == server.COUNT_DEFAULT


def test_settings_block() -> None:
    names = [s["name"] for s in MANIFEST["settings"]]
    assert names == ["fal_api_key", "gen_daily_usd", "gen_per_refresh"]
    key = MANIFEST["settings"][0]
    assert key["secret"] is True and key["default"] == ""
    budget = MANIFEST["settings"][1]
    assert budget["type"] == "slider" and budget["default"] == 2


def test_no_em_dashes_anywhere() -> None:
    root = Path(__file__).resolve().parent.parent
    for path in [
        root / "plugin.json",
        root / "README.md",
        root / "server.py",
        root / "client.js",
    ]:
        text = path.read_text(encoding="utf-8")
        assert chr(0x2014) not in text, path.name
    text = json.dumps(MANIFEST, ensure_ascii=False).lower()
    for word in ("first-class", "tinkerers", "seamless", "powerful", "unlike "):
        assert word not in text


def test_client_imports_only_from_static() -> None:
    """Tesserae serves client.js, client.css and files under static/ or files/ only."""
    import re

    root = Path(__file__).resolve().parent.parent
    for path in [root / "client.js", *sorted((root / "static").glob("*.js"))]:
        for spec in re.findall(
            r"""from\s+['"]([^'"]+)['"]""", path.read_text(encoding="utf-8")
        ):
            target = (path.parent / spec).resolve()
            assert target.exists(), (path.name, spec)
            assert (root / "static") in target.parents, (path.name, spec)


def test_nothing_bundled_but_code() -> None:
    root = Path(__file__).resolve().parent.parent
    pictures = [
        p
        for p in root.rglob("*")
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        and "screenshots" not in p.parts
        and "out" not in p.parts
        and ".git" not in p.parts
    ]
    assert pictures == []
