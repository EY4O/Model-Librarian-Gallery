// Model Librarian Gallery: the library grid, folders, search and the viewer.
// The grid is virtual: only the tiles on screen exist, and items are fetched a page at a time, so it
// scrolls the same at 200 files or 250,000. Day headings come from a per-day count (one small request),
// so every tile's place is known without fetching the pages above it.

import { $, LOOK_COLORS, LOOK_ICONS, TOKEN, api, ask, askText, baseName, closeMenu, copyText, dayLabel, fmtDate, fmtDur, fmtNum, fmtSize, h, icon, lookIcon, media, menu, plural, stars, store, toast } from "./util.js";
import { renderStats } from "./stats.js";
import { editCharacter, renderCharacter, renderCharacters, renderReview } from "./characters.js";
import * as org from "./organize.js";
import { renderSettings } from "./settings.js";

const PAGE = 300;
const HEAD = 40;      // a day heading's height
const GROUP_GAP = 14; // space under a day's last row

const SORT_NAMES = { newest: "Newest first", oldest: "Oldest first", name: "Name A–Z", name_desc: "Name Z–A", largest: "Largest first", smallest: "Smallest first" };
// the View panel's sort: what it's by, then which way round
const SORT_BY = { date: ["newest", "oldest"], name: ["name", "name_desc"], size: ["largest", "smallest"] };
const SORT_BY_NAMES = { date: "Date made", name: "Name", size: "File size" };
const INFO_NAMES = { dims: "Dimensions", size: "File size", date: "Date made", model: "Checkpoint", type: "File type" };
const SHOW = { name: false, info: false, infoKind: "dims", ext: false, badges: true, made: true };
const TILE_MIN = 100, TILE_MAX = 480;
const KIND_NAMES = { "": "Images and videos", image: "Images only", video: "Videos only" };

// ---------------------------------------------------------------- state
const state = {
  sel: new Set(), selAll: false, anchor: -1,  // the selection: ids, or the whole view
  albums: [], album: null,
  folders: [], view: "all", folder: null, seed: 1 + Math.floor(Math.random() * 1e9),
  sort: store.get("sort", "newest"), kind: store.get("kind", ""), tile: store.get("tile", 176),
  layout: store.get("layout", "waterfall"), quality: store.get("quality", "speed"), show: { ...SHOW, ...store.get("show", {}) }, shapes: null,
  chips: [], text: "",                     // the search: filter chips, and the words typed
  total: 0, groups: [], pages: new Map(), loading: new Map(), gen: 0, focus: -1, maxId: 0, selUpto: 0, stale: false,
};

// ---------------------------------------------------------------- the search
const FIELDS = ["model", "lora", "tag", "sampler", "scheduler", "seed", "steps", "rating", "is", "has", "no", "neg", "vae", "ext",
  "cfg", "size", "after", "before", "on", "date", "strength", "character", "review", "album", "color"];
const SUGGESTED = new Set(["model", "lora", "tag", "sampler", "scheduler", "vae", "color"]);
// the colour families' own swatches (as in colors.py), for color: chips
const COLOR_FAMILIES = { red: "#e53935", orange: "#fb8c00", yellow: "#fdd835", green: "#43a047", cyan: "#00acc1", blue: "#1e88e5",
  purple: "#8e24aa", pink: "#ec407a", brown: "#795548", black: "#212121", gray: "#9e9e9e", grey: "#9e9e9e", white: "#f5f5f5" };
