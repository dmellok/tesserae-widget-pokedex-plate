"""Render the widget as a page would: server.fetch() for the data, client.js in a browser.

    PYTHONPATH=../tesserae python tools/preview.py [--w 800 --h 480] [--tag name] [--out dir] [key=value ...]

Options are the cell's (source=type poke_type=water count=12); values starting with [ or {
are JSON. The screenshot lands in tools/out/preview_<tag>.png (or --out) and the page's
console goes to stdout. Phosphor's stylesheet comes from a Tesserae checkout beside this
one (--tesserae), as the host would put it in the widget's shadow root.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import importlib.util
import json
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"


def main() -> None:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--w", type=int, default=800)
    ap.add_argument("--h", type=int, default=480)
    ap.add_argument("--size", default="lg")
    ap.add_argument("--tag", default="demo")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--data", default=str(OUT / "data"))
    ap.add_argument("--tesserae", default=str(ROOT.parent / "tesserae"))
    ap.add_argument("--sample", action="store_true", help="the no-network sample sheet")
    ap.add_argument("pairs", nargs="*")
    a = ap.parse_args()
    options: dict = {}
    for pair in a.pairs:
        k, _, v = pair.partition("=")
        options[k] = json.loads(v) if v[:1] in "[{" else v
    spec = importlib.util.spec_from_file_location("pp_server", ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["pp_server"] = server
    spec.loader.exec_module(server)  # type: ignore[union-attr]
    data_dir = Path(a.data)
    data_dir.mkdir(parents=True, exist_ok=True)
    ctx = {"data_dir": str(data_dir), "cell_w": a.w, "cell_h": a.h, "settled": True}
    data = (
        server.sample_data(options) if a.sample else server.fetch(options, {}, ctx=ctx)
    )
    print(
        "state",
        data["state"],
        "species",
        len(data.get("species") or []),
        data.get("notices"),
    )
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    phosphor = (
        Path(a.tesserae) / "static" / "vendor" / "phosphor" / "phosphor-regular.css"
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:
            if path.startswith("/__phosphor.css"):
                return str(phosphor)
            if path.startswith("/__data.json"):
                return str(OUT / f"preview_{a.tag}.json")
            return super().translate_path(path)

        def log_message(self, *x: object) -> None:
            return None

    handler = functools.partial(Handler, directory=str(ROOT))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    html = f"""<!doctype html><html><body style="margin:0;background:#888"><div id="cell" style="width:{a.w}px;height:{a.h}px"></div>
    <script type="module">
      const m = await import('/client.js');
      const host = document.getElementById('cell');
      const shadow = host.attachShadow({{ mode: 'open' }});
      const css = await (await fetch('/__phosphor.css')).text();
      const sheet = new CSSStyleSheet(); sheet.replaceSync(css);
      document.adoptedStyleSheets = [sheet]; shadow.adoptedStyleSheets = [sheet];
      await document.fonts.ready;
      const data = await (await fetch('/__data.json')).json();
      const t = performance.now();
      await m.default(shadow, {{ data, cell: {{ w: {a.w}, h: {a.h}, size: '{a.size}' }} }});
      window.__done = performance.now() - t;
    </script></body></html>"""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"preview_{a.tag}.json").write_text(json.dumps(data), encoding="utf-8")
    (OUT / f"preview_{a.tag}.html").write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": a.w, "height": a.h}, device_scale_factor=1)
        pg.on("console", lambda msg: print("  LOG", msg.type, msg.text[:200]))
        pg.on("pageerror", lambda e: print("  PAGEERROR", str(e)[:300]))
        pg.goto(f"http://127.0.0.1:{port}/tools/out/preview_{a.tag}.html")
        pg.wait_for_function("window.__done !== undefined", timeout=180000)
        print("render ms", round(pg.evaluate("window.__done")))
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out / f"{a.tag}.png"))
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
