// Model Librarian Gallery on a phone or tablet (a paired device, remote.py; on the PC at /m?t=<token>): the library in big
// tiles with a bar at the bottom, a viewer to swipe through and pinch to zoom, and Browse for folders, albums, artists and
// characters. It does what a paired device may: look, rate, favourite, tag, albums, characters, the artist and a note,
// the Trash (and putting back), and Swipe. Settings, folders, moving and renaming stay on the PC.

import { albumTree, api, askText, dayLabel, dirTree, fmtDate, fmtSize, h, holdFocus, icon, lib, media, plural, store, toast, who } from "./util.js";
import { swipeReview } from "./swipe.js";
import * as org from "./organize.js";

const PAGE = 60;
const S = {
  view: { kind: "all", title: "All" },  // all, recent, fav, trash, folder {folder, sub}, album {name}, artist {name}, character {name}
  q: "", searching: false, page: "grid",
  items: [], total: 0, end: false, loading: null, run: 0,
  sel: new Set(), selAll: false, selecting: false,
  albums: [], groups: [], folders: [], dirs: {}, artists: [], characters: [], trash: 0,
};
const opened = new Set(store.get("phone-open", []));  // the folders and albums open in Browse

const app = document.getElementById("p-app");
const top = h("header.p-top");
const grid = h("div.p-grid");
const empty = h("div.p-empty", { hidden: true });
const more = h("div.p-more");
const browse = h("div.p-browse", { hidden: true });
const bar = h("nav.p-bar", { "aria-label": "Gallery" });
app.append(top, h("main.p-main", {}, grid, empty, more, browse), bar);

const quote = (v) => (/[\s"]/.test(v) ? `"${v.replaceAll('"', "")}"` : v);
const iconBtn = (ico, label, onclick, cls = "") => h(`button.p-icon${cls}`, { type: "button", "aria-label": label, title: label, onclick }, icon(ico));

// ---------------------------------------------------------------- what's shown
function viewQuery() {
  const v = S.view;
  const by = { fav: "is:fav", album: `album:${quote(v.name || "")}`, artist: `artist:${quote(v.name || "")}`, character: `character:${quote(v.name || "")}` }[v.kind];
  return [by, v.sub ? `path:${quote(v.sub)}` : "", S.q].filter(Boolean).join(" ");
}
const sortFor = () => (S.view.kind === "recent" ? "viewed" : S.view.kind === "trash" ? "trashed" : "newest");

function params(offset, limit = PAGE) {
  const p = new URLSearchParams({ sort: sortFor(), offset, limit });
  if (S.view.folder) p.set("folder", S.view.folder);
  const q = viewQuery();
  if (q) p.set("q", q);
  return p;
}

function show(view) {
  S.view = view;
  S.page = "grid";
  S.q = "";
  S.searching = false;
  reload();
}

function reload() {
  S.run++;
  S.items = [];
  S.total = 0;
  S.end = false;
  S.loading = null;
  clearSel(false);
  grid.replaceChildren();
  grid.hidden = more.hidden = S.page !== "grid";
  browse.hidden = S.page !== "browse";
  empty.hidden = true;
  window.scrollTo(0, 0);
  render();
  if (S.page === "grid") loadMore();
}

function loadMore() {
  if (S.end || S.page !== "grid") return Promise.resolve();
  const run = S.run;
  S.loading ||= api(`/files?${params(S.items.length)}`).then((d) => {
    if (run !== S.run) return;
    S.total = d.total;
    const before = S.items[S.items.length - 1];
    S.items.push(...d.items);
    S.end = S.items.length >= d.total || !d.items.length;
    grid.append(...withDays(d.items, before));
    showEmpty();
    renderTop();
  }).catch((e) => toast(e.message, true)).finally(() => {
    if (run !== S.run) return;
    S.loading = null;
    if (!S.end && more.getBoundingClientRect().top < innerHeight + 800) loadMore();  // (the screen isn't full yet)
  });
  return S.loading;
}
new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) loadMore(); }, { rootMargin: "800px" }).observe(more);