const colorOf = (v) => COLOR_FAMILIES[v.toLowerCase()] || (/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v) ? (v.startsWith("#") ? v : `#${v}`) : null);
const quote = (v) => (/[\s"]/.test(v) ? `"${v.replaceAll('"', "")}"` : v);
const chipText = (c) => `${c.key}:${quote(c.value)}`;
const queryString = () => [...state.chips.map(chipText), state.text.trim()].filter(Boolean).join(" ");
const HINTS = [
  ["model:", "a checkpoint or diffusion model"], ["lora:", "a LoRA"], ["tag:", "one of your tags"],
  ["lora:name@0.6-0.8", "a LoRA used at that strength"], ["date:7d", "made in the last 7 days (or today, yesterday, 2026-10)"],
  ["sampler:", "a sampler"], ["seed:", "an exact seed"], ["rating:4", "rated 4 or more"], ["character:", "a character"],
  ["is:fav", "your favourites"], ["has:workflow", "a ComfyUI workflow is saved"], ["no:prompt", "no generation data"],
  ["neg:", "words in the negative prompt"], ["color:", "a colour: red, blue… or #3a7bd5"],
];

function renderChips() {
  $("chips").replaceChildren(...state.chips.map((c, i) => h("span.chip", { title: chipText(c) },
    h("span.k", {}, c.key), c.key === "color" && colorOf(c.value) ? h("i.chip-sw", { style: { background: colorOf(c.value) } }) : null, h("span.v", {}, c.value),
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
    renderSuggest(items.map((it) => ({ label: it.name, hint: it.n != null ? it.n.toLocaleString() : "", swatch: it.swatch, pick: () => pickSuggestion(field, it.name, m[0]) })),
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
      h("span", {}, it.swatch ? h("i.chip-sw.s-sw", { style: { background: it.swatch } }) : null, it.label), h("span.s-hint", {}, it.hint))),
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
      "aria-current": sel ? "true" : null, oncontextmenu: (e) => { e.preventDefault(); lookMenu(e.currentTarget, "folder", f); } },
      lookIcon(f, "folder"),
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
    return h("button.nav-item" + (sel ? ".sel" : ""), { type: "button", onclick: () => selectView("album", a.name), "aria-current": sel ? "true" : null,
      oncontextmenu: (e) => { e.preventDefault(); lookMenu(e.currentTarget, "album", a); } },
      lookIcon(a, "album"), h("span.label", {}, a.name), h("span.n", {}, a.n.toLocaleString()),
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

// right-click on a folder or album: its icon and colour (Eagle's), and what else can be done with it
function lookMenu(anchor, kind, it) {
  const set = async (body) => {
    try {
      await api(`/${kind === "folder" ? "folders" : "albums"}/${it.id}/look`, { method: "POST", body });
      await (kind === "folder" ? loadFolders() : loadAlbums());
    } catch (e) { toast(e.message, true); }
  };
  const colours = h("div.look-colors", { role: "group", "aria-label": "Colour" },
    h("button.look-color.none" + (it.color ? "" : ".on"), { type: "button", title: "No colour", "aria-label": "No colour", onclick: () => { closeMenu(); set({ color: null }); } }),
    LOOK_COLORS.map((c) => h("button.look-color" + (it.color === c ? ".on" : ""), { type: "button", style: { background: c }, "aria-label": `Colour ${c}`,
      onclick: () => { closeMenu(); set({ color: c }); } })));
  const name = kind === "folder" ? baseName(it.path) : it.name;
  menu(anchor, [
    { label: "Change icon…", icon: "smile", onclick: () => iconPicker(anchor, it, (icon) => set({ icon })) },
    { node: colours },
    null,
    ...(kind === "folder"
      ? [{ label: "Look again", icon: "rescan", onclick: () => rescan(it) }, { label: "Show in Explorer", icon: "explorer", onclick: () => api(`/folders/${it.id}/reveal`, { method: "POST" }).catch((e) => toast(e.message, true)) }]
      : [{ label: "Rename…", icon: "pen", onclick: () => renameAlbum(it) }]),
    null,
    kind === "folder" ? { label: `Remove ${name} from the library`, icon: "remove", danger: true, onclick: () => removeFolder(it) }
      : { label: "Delete the album", icon: "remove", danger: true, onclick: () => deleteAlbum(it) },
  ]);
}

function iconPicker(anchor, it, pick) {
  const choose = (v) => { closeMenu(); pick(v); };
  const grid = h("div.look-grid", { role: "group", "aria-label": "Icons" }, LOOK_ICONS.map((n) => h("button.look-pick" + (it.icon === `svg:${n}` ? ".on" : ""),
    { type: "button", title: n, "aria-label": n, onclick: () => choose(`svg:${n}`) }, icon(n))));
  const nums = h("div.look-grid", { role: "group", "aria-label": "Numbers" }, Array.from({ length: 21 }, (_, n) => h("button.look-pick" + (it.icon === `n:${n}` ? ".on" : ""),
    { type: "button", "aria-label": `Number ${n}`, onclick: () => choose(`n:${n}`) }, h("span.look-num", {}, String(n)))));
  const emoji = h("input.look-emoji-in", { type: "text", maxLength: 8, placeholder: "🦊", "aria-label": "An emoji", value: (it.icon || "").startsWith("e:") ? it.icon.slice(2) : "",
    onkeydown: (e) => { e.stopPropagation(); if (e.key === "Enter" && emoji.value.trim()) choose(`e:${emoji.value.trim()}`); if (e.key === "Escape") closeMenu(); } });
  menu(anchor, [{ node: h("div.look-picker", {},
    h("div.menu-label", {}, "Icon"), grid,
    h("div.menu-label", {}, "Number"), nums,
    h("div.menu-label", {}, "Emoji"),
    h("div.row.tight", {}, emoji, h("button.btn.small", { type: "button", onclick: () => emoji.value.trim() && choose(`e:${emoji.value.trim()}`) }, "Use it"),
      h("span.spacer"), h("button.btn.small", { type: "button", onclick: () => choose(null) }, "Default"))) }], { cls: "look-menu" });
}

async function newAlbum() {
  const name = await askText("New album", "Name", "", { ok: "Create" });
  if (!name) return;
  try {
    await api("/albums", { method: "POST", body: { name } });
    await loadAlbums();
    if (selCount()) org.albumDialog(selection(), selCount());
  } catch (e) { toast(e.message, true); }
}

async function renameAlbum(a) {
  const name = await askText(`Rename ${a.name}`, "Name", a.name, { ok: "Rename" });
  if (!name || name === a.name) return;
  try {
    await api(`/albums/${a.id}`, { method: "POST", body: { name } });
    if (state.album === a.name) state.album = name;
    await loadAlbums();
    if (state.view === "album") reload(false);
  } catch (e) { toast(e.message, true); }
}

async function deleteAlbum(a) {
  if (!await ask(`Delete the album ${a.name}?`, `Only the album goes: its ${plural(a.n, "file")} stay exactly where they are.`, { ok: "Delete the album", danger: true })) return;
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
  closeMenu();
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
  $("empty-trash").hidden = state.view !== "trash" || !state.total;
  $("shuffle").hidden = state.view !== "random";
  // the View button says what's not the default, so a filter is never hidden in a menu
  const sorted = ["random", "recent", "trash"].includes(state.view) || state.sort === "newest" ? "" : SORT_NAMES[state.sort];
  const kind = state.kind ? KIND_NAMES[state.kind] : "";
  $("view-label").textContent = [kind, sorted].filter(Boolean).join(" · ") || "View";
  $("view-btn").classList.toggle("set", !!(kind || sorted));
}

function setSide(hidden) {
  $("app").classList.toggle("side-hidden", hidden);
  $("side-show").hidden = !hidden;
  store.set("sideHidden", hidden);
  requestAnimationFrame(() => { draw(); if (!$("viewer").hidden) vApply(); });
}

async function addFolder() {
  let path = "";
  try {
    path = (await api("/folders/pick", { method: "POST" })).path;
  } catch (e) {
    if (e.status !== 501) return toast(e.message, true);
    path = (await askText("Add a folder", "Its full path", "", { ok: "Add", placeholder: "D:\\ComfyUI\\output" })) || "";
  }
  if (!path) return;
  try {
    const { folder } = await api("/folders", { method: "POST", body: { path } });
    toast(`Added ${baseName(folder.path)}: indexing it now.`);
    await loadFolders();
    reload(false);
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
  if (!await ask(`Remove ${baseName(f.path)} from the library?`, `Only the Gallery's index of it goes (and its ratings, tags and albums): the folder and its files stay exactly where they are.`, { ok: "Remove", danger: true })) return;
  await api(`/folders/${f.id}`, { method: "DELETE" }).catch((e) => toast(e.message, true));
  await loadFolders();
  reload(true);
}

// ---------------------------------------------------------------- the grid
const scroller = $("scroll"), sizer = $("sizer");
const tiles = new Map(); // index -> element
const heads = new Map(); // group index -> element

let lastLayout = null;
// a caption under each tile when the name or the info line is shown
const captionHeight = () => (state.show.name || state.show.info ? 8 + (state.show.name ? 18 : 0) + (state.show.info ? 16 : 0) : 0);

function layout() {
  const gap = 8;
  const width = sizer.clientWidth;
  const cols = Math.max(1, Math.floor((width + gap) / (state.tile + gap)));
  const size = Math.floor((width - gap * (cols - 1)) / cols);
  const cap = captionHeight();
  const tileH = size + cap, row = tileH + gap;
  // day headings only while they add up to what's listed (mid-index they can lag behind: plain until the next refresh)
  const counted = state.groups.reduce((s, g) => s + g.n, 0);
  const daily = state.groups.length && counted === state.total ? state.groups : null;
  const water = state.layout === "waterfall" && !!state.shapes && state.shapes.length === state.total;
  const L0 = lastLayout;
  if (L0 && L0.width === width && L0.tile === state.tile && L0.cap === cap && L0.water === water && L0.daily === daily
      && L0.total === state.total && L0.shapes === state.shapes) return L0;
  const groups = daily || [{ label: null, n: state.total, start: 0 }];
  let y = 0;
  for (const g of groups) {
    g.top = y;
    g.head = g.label ? HEAD : 4;
    if (water) {
      // each file goes under the shortest column: tops only ever grow, so the visible ones are found by bisection
      const colH = new Float64Array(cols);
      g.wt = new Float32Array(g.n); g.wc = new Uint16Array(g.n); g.wh = new Float32Array(g.n);
      for (let j = 0; j < g.n; j++) {
        let c = 0;
        for (let k = 1; k < cols; k++) if (colH[k] < colH[c]) c = k;
        const hh = Math.round((size * state.shapes[g.start + j]) / 100) + cap;
        g.wt[j] = colH[c]; g.wc[j] = c; g.wh[j] = hh;
        colH[c] += hh + gap;
      }
      g.body = Math.max(0, Math.max(...colH) - gap);
    } else {
      g.rows = Math.ceil(g.n / cols);
      g.body = Math.max(0, g.rows * row - gap);
    }
    y += g.head + g.body + (g.label ? GROUP_GAP : 0);
  }
  lastLayout = { gap, cols, size, cap, tileH, row, groups, height: y, water, maxH: size * 3 + cap, width, tile: state.tile, daily, total: state.total, shapes: state.shapes };
  return lastLayout;
}

// where tile i sits: its group, row and column
function place(i, L = layout()) {
  let lo = 0, hi = L.groups.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (L.groups[mid].start <= i) lo = mid; else hi = mid - 1; }
  const g = L.groups[lo], j = i - g.start;
  if (L.water) return { g, gi: lo, j, c: g.wc[j], top: g.top + g.head + g.wt[j], left: g.wc[j] * (L.size + L.gap), h: g.wh[j] };
  const r = Math.floor(j / L.cols), c = j % L.cols;
  return { g, gi: lo, j, r, c, top: g.top + g.head + r * L.row, left: c * (L.size + L.gap), h: L.tileH };
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

function loadPage(p) {
  if (state.pages.has(p)) return Promise.resolve();
  if (state.loading.has(p)) return state.loading.get(p);
  const run = fetchPage(p);
  state.loading.set(p, run);
  return run;
}

async function fetchPage(p) {
  const gen = state.gen;
  try {
    const res = await api(query("/files", p * PAGE));
    if (gen !== state.gen) return; // the view changed while this was on its way
    state.pages.set(p, res.items);
    state.maxId = Math.max(state.maxId, res.max_id || 0);
    if (res.total !== state.total) { state.total = res.total; showCount(); if (state.groups.length) loadDays(); }
    draw();
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (gen === state.gen) state.loading.delete(p);
  }
}

async function loadShapes() {
  if (state.layout !== "waterfall") { state.shapes = null; return; }
  const gen = state.gen;
  try {
    const { shapes } = await api(query("/shapes"));
    if (gen !== state.gen) return;
    state.shapes = shapes;
    draw();
  } catch {
    state.shapes = null;
  }
}

async function loadDays() {
  const gen = state.gen;
  if (!["newest", "oldest"].includes(sortFor())) { state.groups = []; return; }
  try {
    const { days } = await api(query("/days"));
    if (gen !== state.gen) return;
    let start = 0;
    state.groups = days.map((d) => { const g = { label: dayLabel(d.day), day: d.day, n: d.n, start }; start += d.n; return g; });
    for (const el of heads.values()) el.remove(); // headings are new; tiles just move (draw places them)
    heads.clear();
    draw();
  } catch {
    state.groups = [];
  }
}

// during a refresh the pages on screen still answer (a click on a tile opens that tile), until the new ones are in
const itemAt = (i) => (state.pages.get(Math.floor(i / PAGE)) ?? state.shownPages?.get(Math.floor(i / PAGE)))?.[i % PAGE];

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
    img.src = media("thumb", item.id) + thumbSize();
    el.append(img);
  }
  if (item.kind === "video") el.append(h("span.badge", {}, icon("play"), fmtDur(item.duration)));
  const sh = state.show;
  const ext = (item.name.split(".").pop() || "").toUpperCase();
  el.append(h("span.t-tr", {}, sh.ext && ext ? h("span.t-ext", {}, ext) : null,
    sh.badges && item.favorite ? h("span.t-fav", { title: "Favourite" }, icon("heart")) : null));
  if (sh.badges && item.rating) el.append(h("span.t-rating", { title: `Rated ${item.rating}` }, stars(item.rating, { size: 10 })));
  if (sh.made && item.color) el.append(h("span.t-fam", { style: { background: item.color }, title: item.family || "" }));
  if (sh.name || sh.info) {
    el.classList.add("capped");
    el.append(h("span.t-cap", {}, sh.name ? h("span.c1", {}, item.name) : null, sh.info ? h("span.c2", {}, infoLine(item, ext)) : null));
  }
  const made = [item.model, item.lora ? `${item.lora}${item.lora_strength != null ? ` ${fmtNum(item.lora_strength)}` : ""}${item.loras > 1 ? ` +${item.loras - 1}` : ""}` : null].filter(Boolean);
  el.append(h("span.name", {}, h("span.n1", {}, item.name), sh.made && made.length ? h("span.n2", {}, made.join(" · ")) : null));
  el.title = `${item.name}\n${item.width && item.height ? `${item.width} × ${item.height} · ` : ""}${fmtSize(item.size)} · ${fmtDate(item.mtime)}`;
  return el;
}

const tileKey = (item) => `${item.id}:${item.thumb}:${item.rating}:${item.favorite}:${isSelected(item) ? 1 : 0}:${thumbSize()}`;

// Quality: thumbnails as sharp as the tile (made as they're asked for); Speed: the ones made ahead
function thumbSize() {
  if (state.quality !== "quality") return "";
  const need = (lastLayout?.size || state.tile) * (window.devicePixelRatio || 1);
  return need > 300 ? `&s=${need > 560 ? 768 : 512}` : "";
}

function infoLine(item, ext) {
  const k = state.show.infoKind;
  if (k === "size") return fmtSize(item.size);
  if (k === "date") return fmtDate(item.mtime);
  if (k === "model") return item.model || "No checkpoint recorded";
  if (k === "type") return item.kind === "video" ? `${ext} video` : `${ext} image`;
  return item.width && item.height ? `${item.width} × ${item.height}` : "";
}

// ---------------------------------------------------------------- the selection
const isSelected = (item) => state.selAll || state.sel.has(item.id);
const selCount = () => (state.selAll ? state.total : state.sel.size);
const selection = () => (state.selAll ? { view: { sort: sortFor(), folder: state.folder, kind: state.kind, q: viewQuery(), seed: state.seed, upto: state.selUpto || undefined } } : [...state.sel]);

function tileClick(e, i) {
  if (e.shiftKey && state.anchor >= 0) return rangeSel(state.anchor, i);
  if (e.ctrlKey || e.metaKey || selCount()) return toggleSel(i); // while some are selected, a click picks
  openViewer(i);
}

async function toggleSel(i) {
  const item = itemAt(i);
  if (!item) return;
  if (state.selAll) { // from "everything" to everything but this one: the whole list, from the Gallery
    try {
      const { ids } = await api("/select", { method: "POST", body: { selection: selection() } });
      state.selAll = false;
      for (const id of ids) state.sel.add(id);
    } catch (e) { return toast(e.message, true); }
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
  state.selUpto = state.maxId; // files that arrive later aren't in it
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
  const rateBtn = h("button.btn", { type: "button", "aria-haspopup": "menu", onclick: (e) => menu(e.currentTarget, [
    ...[5, 4, 3, 2, 1].map((r) => ({ node: h("button.menu-item", { type: "button", role: "menuitem", "aria-label": `Rate ${r}`,
      onclick: () => bulk("rate", r) }, stars(r, { size: 12 }), h("kbd", {}, String(r))) })),
    null, { label: "Clear the rating", icon: "remove", hint: "0", onclick: () => bulk("rate", 0) }]) }, icon("star"), "Rate");
  const moreBtn = h("button.icon-btn", { type: "button", "aria-haspopup": "menu", "aria-label": "More", title: "More", onclick: (e) => menu(e.currentTarget, [
    { label: "Move…", icon: "move", onclick: () => org.moveDialog(sel, n, state.folders, { at: state.folder, after: clearSel }) },
    { label: "Send to Eagle…", icon: "send", onclick: () => org.sendToEagle(sel, n) },
    null,
    { label: "Not favourites", icon: "heart", onclick: () => bulk("favorite", false) },
    state.view === "album" ? null : { label: "Take out of an album…", icon: "album", onclick: () => org.albumDialog(sel, n, { remove: "" }) },
  ].filter((x, i, a) => x !== null || (i > 0 && a[i - 1] !== null))) }, icon("more"));
  bar.replaceChildren(
    h("span.sel-n", {}, state.selAll ? `All ${n.toLocaleString()} selected` : `${n.toLocaleString()} selected`),
    !state.selAll && n < state.total ? h("button.link", { type: "button", onclick: selectAll }, `Select all ${state.total.toLocaleString()}`) : null,
    h("span.spacer"),
    ...(trash ? [
      h("button.btn", { type: "button", onclick: async () => { await org.restoreFiles(sel); clearSel(); } }, icon("restore"), "Put back"),
      h("button.btn.danger", { type: "button", onclick: async () => { await org.emptyTrash(sel, n); clearSel(); } }, icon("trash"), "Empty these"),
    ] : [
      rateBtn,
      h("button.btn", { type: "button", onclick: () => bulk("favorite", true), title: "Favourite (F)" }, icon("heart"), "Favourite"),
      h("button.btn", { type: "button", onclick: () => org.tagDialog(sel, n) }, icon("tag"), "Tag…"),
      state.view === "album" ? h("button.btn", { type: "button", onclick: () => bulkAlbumOut() }, icon("album"), "Take out")
        : h("button.btn", { type: "button", onclick: () => org.albumDialog(sel, n) }, icon("album"), "Album…"),
      moreBtn,
      h("span.bar-sep", { "aria-hidden": "true" }),
      h("button.btn.danger", { type: "button", title: "Move to the Trash (Delete); Ctrl+Z undoes it", onclick: async () => { if (await org.trashFiles(sel)) clearSel(); } }, icon("trash"), "Delete"),
    ]),
    h("button.icon-btn", { type: "button", "aria-label": "Clear the selection", title: "Clear the selection (Esc)", onclick: clearSel }, icon("remove")));
}

async function bulk(action, value) {
  try {
    const r = await api("/ops/bulk", { method: "POST", body: { selection: selection(), action, value } });
    org.done(r, action === "rate" ? (value ? `Rated ${plural(r.count, "file")} ${value}.` : `Cleared ${plural(r.count, "rating")}.`) : value ? `${plural(r.count, "file")} marked as favourite${r.count === 1 ? "" : "s"}.` : `${plural(r.count, "file")} taken out of the favourites.`);
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
  if (!isPage()) await reload(false);
  else if (state.view === "history") org.renderHistory($("stats"));
  pollSoon();
  if (!$("viewer").hidden) refreshViewer();
});

function draw() {
  const L = layout();
  const { gap, cols, size, row, groups, height } = L;
  sizer.style.height = `${height}px`;
  sizer.style.setProperty("--cap", `${L.cap}px`);
  const top = scroller.scrollTop - sizer.offsetTop, view = scroller.clientHeight;
  const lo = top - row * 2, hi = top + view + row * 2;
  const want = new Set(), wantHeads = new Set();
  const put = (i, left, y, w, hh) => {
    want.add(i);
    const item = itemAt(i);
    if (!item) { loadPage(Math.floor(i / PAGE)); return; }
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
    const pos = `${w},${hh},${left},${y}`;
    if (el.dataset.pos === pos) return;
    el.dataset.pos = pos;
    Object.assign(el.style, { width: `${w}px`, height: `${hh}px`, left: `${left}px`, top: `${y}px` });
  };
  groups.forEach((g, gi) => {
    if (g.top + g.head + g.body < lo || g.top > hi) return;
    if (g.label) {
      wantHeads.add(gi);
      if (!heads.has(gi)) {
        const el = h("div.day", { role: "heading", "aria-level": 2 },
          h("button.day-link", { type: "button", title: `Only ${g.label.toLowerCase() === "today" || g.label.toLowerCase() === "yesterday" ? g.label.toLowerCase() : g.label}`,
            onclick: () => addChip("on", g.day, { replace: true }) }, g.label), h("span.n", {}, g.n.toLocaleString()));
        sizer.append(el);
        heads.set(gi, el);
      }
      heads.get(gi).style.top = `${g.top}px`;
    }
    const base = g.top + g.head;
    if (L.water) {
      let a = 0, b = g.n;
      const from = lo - base - L.maxH;
      while (a < b) { const m = (a + b) >> 1; if (g.wt[m] < from) a = m + 1; else b = m; }
      for (let j = a; j < g.n && g.wt[j] <= hi - base; j++) {
        if (g.wt[j] + g.wh[j] >= lo - base) put(g.start + j, g.wc[j] * (size + gap), base + g.wt[j], size, g.wh[j]);
      }
      return;
    }
    const r0 = Math.max(0, Math.floor((lo - base) / row)), r1 = Math.min(g.rows - 1, Math.floor((hi - base) / row));
    for (let r = r0; r <= r1; r++) {
      for (let c = 0; c < cols; c++) {
        const j = r * cols + c;
        if (j >= g.n) break;
        put(g.start + j, c * (size + gap), base + r * row, size, L.tileH);
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
  if (toTop) clearSel(); // a new view, search, sort or kind: the bar never acts on files the grid no longer shows
  const hadFocus = !toTop && document.activeElement?.classList.contains("tile");
  state.gen++;
  state.shownPages = toTop ? null : state.pages;
  state.pages = new Map();
  state.loading.clear();
  if (toTop) { clearGrid(); scroller.scrollTop = 0; state.focus = -1; }
  renderToolbar();
  state.stale = false;
  const gen = state.gen;
  return Promise.all([loadPage(0), loadDays(), loadShapes()]).then(() => {
    if (gen === state.gen) state.shownPages = null;
    // a refresh keeps the tiles on screen until the new list is in (no blank flash); draw swaps the ones that changed
    for (const el of heads.values()) el.remove();
    heads.clear();
    showCount();
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
  else if (top + p.h > scroller.scrollTop + view) scroller.scrollTop = top + p.h - view + 8;
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
  const inColumn = (d) => { // the next file up or down in the same column, in a Waterfall
    for (let k = p.j + d, n = 0; k >= 0 && k < g.n && n < L.cols * 8; k += d, n++) if (g.wc[k] === p.c) return g.start + k;
    const other = L.groups[p.gi + d];
    return other ? (d > 0 ? other.start : other.start + other.n - 1) : null;
  };
  if (e.key === "ArrowRight") to = i + 1;
  else if (e.key === "ArrowLeft") to = i - 1;
  else if (L.water && (e.key === "ArrowDown" || e.key === "ArrowUp")) to = inColumn(e.key === "ArrowDown" ? 1 : -1);
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
  if (to == null) return true;
  focusTile(Math.max(0, Math.min(state.total - 1, to)));
  return true;
}

function showCount() {
  const searching = !!queryString();
  $("count").textContent = !state.total ? (searching ? "no matches" : "") : searching ? plural(state.total, "match", "matches") : plural(state.total, "item");
  $("empty-trash").hidden = state.view !== "trash" || !state.total; // the count of this view, not the one before it
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
    { type: "button", "aria-label": `${n} star${n > 1 ? "s" : ""}`, "aria-pressed": String(n <= item.rating), title: `${n} (press ${n}; again clears it)`,
      onclick: () => rate(item, n) }, icon("star"))));
  $("v-fav").setAttribute("aria-pressed", String(!!item.favorite));
  $("v-fav").classList.toggle("on", !!item.favorite);
}

// ---------------------------------------------------------------- the viewer
let viewing = -1, viewedTimer = null, infoGen = 0, shown = null, deleting = false;

function openViewer(i) {
  const item = itemAt(i);
  if (!item) return;
  closeMenu();
  viewing = i;
  shown = item;
  state.focus = i;
  const v = $("viewer");
  const wasHidden = v.hidden;
  v.hidden = false;
  $("v-name").textContent = item.name;
  $("v-pos").textContent = `${(i + 1).toLocaleString()} of ${state.total.toLocaleString()}`;
  $("v-prev").disabled = i <= 0;
  $("v-next").disabled = i >= state.total - 1;
  const src = media("file", item.id);
  const el = item.kind === "video" ? h("video", { src, controls: true, autoplay: true, loop: true }) : h("img", { src, alt: item.name });
  el.addEventListener("error", () => $("v-media").replaceChildren(h("div.v-broken", {}, h("p", {}, `This ${item.kind === "video" ? "video" : "picture"} can't be shown.`),
    h("p.note", {}, item.size ? "It may be damaged, or only partly saved." : "The file is empty (0 bytes)."))));
  $("v-media").replaceChildren(el);
  viewerReset(item, el);
  renderMarks(item);
  loadDetails(item);
  clearTimeout(viewedTimer);
  viewedTimer = setTimeout(() => api(`/file/${item.id}/viewed`, { method: "POST" }).catch(() => {}), 800);
  if (!itemAt(i + 1) && i + 1 < state.total) loadPage(Math.floor((i + 1) / PAGE));
  if (wasHidden) $("v-media").focus({ preventScroll: true });
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
    h("button.icon-btn.small.p-copy", { type: "button", "aria-label": "Copy", title: "Copy", onclick: () => copyText(text, cls ? "Negative prompt copied" : "Prompt copied") }, icon("copy")),
    long ? h("div.row.tight", {}, h("button.btn.small", { type: "button", onclick: (e) => { body.classList.toggle("clamp"); e.currentTarget.textContent = body.classList.contains("clamp") ? "Show all" : "Show less"; } }, "Show all")) : null);
}

// the picture's colour set, like Eagle's: a swatch per main colour; hover for its value and share, click to find or copy it
function swatches(info) {
  if (!info.colors || !info.colors.length) return null;
  return h("div.swatches", { role: "list", "aria-label": "Colours" }, info.colors.map((c) => {
    const hex = c.hex.toUpperCase();
    const label = `${hex} (${(c.ratio * 100).toFixed(1)}%)`;
    return h("button.swatch", { type: "button", role: "listitem", title: label, "aria-label": label, style: { background: c.hex },
      onclick: (e) => menu(e.currentTarget, [
        { label: `Files with this colour`, icon: "filter", onclick: () => { closeViewer(); addChip("color", c.hex); } },
        { label: `Files with ${c.family} in them`, icon: "filter", onclick: () => { closeViewer(); addChip("color", c.family); } },
        { label: `Copy ${hex}`, icon: "copy", onclick: () => copyText(hex, `${hex} copied`) },
      ]) });
  }));
}

function renderDetails(info) {
  const g = info.generation;
  const gen = []; // what made it
  if (g && g.source) {
    if (g.positive) gen.push(section("Prompt", promptBlock(g.positive)));
    if (g.negative) gen.push(section("Negative", promptBlock(g.negative, ".neg")));
    const settings = [["Seed", g.seed, "seed"], ["Steps", g.steps, "steps"], ["CFG", g.cfg], ["Sampler", g.sampler, "sampler"],
      ["Scheduler", g.scheduler, "scheduler"], ["Size", g.width && g.height ? `${g.width} × ${g.height}` : null],
      g.denoise != null && g.denoise !== 1 ? ["Denoise", g.denoise] : null, g.clip_skip ? ["Clip skip", g.clip_skip] : null].filter((s) => s && s[1] != null);
    if (settings.length) {
      gen.push(section("Settings", h("div.settings", {}, settings.map(([k, v, key]) => h("div.set", {}, h("span.k", {}, k),
        key ? filterLink(key, String(v), typeof v === "number" ? fmtNum(v) : v) : h("span.v", {}, typeof v === "number" ? fmtNum(v) : v))))));
    }
    if (info.uses.length) {
      gen.push(section("Models", h("div.models", {}, info.uses.map((u) => h("div.model", {},
        h("span.kind." + u.kind, {}, KIND[u.kind] || u.kind.toUpperCase()),
        filterLink(u.kind === "lora" ? "lora" : u.kind === "vae" ? "vae" : "model", u.name),
        u.kind === "lora" && u.strength != null ? h("span.strength", { title: u.clip != null && u.clip !== u.strength ? `model ${fmtNum(u.strength)} · clip ${fmtNum(u.clip)}` : `strength ${fmtNum(u.strength)}` },
          h("span.bar", {}, h("span", { style: { width: `${Math.max(4, Math.min(100, Math.abs(u.strength) * 100))}%` } })), fmtNum(u.strength)) : null)))));
    }
    if (g.has_workflow && state.view === "trash") {
      gen.push(section("Workflow", h("p.note", {}, "Put it back from the Trash to open its workflow in ComfyUI or save it as a recipe.")));
    } else if (g.has_workflow) {
      gen.push(section("Workflow", h("p.note", {}, `${g.source === "comfyui" ? "ComfyUI" : g.source}${g.nodes ? ` · ${plural(g.nodes, "node")}` : ""}`),
        h("div.row", {},
          h("button.btn.primary", { type: "button", title: "Opens it in a new tab in ComfyUI (with Model Librarian)", onclick: (e) => openInComfy(info, e.currentTarget) }, icon("play"), "Open in ComfyUI"),
          h("button.btn", { type: "button", title: "Into Model Librarian's recipes", onclick: (e) => saveRecipe(info, e.currentTarget) }, icon("save"), "Save as recipe"),
          h("button.btn", { type: "button", onclick: () => copyWorkflow(info) }, icon("copy"), "Copy workflow"),
          h("a.btn", { href: `/api/file/${info.id}/workflow?download=1&t=${encodeURIComponent(TOKEN)}`, download: "" }, icon("save"), "Save as .json"))));
    } else if (g.source && g.source !== "comfyui") {
      gen.push(section("Made with", h("p.note", {}, { a1111: "Automatic1111 / Forge", novelai: "NovelAI", invokeai: "InvokeAI" }[g.source] || g.source)));
    }
  } else {
    gen.push(section("What made it", h("p.note", {}, "No generation data in this file. It may have been saved without it, or edited since.")));
  }
  const gi = g && g.info;
  if (gi) {
    const bits = [["Albums", gi.albums], ["Characters", gi.characters], ["Project", gi.project ? [gi.project] : []]].filter(([, v]) => v && v.length);
    if (bits.length || gi.note) {
      gen.push(section("From the workflow",
        bits.length ? h("dl", {}, bits.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v.join(", "))])) : null,
        gi.note ? h("p.note", {}, gi.note) : null));
    }
  }
  const ext = info.name.split(".").pop().toUpperCase();
  const parts = [h("header.v-head", {}, h("h2.v-fname", {}, info.name),
    h("p.v-sub", {}, [info.width && info.height ? `${info.width} × ${info.height}` : "", info.kind === "video" ? fmtDur(info.duration) : "", fmtSize(info.size), ext, fmtDate(info.mtime)]
      .filter(Boolean).join(" · ")), swatches(info))];
  parts.push(section("Note", noteEditor(info)));
  parts.push(section("Tags", tagEditor(info)));
  parts.push(section("Albums", albumEditor(info)));
  parts.push(section("Characters", characterEditor(info)));
  parts.push(...gen);
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
        : h("button.btn", { type: "button", onclick: () => org.moveDialog([info.id], 1, state.folders, { at: info.folder_id }) }, icon("move"), "Move…"),
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
    wrap.replaceChildren(...[...list.map((c) => h("span.chip.tag.char." + c.state, { title: `${STATE_TIP[c.state] || ""}${c.source === "workflow" ? " (by the workflow's Gallery Info node)" : ""}` },
      filterLink("character", c.name),
      c.state === "review" ? h("button.x", { type: "button", "aria-label": `Yes, ${c.name} is in it`, title: "Yes", onclick: () => change(c.id, "confirm") }, icon("check")) : null,
      h("button.x", { type: "button", "aria-label": `${c.name} isn't in it`, title: `Not ${c.name}`, onclick: () => change(c.id, "reject") }, icon("remove")))),
      others.length ? pick : null,
      !list.length && !others.length ? h("button.link.dim", { type: "button", onclick: () => editCharacter(null, pageCtx) }, "New character…") : null].filter(Boolean));
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
      if (name === "\u0000new") name = (await askText("New album", "Name", "", { ok: "Create" })) || "";
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

function noteEditor(info) {
  const box = h("textarea.note-input", { rows: 2, placeholder: "A note of your own", "aria-label": "Note", value: info.note?.note || "", spellcheck: true });
  let saved = box.value;
  const save = async () => {
    if (box.value.trim() === saved.trim()) return;
    try {
      await api(`/file/${info.id}/note`, { method: "POST", body: { note: box.value } });
      saved = box.value;
      hint.textContent = "Saved";
      setTimeout(() => { hint.textContent = ""; }, 1500);
    } catch (e) { toast(e.message, true); }
  };
  box.addEventListener("blur", save);
  box.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); box.blur(); } });
  const hint = h("span.note-hint", { "aria-live": "polite" }, info.note?.source === "eagle" ? "From Eagle" : "");
  return h("div.note-wrap", {}, box, hint);
}

// the file in the viewer to the Trash; the next one takes its place
async function deleteViewing() {
  const item = shown;
  if (!item || deleting) return;
  deleting = true; // a second Delete before the next file is up would trash one you haven't seen
  try {
    const at = viewing;
    const r = await org.trashFiles([item.id]);
    if (!r || !r.trashed) return;
    await reload(false);
    if (!state.total) return closeViewer();
    const i = Math.min(at, state.total - 1);
    await loadPage(Math.floor(i / PAGE));
    openViewer(i);
  } finally {
    deleting = false;
  }
}

// ---------------------------------------------------------------- the picture: fit, zoom, pan; flip, rotate, crop
// fit fills the stage (small pictures grow, big ones shrink); the wheel zooms at the cursor, a drag pans, a double-click
// goes between fit and 100%. Edits are shown at once and written only on Save (over the file; Undo puts it back) or Save as copy.
const vz = { fit: true, scale: 1, x: 0, y: 0, nw: 0, nh: 0, rot: 0, flip: false, crop: null, edited: false, item: null };
const ZMIN = 0.05, ZMAX = 8;
const vImg = () => $("v-media").querySelector("img");
const shownDims = () => (vz.rot % 180 ? [vz.nh, vz.nw] : [vz.nw, vz.nh]);
const editable = (item) => !!item && item.kind === "image" && /\.(png|jpe?g|webp)$/i.test(item.name) && state.view !== "trash";

function viewerReset(item, el) {
  Object.assign(vz, { fit: true, scale: 1, x: 0, y: 0, nw: 0, nh: 0, rot: 0, flip: false, crop: null, edited: false, item });
  renderEditBar();
  for (const id of ["v-flip", "v-rotate", "v-crop"]) $(id).disabled = !editable(item);
  for (const id of ["v-actual", "v-fit"]) $(id).disabled = item.kind !== "image";
  $("v-zoom").hidden = item.kind !== "image";
  if (item.kind !== "image") return;
  const ready = () => { vz.nw = el.naturalWidth; vz.nh = el.naturalHeight; vApply(); el.classList.add("on"); };
  if (el.complete && el.naturalWidth) ready();
  else el.addEventListener("load", ready, { once: true });
}

function vApply() {
  const img = vImg();
  if (!img || !vz.nw) return;
  const box = $("v-media"), cw = box.clientWidth, ch = box.clientHeight;
  const [w, h] = shownDims();
  if (vz.fit || vz.crop) {
    const pad = vz.crop ? 24 : 0; // room for the crop handles
    vz.scale = Math.min((cw - pad * 2) / w, (ch - pad * 2) / h);
    vz.x = (cw - w * vz.scale) / 2;
    vz.y = (ch - h * vz.scale) / 2;
  } else {
    const W = w * vz.scale, H = h * vz.scale;
    vz.x = W <= cw ? (cw - W) / 2 : Math.min(0, Math.max(cw - W, vz.x));
    vz.y = H <= ch ? (ch - H) / 2 : Math.min(0, Math.max(ch - H, vz.y));
  }
  const ew = vz.nw * vz.scale, eh = vz.nh * vz.scale;
  Object.assign(img.style, { width: `${ew}px`, height: `${eh}px`, left: `${vz.x + (w * vz.scale) / 2 - ew / 2}px`,
    top: `${vz.y + (h * vz.scale) / 2 - eh / 2}px`, transform: `rotate(${vz.rot}deg) scaleX(${vz.flip ? -1 : 1})` });
  $("v-zoom-pct").textContent = `${Math.round(vz.scale * 100)}%`;
  $("v-zoom-range").value = String(Math.round((1000 * Math.log(vz.scale / ZMIN)) / Math.log(ZMAX / ZMIN)));
  box.classList.toggle("pannable", !vz.crop && (w * vz.scale > cw + 1 || h * vz.scale > ch + 1));
  if (vz.crop) drawCrop();
}

function zoomTo(scale, px, py) {
  if (!vz.nw || vz.crop) return;
  const box = $("v-media");
  if (px == null) { px = box.clientWidth / 2; py = box.clientHeight / 2; }
  const s = Math.max(ZMIN, Math.min(ZMAX, scale));
  vz.x = px - ((px - vz.x) / vz.scale) * s;
  vz.y = py - ((py - vz.y) / vz.scale) * s;
  vz.scale = s;
  vz.fit = false;
  vApply();
}
const fitView = () => { vz.fit = true; vApply(); };

function setEdited() { vz.edited = true; renderEditBar(); }
function flipView() { // a flip of what's shown: turned the other way round, mirrored
  if (!editable(vz.item) || !vz.nw) return;
  vz.flip = !vz.flip;
  vz.rot = (360 - vz.rot) % 360;
  vz.crop = null;
  $("v-media").querySelector(".v-crop")?.remove();
  setEdited();
  vApply();
}
function rotateView() {
  if (!editable(vz.item) || !vz.nw) return;
  vz.rot = (vz.rot + 90) % 360;
  vz.crop = null;
  $("v-media").querySelector(".v-crop")?.remove();
  vz.fit = true;
  setEdited();
  vApply();
}
function cropView() {
  if (!editable(vz.item) || !vz.nw) return;
  const [w, h] = shownDims();
  vz.crop = vz.crop || { x: 0, y: 0, w, h };
  setEdited();
  vApply();
}
function cancelEdit() {
  Object.assign(vz, { rot: 0, flip: false, crop: null, edited: false, fit: true });
  $("v-media").querySelector(".v-crop")?.remove();
  renderEditBar();
  vApply();
}

function renderEditBar() {
  const bar = $("v-edit");
  bar.hidden = !vz.edited;
  $("v-tools").classList.toggle("editing", vz.edited);
  if (!vz.edited) return bar.replaceChildren();
  const num = (key, label) => {
    const inp = h("input.v-num", { type: "number", min: 16, value: String(Math.round(vz.crop[key])), "aria-label": label, title: label,
      onchange: () => {
        const [W, H] = shownDims(), c = vz.crop;
        c[key] = Math.max(16, Math.min(key === "w" ? W - c.x : H - c.y, Math.round(Number(inp.value) || 16)));
        vApply();
      } });
    return inp;
  };
  bar.replaceChildren(
    vz.crop ? h("span.v-crop-size", {}, num("w", "Width"), h("span", { "aria-hidden": "true" }, "×"), num("h", "Height")) : null,
    h("button.btn.primary.small", { type: "button", title: "Keep the original; save the edit beside it as \"name (edited)\" (Enter)", onclick: () => saveEdit(true) }, "Save as copy"),
    h("button.btn.small", { type: "button", title: "Save over the file instead (Undo puts the original back)", onclick: () => saveEdit(false) }, "Replace original"),
    h("button.btn.small", { type: "button", onclick: cancelEdit }, "Cancel"));
}

function drawCrop() {
  const box = $("v-media");
  let ov = box.querySelector(".v-crop");
  if (!ov) {
    ov = h("div.v-crop", { "aria-label": "Crop area: drag to move, the handles to resize" },
      ["nw", "n", "ne", "e", "se", "s", "sw", "w"].map((d) => h("span.v-h." + d, { "data-d": d })));
    ov.addEventListener("pointerdown", cropDrag);
    box.append(ov);
  }
  const c = vz.crop, s = vz.scale;
  Object.assign(ov.style, { left: `${vz.x + c.x * s}px`, top: `${vz.y + c.y * s}px`, width: `${c.w * s}px`, height: `${c.h * s}px` });
  const [wi, hi] = $("v-edit").querySelectorAll(".v-num");
  if (wi && document.activeElement !== wi) wi.value = String(Math.round(c.w));
  if (hi && document.activeElement !== hi) hi.value = String(Math.round(c.h));
}

function cropDrag(e) {
  e.preventDefault();
  e.stopPropagation();
  const d = e.target.dataset.d || "move", ov = e.currentTarget;
  const start = { ...vz.crop }, sx = e.clientX, sy = e.clientY, [W, H] = shownDims(), MIN = 16;
  ov.setPointerCapture(e.pointerId);
  const move = (ev) => {
    const dx = (ev.clientX - sx) / vz.scale, dy = (ev.clientY - sy) / vz.scale;
    let { x, y, w, h: hh } = start;
    if (d === "move") {
      x = Math.max(0, Math.min(W - w, x + dx));
      y = Math.max(0, Math.min(H - hh, y + dy));
    } else {
      if (d.includes("w")) { const nx = Math.max(0, Math.min(x + w - MIN, x + dx)); w += x - nx; x = nx; }
      if (d.includes("e")) w = Math.max(MIN, Math.min(W - x, w + dx));
      if (d.includes("n")) { const ny = Math.max(0, Math.min(y + hh - MIN, y + dy)); hh += y - ny; y = ny; }
      if (d.includes("s")) hh = Math.max(MIN, Math.min(H - y, hh + dy));
    }
    Object.assign(vz.crop, { x, y, w, h: hh });
    drawCrop();
  };
  const up = () => { ov.removeEventListener("pointermove", move); ov.removeEventListener("pointerup", up); };
  ov.addEventListener("pointermove", move);
  ov.addEventListener("pointerup", up);
}

async function saveEdit(copy) {
  const item = vz.item;
  if (!item || !vz.edited) return;
  const c = vz.crop, [W, H] = shownDims();
  const crop = c && (c.x > 0.5 || c.y > 0.5 || c.w < W - 0.5 || c.h < H - 0.5) ? [c.x, c.y, c.w, c.h].map(Math.round) : null;
  if (!crop && !vz.rot && !vz.flip) return cancelEdit();
  try {
    const r = await api(`/file/${item.id}/edit`, { method: "POST", body: { rotate: vz.rot, flip: vz.flip, crop, copy } });
    cancelEdit();
    if (!copy) { // the new picture, not the cached one
      const img = vImg();
      if (img) {
        vz.nw = 0;
        img.classList.remove("on");
        img.addEventListener("load", () => { vz.nw = img.naturalWidth; vz.nh = img.naturalHeight; vApply(); img.classList.add("on"); }, { once: true });
        img.src = `${media("file", item.id)}&v=${Date.now()}`;
      }
    }
    org.done(r, copy ? `Saved ${r.name} beside it.` : `Saved ${item.name}. Undo puts the original back.`);
  } catch (e) { toast(e.message, true); }
}

function viewerKey(e) {
  const k = e.key;
  if (vz.edited && k === "Escape") { cancelEdit(); return true; }
  if (vz.edited && k === "Enter") { saveEdit(true); return true; }
  if (e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
    if (k === "F") { flipView(); return true; }
    if (k === "R") { rotateView(); return true; }
    if (k === "C") { cropView(); return true; }
  }
  if (e.ctrlKey && k === "0") { zoomTo(1); return true; }
  if (e.ctrlKey && (k === "=" || k === "+")) { zoomTo(vz.scale * 1.25); return true; }
  if (e.ctrlKey && k === "-") { zoomTo(vz.scale / 1.25); return true; }
  if (!e.ctrlKey && k === "`") { fitView(); return true; }
  return false;
}

{
  const box = $("v-media");
  box.addEventListener("wheel", (e) => {
    if (!vz.nw || vz.crop) return;
    e.preventDefault();
    const r = box.getBoundingClientRect();
    zoomTo(vz.scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  box.addEventListener("dblclick", (e) => {
    if (!vz.nw || vz.crop) return;
    const r = box.getBoundingClientRect();
    if (vz.fit) zoomTo(Math.abs(vz.scale - 1) < 0.05 ? 2 : 1, e.clientX - r.left, e.clientY - r.top);
    else fitView();
  });
  box.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || vz.crop || !box.classList.contains("pannable") || e.target.tagName !== "IMG") return;
    e.preventDefault();
    box.setPointerCapture(e.pointerId);
    let lx = e.clientX, ly = e.clientY;
    const move = (ev) => { vz.x += ev.clientX - lx; vz.y += ev.clientY - ly; lx = ev.clientX; ly = ev.clientY; vApply(); };
    const up = () => { box.removeEventListener("pointermove", move); box.removeEventListener("pointerup", up); };
    box.addEventListener("pointermove", move);
    box.addEventListener("pointerup", up);
  });
  window.addEventListener("resize", () => { if (!$("viewer").hidden) vApply(); });
  $("v-zoom-range").addEventListener("input", (e) => zoomTo(ZMIN * Math.pow(ZMAX / ZMIN, Number(e.target.value) / 1000)));
  $("v-flip").onclick = flipView;
  $("v-rotate").onclick = rotateView;
  $("v-crop").onclick = cropView;
  $("v-actual").onclick = () => zoomTo(1);
  $("v-fit").onclick = fitView;
}

async function refreshViewer() {
  if (shown) {
    for (const [p, items] of state.pages) {
      const k = items.findIndex((it) => it.id === shown.id);
      if (k >= 0) { viewing = p * PAGE + k; break; }
    }
    $("v-pos").textContent = `${(viewing + 1).toLocaleString()} of ${state.total.toLocaleString()}`;
  }
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
  if (vz.edited) cancelEdit();
  $("viewer").hidden = true;
  $("v-media").replaceChildren();
  const i = viewing;
  viewing = -1;
  shown = null;
  if (state.view === "recent" || state.stale) reload(false); // what you just opened moved to the top; or files came meanwhile
  else if (i >= 0) focusTile(i);
}

function step(d) {
  if (vz.edited) return toast("Save or cancel the edit first.");
  const i = viewing + d;
  if (i < 0 || i >= state.total) return;
  if (itemAt(i)) openViewer(i);
  else loadPage(Math.floor(i / PAGE)).then(() => openViewer(i));
}

// ---------------------------------------------------------------- status
let lastStatus = null, pollTimer = null, lastFiles = -1, lastMeta = -1, lastOffline = 0, toldUpdate = null;

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
    if (st.offline !== lastOffline) { lastOffline = st.offline; loadFolders(); } // a folder went (or came back)
    if (st.files !== lastFiles || (searching && st.meta_pending !== lastMeta)) {
      lastFiles = st.files;
      await loadFolders();
      if ($("viewer").hidden && !isPage()) reload(false); // never pull the list from under an open file
      else state.stale = true; // ...but bring it up to date once it's closed
    } else if (st.thumbs_pending && $("viewer").hidden && !isPage()) {
      // thumbnails made meanwhile: tiles waiting for one pick it up when their page is fetched again
      state.shownPages = state.pages; // (still answering for the tiles on screen meanwhile)
      state.pages = new Map();
      draw();
    }
    lastMeta = st.meta_pending;
    $("trash-count").textContent = st.trash ? st.trash.toLocaleString() : "";
    if (st.update && st.update !== toldUpdate) { toldUpdate = st.update; toast(`Model Librarian Gallery ${st.update} is out: Settings, Updates.`); }
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
function viewPanel(anchor) {
  const seg = (opts, cur, pick) => {
    const wrap = h("div.seg.small", { role: "group" });
    const paint = (v) => wrap.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === v)));
    for (const [v, label] of opts) wrap.append(h("button", { type: "button", "data-v": v, onclick: () => { paint(v); pick(v); } }, label));
    paint(cur);
    return wrap;
  };
  const sw = (on, set, label) => {
    const b = h("button.switch", { type: "button", role: "switch", "aria-checked": String(on), "aria-label": label,
      onclick: () => { const v = b.getAttribute("aria-checked") !== "true"; b.setAttribute("aria-checked", String(v)); set(v); } });
    return b;
  };
  const row = (label, ...controls) => h("div.vp-row", {}, h("span.vp-label", {}, label), h("div.vp-ctl", {}, ...controls));
  const show = (key) => (v) => { state.show[key] = v; store.set("show", state.show); clearGrid(); draw(); };
  const restyle = () => { clearGrid(); draw(); };
  const fixedSort = ["random", "recent", "trash"].includes(state.view);
  const by = Object.keys(SORT_BY).find((k) => SORT_BY[k].includes(state.sort)) || "date";
  const sortBy = h("select", { "aria-label": "Sort by", onchange: () => setSort(sortBy.value, 0) },
    Object.entries(SORT_BY_NAMES).map(([k, v]) => h("option", { value: k, selected: k === by }, v)));
  const dir = h("button.icon-btn.small.vp-dir", { type: "button", "aria-label": "Which way round", title: SORT_NAMES[state.sort],
    onclick: () => { const [a, b] = SORT_BY[sortBy.value]; setSort(sortBy.value, state.sort === a ? 1 : 0); } },
  icon("sort"));
  const paintDir = () => { dir.classList.toggle("up", SORT_BY[sortBy.value][1] === state.sort); dir.title = SORT_NAMES[state.sort]; };
  function setSort(k, which) { state.sort = SORT_BY[k][which]; store.set("sort", state.sort); paintDir(); renderToolbar(); reload(true); }
  paintDir();
  const info = h("select", { "aria-label": "Info line", onchange: () => { state.show.infoKind = info.value; store.set("show", state.show); restyle(); } },
    Object.entries(INFO_NAMES).map(([k, v]) => h("option", { value: k, selected: k === state.show.infoKind }, v)));
  const body = h("div.vp", {},
    row("Layout", seg([["grid", "Grid"], ["waterfall", "Waterfall"]], state.layout, (v) => {
      state.layout = v; store.set("layout", v); clearGrid(); if (v === "waterfall") loadShapes(); else { state.shapes = null; draw(); }
    })),
    row("Thumbnails", seg([["speed", "Speed"], ["quality", "Quality"]], state.quality, (v) => { state.quality = v; store.set("quality", v); restyle(); })),
    h("div.menu-sep"),
    fixedSort ? null : row("Sort by", sortBy, dir),
    row("Show", seg(Object.entries({ "": "All", image: "Images", video: "Videos" }), state.kind, (v) => {
      state.kind = v; store.set("kind", v); renderToolbar(); reload(true);
    })),
    h("div.menu-sep"),
    row("Name", sw(state.show.name, show("name"), "Show the name")),
    row("Info", info, sw(state.show.info, show("info"), "Show an info line")),
    row("Extension label", sw(state.show.ext, show("ext"), "Show the extension label")),
    row("Ratings and favourites", sw(state.show.badges, show("badges"), "Show ratings and favourites")),
    row("What made it", sw(state.show.made, show("made"), "Show the model family colour and the checkpoint")),
    h("div.menu-sep"),
    row("Sidebar", sw(!$("app").classList.contains("side-hidden"), (v) => setSide(!v), "Show the sidebar")),
    row("Details beside a file", sw(!$("viewer").classList.contains("no-details"), (v) => setDetails(v), "Show the details panel")),
    h("button.btn.vp-refresh", { type: "button", onclick: () => { closeMenu(); loadFolders(); reload(false); pollSoon(); } }, icon("rescan"), "Refresh"));
  menu(anchor, [{ node: body }], { cls: "view-panel" });
}
$("view-btn").onclick = (e) => viewPanel(e.currentTarget);

