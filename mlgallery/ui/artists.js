// Model Librarian Gallery: artists - the list, one artist's page and the editor. An artist is found in the pictures
// (the file, its name, where it was downloaded from, an Artists\<name> folder) or typed by you; another name of theirs
// (a handle on another site) makes two into one.

import { api, ask, emptyArt, h, icon, initial, media, menu, modal, plural, stacks, toast } from "./util.js";
import { preview } from "./characters.js";
import { done } from "./organize.js";

function thumb(id, alt = "") {
  const none = () => h("div.c-thumb.none", { "aria-hidden": "true" }, h("span.c-initial", {}, initial(alt)));
  if (!id) return none();
  const img = h("img", { src: media("thumb", id), alt, loading: "lazy", decoding: "async" });
  img.onerror = () => img.replaceWith(none());
  return h("div.c-thumb", {}, img);
}

// a page of theirs (or a picture's source), in your web browser
export async function openLink(url) {
  try { await api("/open-url", { method: "POST", body: { url } }); } catch (e) { toast(e.message, true); }
}

const host = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } };

// ---------------------------------------------------------------- the list
export async function renderArtists(root, ctx) {
  root.setAttribute("aria-busy", "true");
  let data;
  try { data = await api("/artists"); } catch (e) { toast(e.message, true); return; } finally { root.removeAttribute("aria-busy"); }
  const find = h("input.a-find", { type: "search", placeholder: "Find an artist", "aria-label": "Find an artist", autocomplete: "off", spellcheck: false });
  const grid = h("div.c-grid");
  const show = () => {
    const q = find.value.trim().toLowerCase();
    const list = data.artists.filter((a) => !q || a.name.toLowerCase().includes(q) || a.aliases.some((x) => x.toLowerCase().includes(q)));
    grid.replaceChildren(...list.map((a) => h("button.c-card", { type: "button", onclick: () => ctx.show("artist", a.id),
      oncontextmenu: (e) => { e.preventDefault(); artistMenu(e, a, data.artists, ctx, () => renderArtists(root, ctx)); } },
      thumb(a.cover, a.name),
      h("span.c-name", {}, a.name),
      h("span.c-n", {}, plural(a.n, "picture")),
      a.sites.length ? h("span.a-sites", {}, a.sites.join(" · ")) : null)));
  };
  find.addEventListener("input", show);
  find.addEventListener("keydown", (e) => e.stopPropagation());
  grid.addEventListener("keydown", (e) => {  // Shift+F10 on a card: its menu, as the menu key opens it
    if (e.shiftKey && e.key === "F10" && e.target.closest(".c-card")) { e.preventDefault(); e.target.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true })); }
  });
  show();
  root.replaceChildren(...[
    h("header.st-head", {}, h("h1", {}, "Artists"),
      h("div.row", {}, data.artists.length > 12 ? find : null,
        data.artists.length ? h("button.btn.primary", { type: "button", onclick: () => editArtist(null, ctx) }, icon("plus"), "New artist") : null)),  // (none yet: the empty screen's button is the one)
    data.artists.length ? grid
      : h("div.c-empty", {}, emptyArt("brush"), h("h2", {}, "No artists yet"),
        h("p", {}, "Who made a picture is found in it as it's indexed: in the file's own details, in the name a site gives its downloads (Pixiv, Danbooru, DeviantArt and more), in where it was downloaded from, or in a folder called Artists with one folder per artist inside. You can also type it in a picture's details, or for many pictures at once."),
        h("button.btn.primary", { type: "button", onclick: () => editArtist(null, ctx) }, "New artist")),
  ].filter(Boolean));
}

// right-click on an artist in the list (or the menu key, Shift+F10): what can be done with them from here
function artistMenu(e, a, all, ctx, redraw) {
  const at = e.clientX || e.clientY ? { x: e.clientX, y: e.clientY } : null;  // (from the keyboard: under the card)
  menu(e.currentTarget, [
    { label: "Edit…", icon: "pen", onclick: () => editArtist(a, ctx, {}, redraw) },
    a.n ? { label: "Show their pictures", icon: "grid", onclick: () => ctx.open([{ key: "artist", value: a.name }]) } : false,
    ...a.links.map((u) => ({ label: `Open ${host(u)}`, icon: "send", onclick: () => openLink(u) })),
    all.length > 1 ? { label: "Merge into…", icon: "people", onclick: () => mergeArtist(a, all, redraw) } : false,
    null,
    { label: "Delete", icon: "trash", danger: true, onclick: () => deleteArtist(a) },
  ].filter((x) => x !== false), { at });
}

