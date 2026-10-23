// Model Librarian Gallery: the library grid, folders, search and the viewer.
// The grid is virtual: only the tiles on screen exist, and items are fetched a page at a time, so it
// scrolls the same at 200 files or 250,000. Day headings come from a per-day count (one small request),
// so every tile's place is known without fetching the pages above it.

import { $, TOKEN, api, baseName, copyText, dayLabel, fmtDate, fmtDur, fmtNum, fmtSize, h, icon, media, plural, store, toast } from "./util.js";
import { renderStats } from "./stats.js";
import { editCharacter, renderCharacter, renderCharacters, renderReview } from "./characters.js";
import * as org from "./organize.js";
import { renderSettings } from "./settings.js";

const PAGE = 300;
const HEAD = 40;      // a day heading's height
const GROUP_GAP = 14; // space under a day's last row

// ---------------------------------------------------------------- state
const state = {
  sel: new Set(), selAll: false, anchor: -1,  // the selection: ids, or the whole view
  albums: [], album: null,
  folders: [], view: "all", folder: null, seed: 1 + Math.floor(Math.random() * 1e9),
  sort: store.get("sort", "newest"), kind: store.get("kind", ""), tile: store.get("tile", 176),
  chips: [], text: "",                     // the search: filter chips, and the words typed
  total: 0, groups: [], pages: new Map(), loading: new Set(), gen: 0, focus: -1,
};

