// Model Librarian Gallery: small helpers shared by the screens (elements, the API, formatting).

export const TOKEN = document.querySelector('meta[name="mlg-token"]').content;
export const $ = (id) => document.getElementById(id);

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

export const fmtSize = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
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
