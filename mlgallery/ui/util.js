// Model Librarian Gallery: small helpers shared by the screens (elements, the API, formatting).

export const TOKEN = document.querySelector('meta[name="mlg-token"]').content;
export const $ = (id) => document.getElementById(id);

// what this library is for: one or more of ai, photos, illustrations (null until it's been asked); it decides what the screens show
export const lib = { kinds: null };
export const has = (kind) => (lib.kinds || ["ai"]).includes(kind);  // not asked yet: as before, AI generations
export const aiOnly = () => !lib.kinds || lib.kinds.join() === "ai";
// what one file is, from what's been read of it (as the server's library.FILE_IS): generation data, else a date taken, else neither
export const fileIs = (f) => (f.meta === 1 ? "ai" : f.taken ? "photo" : "illustration");
export const KINDS = [
  ["ai", "AI generations", "Images and videos you generate: what made each one, models and LoRAs, characters."],
  ["photos", "Photos", "Pictures from your phone or camera, and scans: grouped by the day they were taken, with People."],
  ["illustrations", "Illustrations", "Art you've downloaded or made yourself, with Characters."],
];
// People in a photo library, Characters in the others, both in a library for photos and more: one list underneath,
// each entry a person or a character
export const mixedWho = () => has("photos") && (has("ai") || has("illustrations"));
export function who(many = true, capital = true) {
  const w = mixedWho() ? (many ? "people & characters" : "person or character")
    : has("photos") ? (many ? "people" : "person") : (many ? "characters" : "character");
  return capital ? w[0].toUpperCase() + w.slice(1) : w;
}

// append, prepend and replaceChildren skip a child that's left out (null or false), as h() does, instead of
// writing "null" on the screen
for (const m of ["append", "prepend", "replaceChildren"]) {
  const real = Element.prototype[m];
  Element.prototype[m] = function (...kids) { return real.apply(this, kids.filter((k) => k != null && k !== false)); };
}

// h("div.cls", {props}, ...children): text is always set as text, never as HTML
export function h(tag, props, ...kids) {
  const [name, ...classes] = tag.split(".");
  const el = document.createElement(name || "div");
  if (classes.length) el.className = classes.join(" ");
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") Object.assign(el.style, v);
    else if (k in el) el[k] = v;
    else el.setAttribute(k, v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}

export const SVG = {
  star: '<path d="M12 3.2l2.6 5.5 6 .8-4.4 4.2 1.1 6-5.3-2.9-5.3 2.9 1.1-6L3.4 9.5l6-.8Z"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
  view: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.1-2.4 3.6M12 17h.01"/>',
  send: '<path d="M4 12 20 4l-6 16-3-7Z"/><path d="m11 13 9-9"/>',
  note: '<path d="M5 4h10l4 4v12H5Z"/><path d="M15 4v4h4M8 12h8M8 16h5"/>',
  person: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  move: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5Z"/><path d="M10 13h6M13.5 10.5 16 13l-2.5 2.5"/>',
  album: '<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M7 3h10"/>',
  tag: '<path d="M3 12V4h8l10 10-8 8L3 12Z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
  restore: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/>',
  rescan: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  remove: '<path d="M6 6l12 12M18 6 6 18"/>',
  folder: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5Z"/>',
  play: '<path d="M7 4.5v15l13-7.5Z"/>',
  explorer: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5Z"/><path d="M12 11v5M9.5 13.5 12 16l2.5-2.5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  save: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19h14"/>',
  heart: '<path d="M12 20.5s-7.5-4.6-9.5-9.3C1.2 7.9 3.3 4.5 6.8 4.5c2 0 3.4 1.1 4.2 2.3h2c.8-1.2 2.2-2.3 4.2-2.3 3.5 0 5.6 3.4 4.3 6.7-2 4.7-9.5 9.3-9.5 9.3Z"/>',
  filter: '<path d="M4 5h16l-6.5 7.5V19l-3-1.5v-5Z"/>',
  minus: '<path d="M5 12h14"/>',
  sort: '<path d="M7 4v16M3.5 16.5 7 20l3.5-3.5M13 6h8M13 11h6M13 16h4"/>',
  graph: '<rect x="3" y="4" width="6" height="5" rx="1.2"/><rect x="15" y="4" width="6" height="5" rx="1.2"/><rect x="9" y="15" width="6" height="5" rx="1.2"/><path d="M6 9v2.5h12V9M12 11.5V15"/>',
};
export function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = SVG[name] || SVG_LOOK[name] || ""; // our own constant markup, never data
  return s;
}

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api" + path, {
    method, headers: { "X-MLG-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) throw Object.assign(new Error((data && data.error) || `HTTP ${res.status}`), { status: res.status });
  return data;
}
export const media = (kind, id) => `/api/${kind}/${id}?t=${encodeURIComponent(TOKEN)}`;

