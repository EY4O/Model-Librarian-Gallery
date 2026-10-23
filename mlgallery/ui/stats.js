// Model Librarian Gallery: the Statistics screen. One request (/api/stats) per range; every bar and row
// opens the library filtered to the files behind it.

import { api, baseName, dayLabel, fmtNum, fmtSize, h, plural, store, toast } from "./util.js";

const RANGES = [["7", "7 days"], ["30", "30 days"], ["90", "90 days"], ["365", "1 year"], ["all", "All time"]];
const KIND = { checkpoint: "CKPT", unet: "UNET", lora: "LORA" };

let range = store.get("statsRange", "30");
let gen = 0;

const isoDay = (t) => { const d = new Date(t * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const sinceChip = () => (range === "all" ? [] : [{ key: "after", value: isoDay(Date.now() / 1000 - Number(range) * 86400) }]);
const quote = (v) => (/[\s"]/.test(v) ? `"${v.replaceAll('"', "")}"` : v);

export async function renderStats(root, { open }) {
  const me = ++gen;
  root.setAttribute("aria-busy", "true");
  let data;
  try {
    data = await api(`/stats?days=${range === "all" ? 0 : range}`);
  } catch (e) {
    toast(e.message, true);
    return;
  } finally {
    if (me === gen) root.removeAttribute("aria-busy");
  }
  if (me !== gen) return;
  const go = (...chips) => open([...chips, ...sinceChip()]);
  root.replaceChildren(...[
    h("header.st-head", {},
      h("h1", {}, "Statistics"),
      h("div.seg", { role: "group", "aria-label": "Time range" }, RANGES.map(([v, label]) => h("button" + (v === range ? ".on" : ""),
        { type: "button", "aria-pressed": String(v === range), onclick: () => { range = v; store.set("statsRange", v); renderStats(root, { open }); } }, label)))),
    data.totals.n ? null : h("p.st-empty", {}, range === "all" ? "Nothing indexed yet: add a folder to see statistics." : "Nothing made in this time range."),
    kpis(data),
    data.totals.n ? timeline(data, go) : null,
    data.totals.n ? h("div.st-grid", {},
      card("Checkpoints", "Most used", bars(data.checkpoints, { kind: "ckpt", label: (r) => r.name, onclick: (r) => go({ key: "model", value: r.name }),
        empty: "No checkpoint recorded in these files." })),
      card("LoRAs", "Most used, with their average strength", bars(data.loras, { kind: "lora", label: (r) => r.name,
        extra: (r) => (r.strength != null ? `avg ${fmtNum(r.strength)}` : ""), onclick: (r) => go({ key: "lora", value: r.name }), empty: "No LoRAs used." })),
      card("Prompt", "The parts of your prompts used most", bars(data.prompt_tags, { kind: "tag", label: (r) => r.name,
        onclick: (r) => go({ key: "text", value: `"${r.name.replaceAll('"', "")}"` }), empty: "No prompts in these files." })),
      settingsCard(data, go),
      storageCard(data, go),
      libraryCard(data, go),
    ) : null,
  ].filter(Boolean));
}

function card(title, sub, ...body) {
  return h("section.st-card", { "aria-label": title }, h("div.st-card-head", {}, h("h2", {}, title), sub ? h("span", {}, sub) : null), ...body);
}

function kpis(d) {
  const t = d.totals;
  const pct = t.n ? Math.round((t.with_data / t.n) * 100) : 0;
  const tile = (label, value, note) => h("div.kpi", {}, h("span.k", {}, label), h("span.v", {}, value), note ? h("span.n", {}, note) : null);
  return h("div.kpis", {},
    tile("Made", t.n.toLocaleString(), `${plural(t.images, "image")} · ${plural(t.videos, "video")}`),
    tile("With generation data", `${pct}%`, `${t.with_data.toLocaleString()} of ${t.n.toLocaleString()}`),
    tile("Checkpoints used", d.models_used.toLocaleString()),
    tile("LoRAs used", d.loras_used.toLocaleString()),
    tile("On disk", fmtSize(t.size || 0), d.drives.length ? `all folders: ${d.drives.map((x) => `${x.drive} ${fmtSize(x.size)}`).join(" · ")}` : null));
}

// a row per item: its name, a bar for its share, and its count; the row opens those files
function bars(rows, { kind, label, extra, onclick, empty, value = (r) => r.n, fmt = (r) => r.n.toLocaleString() }) {
  if (!rows || !rows.length) return h("p.st-none", {}, empty || "Nothing yet.");
  const max = Math.max(...rows.map(value), 1);
  return h("ol.bars." + kind, {}, rows.map((r) => h("li", {},
    h("button.bar-row", { type: "button", title: onclick ? `Show these files` : null, onclick: onclick ? () => onclick(r) : null, disabled: !onclick },
      h("span.bar-label", {}, label(r)),
      extra && extra(r) ? h("span.bar-extra", {}, extra(r)) : null,
      h("span.bar-n", {}, fmt(r)),
      h("span.bar-track", { "aria-hidden": "true" }, h("span.bar-fill", { style: { width: `${Math.max(1.5, (value(r) / max) * 100)}%` } }))))));
}

function settingsCard(d, go) {
  const s = d.settings;
  const tabs = [["Sampler", "sampler"], ["Scheduler", "scheduler"], ["Steps", "steps"], ["CFG", "cfg"], ["Size", "size"]];
  const body = h("div.st-tab-body");
  let cur = store.get("statsSetting", "sampler");
  const seg = h("div.seg.small", { role: "tablist", "aria-label": "Setting" });
  const show = () => {
    seg.replaceChildren(...tabs.map(([label, k]) => h("button" + (k === cur ? ".on" : ""), { type: "button", role: "tab", "aria-selected": String(k === cur),
      onclick: () => { cur = k; store.set("statsSetting", k); show(); } }, label)));
    body.replaceChildren(bars(s[cur], { kind: "set", label: (r) => (typeof r.name === "number" ? fmtNum(r.name) : r.name),
      onclick: (r) => go({ key: cur, value: String(r.name) }), empty: "Not recorded in these files." }));
  };
  show();
  return card("Settings", "What you generate with", seg, body);
}

function storageCard(d, go) {
  return card("Folders", "Files made in this range, by folder",
    bars(d.folders, { kind: "folder", label: (r) => baseName(r.path), extra: (r) => plural(r.n, "file"), value: (r) => r.size, fmt: (r) => fmtSize(r.size),
      onclick: (r) => go({ key: "folder", value: String(r.id) }) }));
}

function libraryCard(d, go) {
  const lib = d.library;
  if (!lib || !lib.found) {
    return card("Never used", "Models you have that none of these files were made with",
      h("p.st-none", {}, "Model Librarian's library wasn't found. Set its data folder in Settings to see which of your models you never use."));
  }
  const c = lib.unused_count;
  const total = c.checkpoint + c.unet + c.lora;
  return card("Never used", `Models in your library that no file in the Gallery was made with (any time)`,
    h("p.st-sum", {}, total ? `${plural(c.checkpoint, "checkpoint")}, ${plural(c.unet, "diffusion model")} and ${plural(c.lora, "LoRA")}, ${fmtSize(lib.unused_size)} in all, of ${lib.total.toLocaleString()} models.`
      : "Every model in your library appears in at least one file."),
    total ? h("ol.unused", {}, lib.unused.map((m) => h("li", {},
      h("span.kind." + m.kind, {}, KIND[m.kind] || m.kind),
      h("span.u-name", { title: m.file }, m.name),
      h("span.u-meta", {}, [m.size ? fmtSize(m.size) : "", m.last_used ? `last used in ComfyUI ${new Date(m.last_used * 1000).toLocaleDateString()}` : ""].filter(Boolean).join(" · "))))) : null,
    total > lib.unused.length ? h("p.st-none", {}, `and ${(total - lib.unused.length).toLocaleString()} more (largest first)`) : null);
}

// the timeline: one column per day (or week), images and videos stacked; hover for the numbers, click for the files
function timeline(d, go) {
  const pts = fill(d.timeline);
  const max = Math.max(...pts.map((p) => p.images + p.videos), 1);
  const nice = niceMax(max);
  const tip = h("div.tl-tip", { role: "status", hidden: true });
  const cols = pts.map((p) => {
    const total = p.images + p.videos;
    const label = d.timeline.by === "week" ? `Week of ${dayLabel(p.at)}` : dayLabel(p.at);
    const col = h("button.tl-col", { type: "button", "aria-label": `${label}: ${plural(p.images, "image")}, ${plural(p.videos, "video")}`, disabled: !total,
      onclick: () => {
        const end = new Date(p.at + "T00:00:00");
        end.setDate(end.getDate() + (d.timeline.by === "week" ? 7 : 1));
        const endDay = isoDay(end.getTime() / 1000);
        go({ key: "after", value: p.at }, { key: "before", value: endDay });
      },
      onmouseenter: (e) => showTip(e.currentTarget, label, p), onfocus: (e) => showTip(e.currentTarget, label, p),
      onmouseleave: () => { tip.hidden = true; }, onblur: () => { tip.hidden = true; } },
      h("span.tl-stack", { style: { height: `${(total / nice) * 100}%` } },
        p.videos ? h("span.tl-v", { style: { flexGrow: p.videos } }) : null,
        p.images ? h("span.tl-i", { style: { flexGrow: p.images } }) : null));
    return col;
  });
  function showTip(el, label, p) {
    tip.replaceChildren(h("strong", {}, label), h("span", {}, h("i.sw.i"), `Images ${p.images.toLocaleString()}`), h("span", {}, h("i.sw.v"), `Videos ${p.videos.toLocaleString()}`));
    tip.hidden = false;
    const box = el.parentElement.getBoundingClientRect(), r = el.getBoundingClientRect();
    tip.style.left = `${Math.min(box.width - 150, Math.max(0, r.left - box.left + r.width / 2 - 70))}px`;
  }
  const ticks = [0, Math.floor(pts.length / 2), pts.length - 1].filter((v, i, a) => a.indexOf(v) === i);
  const table = h("table.sr-only", {}, h("caption", {}, "Files made over time"),
    h("tr", {}, h("th", {}, d.timeline.by === "week" ? "Week of" : "Day"), h("th", {}, "Images"), h("th", {}, "Videos")),
    pts.filter((p) => p.images + p.videos).map((p) => h("tr", {}, h("td", {}, p.at), h("td", {}, p.images), h("td", {}, p.videos))));
  return card("Made over time", d.timeline.by === "week" ? "Per week" : "Per day",
    h("div.tl-legend", {}, h("span", {}, h("i.sw.i"), "Images"), h("span", {}, h("i.sw.v"), "Videos")),
    h("div.tl", {},
      h("div.tl-axis", { "aria-hidden": "true" }, h("span", {}, nice.toLocaleString()), h("span", {}, Math.round(nice / 2).toLocaleString()), h("span", {}, "0")),
      h("div.tl-plot", {}, h("div.tl-grid", { "aria-hidden": "true" }, h("i"), h("i"), h("i")), h("div.tl-cols", {}, cols), tip)),
    h("div.tl-x", { "aria-hidden": "true" }, ticks.map((i) => h("span", { style: { left: `${((i + 0.5) / pts.length) * 100}%` } }, short(pts[i].at, d.timeline.by)))),
    table);
}

function niceMax(v) {
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 3, 4, 5, 6, 8, 10].map((m) => m * p).find((m) => m >= v) || v; // halves stay whole numbers
}

function short(day, by) {
  const [y, m, dd] = day.split("-").map(Number);
  const dt = new Date(y, m - 1, dd);
  return dt.toLocaleDateString(undefined, by === "week" ? { day: "numeric", month: "short", year: "2-digit" } : { day: "numeric", month: "short" });
}

// every day (or week) in the range, including the empty ones, so gaps show as gaps
function fill(tl) {
  if (!tl.points.length) return [];
  const have = new Map(tl.points.map((p) => [p.at, p]));
  const out = [];
  const first = new Date(tl.points[0].at + "T00:00:00");
  const last = new Date(tl.points[tl.points.length - 1].at + "T00:00:00");
  if (range !== "all") {
    const start = new Date(); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - Number(range) + 1);
    if (tl.by === "week") start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    if (start < first) first.setTime(start.getTime());
    const today = new Date(); today.setHours(0, 0, 0, 0);
    if (today > last) last.setTime(today.getTime());
  }
  for (const d = new Date(first); d <= last && out.length < 800; d.setDate(d.getDate() + (tl.by === "week" ? 7 : 1))) {
    const at = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    out.push(have.get(at) || { at, images: 0, videos: 0 });
  }
  return out;
}

export function rangeChip() {
  return sinceChip();
}

export { quote };