// ---------------------------------------------------------------- the search
const FIELDS = ["model", "lora", "tag", "sampler", "scheduler", "seed", "steps", "rating", "is", "has", "no", "neg", "vae", "ext"];
const SUGGESTED = new Set(["model", "lora", "tag", "sampler", "scheduler", "vae"]);
const quote = (v) => (/[\s"]/.test(v) ? `"${v.replaceAll('"', "")}"` : v);
const chipText = (c) => `${c.key}:${quote(c.value)}`;
const queryString = () => [...state.chips.map(chipText), state.text.trim()].filter(Boolean).join(" ");
const HINTS = [
  ["model:", "a checkpoint or diffusion model"], ["lora:", "a LoRA"], ["tag:", "one of your tags"],
  ["sampler:", "a sampler"], ["seed:", "an exact seed"], ["rating:4", "rated 4 or more"],
  ["is:fav", "your favourites"], ["has:workflow", "a ComfyUI workflow is saved"], ["no:prompt", "no generation data"],
  ["neg:", "words in the negative prompt"],
];

function renderChips() {
  $("chips").replaceChildren(...state.chips.map((c, i) => h("span.chip", { title: chipText(c) },
    h("span.k", {}, c.key), h("span.v", {}, c.value),
    h("button.x", { type: "button", "aria-label": `Remove ${chipText(c)}`, onclick: () => { state.chips.splice(i, 1); searchChanged(); $("q").focus(); } }, icon("remove")))));
  $("q-clear").hidden = !state.chips.length && !state.text;
  $("q").placeholder = state.chips.length ? "" : "Search prompts, models, tags…";
  $("f-fav").setAttribute("aria-pressed", String(state.chips.some((c) => c.key === "is" && c.value === "fav")));
  const r = state.chips.find((c) => c.key === "rating");
  $("f-rating").value = r ? (r.value === "5" ? "5" : r.value.replace(">=", "")) : "";
}

function addChip(key, value, { replace = false } = {}) {
  value = String(value).trim();
  if (!value) return;
  if (replace) state.chips = state.chips.filter((c) => c.key !== key);
  if (!state.chips.some((c) => c.key === key && c.value.toLowerCase() === value.toLowerCase())) state.chips.push({ key, value });
  searchChanged();
}

function removeChips(test) {
  state.chips = state.chips.filter((c) => !test(c));
  searchChanged();
}

let searchTimer = null;
function searchChanged({ now = true } = {}) {
  renderChips();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => reload(true), now ? 0 : 260);
}

// "model:krea" or 'lora:"paper cut"' typed and confirmed becomes a chip
function takeChips(text, final) {
  const re = /(?:^|\s)(\w+):("[^"]*"|\S+)(?=\s|$)/g;
  let rest = text, m, took = false;
  while ((m = re.exec(text))) {
    const key = m[1].toLowerCase(), value = m[2].replace(/^"|"$/g, "");
    const end = m.index + m[0].length;
    if (!FIELDS.includes(key) || !value || (!final && end === text.length)) continue; // still typing it
    state.chips.push({ key, value });
    rest = rest.replace(m[0].trim(), "");
    took = true;
  }
  return { rest: rest.replace(/\s{2,}/g, " ").trimStart(), took };
}

let suggestFor = null, suggestIndex = -1;
async function showSuggest() {
  const box = $("suggest"), input = $("q");
  const before = input.value.slice(0, input.selectionStart ?? input.value.length);
  const m = before.match(/(?:^|\s)(\w+):("?[^"\s]*)$/);
  if (m && SUGGESTED.has(m[1].toLowerCase())) {
    const field = m[1].toLowerCase(), q = m[2].replace(/^"/, "");
    const key = `${field}:${q}`;
    suggestFor = key;
    const { items } = await api(`/suggest?field=${field}&q=${encodeURIComponent(q)}`).catch(() => ({ items: [] }));
    if (suggestFor !== key) return; // typed on meanwhile
    renderSuggest(items.map((it) => ({ label: it.name, hint: it.n.toLocaleString(), pick: () => pickSuggestion(field, it.name, m[0]) })),
      items.length ? null : `No ${field === "model" ? "models" : field + "s"} matching "${q}" yet`);
  } else if (!input.value && document.activeElement === input) {
    suggestFor = null;
    renderSuggest(HINTS.map(([k, hint]) => ({ label: k, hint, pick: () => { input.value = k; input.focus(); onInput(); } })), null, "Search by");
  } else {
    suggestFor = null;
    box.hidden = true;
  }
}

function renderSuggest(items, empty, title) {
  const box = $("suggest");
  suggestIndex = -1;
  box.replaceChildren(...[
    title ? h("div.s-title", {}, title) : null,
    ...items.map((it, i) => h("button.s-item", { type: "button", role: "option", id: `sug-${i}`, onmousedown: (e) => { e.preventDefault(); it.pick(); } },
      h("span", {}, it.label), h("span.s-hint", {}, it.hint))),
    empty ? h("div.s-empty", {}, empty) : null].filter(Boolean));
  box.hidden = !items.length && !empty;
  box._items = items;
}

function moveSuggest(d) {
  const box = $("suggest"), items = box._items || [];
  if (box.hidden || !items.length) return false;
  suggestIndex = (suggestIndex + d + items.length) % items.length;
  box.querySelectorAll(".s-item").forEach((b, i) => b.classList.toggle("on", i === suggestIndex));
  $("q").setAttribute("aria-activedescendant", `sug-${suggestIndex}`);
  return true;
}

function pickSuggestion(field, name, typed) {
  const input = $("q");
  input.value = input.value.replace(typed.trim(), "").replace(/\s{2,}/g, " ").trim();
  state.text = input.value;
  $("suggest").hidden = true;
  addChip(field, name);
  input.focus();
}

function onInput() {
  const input = $("q");
  const { rest, took } = takeChips(input.value, false);
  if (took) input.value = rest;
  state.text = input.value;
  renderChips();
  searchChanged({ now: took });
  showSuggest();
}

// ---------------------------------------------------------------- sidebar
async function loadFolders() {
  const { folders } = await api("/folders");
  state.folders = folders;
  if (state.folder && !folders.some((f) => f.id === state.folder)) { state.folder = null; state.view = "all"; }
  renderSide();
}

function renderSide() {
  const total = state.folders.reduce((s, f) => s + (f.online ? f.file_count : 0), 0);
  $("all-count").textContent = total ? total.toLocaleString() : "";
  for (const v of ["all", "recent", "random", "stats", "review", "trash", "duplicates", "history", "settings"]) $(`view-${v}`).classList.toggle("sel", state.view === v);
  $("view-characters").classList.toggle("sel", state.view === "characters" || state.view === "character");
  const items = state.folders.map((f) => {
    const sel = state.view === "folder" && state.folder === f.id;
    return h("button.nav-item" + (sel ? ".sel" : ""), { type: "button", title: f.path, onclick: () => selectView("folder", f.id),
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
  $("albums").replaceChildren(...(state.albums.length ? state.albums.map((a) => {
    const sel = state.view === "album" && state.album === a.name;
    return h("button.nav-item" + (sel ? ".sel" : ""), { type: "button", onclick: () => selectView("album", a.name), "aria-current": sel ? "true" : null },
      icon("album"), h("span.label", {}, a.name), h("span.n", {}, a.n.toLocaleString()),
      h("span.acts", {},
        h("span.act", { role: "button", tabIndex: 0, title: "Rename", "aria-label": `Rename ${a.name}`, onkeydown: actKey,
          onclick: async (e) => { e.stopPropagation(); renameAlbum(a); } }, icon("tag")),
        h("span.act", { role: "button", tabIndex: 0, title: "Delete the album (its files stay where they are)", "aria-label": `Delete ${a.name}`, onkeydown: actKey,
          onclick: async (e) => { e.stopPropagation(); deleteAlbum(a); } }, icon("remove"))));
  }) : [h("p.nav-empty", {}, "Albums gather files from any folder.")]));
  renderToolbar();
}

async function loadAlbums() {
  try {
    state.albums = (await api("/albums")).albums;
    if (state.view === "album" && !state.albums.some((a) => a.name === state.album)) selectView("all");
    renderSide();
  } catch {}
}

async function newAlbum() {
  const name = (prompt("A name for the new album:") || "").trim();
  if (!name) return;
  try {
    await api("/albums", { method: "POST", body: { name } });
    await loadAlbums();
    if (selCount()) org.albumDialog(selection(), selCount());
  } catch (e) { toast(e.message, true); }
}

async function renameAlbum(a) {
  const name = (prompt(`Rename the album ${a.name} to:`, a.name) || "").trim();
  if (!name || name === a.name) return;
  try {
    await api(`/albums/${a.id}`, { method: "POST", body: { name } });
    if (state.album === a.name) state.album = name;
    await loadAlbums();
    if (state.view === "album") reload(false);
  } catch (e) { toast(e.message, true); }
}

async function deleteAlbum(a) {
  if (!confirm(`Delete the album ${a.name}?\n\nOnly the album goes: its ${a.n.toLocaleString()} files stay exactly where they are.`)) return;
  await api(`/albums/${a.id}`, { method: "DELETE" }).catch((e) => toast(e.message, true));
  loadAlbums();
}

function actKey(e) {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.currentTarget.click(); }
}

const PAGES = new Set(["stats", "characters", "character", "review", "duplicates", "history", "settings"]); // screens of their own, instead of the grid
const isPage = () => PAGES.has(state.view);
const pageCtx = { open: (chips) => openFromStats(chips), show: (view, id) => selectView(view, id), refresh: () => loadCharacters() };

function selectView(view, arg = null) {
  if (view === "random" && state.view === "random") state.seed = 1 + Math.floor(Math.random() * 1e9); // Random again: a new shuffle
  state.view = view;
  state.folder = view === "folder" ? arg : null;
  state.character = view === "character" ? arg : null;
  state.album = view === "album" ? arg : null;
  clearSel();
  const page = isPage();
  document.querySelector(".toolbar").hidden = page;
  scroller.hidden = page;
  $("stats").hidden = !page;
  renderSide();
  if (!page) return reload(true);
  $("empty").hidden = true;
  $("stats").scrollTop = 0;
  if (view === "stats") renderStats($("stats"), pageCtx);
  else if (view === "characters") renderCharacters($("stats"), pageCtx);
  else if (view === "character") renderCharacter($("stats"), arg, pageCtx);
  else if (view === "review") renderReview($("stats"), pageCtx);
  else if (view === "duplicates") org.renderDuplicates($("stats"));
  else if (view === "history") org.renderHistory($("stats"));
  else if (view === "settings") renderSettings($("stats"), settingsCtx);
}

const settingsCtx = {
  addFolder: async () => { await addFolder(); if (state.view === "settings") renderSettings($("stats"), settingsCtx); },
  rescan: (f) => rescan(f),
  removeFolder: async (f) => { await removeFolder(f); renderSettings($("stats"), settingsCtx); },
};

async function loadCharacters() {
  try {
    const d = await api("/characters");
    state.characters = d.characters;
    $("char-count").textContent = d.characters.length ? d.characters.length.toLocaleString() : "";
    $("review-count").textContent = d.review ? d.review.toLocaleString() : "";
    $("review-count").hidden = !d.review;
  } catch {}
}

// a bar on the Statistics screen: the library, searched for the files behind it
function openFromStats(chips) {
  const folder = chips.find((c) => c.key === "folder");
  const text = chips.filter((c) => c.key === "text").map((c) => c.value).join(" ");
  state.chips = chips.filter((c) => c.key !== "folder" && c.key !== "text");
  state.text = text;
  $("q").value = text;
  renderChips();
  selectView(folder ? "folder" : "all", folder ? Number(folder.value) : null);
}

const sortFor = () => (state.view === "random" ? "random" : state.view === "recent" ? "viewed" : state.view === "trash" ? "trashed" : state.sort);

function renderToolbar() {
  const f = state.folders.find((x) => x.id === state.folder);
  $("view-title").textContent = { random: "Random", recent: "Recently viewed", trash: "Trash" }[state.view] || (state.view === "album" ? state.album : f ? baseName(f.path) : "All");
  $("view-title").title = f ? f.path : "";
  $("sort-wrap").hidden = ["random", "recent", "trash"].includes(state.view);
  $("empty-trash").hidden = state.view !== "trash" || !state.total;
  $("shuffle").hidden = state.view !== "random";
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

// where tile i sits: its group, row and column
function place(i, L = layout()) {
  let lo = 0, hi = L.groups.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (L.groups[mid].start <= i) lo = mid; else hi = mid - 1; }
  const g = L.groups[lo], j = i - g.start;
  const r = Math.floor(j / L.cols), c = j % L.cols;
  return { g, gi: lo, r, c, top: g.top + g.head + r * L.row, left: c * (L.size + L.gap) };
}

function query(path, offset) {
  const q = new URLSearchParams(offset == null ? {} : { offset, limit: PAGE });
  q.set("sort", sortFor());
  if (state.view === "random") q.set("seed", state.seed);
  if (state.folder) q.set("folder", state.folder);
  if (state.kind) q.set("kind", state.kind);
  const text = viewQuery();
  if (text) q.set("q", text);
  return `${path}?${q}`;
}

// the search, plus what the view itself filters by (an album)
const viewQuery = () => [state.view === "album" ? `album:${quote(state.album)}` : "", queryString()].filter(Boolean).join(" ");

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
  if (!["newest", "oldest"].includes(sortFor())) { state.groups = []; return; }
  try {
    const { days } = await api(query("/days"));
    if (gen !== state.gen) return;
    let start = 0;
    state.groups = days.map((d) => { const g = { label: dayLabel(d.day), n: d.n, start }; start += d.n; return g; });
    for (const el of heads.values()) el.remove(); // headings are new; tiles just move (draw places them)
    heads.clear();
    draw();
  } catch {
    state.groups = [];
  }
}

const itemAt = (i) => state.pages.get(Math.floor(i / PAGE))?.[i % PAGE];

function tileFor(item) {
  const el = h("button.tile" + (isSelected(item) ? ".picked" : ""), { type: "button", onclick: (e) => tileClick(e, item.__i), onfocus: () => { state.focus = item.__i; },
    "aria-label": item.name, role: "gridcell", "aria-selected": String(isSelected(item)) });
  el.append(h("span.t-pick", { role: "checkbox", "aria-checked": String(isSelected(item)), "aria-label": `Select ${item.name}`, title: "Select (Ctrl+click)",
    onclick: (e) => { e.stopPropagation(); toggleSel(item.__i); } }, icon("check")));
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
  if (item.favorite) el.append(h("span.t-fav", { title: "Favourite" }, icon("heart")));
  if (item.rating) el.append(h("span.t-rating", { title: `Rated ${item.rating}` }, "★".repeat(item.rating)));
  el.append(h("span.name", {}, item.name));
  el.title = `${item.name}\n${item.width && item.height ? `${item.width} × ${item.height} · ` : ""}${fmtSize(item.size)} · ${fmtDate(item.mtime)}`;
  return el;
}

const tileKey = (item) => `${item.id}:${item.thumb}:${item.rating}:${item.favorite}:${isSelected(item) ? 1 : 0}`;

// ---------------------------------------------------------------- the selection
const isSelected = (item) => state.selAll || state.sel.has(item.id);
const selCount = () => (state.selAll ? state.total : state.sel.size);
const selection = () => (state.selAll ? { view: { sort: sortFor(), folder: state.folder, kind: state.kind, q: viewQuery(), seed: state.seed } } : [...state.sel]);

function tileClick(e, i) {
  if (e.shiftKey && state.anchor >= 0) return rangeSel(state.anchor, i);
  if (e.ctrlKey || e.metaKey || selCount()) return toggleSel(i); // while some are selected, a click picks
  openViewer(i);
}

function toggleSel(i) {
  const item = itemAt(i);
  if (!item) return;
  if (state.selAll) { // from "everything" to everything but this one: only what's loaded can be listed
    state.selAll = false;
    for (const items of state.pages.values()) for (const it of items) state.sel.add(it.id);
  }
  if (state.sel.has(item.id)) state.sel.delete(item.id);
  else state.sel.add(item.id);
  state.anchor = i;
  selChanged();
}

async function rangeSel(a, b) {
  const [lo, hi] = a < b ? [a, b] : [b, a];
  for (let p = Math.floor(lo / PAGE); p <= Math.floor(hi / PAGE); p++) await loadPage(p);
  for (let i = lo; i <= hi; i++) { const it = itemAt(i); if (it) state.sel.add(it.id); }
  selChanged();
}

function selectAll() {
  state.selAll = true;
  state.sel.clear();
  selChanged();
}

function clearSel() {
  if (!state.sel.size && !state.selAll) return;
  state.sel.clear();
  state.selAll = false;
  state.anchor = -1;
  selChanged();
}

function selChanged() {
  draw();
  const n = selCount();
  const bar = $("selbar");
  bar.hidden = !n;
  document.querySelector(".statusbar").hidden = !!n;
  $("app").classList.toggle("picking", !!n);
  if (!n) return;
  const sel = selection();
  const trash = state.view === "trash";
  const stars = h("div.stars.small", { role: "group", "aria-label": "Rate them" }, [1, 2, 3, 4, 5].map((r) => h("button.star", { type: "button", title: `Rate ${r}`, "aria-label": `Rate ${r}`,
    onclick: () => bulk("rate", r) }, "★")), h("button.btn.small", { type: "button", title: "Clear the rating", onclick: () => bulk("rate", 0) }, "0"));
  bar.replaceChildren(
    h("span.sel-n", {}, state.selAll ? `All ${n.toLocaleString()} selected` : `${n.toLocaleString()} selected`),
    !state.selAll && n < state.total ? h("button.link", { type: "button", onclick: selectAll }, `Select all ${state.total.toLocaleString()}`) : null,
    h("span.spacer"),
    ...(trash ? [
      h("button.btn", { type: "button", onclick: async () => { await org.restoreFiles(sel); clearSel(); } }, icon("restore"), "Put back"),
      h("button.btn.danger", { type: "button", onclick: async () => { await org.emptyTrash(sel, n); clearSel(); } }, icon("trash"), "Empty these"),
    ] : [
      stars,
      h("button.btn", { type: "button", onclick: () => bulk("favorite", true), title: "Favourite (Ctrl+click the heart: unfavourite)" }, icon("heart"), "Favourite"),
      h("button.btn", { type: "button", onclick: () => org.tagDialog(sel, n) }, icon("tag"), "Tag…"),
      state.view === "album" ? h("button.btn", { type: "button", onclick: () => bulkAlbumOut() }, icon("album"), "Take out")
        : h("button.btn", { type: "button", onclick: () => org.albumDialog(sel, n) }, icon("album"), "Album…"),
      h("button.btn", { type: "button", onclick: () => org.moveDialog(sel, n, state.folders) }, icon("move"), "Move…"),
      h("button.btn", { type: "button", onclick: async () => { if (await org.trashFiles(sel, n)) clearSel(); } }, icon("trash"), "Delete"),
    ]),
    h("button.icon-btn", { type: "button", "aria-label": "Clear the selection", title: "Clear the selection (Esc)", onclick: clearSel }, icon("remove")));
}

async function bulk(action, value) {
  try {
    const r = await api("/ops/bulk", { method: "POST", body: { selection: selection(), action, value } });
    org.done(r, action === "rate" ? (value ? `Rated ${plural(r.count, "file")} ${value}.` : `Cleared ${plural(r.count, "rating")}.`) : `${plural(r.count, "file")} marked as favourite${r.count === 1 ? "" : "s"}.`);
  } catch (e) { toast(e.message, true); }
}

async function bulkAlbumOut() {
  try {
    const r = await api("/ops/bulk", { method: "POST", body: { selection: selection(), action: "unalbum", value: state.album } });
    org.done(r, `Took ${plural(r.count, "file")} out of ${state.album}.`);
    clearSel();
  } catch (e) { toast(e.message, true); }
}

// after anything that changed files: the counts, the lists and the grid
org.onChange(async () => {
  await Promise.all([loadFolders(), loadAlbums(), loadCharacters()]);
  if (!isPage()) reload(false);
  else if (state.view === "history") org.renderHistory($("stats"));
  pollSoon();
  if (!$("viewer").hidden) refreshViewer();
});

function draw() {
  const L = layout();
  const { gap, cols, size, row, groups, height } = L;
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
        if (!el || el.dataset.key !== tileKey(item)) {
          const hadFocus = el && document.activeElement === el;
          el?.remove();
          item.__i = i;
          el = tileFor(item);
          el.dataset.key = tileKey(item);
          sizer.append(el);
          tiles.set(i, el);
          if (hadFocus) el.focus({ preventScroll: true });
        }
        el.dataset.i = i;
        el.tabIndex = i === Math.max(0, state.focus) ? 0 : -1; // one tab stop for the grid; arrows move within it
        const pos = `${size},${c * (size + gap)},${g.top + g.head + r * row}`;
        if (el.dataset.pos === pos) continue;
        el.dataset.pos = pos;
        Object.assign(el.style, { width: `${size}px`, height: `${size}px`, left: `${c * (size + gap)}px`, top: `${g.top + g.head + r * row}px` });
      }
    }
  });
  for (const [i, el] of tiles) if (!want.has(i)) { el.remove(); tiles.delete(i); }
  for (const [gi, el] of heads) if (!wantHeads.has(gi)) { el.remove(); heads.delete(gi); }
  showEmpty();
}

