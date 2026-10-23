// Model Librarian Gallery: organising - the dialogs for tags, albums and moves, the Duplicates and History
// pages, and Undo. A selection is a list of file ids, or a whole view ({view: {...}}).

import { api, baseName, fmtDate, fmtSize, h, icon, media, plural, toast } from "./util.js";
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

export async function undo(opId) {
  try {
    const r = await api(`/ops/${opId}/undo`, { method: "POST" });
    toast(r.failed?.length ? `Undone, except ${plural(r.failed.length, "file")} that changed since.` : "Undone.");
  } catch (e) { toast(e.message, true); }
  refreshAll();
}

// Ctrl+Z: the latest thing that can be undone
export async function undoLast() {
  const { ops } = await api("/ops").catch(() => ({ ops: [] }));
  const last = ops.find((o) => o.undoable);
  if (!last) return toast("Nothing to undo.");
  await undo(last.id);
}

function dialog(title, body, { submit, submitLabel = "OK", danger = false, wide = false } = {}) {
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

export async function albumDialog(selection, count, { remove = null } = {}) {
  const { albums } = await api("/albums").catch(() => ({ albums: [] }));
  const input = h("input", { type: "text", placeholder: "An album, or a new name", "aria-label": "Album", autocomplete: "off", value: remove || "" });
  const list = h("datalist", { id: "bulk-albums" }, albums.map((a) => h("option", { value: a.name })));
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

// ---------------------------------------------------------------- move
export async function moveDialog(selection, count, folders) {
  if (!folders.length) return toast("Add a folder to the library first: files move into your library folders.", true);
  const folder = h("select", { "aria-label": "Folder" }, folders.filter((f) => f.online).map((f) => h("option", { value: f.id }, `${baseName(f.path)}  —  ${f.path}`)));
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
    },
  });
  go.disabled = true;
  check();
}

// ---------------------------------------------------------------- the Trash
export async function trashFiles(selection, count, { ask = count > 1 } = {}) {
  if (ask && !confirm(`Move ${what(count)} to the Trash?\n\nThey stay in the Trash until you empty it, and can be put back any time.`)) return null;
  try {
    const r = await api("/ops/trash", { method: "POST", body: { selection } });
    done(r, `${plural(r.trashed, "file")} moved to the Trash${r.failed.length ? ` (${r.failed.length} couldn't be)` : ""}.`);
    return r;
  } catch (e) { toast(e.message, true); return null; }
}

export async function restoreFiles(selection) {
  try {
    const r = await api("/ops/restore", { method: "POST", body: { selection } });
    done(r, `Put back ${plural(r.restored, "file")}.`);
  } catch (e) { toast(e.message, true); }
}

export async function emptyTrash(selection, count) {
  if (!confirm(`Empty ${selection ? what(count) : "the Trash"}?\n\nThe files go to the Windows Recycle Bin: restore them from there if you need them again.`)) return;
  try {
    const r = await api("/ops/empty", { method: "POST", body: selection ? { selection } : {} });
    done(null, `${plural(r.emptied, "file")} sent to the Recycle Bin.`);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- Duplicates
export async function renderDuplicates(root) {
  root.setAttribute("aria-busy", "true");
  let d;
  try { d = await api("/duplicates"); } catch (e) { toast(e.message, true); return; } finally { root.removeAttribute("aria-busy"); }
  if (d.stale) setTimeout(() => { if (root.isConnected && !root.hidden && root.querySelector("h1")?.textContent === "Duplicates") renderDuplicates(root); }, 4000);
  const extra = (g) => g.files.slice(1).map((f) => f.id);
  const all = d.groups.flatMap(extra);
  root.replaceChildren(...[
    h("header.st-head", {}, h("h1", {}, "Duplicates"),
      all.length ? h("button.btn", { type: "button", onclick: async () => {
        if (await trashFiles(all, all.length, { ask: true })) renderDuplicates(root);
      } }, `Keep one of each, Trash the other ${all.length.toLocaleString()}`) : null),
    h("p.st-none", {}, d.groups.length
      ? `${plural(d.groups.length, "group")}; ${fmtSize(d.wasted)} would be freed. In each, the one kept is the first: rated or favourite, with its generation data, the biggest, the oldest. Click one to keep it instead.`
      : "No duplicates: no file is a copy of another, and no picture is saved twice."),
    ...d.groups.map((g) => {
      let keep = 0;
      const files = h("div.d-files");
      const draw = () => files.replaceChildren(...g.files.map((f, i) => h("div.d-file" + (i === keep ? ".keep" : ""), {},
        h("button.d-thumb", { type: "button", title: "Look closer", onclick: () => preview(f) }, h("img", { src: media("thumb", f.id), alt: "", loading: "lazy" })),
        h("div.d-meta", {},
          h("span.d-name", { title: `${f.folder_path}\\${f.rel}` }, f.name),
          h("span.d-sub", {}, [baseName(f.folder_path), f.width ? `${f.width} × ${f.height}` : "", fmtSize(f.size), f.meta === 1 ? "generation data" : "", f.rating ? "★".repeat(f.rating) : ""].filter(Boolean).join(" · ")),
          i === keep ? h("span.d-keep", {}, "Kept") : h("button.link", { type: "button", onclick: () => { keep = i; draw(); } }, "Keep this one instead")))));
      draw();
      return h("section.st-card", { "aria-label": `${g.kind} group` },
        h("div.st-card-head", {}, h("h2", {}, g.kind === "copies" ? "Copies of one file" : "The same picture, saved more than once"),
          h("button.btn.small", { type: "button", onclick: async () => {
            const others = g.files.filter((_, i) => i !== keep).map((f) => f.id);
            if (await trashFiles(others, others.length, { ask: false })) renderDuplicates(root);
          } }, `Trash the other ${g.files.length - 1}`)),
        files);
    }),
  ]);
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