// what's loaded, fetched again in place (after a change, or an undo): where you are in the grid stays
async function refresh() {
  if (S.page !== "grid") return S.page === "browse" ? openBrowse() : null;
  const run = ++S.run, y = window.scrollY;
  try {
    const d = await api(`/files?${params(0, Math.min(Math.max(S.items.length, PAGE), 1000))}`);  // (the most the server sends at once: the rest come as you scroll)
    if (run !== S.run) return;
    S.items = d.items;
    S.total = d.total;
    S.end = S.items.length >= d.total;
    S.loading = null;
    const keep = new Set(S.items.map((i) => i.id));
    for (const id of [...S.sel]) if (!keep.has(id)) S.sel.delete(id);
    grid.replaceChildren(...withDays(S.items));
    window.scrollTo(0, y);
    showEmpty();
    render();
  } catch (e) { toast(e.message, true); }
}
org.onChange(() => { refresh(); loadLists(); });

function showEmpty() {
  empty.hidden = !!S.items.length;
  if (S.items.length) return;
  const [title, text] = S.q ? ["Nothing found", "Try other words, or fewer of them."]
    : { trash: ["The Trash is empty", "What you delete waits here until it's emptied on the PC."],
      recent: ["Nothing viewed yet", "What you open shows up here, the latest first."],
      fav: ["No favourites yet", "Tap the heart on a picture to keep it here."] }[S.view.kind] || ["Nothing here", "No pictures in this view."];
  empty.replaceChildren(h("h2", {}, title), h("p", {}, text));
}

