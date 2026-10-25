// A QR code, for pairing a phone (Settings, Phone and tablet): text in byte mode, error correction M (a code read off a
// screen loses little), versions 1-10 (up to 213 bytes: an address with a pairing code is about 45). Written out from
// the standard (ISO/IEC 18004) so the Gallery needs no library for it; tests compare it, module for module, with segno's.

// error correction M, per version: [EC codewords per block, blocks in group 1, data codewords each, blocks in group 2 (one more each)]
const M = [null, [10, 1, 16, 0], [16, 1, 28, 0], [26, 1, 44, 0], [18, 2, 32, 0], [24, 2, 43, 0], [16, 4, 27, 0], [18, 4, 31, 0],
  [22, 2, 38, 2], [22, 3, 36, 2], [26, 4, 43, 1]];
const ALIGN = [null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]];
const MASKS = [
  (x, y) => (x + y) % 2 === 0, (x, y) => y % 2 === 0, (x) => x % 3 === 0, (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0, (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
  (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0, (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
];

// ---------------------------------------------------------------- Reed-Solomon over GF(256), x^8 + x^4 + x^3 + x^2 + 1
const EXP = new Array(512), LOG = new Array(256);
for (let i = 0, x = 1; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11d; }
for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
const mul = (a, b) => (a && b ? EXP[LOG[a] + LOG[b]] : 0);

function ecc(data, n) {
  let gen = [1];  // (x - a^0)(x - a^1)...(x - a^(n-1))
  for (let i = 0; i < n; i++) {
    const next = new Array(gen.length + 1).fill(0);
    for (let j = 0; j < gen.length; j++) { next[j] ^= gen[j]; next[j + 1] ^= mul(gen[j], EXP[i]); }
    gen = next;
  }
  const rem = new Array(n).fill(0);
  for (const b of data) {
    const f = b ^ rem.shift();
    rem.push(0);
    for (let j = 0; j < n; j++) rem[j] ^= mul(gen[j + 1], f);
  }
  return rem;
}

// ---------------------------------------------------------------- the codewords
const capacity = (v) => { const [, b1, d1, b2] = M[v]; return b1 * d1 + b2 * (d1 + 1); };

function codewords(bytes, v) {
  const bits = [];
  const put = (val, n) => { for (let i = n - 1; i >= 0; i--) bits.push((val >>> i) & 1); };
  put(0b0100, 4);  // byte mode
  put(bytes.length, v < 10 ? 8 : 16);
  for (const b of bytes) put(b, 8);
  const cap = capacity(v) * 8;
  put(0, Math.min(4, cap - bits.length));  // the terminator
  while (bits.length % 8) bits.push(0);
  const data = [];
  for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
  for (let pad = 0xec; data.length < capacity(v); pad ^= 0xec ^ 0x11) data.push(pad);
  // split into blocks, each with its error correction, then interleaved
  const [n, b1, d1, b2] = M[v];
  const blocks = [];
  for (let i = 0, k = 0; i < b1 + b2; i++) {
    const len = i < b1 ? d1 : d1 + 1;
    blocks.push(data.slice(k, k + len));
    k += len;
  }
  const eccs = blocks.map((b) => ecc(b, n));
  const out = [];
  for (let i = 0; i <= d1; i++) for (const b of blocks) if (i < b.length) out.push(b[i]);
  for (let i = 0; i < n; i++) for (const e of eccs) out.push(e[i]);
  return out;
}

// ---------------------------------------------------------------- the matrix
function build(v, words, mask, info = true) {
  const size = 17 + 4 * v;
  const mod = Array.from({ length: size }, () => new Array(size).fill(false));
  const fn = Array.from({ length: size }, () => new Array(size).fill(false));
  const set = (x, y, dark) => { mod[y][x] = dark; fn[y][x] = true; };
  for (let i = 0; i < size; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }  // timing
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {  // finders, with their separators
    for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
      const x = cx + dx, y = cy + dy, d = Math.max(Math.abs(dx), Math.abs(dy));
      if (x >= 0 && x < size && y >= 0 && y < size) set(x, y, d !== 2 && d !== 4);
    }
  }
  const al = ALIGN[v];
  for (let i = 0; i < al.length; i++) for (let j = 0; j < al.length; j++) {
    if ((i === 0 && j === 0) || (i === 0 && j === al.length - 1) || (i === al.length - 1 && j === 0)) continue;  // (under a finder)
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(al[i] + dx, al[j] + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
  }
  format(info ? set : (x, y) => set(x, y, false), size, 0);  // (reserved now, written with the real mask below)
  if (v >= 7 && info) {
    let rem = v;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
    const bits = (v << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const dark = ((bits >>> i) & 1) === 1, a = size - 11 + (i % 3), b = Math.floor(i / 3);
      set(a, b, dark);
      set(b, a, dark);
    }
  } else if (v >= 7) {
    for (let i = 0; i < 18; i++) { set(size - 11 + (i % 3), Math.floor(i / 3), false); set(Math.floor(i / 3), size - 11 + (i % 3), false); }
  }
  // the codewords, two columns at a time, up and down from the bottom right
  let k = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j, y = ((right + 1) & 2) === 0 ? size - 1 - vert : vert;
        if (!fn[y][x] && k < words.length * 8) { mod[y][x] = ((words[k >>> 3] >>> (7 - (k & 7))) & 1) === 1; k++; }
      }
    }
  }
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (!fn[y][x] && MASKS[mask](x, y)) mod[y][x] = !mod[y][x];
  if (info) format(set, size, mask);  // (a mask is judged before its format and version information is in: as segno does)
  return mod;
}

function format(set, size, mask) {
  const data = (0b00 << 3) | mask;  // (M is 00)
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  const bits = ((data << 10) | rem) ^ 0x5412;
  const bit = (i) => ((bits >>> i) & 1) === 1;
  for (let i = 0; i <= 5; i++) set(8, i, bit(i));
  set(8, 7, bit(6));
  set(8, 8, bit(7));
  set(7, 8, bit(8));
  for (let i = 9; i < 15; i++) set(14 - i, 8, bit(i));
  for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(i));
  for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(i));
  set(8, size - 8, true);  // the dark module (there however a mask is judged)
}

