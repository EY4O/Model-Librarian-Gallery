// Model Librarian Gallery: organising - the dialogs for tags, albums and moves, the Duplicates and History
// pages, and Undo. A selection is a list of file ids, or a whole view ({view: {...}}).

import { api, ask, baseName, fmtDate, fmtDur, fmtSize, h, icon, media, plural, stars, toast } from "./util.js";
import { preview } from "./characters.js";

let refreshAll = () => {};
export function onChange(fn) { refreshAll = fn; }

// a toast with Undo, after anything that changed files
export function done(res, text) {
  document.querySelector(".toast")?.remove();
  const t = h("div.toast", { role: "status" }, h("span", {}, text),
    res && res.op ? h("button.btn.small", { type: "button", onclick: () => { t.remove(); undo(res.op); } }, "Undo") : null);
  document.body.append(t);
  setTimeout(() => t.remove(), 8000);
  refreshAll();
}

const stuck = new Set();  // steps Ctrl+Z has tried that can't be undone (all the way): it goes on to the one before
export async function undo(opId) {
  try {
    const r = await api(`/ops/${opId}/undo`, { method: "POST" });
    if (r.failed?.length) stuck.add(opId);
    toast(r.failed?.length ? `Undone, except ${plural(r.failed.length, "file")} that changed since.` : "Undone.");
  } catch (e) { stuck.add(opId); toast(e.message, true); }
  refreshAll();
}

// Ctrl+Z: the latest thing that can be undone
export async function undoLast() {
  const { ops } = await api("/ops").catch(() => ({ ops: [] }));
  const last = ops.find((o) => o.undoable && !stuck.has(o.id));
  if (!last) return toast("Nothing to undo.");
  await undo(last.id);
}

export function dialog(title, body, { submit, submitLabel = "OK", danger = false, wide = false } = {}) {
  const err = h("p.form-err", { role: "alert" });
  const go = h("button.btn.primary" + (danger ? ".danger" : ""), { type: "submit" }, submitLabel);
  const dlg = h("dialog.dialog" + (wide ? ".wide" : ""), { "aria-label": title },
    h("form", { method: "dialog", onsubmit: async (e) => {
      e.preventDefault();
      go.disabled = true;
      try {
        if ((await submit()) !== false) dlg.close();
      } catch (ex) { err.textContent = ex.message; }
      go.disabled = false;
    } }, h("h2", {}, title), body, err,
      h("div.row.end", {}, h("button.btn", { type: "button", onclick: () => dlg.close() }, "Cancel"), go)));
  dlg.addEventListener("keydown", (e) => { if (e.key !== "Escape") e.stopPropagation(); });
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  dlg.querySelector("input, select")?.focus();
  return { dlg, err, go };
}

const what = (n) => (n === 1 ? "this file" : `these ${n.toLocaleString()} files`);

// ---------------------------------------------------------------- tags and albums
export function tagDialog(selection, count) {
  const input = h("input", { type: "text", placeholder: "dusk, coast", "aria-label": "Tags", autocomplete: "off" });
  const list = h("datalist", { id: "bulk-tags" });
  input.setAttribute("list", list.id);
  input.addEventListener("input", async () => {
    const { items } = await api(`/suggest?field=tag&q=${encodeURIComponent(input.value.split(",").pop().trim())}`).catch(() => ({ items: [] }));
    list.replaceChildren(...items.map((t) => h("option", { value: t.name })));
  });
  const mode = h("select", { "aria-label": "Add or remove" }, h("option", { value: "tag" }, "Add"), h("option", { value: "untag" }, "Remove"));
  dialog(`Tags for ${what(count)}`, h("div.field-row", {}, mode, h("div", {}, input, list)), {
    submitLabel: "Apply",
    submit: async () => {
      const tags = input.value.split(",").map((s) => s.trim()).filter(Boolean);
      if (!tags.length) throw new Error("Type a tag (several: separated by commas).");
      const r = await api("/ops/bulk", { method: "POST", body: { selection, action: mode.value, value: tags } });
      done(r, `${mode.value === "tag" ? "Tagged" : "Untagged"} ${plural(r.count, "file")}.`);
    },
  });
}