// newest first: a heading where the day changes (Today, Yesterday, a weekday, a date), across the whole row
const dayOf = (it) => { const d = new Date((it.shot || it.mtime) * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
function withDays(items, before = null) {
  if (sortFor() !== "newest" || S.q) return items.map(tile);
  let last = before ? dayOf(before) : null;
  return items.flatMap((it) => {
    const day = dayOf(it), head = day !== last ? h("h2.p-day", {}, dayLabel(day)) : null;
    last = day;
    return head ? [head, tile(it)] : [tile(it)];
  });
}

// ---------------------------------------------------------------- tiles: a tap opens, a long press selects
function tile(it) {
  const el = h("button.p-tile" + (S.sel.has(it.id) || S.selAll ? ".sel" : ""), { type: "button", "aria-label": it.name },
    h("img", { src: media("thumb", it.id), alt: "", loading: "lazy", draggable: false, onerror: (e) => e.target.replaceWith(h("span.p-ph", {}, it.name)) }),
    it.kind === "video" ? h("span.p-badge", {}, icon("play")) : null,
    it.favorite ? h("span.p-fav", {}, icon("heart")) : null,
    h("span.p-check", {}, icon("check")));
  pressable(el, () => (S.selecting ? toggle(it, el) : viewer(S.items.indexOf(it))), () => { S.selecting = true; toggle(it, el, true); });
  return el;
}

function pressable(el, tap, long) {
  let timer = null, x0 = 0, y0 = 0, fired = false;
  const cancel = () => clearTimeout(timer);
  el.addEventListener("pointerdown", (e) => {
    fired = false;
    x0 = e.clientX;
    y0 = e.clientY;
    timer = setTimeout(() => { fired = true; navigator.vibrate?.(12); long(); }, 450);
  });
  el.addEventListener("pointermove", (e) => { if (Math.hypot(e.clientX - x0, e.clientY - y0) > 10) cancel(); });
  for (const t of ["pointerup", "pointercancel", "pointerleave"]) el.addEventListener(t, cancel);
  el.addEventListener("contextmenu", (e) => e.preventDefault());
  el.addEventListener("click", () => { if (!fired) tap(); });
}

function toggle(it, el, on = !S.sel.has(it.id)) {
  if (S.selAll) {  // all of them, then one let go: the loaded ones, less that one
    S.selAll = false;
    S.sel = new Set(S.items.map((i) => i.id));
    for (const t of grid.querySelectorAll(".p-tile")) t.classList.add("sel");
  }
  if (on) S.sel.add(it.id); else S.sel.delete(it.id);
  el.classList.toggle("sel", on);
  if (!S.sel.size) S.selecting = false;
  render();
}

function clearSel(again = true) {
  S.sel.clear();
  S.selAll = false;
  S.selecting = false;
  for (const t of grid.querySelectorAll(".p-tile.sel")) t.classList.remove("sel");
  if (again) render();
}

const selection = () => (S.selAll ? { view: { sort: sortFor(), folder: S.view.folder, q: viewQuery() } } : [...S.sel]);
const selCount = () => (S.selAll ? S.total : S.sel.size);

async function bulk(action, value, text) {
  try {
    const r = await api("/ops/bulk", { method: "POST", body: { selection: selection(), action, value } });
    org.done(r, text(r.count));
    clearSel();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- the bars
function render() {
  app.classList.toggle("selecting", S.selecting);
  renderTop();
  renderBar();
}

function renderTop() {
  if (S.selecting) {
    top.replaceChildren(iconBtn("remove", "Clear the selection", () => clearSel()),
      h("span.p-title", {}, `${selCount().toLocaleString()} selected`), h("span.spacer"),
      S.selAll ? null : h("button.p-text", { type: "button", onclick: () => { S.selAll = true; for (const t of grid.querySelectorAll(".p-tile")) t.classList.add("sel"); render(); } }, "All"));
    return;
  }
  if (S.searching) {
    const input = h("input.p-search", { type: "search", value: S.q, placeholder: "Search", enterKeyHint: "search", "aria-label": "Search", autocomplete: "off" });
    let wait = null;
    input.addEventListener("input", () => { clearTimeout(wait); wait = setTimeout(() => { S.q = input.value.trim(); reload(); }, 450); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(wait); S.q = input.value.trim(); input.blur(); reload(); } });
    top.replaceChildren(input, h("button.p-text", { type: "button", onclick: () => { S.searching = false; if (S.q) { S.q = ""; reload(); } else renderTop(); } }, "Cancel"));
    if (document.activeElement?.tagName !== "INPUT") setTimeout(() => input.focus(), 0);
    return;
  }
  const back = S.page === "grid" && S.view.kind !== "all" ? iconBtn("back", "Back to All", () => show({ kind: "all", title: "All" })) : null;
  top.replaceChildren(back,
    h("span.p-title", {}, S.page === "browse" ? "Browse" : S.view.title, S.page === "grid" && S.total ? h("span.p-n", {}, S.total.toLocaleString()) : null),
    h("span.spacer"),
    S.page === "grid" ? iconBtn("search", "Search", () => { S.searching = true; renderTop(); }) : null);
}

function renderBar() {
  if (S.selecting) {
    const sel = selection(), n = selCount(), act = (ico, label, fn, cls = "") => h(`button.p-tab${cls}`, { type: "button", onclick: fn }, icon(ico), h("span", {}, label));
    bar.replaceChildren(...(S.view.kind === "trash"
      ? [act("restore", "Put back", async () => { await org.restoreFiles(sel); clearSel(); })]
      : [act("star", "Rate", (e) => rateMenu(e.currentTarget)),
        act("heart", "Favourite", () => bulk("favorite", true, (c) => `${plural(c, "file")} made favourites.`)),
        act("tag", "Tag", () => org.tagDialog(sel, n)),
        act("album", "Album", () => org.albumDialog(sel, n)),
        act("person", who(false), () => org.characterDialog(sel, n, { characters: S.characters, label: who(false), onDone: loadLists })),
        act("trash", "Delete", async () => { if (await org.trashFiles(sel)) clearSel(); }, ".danger")]));
    return;
  }
  const on = S.page === "browse" || S.view.kind === "trash" ? "browse" : S.searching ? "search" : "grid";
  const tab = (id, ico, label, fn) => h("button.p-tab" + (on === id ? ".on" : ""), { type: "button", onclick: fn, "aria-current": on === id ? "page" : null }, icon(ico), h("span", {}, label));
  bar.replaceChildren(
    tab("grid", "grid", "Library", () => {
      if (S.searching) { S.searching = false; if (S.q) { S.q = ""; reload(); } else render(); return; }
      if (S.page === "grid" && S.view.kind === "trash") show({ kind: "all", title: "All" });
      else if (S.page === "grid") window.scrollTo({ top: 0, behavior: "smooth" });
      else { S.page = "grid"; reload(); }
    }),
    tab("browse", "folder", "Browse", openBrowse),
    h("button.p-tab", { type: "button", disabled: S.view.kind === "trash", onclick: startSwipe }, icon("swipe"), h("span", {}, "Swipe")),
    tab("search", "search", "Search", () => {  // (the Trash is in Browse)
      S.searching = true;
      if (S.page !== "grid") { S.page = "grid"; reload(); }
      render();
    }));
}

function rateMenu(anchor) {
  const sheet = h("div.menu.p-rate", { role: "menu" }, [5, 4, 3, 2, 1, 0].map((r) => h("button.menu-item", { type: "button", role: "menuitem", onclick: () => {
    sheet.remove();
    bulk("rate", r, (c) => (r ? `${plural(c, "file")} rated ${r}.` : `Took the rating off ${plural(c, "file")}.`));
  } }, r ? "★".repeat(r) : "No rating")));
  const close = (e) => { if (!sheet.contains(e.target)) { sheet.remove(); document.removeEventListener("pointerdown", close, true); } };
  document.addEventListener("pointerdown", close, true);
  const r = anchor.getBoundingClientRect();
  Object.assign(sheet.style, { position: "fixed", left: `${Math.max(8, r.left - 20)}px`, bottom: `${innerHeight - r.top + 8}px` });
  document.body.append(sheet);
}

function startSwipe() {
  history.pushState({ swipe: true }, "");  // (the phone's Back closes it)
  const back = () => document.querySelector(".swipe [aria-label='Close the review']")?.click();
  addEventListener("popstate", back, { once: true });
  swipeReview({ sort: "newest", folder: S.view.folder, q: viewQuery() }, S.view.title, { onClose: (t) => {
    removeEventListener("popstate", back);
    if (history.state?.swipe) history.back();
    if (t.kept || t.deleted) refresh();
  } });
}

// ---------------------------------------------------------------- Browse: the views, folders, albums, artists, characters
async function loadLists() {
  const [a, f, c, ar, st] = await Promise.all([api("/albums"), api("/folders"), api("/characters"), api("/artists"), api("/status")].map((p) => p.catch(() => null)));
  if (a) { S.albums = a.albums; S.groups = a.groups || []; }
  if (f) S.folders = f.folders;
  if (c) S.characters = c.characters;
  if (ar) S.artists = ar.artists;
  if (st) { S.trash = st.trash || 0; lib.kinds = st.kinds; }
  await Promise.all(S.folders.filter((x) => x.online).map(async (x) => { try { S.dirs[x.id] = (await api(`/folders/${x.id}/dirs`)).dirs; } catch {} }));
}

async function openBrowse() {
  if (S.page !== "browse") { S.page = "browse"; reload(); }
  await loadLists();
  if (S.page === "browse") renderBrowse();
}

function renderBrowse() {
  const setOpen = (key) => { if (opened.has(key)) opened.delete(key); else opened.add(key); store.set("phone-open", [...opened].slice(-300)); renderBrowse(); };
  const row = (ico, label, n, view, { depth = 0, key = null, kids = false } = {}) => h("div.p-line", { style: depth ? { paddingLeft: `${depth * 18}px` } : null },
    key ? h("button.twist" + (opened.has(key) ? ".open" : ""), { type: "button", "aria-label": `${opened.has(key) ? "Close" : "Open"} ${label}`, "aria-expanded": String(opened.has(key)),
      style: kids ? null : { visibility: "hidden" }, tabIndex: kids ? 0 : -1, onclick: () => setOpen(key) }, icon("twist")) : null,
    h("button.p-row", { type: "button", onclick: () => show(view) }, icon(ico), h("span.label", {}, label), n != null ? h("span.n", {}, n.toLocaleString()) : null));
  const section = (title, rows) => (rows.length ? h("section.p-sec", {}, h("h2", {}, title), rows) : null);

  const dirRows = (f, node, depth) => [...node.kids.values()].flatMap((d) => {
    const key = `f:${f.id}/${d.rel}`;
    return [row("folder", d.name, d.n, { kind: "folder", folder: f.id, sub: d.rel, title: d.name }, { depth, key, kids: d.kids.size > 0 }),
      ...(opened.has(key) ? dirRows(f, d, depth + 1) : [])];
  });
  const folders = S.folders.flatMap((f) => {
    const tree = dirTree(S.dirs[f.id] || []), key = `f:${f.id}`, name = f.path.split(/[\\/]/).filter(Boolean).pop() || f.path;
    return [row("folder", name, f.online ? f.file_count : null, { kind: "folder", folder: f.id, title: name }, { key, kids: tree.kids.size > 0 }),
      ...(opened.has(key) ? dirRows(f, tree, 1) : [])];
  });
  const groups = new Map(S.groups.map((g) => [g.name.toLowerCase(), g.n]));
  const albumRows = (node, depth) => [...node.kids.values()].flatMap((n) => {
    const key = `a:${n.path.toLowerCase()}`;
    return [row(n.album?.query != null ? "filter" : "album", n.name, groups.get(n.path.toLowerCase()) ?? n.album?.n ?? 0, { kind: "album", name: n.path, title: n.name },
      { depth, key, kids: n.kids.size > 0 }), ...(opened.has(key) ? albumRows(n, depth + 1) : [])];
  });
  browse.replaceChildren(...[
    section("Library", [row("grid", "All", null, { kind: "all", title: "All" }), row("clock", "Recently viewed", null, { kind: "recent", title: "Recently viewed" }),
      row("heart", "Favourites", null, { kind: "fav", title: "Favourites" }), row("trash", "Trash", S.trash || null, { kind: "trash", title: "Trash" })]),
    section("Folders", folders),
    section("Albums", albumRows(albumTree(S.albums), 0)),
    section(who(), S.characters.map((c) => row("person", c.name, c.n, { kind: "character", name: c.name, title: c.name }))),
    section("Artists", S.artists.map((a) => row("brush", a.name, a.n, { kind: "artist", name: a.name, title: a.name }))),
  ].filter(Boolean));
}

// ---------------------------------------------------------------- the viewer: swipe to the next, pinch or double-tap to zoom
function viewer(start) {
  let i = start, scale = 1, tx = 0, ty = 0, el = null, at = S.items[start]?.id;
  const stage = h("div.pv-stage"), head = h("header.pv-top"), foot = h("footer.pv-bar");
  const box = h("div.p-viewer", { role: "dialog", "aria-modal": "true", tabIndex: -1, "aria-label": "Picture" }, stage, head, foot);
  document.body.append(box);
  const letGo = holdFocus(box);  // (a tablet's keyboard: Tab stays in the viewer)
  document.body.classList.add("p-viewing");
  history.pushState({ viewer: true }, "");  // (the phone's Back closes it)
  const popped = () => close(true);
  addEventListener("popstate", popped);
  function close(fromBack = false) {
    removeEventListener("popstate", popped);
    letGo();
    box.remove();
    document.body.classList.remove("p-viewing");
    if (!fromBack) history.back();
    refresh();
  }

  const item = () => {  // the picture shown, by its id: a refresh (after a change, an undo) brings new objects
    const k = S.items.findIndex((x) => x.id === at);
    if (k >= 0) i = k;
    return S.items[i];
  };
  const place = (glide = false) => {
    if (!el) return;
    el.classList.toggle("glide", glide);
    el.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
  };
  function paint() {
    const it = item();
    if (!it) return close();
    at = it.id;
    scale = 1; tx = 0; ty = 0;
    el = it.kind === "video" ? h("video", { src: media("file", it.id), controls: true, playsInline: true, autoplay: true, preload: "metadata" })
      : h("img", { src: media("file", it.id), alt: it.name, draggable: false });
    stage.replaceChildren(el);
    place();
    const trash = S.view.kind === "trash";
    head.replaceChildren(iconBtn("back", "Close", () => close()), h("span.pv-name", {}, it.name),
      h("span.pv-pos", {}, `${(i + 1).toLocaleString()} of ${S.total.toLocaleString()}`), iconBtn("info", "Details", () => details(it)));
    foot.replaceChildren(...(trash ? [h("button.p-tab", { type: "button", onclick: () => putBack(it) }, icon("restore"), h("span", {}, "Put back"))] : [
      h("div.pv-stars", { role: "group", "aria-label": "Rating" }, [1, 2, 3, 4, 5].map((n) => h("button.pv-star" + (it.rating >= n ? ".on" : ""),
        { type: "button", "aria-label": `Rate ${n}`, onclick: () => mark(it, { rating: it.rating === n ? 0 : n }) }, icon("star")))),
      iconBtn("heart", it.favorite ? "Not a favourite" : "Favourite", () => mark(it, { favorite: !it.favorite }), `.fav${it.favorite ? ".on" : ""}`),
      iconBtn("trash", "Delete", () => remove(it))]));
    api(`/file/${it.id}/viewed`, { method: "POST" }).catch(() => {});
    if (i >= S.items.length - 5) loadMore();
    const next = S.items[i + 1];
    if (next && next.kind !== "video") new Image().src = media("file", next.id);
  }
  const go = (d) => {
    const to = i + d;
    if (to < 0 || to >= S.items.length) { tx = 0; place(true); return; }
    const w = stage.clientWidth;
    tx = -d * w;
    place(true);
    setTimeout(() => { i = to; at = S.items[to]?.id; paint(); }, 180);
  };
  async function mark(it, body) {
    try {
      await api(`/file/${it.id}/mark`, { method: "POST", body });
      const now = S.items.find((x) => x.id === it.id) || it;
      if (body.rating != null) now.rating = body.rating;
      if (body.favorite != null) now.favorite = body.favorite ? 1 : 0;
      paint();
    } catch (e) { toast(e.message, true); }
  }
  const drop = (it) => {  // gone from this view: the next one in its place
    const k = S.items.findIndex((x) => x.id === it.id);
    if (k >= 0) { S.items.splice(k, 1); S.total = Math.max(0, S.total - 1); i = k; }
    if (i >= S.items.length) i = S.items.length - 1;
    if (i < 0) return close();
    at = S.items[i].id;
    paint();
  };
  async function remove(it) { if ((await org.trashFiles([it.id]))?.trashed) drop(it); }
  async function putBack(it) { if ((await org.restoreFiles([it.id]))?.restored) drop(it); }

  // gestures: one finger swipes (or pans a zoomed picture), two pinch, a double tap zooms; a tap shows or hides the bars
  const pts = new Map();
  let pinch = null, swipe = null, lastTap = 0, moved = false;
  stage.addEventListener("pointerdown", (e) => {
    if (e.target.tagName === "VIDEO" && e.clientY > stage.clientHeight - 70) return;  // (the video's own controls)
    try { stage.setPointerCapture(e.pointerId); } catch {}
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    moved = false;
    stage.classList.add("moving");
    if (pts.size === 2 && el?.tagName === "IMG") {
      const [a, b] = [...pts.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), scale, tx, ty, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
      swipe = null;
    } else if (pts.size === 1) swipe = { x: e.clientX, y: e.clientY, tx, ty };
  });
  stage.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch && pts.size === 2) {
      const [a, b] = [...pts.values()], r = stage.getBoundingClientRect();
      const s = Math.min(6, Math.max(1, pinch.scale * Math.hypot(a.x - b.x, a.y - b.y) / pinch.d));
      const cx = pinch.cx - r.left, cy = pinch.cy - r.top;  // (the point between the fingers stays put)
      tx = cx - (cx - pinch.tx) * (s / pinch.scale);
      ty = cy - (cy - pinch.ty) * (s / pinch.scale);
      scale = s;
      moved = true;
      place();
    } else if (swipe) {
      const dx = e.clientX - swipe.x, dy = e.clientY - swipe.y;
      if (Math.hypot(dx, dy) > 8) moved = true;
      if (scale > 1) { tx = swipe.tx + dx; ty = swipe.ty + dy; }
      else { tx = dx; ty = Math.abs(dy) > Math.abs(dx) && dy > 0 ? dy : 0; }
      place();
    }
  });
  const up = (e) => {
    if (!pts.has(e.pointerId)) return;
    pts.delete(e.pointerId);
    stage.classList.remove("moving");
    if (pinch) {
      if (pts.size < 2) pinch = null;
      if (scale <= 1.02) { scale = 1; tx = 0; ty = 0; place(true); }
      return;
    }
    if (!swipe) return;
    const dx = e.clientX - swipe.x, dy = e.clientY - swipe.y;
    swipe = null;
    if (!moved) {  // a tap: two quick ones zoom in or out, one shows or hides the bars
      const now = Date.now();
      if (now - lastTap < 300 && el?.tagName === "IMG") {
        lastTap = 0;
        const r = stage.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
        if (scale > 1) { scale = 1; tx = 0; ty = 0; } else { scale = 2.5; tx = x - x * 2.5; ty = y - y * 2.5; }
        place(true);
      } else {
        lastTap = now;
        setTimeout(() => { if (lastTap === now) box.classList.toggle("bare"); }, 300);
      }
      return;
    }
    if (scale > 1) return;
    if (dy > 120 && Math.abs(dy) > Math.abs(dx)) return close();  // pulled down: back to the grid
    if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy)) return go(dx < 0 ? 1 : -1);
    tx = 0; ty = 0;
    place(true);
  };
  stage.addEventListener("pointerup", up);
  stage.addEventListener("pointercancel", up);
  paint();
}