function clearGrid() {
  for (const el of tiles.values()) el.remove();
  tiles.clear();
  for (const el of heads.values()) el.remove();
  heads.clear();
}

function reload(toTop = false) {
  const hadFocus = !toTop && document.activeElement?.classList.contains("tile");
  state.gen++;
  state.pages.clear();
  state.loading.clear();
  clearGrid();
  if (toTop) { scroller.scrollTop = 0; state.focus = -1; }
  renderToolbar();
  Promise.all([loadPage(0), loadDays()]).then(() => {
    draw();
    if (hadFocus && state.focus >= 0) focusTile(Math.min(state.focus, state.total - 1)); // a refresh doesn't lose your place
  });
}

// keyboard: arrows move between tiles (Up/Down by a row, within and across days), Enter opens
function focusTile(i) {
  if (i < 0 || i >= state.total) return;
  state.focus = i;
  const L = layout(), p = place(i, L);
  const top = p.top + sizer.offsetTop, view = scroller.clientHeight;
  if (top < scroller.scrollTop) scroller.scrollTop = top - (p.r === 0 && p.g.label ? HEAD : 8);
  else if (top + L.size > scroller.scrollTop + view) scroller.scrollTop = top + L.size - view + 8;
  draw();
  const go = () => { const el = tiles.get(i); if (el) { el.focus({ preventScroll: true }); return true; } return false; };
  if (!go()) loadPage(Math.floor(i / PAGE)).then(() => { draw(); go(); });
}

