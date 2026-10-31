/* Pokédex Plate: Pokémon cut out and packed onto one printed page. The server (server.py) sends
   each Pokémon with its picture already cut out (a pixel sprite, the official artwork or a
   painting); this file reads each picture's outline, packs the page with static/pack.js (the
   Specimen Sheet's packer), then sets the pictures and the captions on it as plain elements.
   Pixel sprites are drawn at whole-number scales with no smoothing, so they stay crisp. */
import { packSheet, measureFont } from './static/pack.js';
import { loadImage, maskOf, ovalMask } from './static/cut.js';

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const PAPER = { cream: '#f3ead6', white: '#ffffff', aged: '#e9dcc0' };
// The caption fonts, as the page sets them and as the packer measures them.
const CAP_SERIF = "Georgia, 'Noto Serif', 'Noto Serif CJK JP', serif";
const CAP_SANS = "Arial, 'Helvetica Neue', 'Noto Sans', 'Noto Sans CJK JP', sans-serif";

function head(d) {
  return '<div class="hd"><span class="hd-t">' + esc(d.title) + '</span><span class="hd-p">' + esc(d.plate || '') + '</span></div><div class="hd-rule"></div>';
}

function foot(d) {
  const bits = [...(d.notices || []), ...(d.status || []), ...(d.credits || [])];
  return bits.length ? '<div class="credit">' + bits.map(esc).join(' · ') + '</div>' : '';
}

function message(d, title, body) {
  return head({ ...d, title: d.title || title }) + '<div class="msg"><i class="ph ph-game-controller msg-i"></i><div class="msg-b">' + esc(body) + '</div></div>' + foot(d);
}

/* The caption size for a page: about three hundredths of its short side, larger when few. */
function captionPx(W, H, n) {
  const k = n <= 3 ? 0.04 : n <= 8 ? 0.034 : 0.03;
  return Math.max(11, Math.min(26, Math.round(Math.min(W, H) * k)));
}

/* Each Pokémon's outline and size for the packer. Sized by height, the longest side of the
   picture stands for the Pokémon's height. */
async function specimens(d) {
  return Promise.all(d.species.map(async (s, i) => {
    let img = null, mask = null;
    if (s.pic && s.pic.src) {
      try { img = await loadImage(s.pic.src); mask = maskOf(img, s.pic.pixel ? 1000 : 480); } catch { img = null; }
    }
    if (!mask) mask = ovalMask(1);
    const long = Math.max(mask.w, mask.h);
    // sprites are all drawn on one 96 px canvas in the games, so "the same size" keeps them at
    // one pixel scale; other pictures get about the same area each
    const factor = d.sizing === 'true' ? (Number(s.size) || 1) / long : s.pic && s.pic.pixel && img ? 1 : 60 / Math.sqrt(mask.w * mask.h);
    return { i, s, img, mask, factor, name: s.name, sci: String(s.id), lines: s.lines || [], flippable: false, face: null };
  }));
}

/* Where a picture goes inside its packed box. A pixel sprite takes the largest whole-number
   scale that fits (a few hundredths over is allowed: the packer leaves air round every body), centred
   on whole pixels; anything else fills the box. */
function fitted(it, p) {
  const pic = it.s.pic;
  if (!pic || !it.img) return { x: p.x, y: p.y, w: p.w, h: p.h };
  if (!pic.pixel) return { x: p.x, y: p.y, w: p.w, h: p.h };
  const nw = it.img.naturalWidth, nh = it.img.naturalHeight;
  const k = Math.min(p.w / nw, p.h / nh);
  // smaller than its own size (a crowded small cell): scaled down as it must be
  const f = k < 1 ? k : Math.floor(k + 0.04);
  const w = nw * f, h = nh * f;
  return { x: Math.round(p.x + (p.w - w) / 2), y: Math.round(p.y + (p.h - h) / 2), w, h, f };
}

function captionOf(lines) {
  return lines.map((t, i) => '<span class="ln' + (i ? ' sm' : '') + '">' + esc(t).replace(/\n/g, '<br>') + '</span>').join('');
}

async function sheet(shadow, d) {
  const area = shadow.querySelector('.sh');
  if (!area) return;
  const W = Math.floor(area.clientWidth), H = Math.floor(area.clientHeight);
  if (W < 40 || H < 40) return;
  const items = await specimens(d);
  if (d.panel === 'mono') measureFont(CAP_SANS, '600'); else measureFont(CAP_SERIF, '400');
  const px = captionPx(W, H, items.length);
  const label = px / H;
  const packer = ['spiral', 'voids'].includes(d.packing) ? d.packing : 'balanced';
  // Otherwise a long budget: the growing stops when nothing grows, so the page is the same
  // every time.
  // Sized by height, or sprites at one pixel scale: no growing, so the sizes keep their ratios.
  const fixed = d.sizing === 'true' || d.pictures === 'sprite';
  const placed = packSheet(items, W, H, label, { facing: 'painted', packer, budget: fixed ? 0 : 60000 });
  let out = '';
  items.forEach((it, k) => {
    const p = placed[k];
    if (!p) return;
    const r = fitted(it, p);
    const pic = it.s.pic;
    const box = 'left:' + r.x.toFixed(1) + 'px;top:' + r.y.toFixed(1) + 'px;width:' + r.w.toFixed(1) + 'px;height:' + r.h.toFixed(1) + 'px';
    if (pic && it.img) {
      const cls = 'pk' + (pic.pixel ? ' px' : '') + (pic.multiply ? ' mul' : '');
      out += '<img class="' + cls + '" src="' + pic.src + '" alt="' + esc(it.s.name) + '" style="' + box + '" decoding="sync">';
    } else {
      const icon = Math.max(14, Math.round(Math.min(r.w, r.h) * 0.4));
      out += '<div class="mount" style="' + box + '"><i class="ph ph-sparkle" style="font-size:' + icon + 'px"></i></div>';
    }
    const lines = p.lines || [];
    if (lines.length) {
      // the caption keeps its place under the picture when a sprite was drawn smaller than its box
      const cy = p.cap[1] - (p.y + p.h - (r.y + r.h));
      out += '<div class="cap" style="left:' + p.cap[0].toFixed(1) + 'px;top:' + cy.toFixed(1) + 'px;font-size:' + px + 'px">' + captionOf(lines) + '</div>';
    }
  });
  area.innerHTML = out;
  await Promise.all([...area.querySelectorAll('img')].map((img) => (img.decode ? img.decode().catch(() => {}) : null)));
}