// ---------------------------------------------------------------- a picture's details: tags, albums, characters, the artist, a note
async function details(it) {
  const viewerBox = document.querySelector(".p-viewer");
  const sheet = h("div.pv-sheet", { role: "dialog", "aria-label": "Details" });
  const shade = h("div", { style: { position: "absolute", inset: "0", zIndex: "1" }, onclick: () => { sheet.remove(); shade.remove(); } });
  viewerBox.append(shade, sheet);
  const again = async () => {
    let info;
    try { info = await api(`/file/${it.id}/info`); } catch (e) { toast(e.message, true); return; }
    if (!sheet.isConnected) return;
    const chip = (label, onRemove) => h("span.pv-chip", {}, label, onRemove ? h("button", { type: "button", "aria-label": `Take off ${label}`, onclick: onRemove }, icon("remove")) : null);
    const add = (label, fn) => h("button.pv-add", { type: "button", onclick: fn }, `+ ${label}`);
    const one = (action, value, text) => api("/ops/bulk", { method: "POST", body: { selection: [it.id], action, value } }).then((r) => { org.done(r, text); again(); }).catch((e) => toast(e.message, true));
    const people = (info.characters || []).filter((c) => c.state !== "rejected");
    const gen = info.generation || {};
    sheet.replaceChildren(...[h("div.pv-grab"),  // (a list of parts, some of them only sometimes: flattened, the absent ones left out)
      h("p.pv-title", {}, info.name),
      h("p.pv-meta", {}, [info.width && `${info.width} × ${info.height}`, fmtSize(info.size), info.shot && fmtDate(info.shot), info.rel.includes("/") ? info.rel.replace(/\/[^/]*$/, "") : null].filter(Boolean).join(" · ")),
      h("h3", {}, "Tags"),
      h("div.pv-chips", {}, (info.tags || []).map((t) => chip(t.name, async () => {
        try { await api(`/file/${it.id}/tags`, { method: "POST", body: { remove: [t.name] } }); again(); } catch (e) { toast(e.message, true); }
      })), add("Tag", async () => {
        const name = await askText("Tag", "Tag", "", { ok: "Add" });
        if (name) { try { await api(`/file/${it.id}/tags`, { method: "POST", body: { add: [name] } }); again(); } catch (e) { toast(e.message, true); } }
      })),
      h("h3", {}, "Albums"),
      h("div.pv-chips", {}, (info.albums || []).map((a) => chip(a.name, () => one("unalbum", a.name, `Took it out of ${a.name}.`))),
        add("Album", () => org.albumDialog([it.id], 1))),
      h("h3", {}, who()),
      h("div.pv-chips", {}, people.map((c) => chip(c.name, null)),
        add(who(false), () => org.characterDialog([it.id], 1, { characters: S.characters, label: who(false), onDone: () => { loadLists(); again(); } }))),
      h("h3", {}, "Artist"),
      h("div.pv-line", {}, h("p.pv-text" + (info.credit?.artist?.name ? "" : ".dim"), {}, info.credit?.artist?.name || "No one yet"),
        h("button.pv-add", { type: "button", onclick: async () => {
          const name = await askText("Who made it", "Artist", info.credit?.artist?.name || "", { ok: "Save" });
          if (name == null || name === (info.credit?.artist?.name || "")) return;
          try { await api(`/file/${it.id}/credit`, { method: "POST", body: { artist: name } }); again(); } catch (e) { toast(e.message, true); }
        } }, "Change")),
      info.credit?.url ? h("p.pv-meta", {}, h("a", { href: info.credit.url, target: "_blank", rel: "noopener noreferrer" }, info.credit.site_name || info.credit.url)) : null,
      h("h3", {}, "Note"),
      h("div.pv-line", {}, h("p.pv-text" + (info.note?.note ? "" : ".dim"), {}, info.note?.note || "None"),
        h("button.pv-add", { type: "button", onclick: async () => {
          const note = await askText("Note", "Note", info.note?.note || "", { ok: "Save" });
          if (note == null) return;
          try { await api(`/file/${it.id}/note`, { method: "POST", body: { note } }); again(); } catch (e) { toast(e.message, true); }
        } }, "Change")),
      gen.positive ? [h("h3", {}, "Prompt"), h("p.pv-text", {}, gen.positive)] : null].flat().filter(Boolean));
  };
  sheet.append(h("div.pv-grab"), h("p.pv-meta", {}, "Loading…"));
  org.onChange(() => { refresh(); loadLists(); if (sheet.isConnected) again(); });
  await again();
}

// ---------------------------------------------------------------- start
addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && !document.querySelector(".p-viewer")) { loadLists(); refresh(); } });
loadLists().then(() => render());
reload();