function gridKey(e) {
  if (!e.target.classList?.contains("tile")) return false;
  const i = Number(e.target.dataset.i);
  if (!(i >= 0)) return false;
  state.focus = i;
  const L = layout(), p = place(i, L);
  const g = p.g;
  let to = null;
  if (e.key === "ArrowRight") to = i + 1;
  else if (e.key === "ArrowLeft") to = i - 1;
  else if (e.key === "ArrowDown") {
    // the same column on the next row, or the next day's first row
    if ((p.r + 1) * L.cols < g.n) to = Math.min(g.start + g.n - 1, i + L.cols);
    else { const next = L.groups[p.gi + 1]; if (next) to = next.start + Math.min(p.c, next.n - 1); }
  } else if (e.key === "ArrowUp") {
    if (p.r > 0) to = i - L.cols;
    else { const prev = L.groups[p.gi - 1]; if (prev) { const lastRow = Math.floor((prev.n - 1) / L.cols); to = prev.start + Math.min(prev.n - 1, lastRow * L.cols + p.c); } }
  } else if (e.key === "Home") to = 0;
  else if (e.key === "End") to = state.total - 1;
  else if (e.key === "PageDown" || e.key === "PageUp") {
    const rows = Math.max(1, Math.floor(scroller.clientHeight / L.row) - 1);
    to = Math.max(0, Math.min(state.total - 1, i + (e.key === "PageDown" ? 1 : -1) * rows * L.cols));
  } else if (e.key === " ") { openViewer(i); return true; }
  else if (/^[0-5]$/.test(e.key)) { rate(itemAt(i), Number(e.key)); return true; }
  else if (e.key.toLowerCase() === "f") { favourite(itemAt(i)); return true; }
  else return false;
  focusTile(Math.max(0, Math.min(state.total - 1, to)));
  return true;
}

function showCount() {
  $("count").textContent = state.total ? plural(state.total, "item") : "";
}

