/* Pictures into packing masks. The server sends every picture already cut out (a sprite or the
   official artwork with its own alpha, a painting cut to the creature), so a mask is the
   picture's alpha and nothing is guessed. Adapted from the Specimen Sheet widget's cut.js. */

export function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('picture would not decode'));
    img.src = src;
  });
}

/* A picture's alpha as a 0/1 mask, at most ``cap`` pixels on its longest side. A pixel sprite
   is read at its own size, so a one-pixel limb still counts. */
export function maskOf(img, cap = 480) {
  const nw = img.naturalWidth || img.width, nh = img.naturalHeight || img.height;
  const k = Math.min(1, cap / Math.max(nw, nh));
  const w = Math.max(1, Math.round(nw * k)), h = Math.max(1, Math.round(nh * k));
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.imageSmoothingEnabled = k < 1;
  ctx.drawImage(img, 0, 0, w, h);
  const px = ctx.getImageData(0, 0, w, h).data;
  const data = new Uint8Array(w * h);
  let ink = 0;
  for (let i = 0; i < w * h; i++) if (px[i * 4 + 3] > 96) { data[i] = 1; ink++; }
  return ink ? { w, h, data } : ovalMask(nw / Math.max(1, nh));
}

/* An oval for a Pokémon with no picture: the room it would take. */
export function ovalMask(aspect = 1, size = 60) {
  const w = Math.max(4, Math.round(size * aspect)), h = size;
  const data = new Uint8Array(w * h);
  const cx = w / 2, cy = h / 2, rx = w * 0.48, ry = h * 0.48;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const dx = (x + 0.5 - cx) / rx, dy = (y + 0.5 - cy) / ry;
    data[y * w + x] = dx * dx + dy * dy <= 1 ? 1 : 0;
  }
  return { w, h, data };
}
