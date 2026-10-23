// Model Librarian Gallery: the library grid, folders and the viewer.
// The grid is virtual: only the tiles on screen exist, and items are fetched a page at a time, so it
// scrolls the same at 200 files or 250,000. Day headings come from a per-day count (one small request),
// so every tile's place is known without fetching the pages above it.

const TOKEN = document.querySelector('meta[name="mlg-token"]').content;
const PAGE = 300;
const HEAD = 40;      // a day heading's height
const GROUP_GAP = 14; // space under a day's last row
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- helpers
// h("div.cls", {props}, ...children): text is always set as text, never as HTML
function h(tag, props, ...kids) {
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

const SVG = {
  rescan: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  remove: '<path d="M6 6l12 12M18 6 6 18"/>',
  folder: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5Z"/>',
  play: '<path d="M7 4.5v15l13-7.5Z"/>',
  explorer: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5Z"/><path d="M12 11v5M9.5 13.5 12 16l2.5-2.5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
};
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = SVG[name]; // our own constant markup, never data
  return s;
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api" + path, {
    method, headers: { "X-MLG-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) throw Object.assign(new Error((data && data.error) || `HTTP ${res.status}`), { status: res.status });
  return data;
}
const media = (kind, id) => `/api/${kind}/${id}?t=${encodeURIComponent(TOKEN)}`;

let toastTimer = null;
function toast(text, err = false) {
  document.querySelector(".toast")?.remove();
  const t = h("div.toast" + (err ? ".err" : ""), { role: err ? "alert" : "status" }, text);
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), err ? 7000 : 3500);
}

