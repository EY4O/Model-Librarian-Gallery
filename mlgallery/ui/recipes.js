// Model Librarian Gallery: Recipes - Model Librarian's recipes (what made a picture), brought in, looked through, and
// opened in ComfyUI. They're Model Librarian's own files (recipes.py): the same list as its Recipes tab.

import { TOKEN, api, ask, askText, copyText, emptyArt, fmtDate, fmtNum, h, icon, initial, media, plural, store, toast } from "./util.js";

const KIND = { checkpoint: "CKPT", lora: "LORA", vae: "VAE" };
const ACCEPT = "image/png,image/jpeg,image/webp,video/mp4,video/webm,video/quicktime,.json,application/json";
const isRecipeFile = (f) => /\.(png|jpe?g|webp|mp4|webm|mov|m4v|json)$/i.test(f.name);
const picture = (r) => media("recipes", `${r.id}/thumb`) + `&v=${r.thumb_v || 0}`;
const stem = (s) => String(s || "").split(/[\\/]/).pop().replace(/\.(safetensors|ckpt|pt|pth|bin|gguf|sft)$/i, "");
const refName = (m) => (m ? m.display_name || m.name || stem(m.file) : "");

let filter = store.get("recipes-filter", { q: "", fav: false, tag: "" });  // (as you left it)

function thumb(r, alt) {
  const none = () => h("div.c-thumb.none", { "aria-hidden": "true" }, h("span.c-initial", {}, initial(alt)));
  if (!r.thumb_v) return none();
  const img = h("img", { src: picture(r), alt, loading: "lazy", decoding: "async" });
  img.onerror = () => img.replaceWith(none());
  return h("div.c-thumb", {}, img);
}