export default async function render(shadow, ctx) {
  const d = { ...((ctx && ctx.data) || {}) };
  d.species = Array.isArray(d.species) ? d.species : [];
  const size = (ctx && ctx.cell && ctx.cell.size) || 'md';
  let body;
  if (!d.state) body = message({ title: 'Pokédex Plate' }, 'Pokédex Plate', 'No data from the server yet.');
  else if (d.state === 'error' || !d.species.length) body = message(d, 'Pokédex Plate', d.error || 'Nothing to show.');
  else body = head(d) + '<div class="sh"></div>' + foot(d);
  paint(shadow, d, size, body);
  if (d.state === 'ok' && d.species.length) {
    try { await sheet(shadow, d); } catch (e) { const a = shadow.querySelector('.sh'); if (a) a.innerHTML = '<div class="msg"><div class="msg-b">' + esc(String((e && e.message) || e)) + '</div></div>'; }
  }
}

function paint(shadow, d, size, body) {
  // a panel without colour prints on white: a tinted paper would only come out as a dither
  const paper = (d.panel || 'colour') === 'colour' ? PAPER[d.paper] || PAPER.cream : '#ffffff';
  shadow.innerHTML = '<div class="pp size-' + esc(size) + ' lv-' + esc(d.panel || 'colour') + '" style="--paper:' + paper + '">' + body + '</div>' + STYLE;
}

const STYLE = `<style>
  :host { display: block; height: 100%; }
  .pp { container-type: size; height: 100%; width: 100%; overflow: hidden; position: relative; display: flex; flex-direction: column;
    background: var(--paper); color: #2a2116; --ink: #2a2116; --serif: 'Iowan Old Style', 'Palatino Linotype', Palatino, Georgia, 'Noto Serif', 'Noto Serif CJK JP', serif;
    font-family: var(--serif); font-variant-numeric: tabular-nums; }
  .lv-gray16, .lv-gray4, .lv-mono { color: #000; --ink: #000; }
  .lv-mono { --serif: 'Helvetica Neue', Arial, 'Noto Sans', 'Noto Sans CJK JP', sans-serif; }
  .hd { display: flex; justify-content: space-between; align-items: baseline; gap: 2cqmin; padding: max(6px, 1.6cqmin) max(10px, 3cqmin) max(3px, .6cqmin); flex: 0 0 auto; }
  .hd-t { font-size: max(13px, 3.6cqmin); font-variant: small-caps; letter-spacing: .1em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .hd-p { font-size: max(11px, 2.8cqmin); font-style: italic; letter-spacing: .04em; white-space: nowrap; }
  .hd-rule { border-top: 1.5px solid var(--ink); position: relative; margin: 0 max(10px, 3cqmin); flex: 0 0 auto; }
  .hd-rule::after { content: ""; position: absolute; left: 0; right: 0; top: 2px; border-top: 1px solid var(--ink); }
  .credit { flex: 0 0 auto; font-size: max(8px, 1.6cqmin); line-height: 1.3; opacity: .7; padding: max(2px, .4cqmin) max(10px, 3cqmin) max(4px, 1cqmin); font-style: italic; text-align: center;
    display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden; }
  .lv-mono .credit, .lv-gray4 .credit { opacity: 1; }
  .sh { flex: 1; min-height: 0; position: relative; margin: 0 max(4px, 1cqmin); }
  .pk { position: absolute; display: block; }
  .pk.px { image-rendering: pixelated; image-rendering: crisp-edges; }
  .pk.mul { mix-blend-mode: multiply; }
  .mount { position: absolute; display: flex; align-items: center; justify-content: center; border: 1.5px dashed var(--ink); border-radius: 50%; opacity: .45; box-sizing: border-box; }
  .cap { position: absolute; transform: translate(-50%, 6%); white-space: nowrap; text-align: center; line-height: 1.12; color: var(--ink); font-family: ${CAP_SERIF}; }
  .lv-mono .cap { font-family: ${CAP_SANS}; font-weight: 600; }
  .ln { display: block; letter-spacing: .02em; }
  .ln.sm { font-style: italic; font-size: .82em; opacity: .85; letter-spacing: 0; }
  .lv-mono .ln.sm, .lv-gray4 .ln.sm { opacity: 1; font-style: normal; }
  .msg { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2cqmin; padding: 4cqmin 6cqmin; text-align: center; }
  .msg-i { font-size: 12cqmin; opacity: .5; }
  .msg-b { font-size: max(12px, 3.4cqmin); }
</style>`;