// one artist into another: their names become the other's other names, and their pictures and pages go with them
async function mergeArtist(a, all, redraw) {
  const others = all.filter((x) => x.id !== a.id);
  const list = h("datalist", { id: "artist-merge" }, others.map((x) => h("option", { value: x.name })));
  const input = h("input", { type: "text", placeholder: "Another artist", "aria-label": "Merge into", autocomplete: "off", spellcheck: false });
  input.setAttribute("list", list.id);
  const into = await modal(`Merge ${a.name} into…`, h("div", {},
    h("label.field-row", {}, h("span", {}, "Into"), input, list),
    h("p.st-none", {}, `${[a.name, ...a.aliases].join(", ")} become their other names; ${a.n ? `the ${plural(a.n, "picture")} and ` : ""}the pages go with them.`)), {
    ok: "Merge", onok: async () => {
      const want = input.value.trim().toLowerCase();
      const b = others.find((x) => x.name.toLowerCase() === want) || others.find((x) => x.aliases.some((n) => n.toLowerCase() === want));
      if (!b) throw new Error(want ? "That isn't another artist on the list." : "Which artist?");
      return api(`/artists/${b.id}`, { method: "POST", body: { aliases: [...b.aliases, a.name, ...a.aliases] } });
    } });
  if (!into) return;
  toast(`${a.name} is ${into.name} now.`);
  redraw();
}

// an artist gone: their pictures say No artist (the files stay), one History step to undo
export async function deleteArtist(a) {
  if (!await ask(`Delete ${a.name}?`, `${a.n ? `Their ${plural(a.n, "picture")} will have no artist, even a name found in them; the files stay where they are.` : "The files stay where they are."} Undo, or History, brings ${a.name} back.`,
    { ok: "Delete", danger: true })) return false;
  try {
    done(await api(`/artists/${a.id}`, { method: "DELETE" }), `Deleted the artist ${a.name}: the files stay where they are.`);
    return true;
  } catch (e) { toast(e.message, true); return false; }
}