// tile size, keeping the file at the top of the screen where it is
function setTile(n) {
  n = Math.max(TILE_MIN, Math.min(TILE_MAX, Math.round(n)));
  $("zoom-range").value = n;
  if (n === state.tile) return;
  const L = layout(), top = scroller.scrollTop - sizer.offsetTop;
  let keep = null;
  for (const [i, el] of tiles) if (el.offsetTop + el.offsetHeight > top && (keep === null || i < keep)) keep = i;
  const off = keep !== null ? place(keep, L).top - top : 0;
  state.tile = n;
  store.set("tile", n);
  if (keep !== null) scroller.scrollTop = place(keep, layout()).top + sizer.offsetTop - off;
  draw();
}
$("zoom-range").min = TILE_MIN;
$("zoom-range").max = TILE_MAX;
$("zoom-range").value = state.tile;
$("zoom-range").oninput = (e) => setTile(Number(e.target.value));
$("zoom-out").onclick = () => setTile(state.tile / 1.15);
$("zoom-in").onclick = () => setTile(state.tile * 1.15);
scroller.addEventListener("wheel", (e) => {
  if (!e.ctrlKey) return;
  e.preventDefault(); // the grid zooms, not the page
  setTile(state.tile * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
}, { passive: false });
$("help-btn").onclick = () => showHelp();
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
    else { input.blur(); clearSel(); }
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
function showHelp() {
  if (document.querySelector("dialog.help")) return;
  const keys = [["Grid", [["← → ↑ ↓", "move between files"], ["Enter or Space", "open"], ["Ctrl+click, Shift+click", "select"], ["Ctrl+A", "select everything in view"],
    ["Delete", "move to the Trash"], ["Ctrl+Z", "undo the last change"], ["/ or Ctrl+F", "search"], ["Ctrl+B", "hide or show the sidebar"]]],
    ["Viewer", [["← →", "previous, next"], ["1 – 5, 0", "rate (again: clear)"], ["F", "favourite"], ["I", "details panel"], ["Delete", "move to the Trash"], ["Esc", "back"]]],
    ["To review", [["Y / N", "yes, it's them / no"], ["← →", "next card"]]]];
  const words = [["lighthouse dusk", "words in the prompt, name, models or tags"], ['"paper cut"', "a phrase"], ["model:  lora:  vae:", "a model by (part of) its name"],
    ["lora:aurelia@0.6-0.8", "a LoRA at that strength (strength: any LoRA)"], ["tag:  album:  character:", "your tags, albums, characters"],
    ["rating:4  rating:2-4  rating:0", "rated 4 or more, 2 to 4, unrated"], ["is:fav  is:video  is:review", "favourites, videos, waiting for review"],
    ["date:today  date:7d  date:2026-10", "when it was made (also after: before: on:)"], ["seed:  steps:  cfg:  sampler:  size:1024x1024", "settings"],
    ["has:workflow  no:prompt  neg:blurry", "what's saved in it; words in the negative prompt"]];
  const table = (rows) => h("dl.help-list", {}, rows.map(([k, v]) => [h("dt", {}, h("kbd", {}, k)), h("dd", {}, v)]));
  const dlg = h("dialog.dialog.wide.help", { "aria-label": "Shortcuts and search" },
    h("div.help-body", {},
      h("h2", {}, "Shortcuts and search"),
      h("div.help-cols", {},
        h("div", {}, keys.map(([t, rows]) => [h("h3", {}, t), table(rows)])),
        h("div", {}, h("h3", {}, "Search"), table(words))),
      h("div.row.end", {}, h("button.btn.primary", { type: "button", onclick: () => dlg.close() }, "Done"))));
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}

document.addEventListener("keydown", (e) => {
  const inField = e.target.closest?.("input, select, textarea");
  if (e.key === "?" && !inField && !document.querySelector("dialog[open]")) { e.preventDefault(); showHelp(); return; }
  if (document.querySelector("dialog[open], .preview, .menu")) return;
  if (!$("viewer").hidden && !inField && viewerKey(e)) { e.preventDefault(); return; }
  if (e.ctrlKey && ["=", "+", "-", "0"].includes(e.key) && $("viewer").hidden && !isPage()) {
    e.preventDefault();
    setTile(e.key === "0" ? 176 : state.tile * (e.key === "-" ? 1 / 1.15 : 1.15));
    return;
  }
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
      if (selCount()) { e.preventDefault(); org.trashFiles(selection()).then((r) => r && clearSel()); return; }
      if (one) { e.preventDefault(); org.trashFiles([one.id]); return; }
    }
  }
  if (!$("viewer").hidden && !inField && e.key === "Delete" && state.view !== "trash") { e.preventDefault(); deleteViewing(); return; }
  if (e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
    e.preventDefault();
    setSide(!$("app").classList.contains("side-hidden"));
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
  else if (e.key === " ") { // a video plays or pauses
    const vid = $("v-media").querySelector("video");
    if (vid && e.target.tagName !== "VIDEO") { e.preventDefault(); if (vid.paused) vid.play(); else vid.pause(); }
  }
});

setSide(store.get("sideHidden", false));
setDetails(store.get("details", true));
renderChips();
loadCharacters();
loadAlbums();
loadFolders().then(() => { reload(true); poll(); });
