// Model Librarian Gallery: Settings - folders, thumbnails, videos, Model Librarian, ComfyUI, the data folder.

import { api, baseName, fmtDate, h, plural, toast } from "./util.js";

export async function renderSettings(root, ctx) {
  let s, folders;
  try {
    [s, { folders }] = await Promise.all([api("/settings"), api("/folders")]);
  } catch (e) { toast(e.message, true); return; }
  const save = async (patch, what) => {
    try {
      await api("/settings", { method: "POST", body: patch });
      toast(`${what} saved.`);
      renderSettings(root, ctx);
    } catch (e) { toast(e.message, true); }
  };
  const field = (label, control, note) => h("div.set-row", {}, h("label.set-label", {}, label), h("div.set-control", {}, control, note ? h("p.note", {}, note) : null));
  const text = (key, placeholder, what) => {
    const input = h("input", { type: "text", value: s.settings[key] || "", placeholder, spellcheck: false, "aria-label": what });
    input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") save({ [key]: input.value.trim() }, what); });
    return h("div.inline", {}, input, h("button.btn", { type: "button", onclick: () => save({ [key]: input.value.trim() }, what) }, "Save"));
  };
  const thumbSize = h("select", { "aria-label": "Thumbnail size", onchange: (e) => save({ thumb_size: Number(e.target.value) }, "Thumbnail size") },
    [[256, "256 px (quick, small)"], [384, "384 px"], [512, "512 px (sharp on big screens and large tiles)"]].map(([v, l]) => h("option", { value: v, selected: s.settings.thumb_size === v }, l)));
  const eagle = h("input", { type: "checkbox", checked: s.settings.skip_eagle, onchange: (e) => save({ skip_eagle: e.target.checked }, "Eagle setting"), id: "set-eagle" });
  const comfyNote = h("p.note", {}, "Open in ComfyUI sends a file's workflow there; Model Librarian (1.23 or newer) opens it in a new tab.");
  root.replaceChildren(
    h("header.st-head", {}, h("h1", {}, "Settings"), h("span.st-none", {}, `Model Librarian Gallery ${s.version}`)),
    h("section.st-card.set", { "aria-label": "Folders" },
      h("div.st-card-head", {}, h("h2", {}, "Folders"), h("button.btn.small", { type: "button", onclick: ctx.addFolder }, "Add a folder")),
      folders.length ? h("ol.set-folders", {}, folders.map((f) => h("li", {},
        h("span.sf-name", {}, baseName(f.path)), h("code", {}, f.path),
        h("span.st-none", {}, f.online ? `${plural(f.file_count, "file")}${f.last_scan ? ` · looked through ${fmtDate(f.last_scan)}` : ""}` : "not reachable right now"),
        h("div.row", {}, h("button.btn.small", { type: "button", onclick: () => ctx.rescan(f) }, "Look again"),
          h("button.btn.small", { type: "button", onclick: () => ctx.removeFolder(f) }, "Remove"))))) : h("p.st-none", {}, "No folders yet."),
      field("Eagle libraries", h("label.check", { for: "set-eagle" }, eagle, " Leave them out (recommended)"), "Eagle manages the files in its *.library folders; the Gallery doesn't index or touch them.")),
    h("section.st-card.set", { "aria-label": "Thumbnails and videos" },
      h("div.st-card-head", {}, h("h2", {}, "Thumbnails and videos")),
      field("Thumbnail size", thumbSize, "A bigger size is made in the background as tiles need it; the smaller ones stay in the cache."),
      field("ffmpeg", text("ffmpeg", "Empty: found on PATH", "ffmpeg path"),
        s.ffmpeg_found ? `Using ${s.ffmpeg_found} for video posters and lengths.` : "Not found: videos get no poster or length until it's installed (or its path is set here).")),
    h("section.st-card.set", { "aria-label": "Model Librarian and ComfyUI" },
      h("div.st-card-head", {}, h("h2", {}, "Model Librarian and ComfyUI")),
      field("Model Librarian's data", text("ml_data", "Empty: found by itself", "Model Librarian data folder"),
        s.ml_data_found ? `Using ${s.ml_data_found}: its library shows the models you never use, and Save as recipe puts recipes there.`
          : "Not found. It's the .sm_model_fetcher folder inside your models folder."),
      field("ComfyUI's address", h("div", {}, text("comfy_url", "http://127.0.0.1:8188", "ComfyUI address")), null), comfyNote),
    h("section.st-card.set", { "aria-label": "The Gallery's data" },
      h("div.st-card-head", {}, h("h2", {}, "The Gallery's data")),
      field("Data folder", h("div.row", {}, h("code", {}, s.data_dir), h("button.btn.small", { type: "button", onclick: () => api("/open-data", { method: "POST" }).catch((e) => toast(e.message, true)) }, "Open")),
        "The index, thumbnails, settings and backups. Your media is never written to, except when you move or delete files here."),
      field("Backups", h("div.row", {}, h("span", {}, s.last_backup ? `Last: ${s.last_backup.replace(/^library-|\.db$/g, "")}` : "None yet"),
        h("button.btn.small", { type: "button", onclick: async () => { await api("/backup", { method: "POST" }); toast("Backed up."); renderSettings(root, ctx); } }, "Back up now")),
        "A copy of the index each day; the last 7 are kept."),
      s.shortcut === null ? null : field("Start menu", h("div.row", {},
        s.shortcut ? h("span", {}, "The Gallery is in the Start menu.") : null,
        h("button.btn.small", { type: "button", onclick: async () => {
          try { await api("/shortcut", { method: "POST" }); toast("Added to the Start menu."); renderSettings(root, ctx); } catch (e) { toast(e.message, true); }
        } }, s.shortcut ? "Make it again" : "Add the Gallery to the Start menu")), "Starts it in its own window, with its icon.")),
  );
}