let toastTimer = null;
export function toast(text, err = false) {
  document.querySelector(".toast")?.remove();
  const t = h("div.toast" + (err ? ".err" : ""), { role: err ? "alert" : "status" }, text);
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), err ? 7000 : 3500);
}

export const fmtSize = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n > 0 ? `${Math.max(1, Math.round(n / 1e3))} KB` : "0 KB");
export const fmtDur = (s) => (s == null ? "" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
export const fmtDate = (t) => new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
export const fmtNum = (v) => (v == null ? "—" : Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100));
export const plural = (n, one, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;
export const baseName = (p) => p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
export const store = {
  get(k, d) { try { const v = localStorage.getItem("mlg." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("mlg." + k, JSON.stringify(v)); } catch {} },
};
export async function copyText(text, what = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(`${what}.`); } catch { toast("Couldn't copy here: select the text and press Ctrl+C.", true); }
}

export function dayLabel(day) {
  // "2026-10-23" in local time -> Today, Yesterday, a weekday this past week, else the date
  const [y, m, d] = day.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const ago = Math.round((today - date) / 86400000);
  if (ago === 0) return "Today";
  if (ago === 1) return "Yesterday";
  if (ago > 1 && ago < 7) return date.toLocaleDateString(undefined, { weekday: "long" });
  return date.toLocaleDateString(undefined, { day: "numeric", month: "long", ...(y !== today.getFullYear() ? { year: "numeric" } : {}) });
}

// ---------------------------------------------------------------- in-app dialogs (never the browser's grey boxes)
function modal(title, body, { ok = "OK", danger = false, cancel = "Cancel", onok } = {}) {
  return new Promise((resolve) => {
    let result = null;
    const err = h("p.form-err", { role: "alert" });
    const dlg = h("dialog.dialog", { "aria-label": title },
      h("form", { method: "dialog", onsubmit: async (e) => {
        e.preventDefault();
        try {
          result = onok ? await onok() : true;
          if (result === false) return;
          dlg.close();
        } catch (ex) { err.textContent = ex.message; }
      } },
        h("h2", {}, title), body, err,
        h("div.row.end", {}, h("button.btn", { type: "button", onclick: () => dlg.close() }, cancel),
          h("button.btn.primary" + (danger ? ".danger" : ""), { type: "submit" }, ok))));
    dlg.addEventListener("keydown", (e) => { if (e.key !== "Escape") e.stopPropagation(); });
    dlg.addEventListener("close", () => { dlg.remove(); resolve(result); });
    document.body.append(dlg);
    dlg.showModal();
    (dlg.querySelector("input, textarea, select") || dlg.querySelector("button[type=submit]")).focus();
  });
}

// ask(title, text, {ok, danger}) -> true when confirmed
export async function ask(title, text, opts = {}) {
  return !!(await modal(title, h("p.dialog-text", {}, text), opts));
}

// askText(title, label, value) -> the text typed, or null
export async function askText(title, label, value = "", { ok = "OK", placeholder = "" } = {}) {
  const input = h("input", { type: "text", value, placeholder, "aria-label": label, spellcheck: false });
  const got = await modal(title, h("label.field-row", {}, h("span", {}, label), input), { ok, onok: () => input.value.trim() || false });
  return got || null;
}

// a small menu under a button: items are {label, icon?, onclick, danger?} (or null for a divider)
export function menu(anchor, items, { cls = "" } = {}) {
  document.querySelector(".menu")?._close();
  let open = true;
  const close = () => {
    if (!open) return;
    open = false;
    box.remove();
    document.removeEventListener("mousedown", out, true);
    document.removeEventListener("keydown", key, true);
    document.removeEventListener("scroll", moved, true);
    window.removeEventListener("resize", close);
    window.removeEventListener("blur", close);
    anchor.setAttribute("aria-expanded", "false");
    if (anchor.isConnected) anchor.focus();
  };
  const out = (e) => { if (!box.contains(e.target) && e.target !== anchor) close(); };
  const moved = (e) => { if (!box.contains(e.target)) close(); }; // it's placed under its button: scrolling away leaves it behind
  const key = (e) => {
    const all = [...box.querySelectorAll("button")];
    const i = all.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (document.activeElement?.tagName === "SELECT") return; // a select inside uses its own arrows
    else if (e.key === "ArrowDown") { e.preventDefault(); all[(i + 1) % all.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); all[(i - 1 + all.length) % all.length]?.focus(); }
  };
  const box = h("div.menu", { role: "menu" }, items.map((it) => it === null ? h("div.menu-sep", { role: "separator" })
    : it.node ? it.node
    : h("button.menu-item" + (it.danger ? ".danger" : ""), { type: "button", role: "menuitem", onclick: () => { close(); it.onclick(); } },
      it.icon ? icon(it.icon) : h("span.menu-gap"), h("span", {}, it.label), it.hint ? h("kbd", {}, it.hint) : null)));
  if (cls) box.classList.add(cls);
  box.addEventListener("click", (e) => { if (e.target.closest(".menu-item")) close(); }, true); // any item, its own handler runs after
  box._close = close;
  document.body.append(box);
  const r = anchor.getBoundingClientRect();
  const w = box.offsetWidth, ht = box.offsetHeight;
  box.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w))}px`;
  box.style.top = `${r.bottom + 6 + ht > window.innerHeight ? Math.max(8, r.top - ht - 6) : r.bottom + 6}px`;
  anchor.setAttribute("aria-expanded", "true");
  document.addEventListener("mousedown", out, true);
  document.addEventListener("keydown", key, true);
  document.addEventListener("scroll", moved, true);
  window.addEventListener("resize", close);
  window.addEventListener("blur", close);
  box.querySelector("button, input")?.focus();
  return box;
}

// drawn stars for a rating (filled up to n of 5), never the ★ character
export function stars(n, { max = 5, size = 12 } = {}) {
  return h("span.stars-ro", { "aria-label": `Rated ${n} of ${max}`, role: "img" }, Array.from({ length: n }, () => {
    const s = icon("star");
    s.setAttribute("width", size);
    s.setAttribute("height", size);
    return s;
  }));
}

export const closeMenu = () => document.querySelector(".menu")?._close();

// cards in two stacks that each flow on their own (no row-aligned holes); 1st, 3rd… on the left. Narrow: one column, in order
export function stacks(cards) {
  const all = cards.filter(Boolean);
  all.forEach((c, i) => { c.style.order = String(i); });
  return h("div.st-stacks", {}, h("div.st-stack", {}, all.filter((_, i) => i % 2 === 0)), h("div.st-stack", {}, all.filter((_, i) => i % 2 === 1)));
}

// ---------------------------------------------------------------- folder and album icons
export const SVG_LOOK = {
  image: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z"/><circle cx="12" cy="13" r="3.5"/>',
  film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  book: '<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v14H6.5A1.5 1.5 0 0 0 5 18.5Z"/><path d="M5 18.5A1.5 1.5 0 0 0 6.5 20H19v-3"/>',
  bookmark: '<path d="M6 3h12v18l-6-4-6 4Z"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  palette: '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.5-.8 1.2-1.6-.4-1-.1-2.4 1.4-2.4H17a4 4 0 0 0 4-4c0-5.5-4-10-9-10Z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
  brush: '<path d="M18 3 9 12l3 3 9-9a2.1 2.1 0 0 0-3-3Z"/><path d="M9 12c-2.5 0-4 1.5-4 4 0 1.5-1 2.5-2 3 4 1 9 0 9-4Z"/>',
  pen: '<path d="M16 3 21 8 9 20H4v-5Z"/><path d="m13.5 5.5 5 5"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/><path d="M19 16v4M17 18h4"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/>',
  cloud: '<path d="M7 18h10.5a4.5 4.5 0 0 0 .5-9 6 6 0 0 0-11.5 1.5A3.8 3.8 0 0 0 7 18Z"/>',
  leaf: '<path d="M5 19c0-9 5-14 15-15 0 10-5 15-14 15Z"/><path d="M5 19 14 10"/>',
  flower: '<circle cx="12" cy="9" r="2"/><circle cx="12" cy="5" r="2"/><circle cx="16" cy="9" r="2"/><circle cx="12" cy="13" r="2"/><circle cx="8" cy="9" r="2"/><path d="M12 15v6"/>',
  tree: '<path d="M12 3 6 11h3l-4 6h14l-4-6h3Z"/><path d="M12 17v4"/>',
  mountain: '<path d="m3 20 6-10 4 6 2-3 6 7Z"/>',
  flame: '<path d="M12 21c-4 0-6-2.8-6-6 0-4 4-6 4-10 3 2 4 4 4 6 1-1 1.5-2 1.5-3 2 2 2.5 4.5 2.5 7 0 3.2-2 6-6 6Z"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7Z"/>',
  drop: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11Z"/>',
  snow: '<path d="M12 2v20M3.3 7l17.4 10M3.3 17 20.7 7M9 4l3 2 3-2M9 20l3-2 3 2"/>',
  people: '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14a5 5 0 0 1 5 5"/>',
  smile: '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0M9 9.5h.01M15 9.5h.01"/>',
  cat: '<path d="M5 20V8l3-4 3 4h2l3-4 3 4v12Z"/><path d="M9.5 12h.01M14.5 12h.01M10.5 15.5h3"/>',
  paw: '<circle cx="7" cy="10" r="1.8"/><circle cx="10.5" cy="6.5" r="1.8"/><circle cx="14.5" cy="6.5" r="1.8"/><circle cx="18" cy="10" r="1.8"/><path d="M12.5 11.5c-3 0-5.5 3.5-5.5 6 0 1.5 1.2 2.5 2.7 2.5 1 0 1.8-.5 2.8-.5s1.8.5 2.8.5c1.5 0 2.7-1 2.7-2.5 0-2.5-2.5-6-5.5-6Z"/>',
  robot: '<rect x="5" y="8" width="14" height="11" rx="2"/><path d="M12 4.5V8M9 13h.01M15 13h.01M9.5 16h5M3 12v3M21 12v3"/><circle cx="12" cy="3.5" r="1"/>',
  ghost: '<path d="M5 21V10a7 7 0 0 1 14 0v11l-2.5-2-2.3 2-2.2-2-2.2 2-2.3-2Z"/><path d="M9.5 10.5h.01M14.5 10.5h.01"/>',
  skull: '<path d="M12 3a8 8 0 0 0-8 8c0 2.5 1.2 4.3 3 5.5V20h10v-3.5c1.8-1.2 3-3 3-5.5a8 8 0 0 0-8-8Z"/><circle cx="9" cy="11.5" r="1.5"/><circle cx="15" cy="11.5" r="1.5"/><path d="M10 20v-2M14 20v-2"/>',
  gamepad: '<path d="M7 8h10a4 4 0 0 1 4 4v1.5a3.5 3.5 0 0 1-6.3 2.1L14 15h-4l-.7.6A3.5 3.5 0 0 1 3 13.5V12a4 4 0 0 1 4-4Z"/><path d="M7.5 10.5v3M6 12h3M15.5 11.5h.01M17.5 12.5h.01"/>',
  sword: '<path d="M20 4 9 15M20 4v4M20 4h-4M7 13l4 4M5 19l3-3M4 18l2 2"/>',
  shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6Z"/>',
  crown: '<path d="m3 8 4.5 4L12 5l4.5 7L21 8l-2 11H5Z"/>',
  gem: '<path d="M6 3h12l4 6-10 12L2 9Z"/><path d="M2 9h20M12 21 8 9l4-6 4 6Z"/>',
  trophy: '<path d="M7 4h10v5a5 5 0 0 1-10 0Z"/><path d="M7 6H4v1a3 3 0 0 0 3 3M17 6h3v1a3 3 0 0 1-3 3M12 14v4M8 21h8M9 18h6"/>',
  gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v9h14v-9M12 8v13M12 8c-1-3-5-4-5-1.5S10 8 12 8c2 0 5 1 5-1.5S13 5 12 8Z"/>',
  cup: '<path d="M4 8h13v6a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5Z"/><path d="M17 10h1.5a2.5 2.5 0 0 1 0 5H17M7 3v2M10.5 3v2M14 3v2"/>',
  home: '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10M10 20v-6h4v6"/>',
  building: '<rect x="5" y="3" width="14" height="18" rx="1"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M9 15h2M13 15h2M10 21v-3h4v3"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3Z"/>',
  pin: '<path d="M12 21s7-6.2 7-11.5a7 7 0 0 0-14 0C5 14.8 12 21 12 21Z"/><circle cx="12" cy="9.5" r="2.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/>',
  box: '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5Z"/><path d="m3 7.5 9 4.5 9-4.5M12 12v9"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v11h14V9M10 13h4"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3Z"/>',
  code: '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>',
  chat: '<path d="M4 5h16v11H9l-5 4Z"/>',
  rocket: '<path d="M12 15c-2-2-3-4-3-6.5C9 5 12 2.5 12 2.5S15 5 15 8.5c0 2.5-1 4.5-3 6.5Z"/><path d="M9 11 6 14l1 4 3-2M15 11l3 3-1 4-3-2M11 18l1 3 1-3"/>',
};
// what a folder or album can show in the sidebar, in the picker's order
export const LOOK_ICONS = ["folder", "album", "star", "heart", "bookmark", "flag", "tag", "check", "image", "camera", "film", "music", "palette", "brush", "pen", "sparkle", "sun", "moon", "cloud", "leaf", "flower", "tree", "mountain", "flame", "bolt", "drop", "snow", "person", "people", "smile", "cat", "paw", "robot", "ghost", "skull", "gamepad", "sword", "shield", "crown", "gem", "trophy", "gift", "cup", "home", "building", "globe", "pin", "clock", "calendar", "lock", "key", "box", "archive", "eye", "bulb", "code", "chat", "rocket", "book", "grid"];
// Eagle's colours: grey, red, orange, yellow, green, aqua, blue, purple, pink
export const LOOK_COLORS = ["#9e9e9e", "#ef5350", "#f59e0b", "#facc15", "#4ade80", "#22d3ee", "#60a5fa", "#a78bfa", "#f472b6"];

// a folder's or album's icon (svg:name, n:12, e:🦊, or the default) in its colour
export function lookIcon(item, fallback) {
  const v = item.icon || "";
  let el;
  if (v.startsWith("n:")) el = h("span.look-num", {}, v.slice(2));
  else if (v.startsWith("e:")) el = h("span.look-emoji", {}, v.slice(2));
  else el = icon(v.startsWith("svg:") && (SVG[v.slice(4)] || SVG_LOOK[v.slice(4)]) ? v.slice(4) : fallback);
  if (item.color) el.style.color = item.color;
  return el;
}