// the artist of many files at once: a name, no artist, or back to what was found in them
export function artistDialog(selection, count) {
  const input = h("input", { type: "text", placeholder: "Their name", "aria-label": "Artist", autocomplete: "off", spellcheck: false });
  const list = h("datalist", { id: "bulk-artists" });
  input.setAttribute("list", list.id);
  input.addEventListener("input", async () => {
    const { items } = await api(`/suggest?field=artist&q=${encodeURIComponent(input.value)}`).catch(() => ({ items: [] }));
    list.replaceChildren(...items.map((t) => h("option", { value: t.name })));
  });
  const mode = h("select", { "aria-label": "What to do" }, h("option", { value: "set" }, "Set to"), h("option", { value: "none" }, "No artist"),
    h("option", { value: "found" }, "What was found in them"));
  mode.onchange = () => { input.disabled = mode.value !== "set"; };
  dialog(`Artist of ${what(count)}`, h("div", {}, h("div.field-row", {}, mode, h("div", {}, input, list)),
    h("p.note", {}, "What you set wins over what's found in the files, their names and their folders.")), {
    submitLabel: "Apply",
    submit: async () => {
      const value = mode.value === "set" ? input.value.trim() : mode.value === "none" ? "" : null;
      if (mode.value === "set" && !value) throw new Error("Type the artist's name.");
      const r = await api("/ops/bulk", { method: "POST", body: { selection, action: "artist", value } });
      done(r, value ? `${plural(r.count, "file")} by ${value} now.` : value === "" ? `${plural(r.count, "file")} with no artist.` : `${plural(r.count, "file")} with the artist found in them.`);
    },
  });
}

// these files are that character (or person): one you have, or a new name
export function characterDialog(selection, count, { characters = [], label = "Character", onDone } = {}) {
  const input = h("input", { type: "text", placeholder: characters.length ? characters[0].name : "Their name", "aria-label": label, autocomplete: "off", spellcheck: false });
  const list = h("datalist", { id: "bulk-characters" }, characters.map((c) => h("option", { value: c.name })));
  input.setAttribute("list", list.id);
  dialog(`${label} in ${what(count)}`, h("div", {}, h("label.field-row", {}, h("span", {}, label), h("div", {}, input, list)),
    h("p.note", {}, `A name that's new makes a new one. They count as confirmed, as a yes in To review does; you can undo it.`)), {
    submitLabel: "Add",
    submit: async () => {
      const name = input.value.trim();
      if (!name) throw new Error("Pick one, or type a new name.");
      const r = await api("/ops/bulk", { method: "POST", body: { selection, action: "character", value: name } });
      done(r, `${plural(r.count, "file")} added to ${name}.`);
      onDone?.();
    },
  });
}

export async function albumDialog(selection, count, { remove = null } = {}) {
  const { albums } = await api("/albums").catch(() => ({ albums: [] }));
  const input = h("input", { type: "text", placeholder: "An album, or a new name", "aria-label": "Album", autocomplete: "off", value: remove || "" });
  const list = h("datalist", { id: "bulk-albums" }, albums.filter((a) => a.query == null).map((a) => h("option", { value: a.name })));  // (a smart album's search decides)
  input.setAttribute("list", list.id);
  const mode = h("select", { "aria-label": "Add or take out" }, h("option", { value: "album" }, "Add to"), h("option", { value: "unalbum", selected: !!remove }, "Take out of"));
  dialog(`Album for ${what(count)}`, h("div.field-row", {}, mode, h("div", {}, input, list)), {
    submitLabel: "Apply",
    submit: async () => {
      const name = input.value.trim();
      if (!name) throw new Error("Pick an album, or type a name for a new one.");
      const r = await api("/ops/bulk", { method: "POST", body: { selection, action: mode.value, value: name } });
      done(r, `${mode.value === "album" ? "Added" : "Took"} ${plural(r.count, "file")} ${mode.value === "album" ? "to" : "out of"} ${name}.`);
    },
  });
}