// ---------------------------------------------------------------- bringing recipes in
async function upload(files) {
  const fd = new FormData();
  for (const f of files) fd.append("file", f, f.name);
  const res = await fetch("/api/recipes/import", { method: "POST", headers: { "X-MLG-Token": TOKEN }, body: fd });
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

function told(r, after) {
  const { saved, skipped } = r;
  const why = skipped.length ? `${skipped.length === 1 ? `${skipped[0].name}: ${skipped[0].error}` : `${plural(skipped.length, "file")} had no recipe in them`}` : "";
  if (!saved.length) return toast(why || "Nothing to bring in.", true);
  toast(`Saved ${saved.length === 1 ? `the recipe "${saved[0].title}"` : plural(saved.length, "recipe")}${why ? `; ${why}` : ""}.`, !!skipped.length && saved.length === 0);
  after(saved);
}

export async function importFiles(files, after) {
  files = [...files].filter(isRecipeFile);
  if (!files.length) return toast("Pictures, videos or recipe files (.json) only.", true);
  toast(`Reading ${plural(files.length, "file")}…`);
  try { told(await upload(files), after); } catch (e) { toast(e.message, true); }
}

async function importLink(after) {
  const url = await askText("Bring in a recipe from a link", "A CivitAI image or post, or a picture's own link", "", { ok: "Bring it in", placeholder: "https://civitai.com/images/…" });
  if (!url) return;
  await fromLink(url.trim(), after);
}

async function fromLink(url, after) {
  toast("Reading the link…");
  try {
    const r = await api("/recipes/import", { method: "POST", body: { url } });
    if (r.choices) return pickFromPost(r.choices, after);
    told(r, after);
  } catch (e) { toast(e.message, true); }
}

// a CivitAI post: which of its pictures
function pickFromPost(choices, after) {
  const dlg = h("dialog.dialog.rc-pick", { "aria-label": "Which picture?" },
    h("h2", {}, "Which picture's recipe?"),
    h("p.note", {}, "This post has several. The ones without generation data are dimmed."),
    h("div.rc-choices", {}, choices.map((c) => h("button.rc-choice" + (c.has_meta ? "" : ".none"), { type: "button", title: c.has_meta ? "" : "No generation data",
      onclick: () => { dlg.close(); fromLink(c.url, after); } }, h("img", { src: c.thumb, alt: "", loading: "lazy", referrerpolicy: "no-referrer" })))),
    h("div.row.end", {}, h("button.btn", { type: "button", onclick: () => dlg.close() }, "Cancel")));
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}

// from the grid's selection (its More menu)
export async function saveAsRecipes(sel, n, ctx) {
  toast(`Reading ${plural(n, "file")}…`);
  try {
    const r = await api("/recipes/from-files", { method: "POST", body: { selection: sel } });
    told(r, (saved) => { ctx.counts(); if (saved.length === 1) toast(`Saved as the recipe "${saved[0].title}": it's in Recipes, and in Model Librarian's Recipes tab.`); });
  } catch (e) { toast(e.message, true); }
}

// a drop zone: pictures, videos and recipe files from Explorer
function dropIn(el, after) {
  const files = (e) => !!e.dataTransfer?.types?.includes("Files");
  el.addEventListener("dragover", (e) => { if (!files(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; el.classList.add("rc-over"); });
  el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("rc-over"); });
  el.addEventListener("drop", (e) => {
    el.classList.remove("rc-over");
    if (!files(e)) return;
    e.preventDefault();
    importFiles(e.dataTransfer.files, after);
  });
}

// ---------------------------------------------------------------- the list
export async function renderRecipes(root, ctx) {
  root.setAttribute("aria-busy", "true");
  let data;
  try { data = await api("/recipes"); } catch (e) { toast(e.message, true); return; } finally { root.removeAttribute("aria-busy"); }
  const again = (saved) => { ctx.counts(); if (saved?.length === 1) ctx.show("recipe", saved[0].id); else renderRecipes(root, ctx); };
  const pick = h("input", { type: "file", multiple: true, accept: ACCEPT, hidden: true, onchange: () => { importFiles(pick.files, again); pick.value = ""; } });
  const grid = h("div.c-grid.rc-grid");
  const search = h("input.rc-search", { type: "search", placeholder: "Search titles, prompts, models", "aria-label": "Search recipes", value: filter.q, autocomplete: "off" });
  search.spellcheck = false;
  const favBtn = h("button.btn.small" + (filter.fav ? ".on" : ""), { type: "button", "aria-pressed": String(filter.fav), title: "Only favourites" }, icon("heart"), "Favourites");
  const tagSel = h("select.rc-tag", { "aria-label": "Tag" }, h("option", { value: "" }, "Any tag"),
    data.tags.map((t) => h("option", { value: t, selected: t === filter.tag }, t)));
  const shown = h("span.st-none");
  const draw = () => {
    const words = filter.q.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = data.recipes.filter((r) => (!filter.fav || r.fav) && (!filter.tag || r.tags.includes(filter.tag)) && words.every((w) =>
      [r.title, r.prompt, r.model, r.base_model, ...r.loras, ...r.tags].some((s) => String(s || "").toLowerCase().includes(w))));
    shown.textContent = hits.length === data.recipes.length ? plural(hits.length, "recipe") : `${hits.length.toLocaleString()} of ${plural(data.recipes.length, "recipe")}`;
    grid.replaceChildren(...hits.map((r) => h("button.c-card.rc-card", { type: "button", onclick: () => ctx.show("recipe", r.id) },
      thumb(r, r.title),
      h("span.c-name", { title: r.title }, r.fav ? h("span.rc-fav", { "aria-label": "Favourite" }, icon("heart")) : null, r.title),
      h("span.c-n", {}, [r.model || r.base_model, r.loras.length ? plural(r.loras.length, "LoRA") : ""].filter(Boolean).join(" · ") || "No models named"))));
    if (!hits.length && data.recipes.length) grid.replaceChildren(h("p.st-none", {}, "No recipe matches."));
  };
  const keep = () => { store.set("recipes-filter", filter); draw(); };
  search.addEventListener("input", () => { filter.q = search.value; keep(); });
  search.addEventListener("keydown", (e) => e.stopPropagation());
  favBtn.onclick = () => { filter.fav = !filter.fav; favBtn.classList.toggle("on", filter.fav); favBtn.setAttribute("aria-pressed", String(filter.fav)); keep(); };
  tagSel.onchange = () => { filter.tag = tagSel.value; keep(); };
  if (filter.tag && !data.tags.includes(filter.tag)) filter.tag = "";
  const zone = h("div.rc-drop", {}, icon("save"),
    h("span", {}, "Drop pictures, videos or recipe files here to bring in what made them, or ",
      h("button.link", { type: "button", onclick: () => pick.click() }, "choose files"), "."));
  root.replaceChildren(...[
    h("header.st-head", {}, h("h1", {}, "Recipes"),
      h("div.row", {},
        h("button.btn", { type: "button", onclick: () => importLink(again) }, icon("plus"), "Paste a link…"),
        h("button.btn.primary", { type: "button", onclick: () => pick.click() }, icon("plus"), "Choose files…"), pick)),
    h("p.note.rc-lede", {}, "Model Librarian's recipes, the same list as its Recipes tab in ComfyUI. Open one there to download the models you're missing and fill a workflow."),
    zone,
    data.recipes.length ? h("div.rc-bar", {}, search, favBtn, data.tags.length ? tagSel : null, shown) : null,
    data.recipes.length ? grid
      : h("div.c-empty", {}, emptyArt("book"), h("h2", {}, "No recipes yet"),
        h("p", {}, "A recipe is what made a picture: its prompt, settings, checkpoint and LoRAs with their strengths. Drop a picture or a ComfyUI video here, paste a CivitAI link, or select pictures in the library and use More, Save as recipes. With the Chrome extension, Save for the Gallery on CivitAI works with ComfyUI closed too.")),
  ].filter(Boolean));
  dropIn(root, again);
  draw();
}

// ---------------------------------------------------------------- one recipe
function section(title, ...kids) {
  return h("section.st-card", { "aria-label": title }, h("div.st-card-head", {}, h("h2", {}, title)), ...kids);
}

function promptBlock(text, neg = false) {
  return h("div.prompt-wrap", {}, h("div.prompt" + (neg ? ".neg" : ""), {}, text),
    h("button.icon-btn.small.p-copy", { type: "button", "aria-label": "Copy", title: "Copy", onclick: () => copyText(text, neg ? "Negative prompt copied" : "Prompt copied") }, icon("copy")));
}

function strength(l) {
  const w = typeof l.weight === "number" ? l.weight : null;
  if (w == null) return null;
  const clip = typeof l.clip_weight === "number" && l.clip_weight !== w ? l.clip_weight : null;
  return h("span.strength", { title: clip != null ? `model ${fmtNum(w)} · clip ${fmtNum(clip)}` : `strength ${fmtNum(w)}` },
    h("span.bar", {}, h("span", { style: { width: `${Math.max(4, Math.min(100, Math.abs(w) * 100))}%` } })), fmtNum(w));
}

// what Find its pictures searches for: its checkpoint and LoRAs, by the names the library knows them by
function chipsFor(r) {
  const chips = [];
  if (refName(r.model)) chips.push({ key: "model", value: stem(r.model.file) || refName(r.model) });
  for (const l of r.loras || []) if (refName(l)) chips.push({ key: "lora", value: stem(l.file) || refName(l) });
  return chips;
}

function allText(r) {
  const s = r.settings || {};
  const lines = [r.title, "", r.prompt?.positive || ""];
  if (r.prompt?.negative) lines.push("", `Negative prompt: ${r.prompt.negative}`);
  const set = [["Steps", s.steps], ["Sampler", s.sampler_raw || s.sampler], ["Scheduler", s.scheduler], ["CFG scale", s.cfg], ["Seed", s.seed],
    ["Size", s.width && s.height ? `${s.width}x${s.height}` : null], ["Denoising strength", s.denoise], ["Clip skip", s.clip_skip]].filter(([, v]) => v != null && v !== "");
  if (set.length) lines.push("", set.map(([k, v]) => `${k}: ${v}`).join(", "));
  if (refName(r.model)) lines.push("", `Checkpoint: ${refName(r.model)}`);
  for (const l of r.loras || []) lines.push(`LoRA: ${refName(l)}${typeof l.weight === "number" ? ` (${fmtNum(l.weight)})` : ""}`);
  return lines.join("\n").trim();
}

export async function renderRecipe(root, id, ctx) {
  root.setAttribute("aria-busy", "true");
  let r, sum;
  try { ({ recipe: r, summary: sum } = await api(`/recipes/${id}`)); } catch (e) { toast(e.message, true); ctx.show("recipes"); return; } finally { root.removeAttribute("aria-busy"); }
  const again = () => renderRecipe(root, id, ctx);
  const patch = async (body, what) => {
    try { await api(`/recipes/${id}`, { method: "POST", body }); if (what) toast(what); again(); } catch (e) { toast(e.message, true); }
  };
  const toComfy = async (mode, btn) => {
    btn.disabled = true;
    try {
      const out = await api(`/recipes/${id}/comfy`, { method: "POST", body: { mode } });
      toast(mode === "workflow" ? `Opened in ComfyUI as a new workflow${out.clients > 1 ? ` (in the first of its ${out.clients} tabs to answer)` : ""}.`
        : "Opened in Model Librarian's Recipes tab in ComfyUI.");
    } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
  };
  const s = r.settings || {}, chips = chipsFor(r), src = r.source || {};
  const settings = [["Seed", s.seed], ["Steps", s.steps], ["CFG", s.cfg], ["Sampler", s.sampler || s.sampler_raw], ["Scheduler", s.scheduler],
    ["Size", s.width && s.height ? `${s.width} × ${s.height}` : null], s.denoise != null && s.denoise !== 1 ? ["Denoise", s.denoise] : null,
    s.clip_skip ? ["Clip skip", s.clip_skip] : null].filter((x) => x && x[1] != null && x[1] !== "");
  const models = [r.model && refName(r.model) ? { kind: "checkpoint", ref: r.model } : null, r.vae && refName(r.vae) ? { kind: "vae", ref: r.vae } : null,
    ...(r.loras || []).filter(refName).map((l) => ({ kind: "lora", ref: l }))].filter(Boolean);
  const tagAdd = h("input.pt-add", { type: "text", placeholder: "Add a tag", "aria-label": "Add a tag", autocomplete: "off" });
  tagAdd.spellcheck = false;
  tagAdd.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key !== "Enter") return;
    e.preventDefault();
    const more = tagAdd.value.split(",").map((t) => t.trim()).filter(Boolean);
    if (more.length) patch({ tags: [...(r.tags || []), ...more] });
  });
  const notes = h("textarea.rc-notes", { rows: 3, placeholder: "Notes for yourself", "aria-label": "Notes" }, r.notes || "");
  notes.addEventListener("keydown", (e) => e.stopPropagation());
  notes.addEventListener("blur", () => { if (notes.value !== (r.notes || "")) patch({ notes: notes.value }, "Notes saved."); });
  const openBtn = h("button.btn.primary", { type: "button", title: "Download what's missing and fill a workflow, in ComfyUI (Model Librarian 1.29.0 or newer)",
    onclick: (e) => toComfy("recipe", e.currentTarget) }, icon("send"), "Open in Model Librarian");
  const wfBtn = sum.workflow ? h("button.btn", { type: "button", title: "The workflow it was made with, in a new ComfyUI tab", onclick: (e) => toComfy("workflow", e.currentTarget) }, icon("play"), "Open as a new workflow") : null;
  root.replaceChildren(...[
    h("div.c-crumb", {}, h("button.link", { type: "button", onclick: () => ctx.show("recipes") }, "Recipes"), " / ", r.title),
    h("header.c-head", {},
      h("div.c-portrait.rc-portrait", {}, thumb(sum, r.title)),
      h("div.c-title", {},
        h("h1", {}, r.title),
        h("p.c-facts", {}, [r.base_model, r.format && r.format !== "lora-manager" ? `from ${r.format === "civitai" ? "CivitAI" : r.format === "comfyui" || r.format === "comfyui-workflow" ? "ComfyUI" : r.format}` : r.format ? "from LoRA Manager" : "",
          r.created ? `saved ${fmtDate(r.created)}` : "", r.media === "video" ? "a video" : ""].filter(Boolean).join(" · "),
          /^https:\/\//i.test(src.url || "") ? [" · ", h("a", { href: src.url, target: "_blank", rel: "noreferrer" }, src.site === "civitai" ? "on CivitAI" : "its link")] : null),
        h("div.row", {}, openBtn, wfBtn,
          h("button.btn", { type: "button", disabled: !chips.length, title: chips.length ? chips.map((c) => `${c.key}:${c.value}`).join(" ") : "It names no models",
            onclick: () => ctx.open(chips) }, icon("grid"), "Find its pictures")),
        h("div.row", {},
          h("button.btn.small" + (r.fav ? ".on" : ""), { type: "button", "aria-pressed": String(!!r.fav), onclick: () => patch({ fav: !r.fav }) }, icon("heart"), r.fav ? "Favourite" : "Favourite it"),
          h("button.btn.small", { type: "button", onclick: async () => {
            const t = await askText("Rename the recipe", "Name", r.title, { ok: "Rename" });
            if (t && t.trim() && t.trim() !== r.title) patch({ title: t.trim() }, "Renamed.");
          } }, icon("pen"), "Rename…"),
          h("button.btn.small", { type: "button", onclick: () => copyText(allText(r), "Recipe copied") }, icon("copy"), "Copy all"),
          h("button.btn.small.danger", { type: "button", onclick: async () => {
            if (!await ask(`Delete the recipe ${r.title}?`, "It's taken out of Model Librarian's recipes too (into recipes\\_deleted in its folder, where it can be put back by hand). Pictures are untouched.", { ok: "Delete the recipe", danger: true })) return;
            try { await api(`/recipes/${id}`, { method: "DELETE" }); toast(`Deleted the recipe ${r.title}.`); ctx.counts(); ctx.show("recipes"); } catch (e) { toast(e.message, true); }
          } }, icon("trash"), "Delete")))),
    h("div.rc-cols", {},
      h("div.rc-col", {},
        r.prompt?.positive ? section("Prompt", promptBlock(r.prompt.positive)) : null,
        r.prompt?.negative ? section("Negative", promptBlock(r.prompt.negative, true)) : null,
        settings.length ? section("Settings", h("div.settings", {}, settings.map(([k, v]) => h("div.set", {}, h("span.k", {}, k), h("span.v", {}, typeof v === "number" ? fmtNum(v) : String(v)))))) : null),
      h("div.rc-col", {},
        section("Models", models.length ? h("div.models", {}, models.map(({ kind, ref }) => h("div.model", {},
          h("span.kind." + kind, {}, KIND[kind]),
          h("span.link.rc-model", { title: ref.file || refName(ref) }, refName(ref)),
          kind === "lora" ? strength(ref) : null))) : h("p.st-none", {}, "No models named.")),
        section("Tags and notes",
          h("div.tags.pt-tags", {}, (r.tags || []).map((t) => h("span.chip.tag", {}, h("span.pt-t", {}, t),
            h("button.x", { type: "button", "aria-label": `Take the tag ${t} off`, title: "Take it off", onclick: () => patch({ tags: r.tags.filter((x) => x !== t) }) }, icon("remove")))), tagAdd),
          notes))),
  ].filter(Boolean));
}