// ---------------------------------------------------------------- one artist
export async function renderArtist(root, id, ctx) {
  root.setAttribute("aria-busy", "true");
  let a, recent;
  try {
    a = await api(`/artists/${id}`);
    recent = await api(`/files?limit=24&q=${encodeURIComponent(`artist:"${a.name.replaceAll('"', "")}"`)}`);
  } catch (e) {
    toast(e.message, true);
    ctx.show("artists");
    return;
  } finally { root.removeAttribute("aria-busy"); }
  const filter = (more = []) => ctx.open([{ key: "artist", value: a.name }, ...more]);
  const dates = a.first ? [...new Set([a.first, a.last].map((t) => new Date(t * 1000).toLocaleDateString()))].join(" – ") : "";
  const max = Math.max(1, ...a.sites.map((s) => s.n));
  root.replaceChildren(...[
    h("div.c-crumb", {}, h("button.link", { type: "button", onclick: () => ctx.show("artists") }, "Artists"), " / ", a.name),
    h("header.c-head", {},
      h("div.c-portrait", {}, thumb(a.cover, a.name)),
      h("div.c-title", {},
        h("h1", {}, a.name),
        a.aliases.length ? h("p.c-aliases", {}, `Also: ${a.aliases.join(", ")}`) : null,
        h("p.c-facts", {}, [plural(a.n, "picture"), dates].filter(Boolean).join(" · ")),
        a.links.length ? h("div.a-links", {}, a.links.map((u) => h("button.link", { type: "button", title: u, onclick: () => openLink(u) }, icon("send"), host(u)))) : null,
        h("div.row", {},
          h("button.btn.primary", { type: "button", onclick: () => filter(), disabled: !a.n }, icon("grid"), "Show all pictures"),
          h("button.btn", { type: "button", onclick: () => editArtist(a, ctx) }, "Edit"),
          h("button.btn.danger.c-del", { type: "button", onclick: async () => { if (await deleteArtist(a)) ctx.show("artists"); } }, "Delete")))),
    stacks([
      h("section.st-card", { "aria-label": "Where they're from" },
        h("div.st-card-head", {}, h("h2", {}, "Where they're from"), h("span", {}, "The sites their pictures came from")),
        a.sites.length ? h("ol.bars", {}, a.sites.map((s) => h("li", {}, h("button.bar-row", { type: "button", onclick: () => filter([{ key: "site", value: s.site }]) },
          h("span.bar-label", {}, s.name), h("span.bar-n", {}, s.n.toLocaleString()),
          h("span.bar-track", { "aria-hidden": "true" }, h("span.bar-fill", { style: { width: `${(s.n / max) * 100}%` } }))))))
          : h("p.st-none", {}, "No site known for their pictures.")),
      h("section.st-card", { "aria-label": "Where they're kept" },
        h("div.st-card-head", {}, h("h2", {}, "Where they're kept"), h("span", {}, "New downloads of theirs are filed in the first")),
        a.folders.length ? h("ol.a-folders", {}, a.folders.map((f) => h("li", {}, h("span.a-path", { title: f.path }, f.path), h("span.bar-n", {}, f.n.toLocaleString()))))
          : h("p.st-none", {}, "None of their pictures is in a library folder that's reachable now.")),
    ]),
    recent.items.length ? h("section.st-card", { "aria-label": "Latest" },
      h("div.st-card-head", {}, h("h2", {}, "Latest"), h("button.link", { type: "button", onclick: () => filter() }, `All ${a.n.toLocaleString()}`)),
      h("div.c-files", {}, recent.items.map((f) => h("button.c-file", { type: "button", title: f.name, onclick: () => preview(f),
        oncontextmenu: async (e) => {
          e.preventDefault();
          try {
            await api(`/artists/${a.id}`, { method: "POST", body: { cover: f.id } });
            toast(`That's ${a.name}'s picture on the list now.`);
            renderArtist(root, id, ctx);
          } catch (ex) { toast(ex.message, true); }
        } }, thumb(f.id, f.name)))),
      h("p.st-none", {}, "Right-click a picture to show it for them on the list.")) : null,
  ].filter(Boolean));
}

// ---------------------------------------------------------------- the editor
export function editArtist(a, ctx, preset = {}, saved = null) {  // (saved: what follows, else their page)
  const isNew = !a;
  const name = h("input", { type: "text", value: a ? a.name : preset.name || "", placeholder: "Their name", "aria-label": "Name", required: true });
  const aliases = h("input", { type: "text", value: a ? a.aliases.join(", ") : "", placeholder: "Their handles elsewhere, separated by commas", "aria-label": "Also known as" });
  const links = h("textarea", { rows: 3, placeholder: "https://www.pixiv.net/users/12345\nhttps://x.com/their_handle", "aria-label": "Their pages", spellcheck: false },
    a ? a.links.join("\n") : "");
  const err = h("p.form-err", { role: "alert" });
  const dlg = h("dialog.dialog", { "aria-label": isNew ? "New artist" : `Edit ${a.name}` },
    h("form", { method: "dialog", onsubmit: async (e) => {
      e.preventDefault();
      const body = { name: name.value.trim(), aliases: aliases.value.split(",").map((s) => s.trim()).filter(Boolean),
        links: links.value.split(/\s+/).map((s) => s.trim()).filter(Boolean) };
      try {
        const got = await api(isNew ? "/artists" : `/artists/${a.id}`, { method: "POST", body });
        dlg.close();
        saved ? saved(got) : ctx.show("artist", got.id);
      } catch (ex) { err.textContent = ex.message; }
    } },
      h("h2", {}, isNew ? "New artist" : `Edit ${a.name}`),
      h("label.field-row", {}, h("span", {}, "Name"), name),
      h("label.field-row", {}, h("span", {}, "Also known as"), aliases),
      h("p.st-none", {}, "Another name that's already an artist here makes the two one artist, with all their pictures."),
      h("label.field-row.top", {}, h("span", {}, "Their pages"), links),
      err,
      h("div.row.end", {}, h("button.btn", { type: "button", onclick: () => dlg.close() }, "Cancel"), h("button.btn.primary", { type: "submit" }, isNew ? "Create" : "Save"))));
  dlg.addEventListener("keydown", (e) => { if (e.key !== "Escape") e.stopPropagation(); });
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  name.focus();
}

// ---------------------------------------------------------------- in a picture's details
const FROM = { user: "typed by you", note: "from the page it was saved from", file: "from the file", download: "from where it was downloaded", name: "from its name", folder: "from its folder" };

// the artist and the source of one file, with where each came from; yours to change
export function creditEditor(info, { onFilter, onChanged }) {
  const wrap = h("div.credit");
  const render = (cr) => {
    const list = h("datalist", { id: "artist-sugg" });
    const input = h("input.credit-in", { type: "text", placeholder: cr.artist ? "Another artist" : "Who made it?", "aria-label": "Artist", autocomplete: "off", spellcheck: false });
    input.setAttribute("list", list.id);
    input.addEventListener("input", async () => {
      const { items } = await api(`/suggest?field=artist&q=${encodeURIComponent(input.value)}`).catch(() => ({ items: [] }));
      list.replaceChildren(...items.map((it) => h("option", { value: it.name })));
    });
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter" && input.value.trim()) { e.preventDefault(); save({ artist: input.value.trim() }); }
      if (e.key === "Escape") input.blur();
    });
    const src = h("input.credit-in", { type: "url", placeholder: "Its page, like https://www.pixiv.net/artworks/12345", "aria-label": "Source", spellcheck: false, hidden: true });
    src.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); save({ url: src.value.trim() || "" }); }
      if (e.key === "Escape") src.hidden = true;
    });
    const typed = cr.found.find((r) => r.src === "user");
    const norm = (n) => n.toLowerCase().replace(/[_\-.]+/g, " ").replace(/\s+/g, " ").trim();  // (mika_art is Mika Art, as the Gallery matches names)
    const others = cr.found.filter((r) => r.src !== "user" && r.artist && (!cr.artist || ![cr.artist.name, ...(cr.artist.aliases || [])].some((n) => norm(n) === norm(r.artist))));
    wrap.replaceChildren(...[
      h("div.credit-row", {},
        cr.artist ? h("span.chip.tag", { title: FROM[cr.artist_from] || "" }, h("button.link", { type: "button", onclick: () => onFilter("artist", cr.artist.name) }, cr.artist.name),
          h("button.x", { type: "button", "aria-label": `${cr.artist.name} didn't make it`, title: "Not them: no artist", onclick: () => save({ artist: "" }) }, icon("remove"))) : null,
        cr.artist ? h("span.credit-from", {}, FROM[cr.artist_from] || "") : typed && typed.artist === "" ? h("span.credit-from", {}, "No artist (you said)") : null),
      h("div.credit-row", {}, input, list),
      others.length ? h("p.credit-also", {}, "Also found: ", ...others.map((r, i) => [i ? ", " : "", h("button.link", { type: "button", title: `Use ${r.artist} (${FROM[r.src]})`, onclick: () => save({ artist: r.artist }) }, r.artist)])) : null,
      cr.url || cr.site ? h("div.credit-row", {},
        h("span.k", {}, "Source"),
        cr.url ? h("button.link", { type: "button", title: cr.url, onclick: () => openLink(cr.url) }, cr.site_name || host(cr.url)) : h("button.link", { type: "button", onclick: () => onFilter("site", cr.site) }, cr.site_name),
        cr.site && cr.url ? h("button.icon-btn.small", { type: "button", "aria-label": `Everything from ${cr.site_name}`, title: `Everything from ${cr.site_name}`, onclick: () => onFilter("site", cr.site) }, icon("filter")) : null,
        h("span.credit-from", {}, FROM[cr.source_from] || "")) : null,
      h("div.row.tight", {},
        h("button.link.dim", { type: "button", onclick: () => { src.hidden = false; src.value = cr.url || ""; src.focus(); } }, cr.url ? "Change the source" : "Add a source"),
        typed ? h("button.link.dim", { type: "button", title: "Forget what you typed: what was found in it counts again", onclick: () => save({ artist: null, url: null }) }, "Use what was found") : null),
      src,
    ].filter(Boolean));
  };
  const save = async (body) => {
    try {
      render(await api(`/file/${info.id}/credit`, { method: "POST", body }));
      onChanged?.();
    } catch (e) { toast(e.message, true); }
  };
  render(info.credit);
  return wrap;
}