// ---------------------------------------------------------------- rename by a pattern
const TOKENS = [["{artist}", "who made it"], ["{date}", "when it was taken (else modified)"], ["{n}", "1, 2, 3…"], ["{name}", "its name now"],
  ["{model}", "its checkpoint"], ["{seed}", "its seed"], ["{lora}", "its first LoRA"]];

export function renameDialog(selection, count, { pattern = "{date}_{n}" } = {}) {
  const input = h("input", { type: "text", value: pattern, "aria-label": "Pattern", spellcheck: false, autocomplete: "off" });
  const start = h("input", { type: "number", value: 1, min: 0, "aria-label": "Start at", style: { width: "80px" } });
  const chips = h("div.rn-tokens", {}, TOKENS.map(([t, what]) => h("button.btn.small", { type: "button", title: what, onclick: () => {
    const at = input.selectionStart ?? input.value.length;
    input.value = input.value.slice(0, at) + t + input.value.slice(input.selectionEnd ?? at);
    input.focus();
    check();
  } }, t)));
  const plan = h("div.plan", { "aria-live": "polite" });
  let timer = null, last = null;
  const check = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const p = await api("/ops/rename/plan", { method: "POST", body: { selection, pattern: input.value, start: Number(start.value) || 0 } });
        last = p;
        if (p.limited) p.preview.push({ from: "…", to: `only the first ${p.count.toLocaleString()} at once` });
        plan.replaceChildren(
          p.preview.length ? h("ol.rn-preview", {}, p.preview.map((x) => h("li", {}, h("span.rn-from", {}, x.from), " → ", h("span.rn-to", {}, x.to)))) : null,
          h("p.note", {}, p.count ? `${plural(p.count, "file")} will be renamed${p.count > p.preview.length ? ` (the first ${p.preview.length} shown)` : ""}.` : "Nothing would change."),
          p.same ? h("p.note", {}, `${plural(p.same, "file has", "files have")} that name already.`) : null,
          p.unavailable ? h("p.note", {}, `${plural(p.unavailable, "file isn't", "files aren't")} reachable right now.`) : null);
        go.disabled = !p.count;
      } catch (e) {
        last = null;
        plan.replaceChildren(h("p.form-err", {}, e.message));
        go.disabled = true;
      }
    }, 200);
  };
  input.addEventListener("input", check);
  start.addEventListener("input", check);
  const { go } = dialog(`Rename ${what(count)}`, h("div", {},
    h("label.field-row", {}, h("span", {}, "Pattern"), input),
    chips,
    h("label.field-row", {}, h("span", {}, "Start {n} at"), start),
    plan,
    h("p.note", {}, "Each keeps its folder and its extension; a name that's taken gets a number, like \"name (2)\". What a file doesn't have is left out. You can undo it.")), {
    submitLabel: "Rename", wide: true,
    submit: async () => {
      if (!last || !last.count) return false;
      const r = await api("/ops/rename", { method: "POST", body: { selection, pattern: input.value, start: Number(start.value) || 0 } });
      done(r, `Renamed ${plural(r.renamed, "file")}${r.failed.length ? `; ${r.failed.length} couldn't be` : ""}.`);
    },
  });
  go.disabled = true;
  check();
}