function showEmpty() {
  const empty = $("empty");
  if (state.total || isPage()) { empty.hidden = true; return; }
  empty.hidden = false;
  if (!state.folders.length) {
    empty.replaceChildren(h("h2", {}, "Add a folder to start"),
      h("p", {}, "Pick a folder with your images and videos, such as ComfyUI's output folder. The Gallery reads it and moves or deletes files only when you ask."),
      h("button.btn.primary", { type: "button", onclick: addFolder }, "Add a folder"),
      h("ul.setup", { id: "setup" }));
    welcome();
  } else if (queryString()) {
    empty.replaceChildren(h("h2", {}, "No matches"), h("p", {}, "Nothing here matches this search."),
      h("button.btn", { type: "button", onclick: clearSearch }, "Clear the search"));
  } else if (state.view === "trash") {
    empty.replaceChildren(h("h2", {}, "The Trash is empty"), h("p", {}, "Files you delete in the Gallery wait here, so you can put them back. Emptying it sends them to the Windows Recycle Bin."));
  } else if (state.view === "album") {
    empty.replaceChildren(h("h2", {}, "Nothing in this album yet"), h("p", {}, "Select files (Ctrl+click) and choose Album…, or name the album in a workflow's Gallery Info node."));
  } else if (state.view === "recent") {
    empty.replaceChildren(h("h2", {}, "Nothing viewed yet"), h("p", {}, "Files you open appear here, the last one first."));
  } else if (lastStatus && (lastStatus.scanning || lastStatus.queued)) {
    empty.replaceChildren(h("h2", {}, "Indexing…"), h("p", {}, "Your files appear here as they're found."));
  } else {
    empty.replaceChildren(h("h2", {}, "Nothing here"), h("p", {}, state.kind ? "No files of that kind in this view." : "No images or videos were found in this folder."));
  }
}

// the first start: what the Gallery found by itself, and the Start menu
async function welcome() {
  const s = await api("/settings").catch(() => null);
  const box = $("setup");
  if (!s || !box) return;
  const item = (ok, yes, no) => h("li" + (ok ? ".ok" : ""), {}, h("span.mark", { "aria-hidden": "true" }, ok ? "✓" : "·"), ok ? yes : no);
  box.replaceChildren(
    item(!!s.ml_data_found, "Model Librarian found: your models are linked to your files", "Model Librarian not found (optional): set its folder in Settings to link your models"),
    item(!!s.ffmpeg_found, "ffmpeg found: videos get posters and lengths", "ffmpeg not found (optional): videos show without a poster"),
    s.shortcut === null ? null : s.shortcut ? item(true, "In the Start menu", "")
      : h("li", {}, h("span.mark", { "aria-hidden": "true" }, "·"), h("button.link", { type: "button", onclick: async (e) => {
        try { await api("/shortcut", { method: "POST" }); e.currentTarget.closest("li").replaceWith(item(true, "In the Start menu", "")); } catch (ex) { toast(ex.message, true); }
      } }, "Add the Gallery to the Start menu")));
}

function clearSearch() {
  state.chips = [];
  state.text = "";
  $("q").value = "";
  searchChanged();
}

// ---------------------------------------------------------------- marks: rating and favourite
function updateItem(id, patch) {
  for (const items of state.pages.values()) for (const it of items) if (it.id === id) Object.assign(it, patch);
  draw();
  if (viewing >= 0 && itemAt(viewing)?.id === id) renderMarks(itemAt(viewing));
}

async function rate(item, n) {
  if (!item) return;
  const rating = item.rating === n ? 0 : n; // the same number again clears it
  try {
    const r = await api(`/file/${item.id}/mark`, { method: "POST", body: { rating } });
    updateItem(item.id, { rating: r.rating });
  } catch (e) { toast(e.message, true); }
}

async function favourite(item) {
  if (!item) return;
  try {
    const r = await api(`/file/${item.id}/mark`, { method: "POST", body: { favorite: !item.favorite } });
    updateItem(item.id, { favorite: r.favorite });
  } catch (e) { toast(e.message, true); }
}

function renderMarks(item) {
  $("v-stars").replaceChildren(...[1, 2, 3, 4, 5].map((n) => h("button.star" + (n <= item.rating ? ".on" : ""),
    { type: "button", "aria-label": `${n} star${n > 1 ? "s" : ""}`, "aria-pressed": String(n <= item.rating), title: `${n} (press ${n})`,
      onclick: () => rate(item, n) }, "★")));
  $("v-fav").setAttribute("aria-pressed", String(!!item.favorite));
  $("v-fav").classList.toggle("on", !!item.favorite);
}

// ---------------------------------------------------------------- the viewer
let viewing = -1, viewedTimer = null, infoGen = 0;

function openViewer(i) {
  const item = itemAt(i);
  if (!item) return;
  viewing = i;
  state.focus = i;
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
  renderMarks(item);
  loadDetails(item);
  clearTimeout(viewedTimer);
  viewedTimer = setTimeout(() => api(`/file/${item.id}/viewed`, { method: "POST" }).catch(() => {}), 800);
  if (!itemAt(i + 1) && i + 1 < state.total) loadPage(Math.floor((i + 1) / PAGE));
  if (wasHidden) $("v-close").focus({ focusVisible: false });
}

const folderOf = (item) => state.folders.find((f) => f.id === item.folder_id);

async function loadDetails(item) {
  const gen = ++infoGen;
  $("v-panel").setAttribute("aria-busy", "true");
  try {
    const info = await api(`/file/${item.id}/info`);
    if (gen !== infoGen) return;
    renderDetails(info);
  } catch (e) {
    if (gen === infoGen) $("v-panel").replaceChildren(h("p.note", {}, e.message));
  } finally {
    if (gen === infoGen) $("v-panel").removeAttribute("aria-busy");
  }
}

const KIND = { checkpoint: "CKPT", unet: "UNET", lora: "LORA", vae: "VAE" };

function section(title, ...kids) {
  return h("section", {}, h("h3", {}, title), ...kids);
}

function filterLink(key, value, label = value) {
  return h("button.link", { type: "button", title: `Show everything with ${key}:${value}`, onclick: () => { closeViewer(); addChip(key, value); } }, label);
}

function promptBlock(text, cls = "") {
  const long = text.length > 420;
  const body = h("div.prompt" + cls + (long ? ".clamp" : ""), {}, text);
  return h("div.prompt-wrap", {}, body,
    h("div.row.tight", {},
      h("button.btn.small", { type: "button", onclick: () => copyText(text, "Prompt copied") }, icon("copy"), "Copy"),
      long ? h("button.btn.small", { type: "button", onclick: (e) => { body.classList.toggle("clamp"); e.currentTarget.textContent = body.classList.contains("clamp") ? "Show all" : "Show less"; } }, "Show all") : null));
}