// how hard a mask makes it to read (the standard's four rules): the lowest wins
function penalty(m) {
  const size = m.length;
  let p = 0;
  for (const line of [m, m[0].map((_, x) => m.map((row) => row[x]))]) {  // rows, then columns
    for (const row of line) {
      for (let i = 0, run = 1; i < size; i++) {
        if (i + 1 < size && row[i + 1] === row[i]) run++;
        else { if (run >= 5) p += 3 + run - 5; run = 1; }
      }
      // 1:1:3:1:1, with 4 light modules (or the edge) before or after it
      const s = row.map((d) => (d ? 1 : 0)).join("");
      for (let i = s.indexOf("1011101"); i >= 0;) {
        const before = s.slice(Math.max(i - 4, 0), i), after = s.slice(i + 7, i + 11);
        if (i === 0 || i === size - 7 || !before.includes("1") || !after.includes("1")) { p += 40; i = s.indexOf("1011101", i + 7); }
        else i = s.indexOf("1011101", i + 4);
      }
    }
  }
  for (let y = 0; y < size - 1; y++) for (let x = 0; x < size - 1; x++) {
    const c = m[y][x];
    if (c === m[y][x + 1] && c === m[y + 1][x] && c === m[y + 1][x + 1]) p += 3;
  }
  const dark = m.reduce((a, row) => a + row.filter(Boolean).length, 0);
  return p + 10 * Math.floor(Math.abs((dark * 100) / (size * size) - 50) / 5);
}

// -> {version, mask, size, modules: rows of true (dark) / false}; version and mask only to compare with another encoder
export function qr(text, { version = null, mask = null } = {}) {
  const bytes = [...new TextEncoder().encode(String(text))];
  const v = version || [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].find((n) => capacity(n) * 8 >= 4 + (n < 10 ? 8 : 16) + bytes.length * 8);
  if (!v) throw new Error("Too long for a QR code here");
  const words = codewords(bytes, v);
  let best = null;
  for (const k of mask == null ? [0, 1, 2, 3, 4, 5, 6, 7] : [mask]) {
    const score = mask == null ? penalty(build(v, words, k, false)) : 0;
    if (!best || score < best.score) best = { version: v, mask: k, score };
  }
  const modules = build(v, words, best.mask);
  return { version: v, mask: best.mask, size: modules.length, modules };
}

// an SVG of it, with its quiet zone (dark on white, as scanners expect, whatever the theme)
export function qrSvg(text, px = 6) {
  const { size, modules } = qr(text);
  const q = 4, n = size + 2 * q;
  let d = "";
  modules.forEach((row, y) => row.forEach((dark, x) => { if (dark) d += `M${x + q} ${y + q}h1v1h-1z`; }));
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n} ${n}" width="${n * px}" height="${n * px}" shape-rendering="crispEdges" role="img">`
    + `<rect width="${n}" height="${n}" style="fill:#fff;stroke:none"/><path d="${d}" style="fill:#000;stroke:none"/></svg>`;  // (inline: no stylesheet's icon rules apply)
}