// ---------------------------------------------------------------- move
export async function moveDialog(selection, count, folders, { at = null, after = null } = {}) {
  if (!folders.length) return toast("Add a folder to the library first: files move into your library folders.", true);
  const folder = h("select", { "aria-label": "Folder" }, folders.filter((f) => f.online).map((f) => h("option", { value: f.id, selected: f.id === at }, `${baseName(f.path)}  —  ${f.path}`)));
  const sub = h("input", { type: "text", placeholder: "Optional: a folder inside it, e.g. sorted/coast", "aria-label": "Folder inside", spellcheck: false });
  const plan = h("div.plan", { "aria-live": "polite" });
  let timer = null, last = null;
  const check = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const p = await api("/ops/move/plan", { method: "POST", body: { selection, folder: Number(folder.value), sub: sub.value } });
        last = p;
        plan.replaceChildren(
          h("p", {}, p.count ? `${plural(p.count, "file")} (${fmtSize(p.size)}) will move to ` : "Nothing would move to ", h("code", {}, p.dest)),
          p.copies ? h("p.note", {}, `${plural(p.copies, "file")} on another drive: copied, checked, and only then removed from where ${p.copies === 1 ? "it is" : "they are"}.`) : null,
          p.renamed ? h("p.note", {}, `${plural(p.renamed, "name")} already taken there: they get a number, like "name (2)".`) : null,
          p.already ? h("p.note", {}, `${plural(p.already, "file is", "files are")} already there.`) : null,
          p.unavailable ? h("p.note", {}, `${plural(p.unavailable, "file isn't", "files aren't")} reachable right now and will stay where ${p.unavailable === 1 ? "it is" : "they are"}.`) : null);
        go.disabled = !p.count;
      } catch (e) {
        last = null;
        plan.replaceChildren(h("p.form-err", {}, e.message));
        go.disabled = true;
      }
    }, 200);
  };
  folder.onchange = check;
  sub.oninput = check;
  const { go } = dialog(`Move ${what(count)}`, h("div", {},
    h("label.field-row", {}, h("span", {}, "Into"), folder),
    h("label.field-row", {}, h("span", {}, "Folder inside"), sub), plan,
    h("p.note", {}, "Ratings, tags, albums and characters move with the files. You can undo it.")), {
    submitLabel: "Move",
    submit: async () => {
      if (!last || !last.count) return false;
      const r = await api("/ops/move", { method: "POST", body: { selection, folder: Number(folder.value), sub: sub.value } });
      done(r, `Moved ${plural(r.moved, "file")}${r.failed.length ? `; ${r.failed.length} couldn't be: ${r.failed[0].error}` : ""}.`);
      if (r.moved && after) after();
    },
  });
  go.disabled = true;
  check();
}

