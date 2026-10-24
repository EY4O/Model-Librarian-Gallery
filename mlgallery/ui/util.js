// Model Librarian Gallery: small helpers shared by the screens (elements, the API, formatting).

export const TOKEN = document.querySelector('meta[name="mlg-token"]').content;
export const $ = (id) => document.getElementById(id);

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
  graph: '<rect x="3" y="4" width="6" height="5" rx="1.2"/><rect x="15" y="4" width="6" height="5" rx="1.2"/><rect x="9" y="15" width="6" height="5" rx="1.2"/><path d="M6 9v2.5h12V9M12 11.5V15"/>',
};
export function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = SVG[name]; // our own constant markup, never data
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
export function menu(anchor, items) {
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
    else if (e.key === "ArrowDown") { e.preventDefault(); all[(i + 1) % all.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); all[(i - 1 + all.length) % all.length]?.focus(); }
  };
  const box = h("div.menu", { role: "menu" }, items.map((it) => it === null ? h("div.menu-sep", { role: "separator" })
    : it.node ? it.node
    : h("button.menu-item" + (it.danger ? ".danger" : ""), { type: "button", role: "menuitem", onclick: () => { close(); it.onclick(); } },
      it.icon ? icon(it.icon) : h("span.menu-gap"), h("span", {}, it.label), it.hint ? h("kbd", {}, it.hint) : null)));
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