function renderDetails(info) {
  const g = info.generation;
  const parts = [];
  if (g && g.source) {
    if (g.positive) parts.push(section("Prompt", promptBlock(g.positive)));
    if (g.negative) parts.push(section("Negative", promptBlock(g.negative, ".neg")));
    const settings = [["Seed", g.seed, "seed"], ["Steps", g.steps, "steps"], ["CFG", g.cfg], ["Sampler", g.sampler, "sampler"],
      ["Scheduler", g.scheduler, "scheduler"], ["Size", g.width && g.height ? `${g.width} × ${g.height}` : null],
      g.denoise != null && g.denoise !== 1 ? ["Denoise", g.denoise] : null, g.clip_skip ? ["Clip skip", g.clip_skip] : null].filter((s) => s && s[1] != null);
    if (settings.length) {
      parts.push(section("Settings", h("div.settings", {}, settings.map(([k, v, key]) => h("div.set", {}, h("span.k", {}, k),
        key ? filterLink(key, String(v), typeof v === "number" ? fmtNum(v) : v) : h("span.v", {}, typeof v === "number" ? fmtNum(v) : v))))));
    }
    if (info.uses.length) {
      parts.push(section("Models", h("div.models", {}, info.uses.map((u) => h("div.model", {},
        h("span.kind." + u.kind, {}, KIND[u.kind] || u.kind.toUpperCase()),
        filterLink(u.kind === "lora" ? "lora" : u.kind === "vae" ? "vae" : "model", u.name),
        u.kind === "lora" && u.strength != null ? h("span.strength", { title: u.clip != null && u.clip !== u.strength ? `model ${fmtNum(u.strength)} · clip ${fmtNum(u.clip)}` : `strength ${fmtNum(u.strength)}` },
          h("span.bar", {}, h("span", { style: { width: `${Math.max(4, Math.min(100, Math.abs(u.strength) * 100))}%` } })), fmtNum(u.strength)) : null)))));
    }
    if (g.has_workflow) {
      parts.push(section("Workflow", h("p.note", {}, `${g.source === "comfyui" ? "ComfyUI" : g.source}${g.nodes ? ` · ${plural(g.nodes, "node")}` : ""}`),
        h("div.row", {},
          h("button.btn.primary", { type: "button", title: "Opens it in a new tab in ComfyUI (with Model Librarian)", onclick: (e) => openInComfy(info, e.currentTarget) }, icon("play"), "Open in ComfyUI"),
          h("button.btn", { type: "button", title: "Into Model Librarian's recipes", onclick: (e) => saveRecipe(info, e.currentTarget) }, icon("save"), "Save as recipe"),
          h("button.btn", { type: "button", onclick: () => copyWorkflow(info) }, icon("copy"), "Copy workflow"),
          h("a.btn", { href: `/api/file/${info.id}/workflow?download=1&t=${encodeURIComponent(TOKEN)}`, download: "" }, icon("save"), "Save as .json"))));
    } else if (g.source && g.source !== "comfyui") {
      parts.push(section("Made with", h("p.note", {}, { a1111: "Automatic1111 / Forge", novelai: "NovelAI", invokeai: "InvokeAI" }[g.source] || g.source)));
    }
  } else {
    parts.push(section("What made it", h("p.note", {}, "No generation data in this file. It may have been saved without it, or edited since.")));
  }
  const gi = g && g.info;
  if (gi) {
    const bits = [["Albums", gi.albums], ["Characters", gi.characters], ["Project", gi.project ? [gi.project] : []]].filter(([, v]) => v && v.length);
    if (bits.length || gi.note) {
      parts.push(section("From the workflow",
        bits.length ? h("dl", {}, bits.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v.join(", "))])) : null,
        gi.note ? h("p.note", {}, gi.note) : null));
    }
  }
  parts.push(section("Characters", characterEditor(info)));
  parts.push(section("Albums", albumEditor(info)));
  parts.push(section("Tags", tagEditor(info)));
  const rows = [
    ["Type", `${info.kind === "video" ? "Video" : "Image"} · ${info.name.split(".").pop().toUpperCase()}`],
    ["Dimensions", info.width && info.height ? `${info.width} × ${info.height}` : "—"],
    info.kind === "video" ? ["Length", fmtDur(info.duration) || "—"] : null,
    ["Size", fmtSize(info.size)],
    ["Modified", fmtDate(info.mtime)],
  ].filter(Boolean);
  parts.push(section("File", h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))));
  parts.push(section("Location", h("div.path", {}, info.path),
    h("div.row", {},
      h("button.btn", { type: "button", onclick: () => reveal(info) }, icon("explorer"), "Show in Explorer"),
      h("button.btn", { type: "button", onclick: () => copyText(info.path, "Path copied") }, icon("copy"), "Copy path"),
      state.view === "trash" ? h("button.btn", { type: "button", onclick: () => org.restoreFiles([info.id]) }, icon("restore"), "Put back")
        : h("button.btn", { type: "button", onclick: () => org.moveDialog([info.id], 1, state.folders) }, icon("move"), "Move…"),
      state.view === "trash" ? null : h("button.btn", { type: "button", title: "Move to the Trash (Delete)", onclick: () => deleteViewing() }, icon("trash"), "Delete"))));
  $("v-panel").replaceChildren(...parts);
}

async function openInComfy(info, btn) {
  btn.disabled = true;
  try {
    await api(`/file/${info.id}/open-in-comfy`, { method: "POST" });
    toast("Sent to ComfyUI: it opens in a new tab there.");
  } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
}

async function saveRecipe(info, btn) {
  btn.disabled = true;
  try {
    const r = await api(`/file/${info.id}/recipe`, { method: "POST", body: {} });
    toast(`Saved as the recipe "${r.title}": it's in Model Librarian's Recipes tab.`);
  } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
}

async function copyWorkflow(info) {
  try {
    const wf = await api(`/file/${info.id}/workflow`);
    await copyText(JSON.stringify(wf), "Workflow copied: paste it into ComfyUI (Ctrl+V)");
  } catch (e) { toast(e.message, true); }
}

const STATE_TIP = { auto: "Recognised by its rules", confirmed: "Confirmed", review: "A weak rule matched: is it them?" };