// ---------------------------------------------------------------- the Trash
// to the Trash straight away: it's undone from the toast (or Ctrl+Z, or History), so no "are you sure"
export async function trashFiles(selection) {
  try {
    const r = await api("/ops/trash", { method: "POST", body: { selection } });
    const why = r.failed.length ? (r.failed[0].error === "not reachable" ? "its folder isn't reachable right now" : r.failed[0].error) : "";
    done(r, r.trashed ? `${plural(r.trashed, "file")} moved to the Trash${r.failed.length ? ` (${r.failed.length} couldn't be: ${why})` : ""}.`
      : `Nothing moved to the Trash: ${why}.`);
    return r;
  } catch (e) { toast(e.message, true); return null; }
}

export async function restoreFiles(selection) {
  try {
    const r = await api("/ops/restore", { method: "POST", body: { selection } });
    done(r, `Put back ${plural(r.restored, "file")}.`);
    return r;
  } catch (e) { toast(e.message, true); return null; }
}

export async function emptyTrash(selection, count) {
  if (!await ask(`Empty ${selection ? what(count) : "the Trash"}?`, selection
    ? "The files go to the Windows Recycle Bin. The Gallery can't undo this: you'd restore them from the Recycle Bin."
    : "The files go to the Windows Recycle Bin, and so do the originals kept for undoing edits (after this, those edits can't be undone here). The Gallery can't undo this: you'd restore them from the Recycle Bin.",
    { ok: selection ? "Empty these" : "Empty the Trash", danger: true })) return;
  try {
    const r = await api("/ops/empty", { method: "POST", body: selection ? { selection } : {} });
    done(null, `${plural(r.emptied, "file")}${r.backups ? ` and ${plural(r.backups, "kept original")}` : ""} sent to the Recycle Bin.`);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- Send to Eagle
export async function sendToEagle(selection, count) {
  let folders;
  try { ({ folders } = await api("/eagle/folders")); } catch (e) { return toast(e.message, true); }
  const folder = h("select", { "aria-label": "Eagle folder" }, h("option", { value: "" }, "No folder (Eagle's All)"), folders.map((f) => h("option", { value: f.id }, f.name)));
  dialog(`Send ${what(count)} to Eagle`, h("div", {},
    h("label.field-row", {}, h("span", {}, "Into"), folder),
    h("p.note", {}, "Eagle adds a copy of each to its own library, with the Gallery's tags and characters, and the prompt and settings as its note. The Gallery's files stay where they are.")), {
    submitLabel: "Send",
    submit: async () => {
      const r = await api("/eagle/send", { method: "POST", body: { selection, folder: folder.value || null } });
      toast(`Sent ${plural(r.sent, "file")} to Eagle.`);
    },
  });
}

// ---------------------------------------------------------------- Duplicates
export async function renderDuplicates(root) {
  root.setAttribute("aria-busy", "true");
  let d;
  try { d = await api("/duplicates"); } catch (e) { toast(e.message, true); return; } finally { root.removeAttribute("aria-busy"); }
  if (d.stale) setTimeout(() => { if (root.isConnected && !root.hidden && root.querySelector("h1")?.textContent === "Duplicates") renderDuplicates(root); }, 4000);
  const keepOf = new Map();  // each group's file to keep (the first, unless you picked another)
  const extra = (g) => g.files.filter((_, i) => i !== (keepOf.get(g) ?? 0)).map((f) => f.id);
  const all = d.groups.flatMap(extra);
  const bothOf = (g) => (g.files.length === 2 ? "Keep both" : `Keep all ${g.files.length}`);
  // they're not to be thinned out: the group goes from the list (remembered; it comes back if another copy turns up)
  const keepAll = async (g, card) => {
    try {
      const r = await api("/duplicates/keep", { method: "POST", body: { ids: g.files.map((f) => f.id) } });
      card?.remove();
      d.groups.splice(d.groups.indexOf(g), 1);  // (and not in Keep one of each any more)
      done(r, `${g.files.length === 2 ? "Both kept" : `All ${g.files.length} kept`}: they won't be offered as duplicates again.`);
    } catch (e) { toast(e.message, true); }
  };
  root.replaceChildren(...[
    h("header.st-head", {}, h("h1", {}, "Duplicates"),
      all.length ? h("button.btn", { type: "button", onclick: async () => {
        if (await trashFiles(d.groups.flatMap(extra))) renderDuplicates(root);
      } }, `Keep one of each, Trash the other ${all.length.toLocaleString()}`) : null),
    d.groups.length ? h("div.st-lede", {},
      h("p", {}, `${plural(d.groups.length, "group")}, ${fmtSize(d.wasted)} to free.`),
      h("p.st-none", {}, "Each keeps its first file: the rated or favourite one, then the one with generation data, then the biggest. Pick another to keep it instead; the rest go to the Trash."))
      : h("p.st-none", {}, d.kept ? "No duplicates left to look at." : "No duplicates: no file is a copy of another, and no picture is saved twice."),
    d.kept ? h("p.st-none", {}, `${plural(d.kept, "group")} you kept all of ${d.kept === 1 ? "isn't" : "aren't"} shown. `,
      h("button.link", { type: "button", onclick: async () => {
        try { const r = await api("/duplicates/unkeep", { method: "POST" }); done(r, `${plural(r.count, "group")} offered again.`); renderDuplicates(root); } catch (e) { toast(e.message, true); }
      } }, "Show them again")) : null,
    ...d.groups.map((g) => {
      let keep = 0;
      const files = h("div.d-files");
      const draw = () => (keepOf.set(g, keep), files).replaceChildren(...g.files.map((f, i) => h("div.d-file" + (i === keep ? ".keep" : ""), {},
        h("button.d-thumb", { type: "button", title: "Look closer", onclick: () => preview(f) }, h("img", { src: media("thumb", f.id), alt: "", loading: "lazy" })),
        h("div.d-meta", {},
          h("span.d-name", { title: `${f.folder_path}\\${f.rel}` }, f.name),
          h("span.d-sub", {}, [baseName(f.folder_path), f.width ? `${f.width} × ${f.height}` : "", fmtSize(f.size), f.meta === 1 ? "generation data" : ""].filter(Boolean).join(" · "),
            f.rating ? stars(f.rating, { size: 10 }) : null),
          i === keep ? h("span.d-keep", {}, "Kept") : h("button.link", { type: "button", onclick: () => { keep = i; draw(); } }, "Keep this one instead")))));
      draw();
      const card = h("section.st-card", { "aria-label": `${g.kind} group` },
        h("div.st-card-head", {}, h("h2", {}, g.kind === "copies" ? "Copies of one file" : g.files.some((f) => f.kind === "video") ? "The same video, saved more than once" : "The same picture, saved more than once"),
          h("button.btn.small", { type: "button", title: "Look at them side by side, at full size (videos play together)", onclick: () => compare(g, keep, async (k) => {
            keep = k;
            draw();
            const others = g.files.filter((_, i) => i !== keep).map((f) => f.id);
            if (await trashFiles(others)) renderDuplicates(root);
          }, { both: bothOf(g), keepAll: () => keepAll(g, card) }) }, "Compare"),
          h("button.btn.small", { type: "button", title: "They're not duplicates to thin out: don't offer them again", onclick: () => keepAll(g, card) }, bothOf(g)),
          h("button.btn.small", { type: "button", onclick: async () => {
            const others = g.files.filter((_, i) => i !== keep).map((f) => f.id);
            if (await trashFiles(others)) renderDuplicates(root);
          } }, `Trash the other ${g.files.length - 1}`)),
        files);
      return card;
    }),
  ]);
}

// the copies of one picture side by side (four at a time, ← → for more), filling the window: fitted, or at actual size
// (scrolling together); videos play, pause and seek together, the kept one with sound. Pick the one to keep, or keep them all
const SIDE = 4;
function compare(g, keep, choose, { both = "Keep all", keepAll = null } = {}) {
  let actual = false, page = Math.floor(keep / SIDE);
  const pages = Math.ceil(g.files.length / SIDE);
  const videos = () => panes.map((p) => p.video).filter(Boolean);
  const follow = (v, what) => {  // the others do as this one did
    if (v._quiet) return;
    for (const o of videos()) {
      if (o === v) continue;
      o._quiet = true;
      const settle = () => { clearTimeout(o._settle); o._settle = setTimeout(() => { o._quiet = false; }, 120); };
      if (what === "seek" || Math.abs(o.currentTime - v.currentTime) > 0.3) {
        o.addEventListener("seeked", settle, { once: true });  // (its own seek: not one to pass on)
        o.currentTime = Math.min(v.currentTime, o.duration || v.currentTime);
      } else settle();
      if (what === "play") o.play().catch(() => {});
      if (what === "pause") o.pause();
      setTimeout(() => { if (o._quiet) settle(); }, 1500);  // (a seek that never says it's done)
    }
  };
  const pane = (f, i) => {
    const video = f.kind === "video" ? h("video", { src: media("file", f.id), controls: true, loop: true, playsInline: true, preload: "metadata", muted: i !== keep }) : null;
    if (video) for (const t of ["play", "pause", "seeked"]) video.addEventListener(t, () => follow(video, t === "seeked" ? "seek" : t));
    const img = video || h("img", { src: media("file", f.id), alt: f.name, draggable: false });
    const scroller = h("div.cmp-pic", {}, img);
    scroller.addEventListener("scroll", () => {  // at actual size, the others follow: the same spot in each
      if (!actual || scroller._following) return;
      for (const p of panes) if (p.scroller !== scroller) { p.scroller._following = true; p.scroller.scrollTop = scroller.scrollTop; p.scroller.scrollLeft = scroller.scrollLeft; requestAnimationFrame(() => { p.scroller._following = false; }); }
    });
    return { scroller, video, el: h("figure.cmp-one" + (i === keep ? ".keep" : ""), {}, scroller,
      h("figcaption", {},
        h("span.cmp-name", { title: `${f.folder_path}\\${f.rel}` }, f.name),
        h("span.cmp-sub", {}, [f.width ? `${f.width} × ${f.height}` : "", f.duration ? fmtDur(f.duration) : "", fmtSize(f.size), fmtDate(f.mtime), f.meta === 1 ? "generation data" : ""].filter(Boolean).join(" · ")),
        f.rating ? stars(f.rating, { size: 10 }) : null,
        h("button.btn.small" + (i === keep ? ".primary" : ""), { type: "button", onclick: () => { shut(); choose(i); } }, icon("check"), "Keep this one"))) };
  };
  let panes = [];
  const row = h("div.cmp-row");
  const where = h("span.cmp-where");
  const prev = h("button.icon-btn", { type: "button", "aria-label": "The ones before", title: "The ones before (←)", onclick: () => turn(-1) }, "‹");
  const next = h("button.icon-btn", { type: "button", "aria-label": "The next ones", title: "The next ones (→)", onclick: () => turn(1) }, "›");
  const turn = (d) => {
    const to = Math.max(0, Math.min(pages - 1, page + d));
    if (d && to === page) return;  // (no page that way: what's playing goes on)
    page = to;
    for (const v of videos()) v.pause();
    const from = page * SIDE, shown = g.files.slice(from, from + SIDE);
    panes = shown.map((f, k) => pane(f, from + k));
    row.style.gridTemplateColumns = `repeat(${shown.length}, minmax(0, 1fr))`;
    row.replaceChildren(...panes.map((p) => p.el));
    where.textContent = pages > 1 ? `${from + 1}–${from + shown.length} of ${g.files.length}` : "";
    prev.hidden = next.hidden = pages < 2;
    prev.disabled = page === 0;
    next.disabled = page === pages - 1;
  };
  const size = h("button.btn.small", { type: "button", onclick: () => {
    actual = !actual;
    box.classList.toggle("actual", actual);
    size.textContent = actual ? "Fit to the window" : "Actual size";
  } }, "Actual size");
  const shut = () => { for (const v of videos()) v.pause(); box.remove(); document.removeEventListener("keydown", esc, true); };
  const esc = (e) => {
    if (e.key === "Escape") shut();
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") turn(e.key === "ArrowLeft" ? -1 : 1);
    else return;
    e.stopPropagation();
    e.preventDefault();
  };
  const box = h("div.cmp", { role: "dialog", "aria-label": "Compare" },
    h("header.cmp-bar", {}, h("span", {}, `${plural(g.files.length, "copy", "copies")}: keep one, the rest go to the Trash`), h("span.spacer"), prev, where, next,
      keepAll ? h("button.btn.small", { type: "button", title: "They're not duplicates to thin out: don't offer them again", onclick: () => { shut(); keepAll(); } }, both) : null, size,
      h("button.icon-btn", { type: "button", "aria-label": "Close", title: "Close (Esc)", onclick: shut }, icon("remove"))),
    row);
  turn(0);
  document.addEventListener("keydown", esc, true);
  document.body.append(box);
}

// ---------------------------------------------------------------- History
export async function renderHistory(root) {
  const { ops } = await api("/ops").catch(() => ({ ops: [] }));
  root.replaceChildren(
    h("header.st-head", {}, h("h1", {}, "History"), h("span.st-none", {}, "What you changed, last first. Ctrl+Z undoes the latest.")),
    ops.length ? h("ol.history", {}, ops.map((o) => h("li" + (o.undone ? ".undone" : ""), {},
      h("span.h-when", {}, fmtDate(o.at)),
      h("span.h-what", {}, o.summary),
      o.undone ? h("span.st-none", {}, "Undone") : o.undoable ? h("button.btn.small", { type: "button", onclick: async () => { await undo(o.id); renderHistory(root); } }, "Undo")
        : h("span.st-none", { title: "Restore the files from the Windows Recycle Bin" }, "In the Recycle Bin"))))
      : h("p.st-none", {}, "Nothing yet: moves, deletes and changes to many files at once show up here."));
}

export { icon };