const fmtSize = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
const fmtDur = (s) => (s == null ? "" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
const fmtDate = (t) => new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
const plural = (n, one, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const baseName = (p) => p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const store = {
  get(k, d) { try { const v = localStorage.getItem("mlg." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("mlg." + k, JSON.stringify(v)); } catch {} },
};

function dayLabel(day) {
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

// ---------------------------------------------------------------- state
const state = {
  folders: [], folder: null, random: false, seed: 1 + Math.floor(Math.random() * 1e9),
  sort: store.get("sort", "newest"), kind: store.get("kind", ""), tile: store.get("tile", 176),
  total: 0, groups: [], pages: new Map(), loading: new Set(), gen: 0,
};

// ---------------------------------------------------------------- sidebar
async function loadFolders() {
  const { folders } = await api("/folders");
  state.folders = folders;
  if (state.folder && !folders.some((f) => f.id === state.folder)) state.folder = null;
  renderSide();
}

function renderSide() {
  const total = state.folders.reduce((s, f) => s + (f.online ? f.file_count : 0), 0);
  $("all-count").textContent = total ? total.toLocaleString() : "";
  $("view-all").classList.toggle("sel", !state.folder && !state.random);
  $("view-random").classList.toggle("sel", state.random);
  const items = state.folders.map((f) => {
    const sel = state.folder === f.id;
    return h("button.nav-item" + (sel ? ".sel" : ""), { type: "button", title: f.path, onclick: () => selectView({ folder: f.id }),
      "aria-current": sel ? "true" : null },
      icon("folder"),
      h("span.label", {}, baseName(f.path)),
      f.online ? h("span.n", {}, f.file_count.toLocaleString())
        : h("span.off", { title: "Not reachable right now (a drive that isn't plugged in?): its files are kept" }, "offline"),
      h("span.acts", {},
        h("span.act", { role: "button", tabIndex: 0, title: "Look for new and changed files", "aria-label": `Rescan ${baseName(f.path)}`,
          onclick: (e) => { e.stopPropagation(); rescan(f); }, onkeydown: actKey }, icon("rescan")),
        h("span.act", { role: "button", tabIndex: 0, title: "Remove from the library (the folder and its files stay where they are)",
          "aria-label": `Remove ${baseName(f.path)}`, onclick: (e) => { e.stopPropagation(); removeFolder(f); }, onkeydown: actKey }, icon("remove"))));
  });
  $("folders").replaceChildren(...(items.length ? items
    : [h("p.nav-empty", {}, "No folders yet. ", h("button", { type: "button", onclick: addFolder }, "Add one"))]));
  renderToolbar();
}

function actKey(e) {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.currentTarget.click(); }
}

function selectView({ folder = null, random = false }) {
  if (random && state.random) state.seed = 1 + Math.floor(Math.random() * 1e9); // Random again: a new shuffle
  state.folder = folder;
  state.random = random;
  renderSide();
  reload(true);
}

function renderToolbar() {
  const f = state.folders.find((x) => x.id === state.folder);
  $("view-title").textContent = state.random ? "Random" : f ? baseName(f.path) : "All";
  $("view-title").title = f ? f.path : "";
  $("sort-wrap").hidden = state.random;
  $("shuffle").hidden = !state.random;
  for (const b of $("kind").querySelectorAll("button")) {
    b.classList.toggle("on", b.dataset.kind === state.kind);
    b.setAttribute("aria-pressed", String(b.dataset.kind === state.kind));
  }
}

function setSide(hidden) {
  $("app").classList.toggle("side-hidden", hidden);
  $("side-show").hidden = !hidden;
  store.set("sideHidden", hidden);
  requestAnimationFrame(draw);
}

async function addFolder() {
  let path = "";
  try {
    path = (await api("/folders/pick", { method: "POST" })).path;
  } catch (e) {
    if (e.status !== 501) return toast(e.message, true);
    path = prompt("The folder to add (its full path):") || "";
  }
  if (!path) return;
  try {
    const { folder } = await api("/folders", { method: "POST", body: { path } });
    toast(`Added ${baseName(folder.path)}: indexing it now.`);
    await loadFolders();
    pollSoon();
  } catch (e) {
    toast(e.message, true);
  }
}

async function rescan(f) {
  await api(`/folders/${f.id}/scan`, { method: "POST" }).catch((e) => toast(e.message, true));
  pollSoon();
}

async function removeFolder(f) {
  if (!confirm(`Remove ${f.path} from the library?\n\nOnly the Gallery's index of it goes: the folder and its files stay exactly where they are.`)) return;
  await api(`/folders/${f.id}`, { method: "DELETE" }).catch((e) => toast(e.message, true));
  await loadFolders();
  reload(true);
}

// ---------------------------------------------------------------- the grid
const scroller = $("scroll"), sizer = $("sizer");
const tiles = new Map(); // index -> element
const heads = new Map(); // group index -> element

function layout() {
  const gap = 8;
  const width = sizer.clientWidth;
  const cols = Math.max(1, Math.floor((width + gap) / (state.tile + gap)));
  const size = Math.floor((width - gap * (cols - 1)) / cols);
  const row = size + gap;
  // day headings only while they add up to what's listed (mid-index they can lag behind: plain until the next refresh)
  const counted = state.groups.reduce((s, g) => s + g.n, 0);
  const groups = state.groups.length && counted === state.total ? state.groups : [{ label: null, n: state.total, start: 0 }];
  let y = 0;
  for (const g of groups) {
    g.top = y;
    g.head = g.label ? HEAD : 4;
    g.rows = Math.ceil(g.n / cols);
    y += g.head + Math.max(0, g.rows * row - gap) + (g.label ? GROUP_GAP : 0);
  }
  return { gap, cols, size, row, groups, height: y };
}

function query(path, offset) {
  const q = new URLSearchParams(offset == null ? {} : { offset, limit: PAGE });
  if (state.random) { q.set("sort", "random"); q.set("seed", state.seed); } else q.set("sort", state.sort);
  if (state.folder) q.set("folder", state.folder);
  if (state.kind) q.set("kind", state.kind);
  return `${path}?${q}`;
}

async function loadPage(p) {
  if (state.pages.has(p) || state.loading.has(p)) return;
  const gen = state.gen;
  state.loading.add(p);
  try {
    const res = await api(query("/files", p * PAGE));
    if (gen !== state.gen) return; // the view changed while this was on its way
    state.pages.set(p, res.items);
    if (res.total !== state.total) { state.total = res.total; showCount(); if (state.groups.length) loadDays(); }
    draw();
  } catch (e) {
    toast(e.message, true);
  } finally {
    state.loading.delete(p);
  }
}

async function loadDays() {
  const gen = state.gen;
  if (state.random || !["newest", "oldest"].includes(state.sort)) { state.groups = []; return; }
  try {
    const { days } = await api(query("/days"));
    if (gen !== state.gen) return;
    let start = 0;
    state.groups = days.map((d) => { const g = { label: dayLabel(d.day), n: d.n, start }; start += d.n; return g; });
    for (const el of heads.values()) el.remove();
    heads.clear();
    for (const el of tiles.values()) el.remove();
    tiles.clear();
    draw();
  } catch {
    state.groups = [];
  }
}

const itemAt = (i) => state.pages.get(Math.floor(i / PAGE))?.[i % PAGE];

function tileFor(item) {
  const el = h("button.tile", { type: "button", onclick: () => openViewer(item.__i), "aria-label": item.name });
  if (item.thumb === -1 || item.thumb === -2) {
    el.append(h("div.ph", {}, item.thumb === -2 ? `${item.name}\n\nVideo posters need ffmpeg` : item.name));
  } else {
    const img = h("img", { alt: "", decoding: "async", draggable: false });
    img.onload = () => img.classList.add("on");
    img.onerror = () => { img.remove(); el.prepend(h("div.ph", {}, item.name)); };
    img.src = media("thumb", item.id);
    el.append(img);
  }
  if (item.kind === "video") el.append(h("span.badge", {}, icon("play"), fmtDur(item.duration)));
  el.append(h("span.name", {}, item.name));
  el.title = `${item.name}\n${item.width && item.height ? `${item.width} × ${item.height} · ` : ""}${fmtSize(item.size)} · ${fmtDate(item.mtime)}`;
  return el;
}

function draw() {
  const { gap, cols, size, row, groups, height } = layout();
  sizer.style.height = `${height}px`;
  const top = scroller.scrollTop - sizer.offsetTop, view = scroller.clientHeight;
  const lo = top - row * 2, hi = top + view + row * 2;
  const want = new Set(), wantHeads = new Set();
  groups.forEach((g, gi) => {
    const gBottom = g.top + g.head + g.rows * row;
    if (gBottom < lo || g.top > hi) return;
    if (g.label) {
      wantHeads.add(gi);
      if (!heads.has(gi)) {
        const el = h("div.day", { role: "heading", "aria-level": 2 }, g.label, h("span.n", {}, g.n.toLocaleString()));
        sizer.append(el);
        heads.set(gi, el);
      }
      heads.get(gi).style.top = `${g.top}px`;
    }
    const r0 = Math.max(0, Math.floor((lo - g.top - g.head) / row)), r1 = Math.min(g.rows - 1, Math.floor((hi - g.top - g.head) / row));
    for (let r = r0; r <= r1; r++) {
      for (let c = 0; c < cols; c++) {
        const j = r * cols + c;
        if (j >= g.n) break;
        const i = g.start + j;
        want.add(i);
        const item = itemAt(i);
        if (!item) { loadPage(Math.floor(i / PAGE)); continue; }
        let el = tiles.get(i);
        if (el && el.dataset.id === String(item.id) && el.dataset.thumb === String(item.thumb)) {
          if (el.dataset.size === String(size)) continue;
        } else {
          el?.remove();
          item.__i = i;
          el = tileFor(item);
          el.dataset.id = item.id;
          el.dataset.thumb = item.thumb;
          sizer.append(el);
          tiles.set(i, el);
        }
        el.dataset.size = size;
        Object.assign(el.style, { width: `${size}px`, height: `${size}px`, left: `${c * (size + gap)}px`, top: `${g.top + g.head + r * row}px` });
      }
    }
  });
  for (const [i, el] of tiles) if (!want.has(i)) { el.remove(); tiles.delete(i); }
  for (const [gi, el] of heads) if (!wantHeads.has(gi)) { el.remove(); heads.delete(gi); }
  showEmpty();
}

function reload(toTop = false) {
  state.gen++;
  state.pages.clear();
  state.loading.clear();
  for (const el of tiles.values()) el.remove();
  tiles.clear();
  for (const el of heads.values()) el.remove();
  heads.clear();
  if (toTop) scroller.scrollTop = 0;
  renderToolbar();
  Promise.all([loadPage(0), loadDays()]).then(draw);
}

function showCount() {
  $("count").textContent = state.total ? plural(state.total, "item") : "";
}

function showEmpty() {
  const empty = $("empty");
  if (state.total) { empty.hidden = true; return; }
  empty.hidden = false;
  if (!state.folders.length) {
    empty.replaceChildren(h("h2", {}, "Add a folder to start"),
      h("p", {}, "Pick a folder with your images and videos, such as ComfyUI's output folder. The Gallery reads it and never changes your files."),
      h("button.btn.primary", { type: "button", onclick: addFolder }, "Add a folder"));
  } else if (lastStatus && (lastStatus.scanning || lastStatus.queued)) {
    empty.replaceChildren(h("h2", {}, "Indexing…"), h("p", {}, "Your files appear here as they're found."));
  } else {
    empty.replaceChildren(h("h2", {}, "Nothing here"), h("p", {}, state.kind ? "No files of that kind in this view." : "No images or videos were found in this folder."));
  }
}

// ---------------------------------------------------------------- the viewer
let viewing = -1;

function openViewer(i) {
  const item = itemAt(i);
  if (!item) return;
  viewing = i;
  const v = $("viewer");
  const wasHidden = v.hidden;
  v.hidden = false;
  $("v-back").textContent = $("view-title").textContent;
  $("v-name").textContent = item.name;
  $("v-meta").textContent = [folderOf(item) ? baseName(folderOf(item).path) : "", item.width && item.height ? `${item.width} × ${item.height}` : "",
    item.kind === "video" ? fmtDur(item.duration) : "", fmtSize(item.size), fmtDate(item.mtime)].filter(Boolean).join(" · ");
  $("v-pos").textContent = `${(i + 1).toLocaleString()} of ${state.total.toLocaleString()}`;
  $("v-prev").disabled = i <= 0;
  $("v-next").disabled = i >= state.total - 1;
  const src = media("file", item.id);
  $("v-media").replaceChildren(item.kind === "video"
    ? h("video", { src, controls: true, autoplay: true, loop: true })
    : h("img", { src, alt: item.name }));
  renderDetails(item);
  if (!itemAt(i + 1) && i + 1 < state.total) loadPage(Math.floor((i + 1) / PAGE));
  if (wasHidden) $("v-close").focus();
}

const folderOf = (item) => state.folders.find((f) => f.id === item.folder_id);
const fullPath = (item) => { const f = folderOf(item); return f ? `${f.path.replace(/[\\/]+$/, "")}\\${item.rel.replaceAll("/", "\\")}` : item.rel; };

function renderDetails(item) {
  const rows = [
    ["Type", `${item.kind === "video" ? "Video" : "Image"} · ${item.name.split(".").pop().toUpperCase()}`],
    ["Dimensions", item.width && item.height ? `${item.width} × ${item.height}` : "—"],
    item.kind === "video" ? ["Length", fmtDur(item.duration) || "—"] : null,
    ["Size", fmtSize(item.size)],
    ["Modified", fmtDate(item.mtime)],
  ].filter(Boolean);
  const path = fullPath(item);
  $("v-panel").replaceChildren(
    h("section", {}, h("h3", {}, "File"), h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))),
    h("section", {}, h("h3", {}, "Location"),
      h("div.path", {}, path),
      h("div.row", {},
        h("button.btn", { type: "button", onclick: () => reveal(item) }, icon("explorer"), "Show in Explorer"),
        h("button.btn", { type: "button", onclick: () => copyPath(path) }, icon("copy"), "Copy path"))),
    h("section", {}, h("h3", {}, "What made it"),
      h("p.note", {}, "The prompt, settings, models and workflow saved in the file will show here.")));
}

async function reveal(item) {
  await api(`/file/${item.id}/reveal`, { method: "POST" }).catch((e) => toast(e.message, true));
}

async function copyPath(path) {
  try { await navigator.clipboard.writeText(path); toast("Path copied."); } catch { toast("Couldn't copy here: select the path and press Ctrl+C.", true); }
}

function setDetails(open) {
  $("viewer").classList.toggle("no-details", !open);
  $("v-details").setAttribute("aria-pressed", String(open));
  $("v-details").title = open ? "Hide details (I)" : "Show details (I)";
  store.set("details", open);
}

function closeViewer() {
  $("viewer").hidden = true;
  $("v-media").replaceChildren();
  const el = tiles.get(viewing);
  viewing = -1;
  el?.focus();
}

function step(d) {
  const i = viewing + d;
  if (i < 0 || i >= state.total) return;
  if (itemAt(i)) openViewer(i);
  else loadPage(Math.floor(i / PAGE)).then(() => openViewer(i));
}

// ---------------------------------------------------------------- status
let lastStatus = null, pollTimer = null, lastFiles = -1;

async function poll() {
  clearTimeout(pollTimer);
  try {
    const st = await api("/status");
    lastStatus = st;
    const busy = st.scanning || st.queued || st.thumbs_pending;
    $("status-dot").classList.toggle("busy", !!busy);
    $("status").textContent = st.scanning ? `Indexing ${baseName(st.scanning.path)}: ${plural(st.scanning.seen || 0, "file")} found`
      : st.queued ? "Indexing…"
      : st.thumbs_pending ? `Making thumbnails: ${st.thumbs_pending.toLocaleString()} to go`
      : st.files ? `Up to date · ${plural(st.files, "file")}` : "Up to date";
    $("status-right").textContent = st.thumbs_failed ? `${plural(st.thumbs_failed, "file")} without a thumbnail` : "";
    if (st.files !== lastFiles) {
      lastFiles = st.files;
      await loadFolders();
      reload(false);
    } else if (st.thumbs_pending) {
      // thumbnails made meanwhile: tiles waiting for one pick it up when their page is fetched again
      for (const p of [...state.pages.keys()]) state.pages.delete(p);
      draw();
    }
    pollTimer = setTimeout(poll, busy ? 1500 : 6000);
  } catch {
    pollTimer = setTimeout(poll, 4000);
  }
}
const pollSoon = () => { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 300); };