function characterEditor(info) {
  const wrap = h("div.tags");
  const render = (list) => {
    const known = new Set(list.map((c) => c.id));
    const others = (state.characters || []).filter((c) => !known.has(c.id));
    const pick = h("select.pick", { "aria-label": "Add a character" }, h("option", { value: "" }, "+ Character"),
      others.map((c) => h("option", { value: c.id }, c.name)));
    pick.onchange = () => { if (pick.value) change(Number(pick.value), "confirm"); };
    wrap.replaceChildren(...list.map((c) => h("span.chip.tag.char." + c.state, { title: `${STATE_TIP[c.state] || ""}${c.source === "workflow" ? " (by the workflow's Gallery Info node)" : ""}` },
      filterLink("character", c.name),
      c.state === "review" ? h("button.x", { type: "button", "aria-label": `Yes, ${c.name} is in it`, title: "Yes", onclick: () => change(c.id, "confirm") }, icon("check")) : null,
      h("button.x", { type: "button", "aria-label": `${c.name} isn't in it`, title: `Not ${c.name}`, onclick: () => change(c.id, "reject") }, icon("remove")))),
      others.length ? pick : null,
      !list.length && !others.length ? h("button.link.dim", { type: "button", onclick: () => editCharacter(null, pageCtx) }, "New character…") : null);
  };
  const change = async (cid, verdict) => {
    try {
      const { characters } = await api(`/file/${info.id}/characters`, { method: "POST", body: { character: cid, verdict } });
      render(characters);
      loadCharacters();
    } catch (e) { toast(e.message, true); }
  };
  render(info.characters || []);
  return wrap;
}

function albumEditor(info) {
  const wrap = h("div.tags");
  const render = (list) => {
    const known = new Set(list.map((a) => a.id));
    const others = state.albums.filter((a) => !known.has(a.id));
    const pick = h("select.pick", { "aria-label": "Add to an album" }, h("option", { value: "" }, "+ Album"),
      others.map((a) => h("option", { value: a.name }, a.name)), h("option", { value: "\u0000new" }, "New album…"));
    pick.onchange = async () => {
      let name = pick.value;
      if (name === "\u0000new") name = (prompt("A name for the new album:") || "").trim();
      if (name) change("album", name);
      else pick.value = "";
    };
    wrap.replaceChildren(...list.map((a) => h("span.chip.tag" + (a.source === "workflow" ? ".wf" : ""), { title: a.source === "workflow" ? "From the workflow's Gallery Info node" : "" },
      h("button.link", { type: "button", onclick: () => { closeViewer(); selectView("album", a.name); } }, a.name),
      h("button.x", { type: "button", "aria-label": `Take it out of ${a.name}`, onclick: () => change("unalbum", a.name) }, icon("remove")))), pick);
  };
  const change = async (action, name) => {
    try {
      await api("/ops/bulk", { method: "POST", body: { selection: [info.id], action, value: name } });
      await loadAlbums();
      render((await api(`/file/${info.id}/info`)).albums);
    } catch (e) { toast(e.message, true); }
  };
  render(info.albums || []);
  return wrap;
}

// the file in the viewer to the Trash; the next one takes its place
async function deleteViewing() {
  const item = itemAt(viewing);
  if (!item) return;
  const at = viewing;
  const r = await org.trashFiles([item.id], 1, { ask: false });
  if (!r || !r.trashed) return;
  state.pages.clear();
  state.total = Math.max(0, state.total - 1);
  if (!state.total) return closeViewer();
  const i = Math.min(at, state.total - 1);
  await loadPage(Math.floor(i / PAGE));
  openViewer(i);
}

async function refreshViewer() {
  const item = itemAt(viewing);
  if (item) loadDetails(item);
}

function tagEditor(info) {
  const wrap = h("div.tags");
  const input = h("input.tag-input", { type: "text", placeholder: "Add a tag", "aria-label": "Add a tag", autocomplete: "off", spellcheck: false });
  const list = h("datalist", { id: `tags-${info.id}` });
  input.setAttribute("list", list.id);
  const render = (tags) => {
    wrap.replaceChildren(...tags.map((t) => h("span.chip.tag" + (t.source === "workflow" ? ".wf" : ""), { title: t.source === "workflow" ? "From the workflow's Gallery Info node" : "Your tag" },
      filterLink("tag", t.name),
      h("button.x", { type: "button", "aria-label": `Remove the tag ${t.name}`, onclick: () => change({ remove: [t.name] }) }, icon("remove")))), input, list);
  };
  const change = async (body) => {
    try {
      const { tags } = await api(`/file/${info.id}/tags`, { method: "POST", body });
      render(tags);
      input.focus();
    } catch (e) { toast(e.message, true); }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && input.value.trim()) { e.preventDefault(); change({ add: [input.value.trim()] }); input.value = ""; }
    e.stopPropagation(); // typing here isn't a shortcut
  });
  input.addEventListener("input", async () => {
    const { items } = await api(`/suggest?field=tag&q=${encodeURIComponent(input.value)}`).catch(() => ({ items: [] }));
    list.replaceChildren(...items.map((t) => h("option", { value: t.name })));
  });
  render(info.tags);
  return wrap;
}

async function reveal(info) {
  await api(`/file/${info.id}/reveal`, { method: "POST" }).catch((e) => toast(e.message, true));
}

function setDetails(open) {
  $("viewer").classList.toggle("no-details", !open);
  $("v-details").setAttribute("aria-pressed", String(open));
  $("v-details").title = open ? "Hide details (I)" : "Show details (I)";
  store.set("details", open);
}

function closeViewer() {
  if ($("viewer").hidden) return;
  $("viewer").hidden = true;
  $("v-media").replaceChildren();
  const i = viewing;
  viewing = -1;
  if (state.view === "recent") reload(false); // what you just opened moved to the top
  else if (i >= 0) focusTile(i);
}

function step(d) {
  const i = viewing + d;
  if (i < 0 || i >= state.total) return;
  if (itemAt(i)) openViewer(i);
  else loadPage(Math.floor(i / PAGE)).then(() => openViewer(i));
}

// ---------------------------------------------------------------- status
let lastStatus = null, pollTimer = null, lastFiles = -1, lastMeta = -1;

async function poll() {
  clearTimeout(pollTimer);
  try {
    const st = await api("/status");
    lastStatus = st;
    const busy = st.scanning || st.queued || st.thumbs_pending || st.meta_pending;
    $("status-dot").classList.toggle("busy", !!busy);
    $("status").textContent = st.scanning ? `Indexing ${baseName(st.scanning.path)}: ${plural(st.scanning.seen || 0, "file")} found`
      : st.queued ? "Indexing…"
      : st.thumbs_pending ? `Making thumbnails: ${st.thumbs_pending.toLocaleString()} to go`
      : st.meta_pending ? `Reading prompts and settings: ${st.meta_pending.toLocaleString()} to go`
      : st.files ? `Up to date · ${plural(st.files, "file")}` : "Up to date";
    $("status-right").textContent = st.thumbs_failed ? `${plural(st.thumbs_failed, "file")} without a thumbnail` : "";
    const searching = !!queryString();
    if (st.files !== lastFiles || (searching && st.meta_pending !== lastMeta)) {
      lastFiles = st.files;
      await loadFolders();
      if ($("viewer").hidden && !isPage()) reload(false); // never pull the list from under an open file
    } else if (st.thumbs_pending && $("viewer").hidden && !isPage()) {
      // thumbnails made meanwhile: tiles waiting for one pick it up when their page is fetched again
      for (const p of [...state.pages.keys()]) state.pages.delete(p);
      draw();
    }
    lastMeta = st.meta_pending;
    $("trash-count").textContent = st.trash ? st.trash.toLocaleString() : "";
    loadCharacters(); // matches change as files are read
    pollTimer = setTimeout(poll, busy ? 1500 : 4000);
  } catch {
    pollTimer = setTimeout(poll, 4000);
  }
}
const pollSoon = () => { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 300); };

