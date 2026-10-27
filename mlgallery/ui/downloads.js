// Model Librarian Gallery: New downloads - the pictures in your Downloads folder (and others set in Settings), offered
// for filing into the library: who made each and where it's from, as far as it says, and whether the library has it.

import { TOKEN, api, ask, baseName, emptyArt, midName, fmtDate, fmtSize, h, icon, plural, toast } from "./util.js";
import { preview } from "./characters.js";
import { openLink } from "./artists.js";
import * as org from "./organize.js";

const PAGE = 60;
const pic = (kind, it) => `/api/inbox/${kind}/${it.key}?t=${encodeURIComponent(TOKEN)}&v=${Math.round(it.mtime)}-${it.size}`;  // (a new download of the same name: its own picture)
const FROM = { note: "from the page it was saved from", file: "from the file", download: "from where it was downloaded", name: "from its name" };

// what's selected, how many are shown and the card the keyboard was on: kept while the page is drawn again (after filing, an Undo)
const kept = { sel: new Set(), shown: PAGE, focusAt: -1 };

export async function renderDownloads(root, ctx) {
  const sel = kept.sel;
  let data = null;
  const where = (s, folders) => {
    if (!s) return "";
    const f = folders.find((x) => x.id === s.folder);
    return f ? [baseName(f.path), ...(s.sub ? s.sub.split("/") : [])].join(" \\ ") : "";
  };
  const load = async () => {
    root.setAttribute("aria-busy", "true");
    try { data = await api(`/inbox?limit=${kept.shown}`); } catch (e) { toast(e.message, true); return; } finally { root.removeAttribute("aria-busy"); }
    for (const k of [...sel]) if (!data.items.some((it) => it.key === k)) sel.delete(k);
    draw();
  };
  const after = (r, text) => org.done(r, text);  // (which draws this screen again, as Undo does)
  const fileThem = async (keys, dest, artist) => {
    try {
      const r = await api("/inbox/file", { method: "POST", body: { items: keys, folder: dest.folder, sub: dest.sub, ...(artist !== undefined ? { artist } : {}) } });
      keys.forEach((k) => sel.delete(k));
      after(r, `Filed ${plural(r.filed, "download")} in ${baseName(r.dest)}${r.failed.length ? `; ${r.failed.length} couldn't be: ${r.failed[0].error}` : ""}.`);
    } catch (e) { toast(e.message, true); }
  };
  const dismiss = async (keys) => {
    try {
      await api("/inbox/dismiss", { method: "POST", body: { items: keys } });
      keys.forEach((k) => sel.delete(k));
      toast(`${plural(keys.length, "download")} won't be offered again (Settings can bring ${keys.length === 1 ? "it" : "them"} back).`);
      ctx.counts();
      load();
    } catch (e) { toast(e.message, true); }
  };
  const recycle = async (keys) => {
    const n = keys.length;
    const dups = data.items.filter((it) => keys.includes(it.key) && it.have).length;
    if (!await ask(`Delete ${n === 1 ? "this download" : `these ${n} downloads`}?`,
      `${dups === n ? (n === 1 ? "The library has it already." : "The library has each of them already.") : dups ? `${dups} of them ${dups === 1 ? "is" : "are"} in the library already; the rest aren't.` : n === 1 ? "The library doesn't have it." : "The library doesn't have them."} ${n === 1 ? "It goes" : "They go"} to the Windows Recycle Bin, where you can get ${n === 1 ? "it" : "them"} back.`,
      { ok: "Delete", danger: true })) return;
    try {
      const r = await api("/inbox/recycle", { method: "POST", body: { items: keys } });
      keys.forEach((k) => sel.delete(k));
      toast(`Sent ${plural(r.recycled, "download")} to the Recycle Bin${r.failed.length ? `; ${r.failed.length} couldn't go: ${r.failed[0].error}` : ""}.`);
      ctx.counts();
      load();
    } catch (e) { toast(e.message, true); }
  };
  const fileDialog = (keys) => {
    const first = data.items.find((it) => it.key === keys[0]);
    const folders = ctx.folders().filter((f) => f.online);
    if (!folders.length) return toast("Add a folder to the library first: downloads are filed into your library folders.", true);
    const picked = data.items.filter((it) => keys.includes(it.key));
    const differ = new Set(picked.map((it) => (it.suggest ? `${it.suggest.folder}/${it.suggest.sub}` : ""))).size > 1;
    // (several with different suggestions - other artists' folders, say: not all into the first one's)
    const s = differ ? { folder: first?.suggest?.folder || folders[0].id, sub: "" } : first?.suggest || { folder: folders[0].id, sub: "" };
    const folder = h("select", { "aria-label": "Folder" }, folders.map((f) => h("option", { value: f.id, selected: f.id === s.folder }, `${baseName(f.path)}  —  ${f.path}`)));
    const sub = h("input", { type: "text", value: s.sub || "", placeholder: "Optional: a folder inside it, e.g. Artists/their name", "aria-label": "Folder inside", spellcheck: false });
    const list = h("datalist", { id: "file-artists" });
    const artist = h("input", { type: "text", placeholder: keys.length === 1 && first?.artist ? first.artist : "Leave empty to keep what was found", "aria-label": "Artist", autocomplete: "off", spellcheck: false });
    artist.setAttribute("list", list.id);
    artist.addEventListener("input", async () => {
      const { items } = await api(`/suggest?field=artist&q=${encodeURIComponent(artist.value)}`).catch(() => ({ items: [] }));
      list.replaceChildren(...items.map((it) => h("option", { value: it.name })));
    });
    org.dialog(keys.length === 1 ? `File ${first.name}` : `File ${keys.length} downloads`, h("div", {},
      h("label.field-row", {}, h("span", {}, "Into"), folder),
      h("label.field-row", {}, h("span", {}, "Folder inside"), sub),
      h("label.field-row", {}, h("span", {}, "Artist"), h("div", {}, artist, list)),
      h("p.note", {}, `${differ ? "They're suggested for different folders, so they all go where you pick here. " : ""}They're moved out of the downloads folder into the library, with who made them and where they're from. History can undo it.`)), {
      submitLabel: "File",
      submit: () => fileThem(keys, { folder: Number(folder.value), sub: sub.value }, artist.value.trim() ? artist.value.trim() : undefined),
    });
  };
  const card = (it) => {
    const picked = sel.has(it.key);
    const toggle = () => { if (sel.has(it.key)) sel.delete(it.key); else sel.add(it.key); draw(); };
    const img = h("img", { src: pic("thumb", it), alt: it.name, loading: "lazy", decoding: "async" });
    img.onerror = () => img.replaceWith(h("span.dl-none", {}, it.kind === "video" ? "Video" : it.name.split(".").pop().toUpperCase()));
    return h("article.dl-card" + (picked ? ".picked" : ""), { "aria-label": it.name, tabIndex: 0 },
      h("label.dl-pick", { title: "Select" }, h("input", { type: "checkbox", checked: picked, onchange: toggle, "aria-label": `Select ${it.name}` })),
      h("button.dl-thumb", { type: "button", title: "Look closer", onclick: () => preview({ name: it.name, kind: it.kind, src: pic("file", it) }) }, img),
      h("div.dl-body", {},
        h("p.dl-name", { title: it.title ? `${it.title}\n${it.name}` : it.name }, it.title || midName(it.name)),
        h("p.dl-sub", {}, `${fmtSize(it.size)} · ${fmtDate(it.mtime)}`),
        it.artist ? h("p.dl-artist", { title: FROM[it.artist_from] || "" }, it.artist, h("span.credit-from", {}, ` ${FROM[it.artist_from] || ""}`)) : h("p.dl-artist.dim", {}, "Artist not known"),
        it.site ? h("p.dl-site", {}, it.url ? h("button.link", { type: "button", title: it.url, onclick: () => openLink(it.url) }, it.site_name) : it.site_name,
          it.tags ? h("span.credit-from", {}, ` · ${plural(it.tags, "tag")}`) : null) : null,
        it.have ? h("p.dl-have", { title: it.have.path }, icon("check"), it.have.trash ? "In the library's Trash already" : "In the library already") : null,
        it.suggest ? h("p.dl-dest", { title: it.suggest.why ? `Suggested: ${it.suggest.why}` : "" }, "→ ", where(it.suggest, ctx.folders())) : null),
      h("div.dl-acts", {},
        it.have
          ? h("button.btn.small", { type: "button", title: "The library has it: send this copy to the Recycle Bin", onclick: () => recycle([it.key]) }, icon("trash"), "Delete")
          : h("button.btn.small.primary", { type: "button", disabled: !it.suggest, title: it.suggest ? `File it in ${where(it.suggest, ctx.folders())}` : "", onclick: () => fileThem([it.key], it.suggest) }, icon("move"), "File"),
        h("button.btn.small", { type: "button", onclick: () => fileDialog([it.key]) }, it.have ? "File anyway…" : "Elsewhere…"),
        h("button.icon-btn.small", { type: "button", "aria-label": `Dismiss ${it.name}`, title: "Dismiss: don't offer it again", onclick: () => dismiss([it.key]) }, icon("remove"))));
  };
  // the keyboard: arrows between the cards, and a letter for what to do with the one you're on
  const grid = (cards) => {
    const el = h("div.dl-grid", {}, cards);
    el.addEventListener("pointerdown", () => { kept.focusAt = -1; });  // (the mouse again: a redraw doesn't pull the page back to a card)
    el.addEventListener("keydown", (e) => {
      const at = e.target.closest?.(".dl-card");
      if (!at || e.ctrlKey || e.altKey || e.metaKey || e.target.closest("button, input") && e.target !== at) return;
      const list = [...el.querySelectorAll(".dl-card")], i = list.indexOf(at), it = data.items[i];
      const cols = Math.max(1, list.filter((c) => c.offsetTop === list[0].offsetTop).length);
      const go = (j) => { if (list[j]) { list[j].focus(); kept.focusAt = j; } };
      const k = e.key.toLowerCase();
      if (e.key === "ArrowRight") go(i + 1);
      else if (e.key === "ArrowLeft") go(i - 1);
      else if (e.key === "ArrowDown") go(i + cols);
      else if (e.key === "ArrowUp") go(i - cols);
      else if (k === "f") {
        kept.focusAt = i;
        if (it.have) recycle([it.key]);
        else if (it.suggest) fileThem([it.key], it.suggest);
        else toast("There's no folder to suggest for it: E picks one.");
      }
      else if (k === "e") { kept.focusAt = i; fileDialog([it.key]); }
      else if (k === "d") { kept.focusAt = i; dismiss([it.key]); }
      else if (k === "x") { kept.focusAt = i; if (sel.has(it.key)) sel.delete(it.key); else sel.add(it.key); draw(); }
      else if (e.key === " " || e.key === "Enter") preview({ name: it.name, kind: it.kind, src: pic("file", it) });
      else return;
      e.preventDefault();
      e.stopPropagation();
    });
    return el;
  };
  const draw = () => {
    const folders = data.folders.filter((f) => f.ok);
    const keys = [...sel];
    const bar = keys.length ? h("div.dl-bar", {},
      h("span.sel-n", {}, `${keys.length.toLocaleString()} selected`),
      h("button.btn.primary", { type: "button", onclick: () => fileDialog(keys) }, icon("move"), "File…"),
      h("button.btn", { type: "button", onclick: () => dismiss(keys) }, "Dismiss"),
      h("button.btn.danger", { type: "button", onclick: () => recycle(keys) }, icon("trash"), "Delete the downloads"),
      h("button.icon-btn", { type: "button", "aria-label": "Clear the selection", onclick: () => { sel.clear(); draw(); } }, icon("remove"))) : null;
    root.replaceChildren(...[
      h("header.st-head", {}, h("div", {}, h("h1", {}, "New downloads"),
        h("p.st-none", {}, folders.length ? `In ${folders.map((f) => baseName(f.path)).join(", ")}` : "No downloads folder set: Settings, New downloads."),
        data.items.length ? h("p.dl-keys", {}, h("kbd", {}, "← → ↑ ↓"), " move ", h("kbd", {}, "F"), " file (or delete one the library has) ", h("kbd", {}, "E"), " elsewhere ",
          h("kbd", {}, "D"), " dismiss ", h("kbd", {}, "X"), " select ", h("kbd", {}, "Space"), " look closer") : null),
        h("div.row", {},
          h("button.btn", { type: "button", title: "Look in the folders again", onclick: async (e) => {
            e.currentTarget.disabled = true;
            await load();  // (the folders are read afresh each time)
            ctx.counts();
            toast(data ? (data.total ? `${plural(data.total, "download")} to file.` : "Nothing new to file.") : "Couldn't look just now.");
          } }, icon("rescan"), "Refresh"),
          data.items.length ? h("button.btn", { type: "button", onclick: () => { data.items.forEach((it) => sel.add(it.key)); draw(); } }, `Select all ${data.items.length.toLocaleString()}`) : null,
          h("button.btn", { type: "button", onclick: () => ctx.show("settings") }, "Folders…"))),
      bar,
      data.items.length ? grid(data.items.map(card))
        : h("div.c-empty", {}, emptyArt("save"), h("h2", {}, "Nothing new"),
          h("p", {}, "Pictures and videos you download show up here, with who made them and where they're from when that's known, to file into the library in one click."),
          h("button.link", { type: "button", onclick: async () => { const r = await api("/inbox/undismiss", { method: "POST" }).catch(() => ({ back: 0 })); toast(r.back ? `${plural(r.back, "dismissed download")} offered again.` : "None were dismissed."); ctx.counts(); load(); } }, "Offer the dismissed ones again")),
      data.total > data.items.length ? h("div.row.center", {}, h("button.btn", { type: "button", onclick: () => { kept.shown += PAGE * 2; load(); } }, `Show more (${(data.total - data.items.length).toLocaleString()} more)`)) : null,
    ].filter(Boolean));
    const cards = root.querySelectorAll(".dl-card");  // back on the card you were on (or the one that took its place)
    if (kept.focusAt >= 0 && cards.length) cards[Math.min(kept.focusAt, cards.length - 1)].focus({ preventScroll: false });
  };
  await load();
}