// ---------------------------------------------------------------- wiring
$("add").onclick = addFolder;
$("view-all").onclick = () => selectView({});
$("view-random").onclick = () => selectView({ random: true });
$("shuffle").onclick = () => selectView({ random: true });
$("side-hide").onclick = () => setSide(true);
$("side-show").onclick = () => setSide(false);
$("sort").value = state.sort;
$("tile").value = state.tile;
$("sort").onchange = (e) => { state.sort = e.target.value; store.set("sort", state.sort); reload(true); };
for (const b of $("kind").querySelectorAll("button")) {
  b.onclick = () => { state.kind = b.dataset.kind; store.set("kind", state.kind); reload(true); };
}
$("tile").oninput = (e) => { state.tile = Number(e.target.value); store.set("tile", state.tile); draw(); };
scroller.addEventListener("scroll", () => requestAnimationFrame(draw), { passive: true });
new ResizeObserver(() => draw()).observe(scroller);
$("v-close").onclick = closeViewer;
$("v-prev").onclick = () => step(-1);
$("v-next").onclick = () => step(1);
$("v-details").onclick = () => setDetails($("viewer").classList.contains("no-details"));
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
    e.preventDefault();
    if ($("viewer").hidden) setSide(!$("app").classList.contains("side-hidden"));
    return;
  }
  if ($("viewer").hidden || e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.target.closest?.("input, select, textarea")) return;
  if (e.key === "Escape") closeViewer();
  else if (e.key === "ArrowRight") step(1);
  else if (e.key === "ArrowLeft") step(-1);
  else if (e.key.toLowerCase() === "i") setDetails($("viewer").classList.contains("no-details"));
});

setSide(store.get("sideHidden", false));
setDetails(store.get("details", true));
loadFolders().then(() => { reload(true); poll(); });