// ---------------------------------------------------------------- wiring
$("add").onclick = addFolder;
$("view-all").onclick = () => selectView("all");
$("view-recent").onclick = () => selectView("recent");
$("view-random").onclick = () => selectView("random");
$("view-stats").onclick = () => selectView("stats");
$("view-trash").onclick = () => selectView("trash");
$("view-duplicates").onclick = () => selectView("duplicates");
$("view-history").onclick = () => selectView("history");
$("view-settings").onclick = () => selectView("settings");
$("album-new").onclick = newAlbum;
$("empty-trash").onclick = () => org.emptyTrash(null, state.total);
$("view-characters").onclick = () => selectView("characters");
$("view-review").onclick = () => selectView("review");
$("shuffle").onclick = () => selectView("random");
$("side-hide").onclick = () => setSide(true);
$("side-show").onclick = () => setSide(false);
$("sort").value = state.sort;
$("tile").value = state.tile;
$("sort").onchange = (e) => { state.sort = e.target.value; store.set("sort", state.sort); reload(true); };
for (const b of $("kind").querySelectorAll("button")) {
  b.onclick = () => { state.kind = b.dataset.kind; store.set("kind", state.kind); reload(true); };
}
$("tile").oninput = (e) => { state.tile = Number(e.target.value); store.set("tile", state.tile); draw(); };
$("f-fav").onclick = () => {
  if (state.chips.some((c) => c.key === "is" && c.value === "fav")) removeChips((c) => c.key === "is" && c.value === "fav");
  else addChip("is", "fav");
};
$("f-rating").onchange = (e) => {
  const v = e.target.value;
  state.chips = state.chips.filter((c) => c.key !== "rating");
  if (v !== "") state.chips.push({ key: "rating", value: v === "0" ? "0" : v === "5" ? "5" : `>=${v}` });
  searchChanged();
};
$("q").addEventListener("input", onInput);
$("q").addEventListener("focus", showSuggest);
$("q").addEventListener("blur", () => setTimeout(() => { $("suggest").hidden = true; }, 120));
$("q").addEventListener("keydown", (e) => {
  const input = $("q");
  if (e.key === "ArrowDown" && moveSuggest(1)) e.preventDefault();
  else if (e.key === "ArrowUp" && moveSuggest(-1)) e.preventDefault();
  else if (e.key === "Enter") {
    e.preventDefault();
    const items = $("suggest")._items || [];
    if (!$("suggest").hidden && suggestIndex >= 0 && items[suggestIndex]) return items[suggestIndex].pick();
    const { rest } = takeChips(input.value, true);
    input.value = rest;
    state.text = rest;
    $("suggest").hidden = true;
    searchChanged();
  } else if (e.key === "Backspace" && !input.value && state.chips.length) {
    state.chips.pop();
    searchChanged();
  } else if (e.key === "Escape") {
    if (!$("suggest").hidden) $("suggest").hidden = true;
    else if (input.value || state.chips.length) clearSearch();
    else input.blur();
  }
  e.stopPropagation();
});
$("q-clear").onclick = () => { clearSearch(); $("q").focus(); };
$("search").addEventListener("mousedown", (e) => { if (e.target === $("search") || e.target === $("chips")) { e.preventDefault(); $("q").focus(); } });
scroller.addEventListener("scroll", () => requestAnimationFrame(draw), { passive: true });
new ResizeObserver(() => draw()).observe(scroller);
$("v-close").onclick = closeViewer;
$("v-prev").onclick = () => step(-1);
$("v-next").onclick = () => step(1);
$("v-fav").onclick = () => favourite(itemAt(viewing));
$("v-details").onclick = () => setDetails($("viewer").classList.contains("no-details"));
document.addEventListener("keydown", (e) => {
  const inField = e.target.closest?.("input, select, textarea");
  if (document.querySelector("dialog[open], .preview")) return;
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z" && !inField) {
    e.preventDefault();
    org.undoLast();
    return;
  }
  if ($("viewer").hidden && !inField && !isPage()) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); selectAll(); return; }
    if (e.key === "Escape" && selCount()) { e.preventDefault(); clearSel(); return; }
    if (e.key === "Delete" && state.view !== "trash") {
      const one = !selCount() && e.target.classList?.contains("tile") ? itemAt(Number(e.target.dataset.i)) : null;
      if (selCount()) { e.preventDefault(); org.trashFiles(selection(), selCount()).then((r) => r && clearSel()); return; }
      if (one) { e.preventDefault(); org.trashFiles([one.id], 1, { ask: false }); return; }
    }
  }
  if (!$("viewer").hidden && !inField && e.key === "Delete" && state.view !== "trash") { e.preventDefault(); deleteViewing(); return; }
  if (e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
    e.preventDefault();
    if ($("viewer").hidden) setSide(!$("app").classList.contains("side-hidden"));
    return;
  }
  if ((e.ctrlKey && e.key.toLowerCase() === "f") || (e.key === "/" && !inField)) {
    if (!$("viewer").hidden) return;
    e.preventDefault();
    $("q").focus();
    $("q").select();
    return;
  }
  if (inField || e.ctrlKey || e.altKey || e.metaKey) return;
  if ($("viewer").hidden) {
    if (gridKey(e)) e.preventDefault();
    else if (e.key === "ArrowDown" || e.key === "ArrowRight") { if (state.total && !e.target.classList?.contains("tile")) { e.preventDefault(); focusTile(Math.max(0, state.focus)); } }
    return;
  }
  if (e.key === "Escape") closeViewer();
  else if (e.key === "ArrowRight") step(1);
  else if (e.key === "ArrowLeft") step(-1);
  else if (e.key.toLowerCase() === "i") setDetails($("viewer").classList.contains("no-details"));
  else if (/^[0-5]$/.test(e.key)) rate(itemAt(viewing), Number(e.key));
  else if (e.key.toLowerCase() === "f") favourite(itemAt(viewing));
});

setSide(store.get("sideHidden", false));
setDetails(store.get("details", true));
renderChips();
loadCharacters();
loadAlbums();
loadFolders().then(() => { reload(true); poll(); });
