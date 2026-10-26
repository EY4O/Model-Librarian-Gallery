// Model Librarian Gallery: Settings - the library, folders, phones and tablets, thumbnails, videos, Model Librarian, ComfyUI, the data folder, updates.

import { KINDS, Pace, api, ask, askText, baseName, fmtDate, fmtLeft, fmtSize, h, job, lib, plural, progressBar, toast } from "./util.js";
import { dialog, done } from "./organize.js";
import { qrSvg } from "./qr.js";

const field = (label, control, note) => h("div.set-row", {}, h("label.set-label", {}, label), h("div.set-control", {}, control, note ? h("p.note", {}, note) : null));

export async function renderSettings(root, ctx) {
  let s, folders, lb, inbox, remote;
  try {
    [s, { folders }, lb, { folders: inbox }, remote] = await Promise.all([api("/settings"), api("/folders"), api("/libraries"), api("/inbox/folders"), api("/remote")]);
  } catch (e) { toast(e.message, true); return; }
  const save = async (patch, what) => {
    try {
      await api("/settings", { method: "POST", body: patch });
      toast(`${what} saved.`);
      renderSettings(root, ctx);
    } catch (e) { toast(e.message, true); }
  };
  const text = (key, placeholder, what) => {
    const input = h("input", { type: "text", value: s.settings[key] || "", placeholder, spellcheck: false, "aria-label": what });
    input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") save({ [key]: input.value.trim() }, what); });
    return h("div.inline", {}, input, h("button.btn", { type: "button", onclick: () => save({ [key]: input.value.trim() }, what) }, "Save"));
  };
  const secret = (key, what) => {
    const input = h("input", { type: "password", value: s.settings[key] || "", placeholder: "None", autocomplete: "off", "aria-label": what });
    input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") save({ [key]: input.value.trim() }, what); });
    return h("div.inline", {}, input, h("button.btn", { type: "button", onclick: () => save({ [key]: input.value.trim() }, what) }, "Save"));
  };
  const thumbSize = h("select", { "aria-label": "Thumbnail size", onchange: (e) => save({ thumb_size: Number(e.target.value) }, "Thumbnail size") },
    [[256, "256 px (quick, small)"], [384, "384 px"], [512, "512 px (sharp on big screens and large tiles)"]].map(([v, l]) => h("option", { value: v, selected: s.settings.thumb_size === v }, l)));
  const eagle = h("input", { type: "checkbox", checked: s.settings.skip_eagle, onchange: (e) => save({ skip_eagle: e.target.checked }, "Eagle setting"), id: "set-eagle" });
  const comfyNote = h("p.note", {}, "Open in ComfyUI sends a file's workflow there; Model Librarian (1.23 or newer) opens it in a new tab.");
  root.replaceChildren(
    h("header.st-head", {}, h("h1", {}, "Settings"), h("span.st-none", {}, `Model Librarian Gallery ${s.version}`)),
    libraries(lb, () => renderSettings(root, ctx), ctx),
    h("section.st-card.set", { "aria-label": "Folders" },
      h("div.st-card-head", {}, h("h2", {}, "Folders"), h("button.btn.small", { type: "button", onclick: ctx.addFolder }, "Add a folder")),
      folders.length ? h("ol.set-folders", {}, folders.map((f) => h("li", {},
        h("span.sf-name", {}, baseName(f.path)), h("code", {}, f.path),
        h("span.st-none", {}, f.online ? `${plural(f.file_count, "file")}${f.last_scan ? ` · looked through ${fmtDate(f.last_scan)}` : ""}` : "not reachable right now"),
        h("div.row", {}, h("button.btn.small", { type: "button", onclick: () => ctx.rescan(f) }, "Look again"),
          h("button.btn.small", { type: "button", onclick: () => ctx.removeFolder(f) }, "Remove"))))) : h("p.st-none", {}, "No folders yet."),
      field("Eagle libraries", h("label.check", { for: "set-eagle" }, eagle, " Leave them out (recommended)"), "Eagle manages the files in its *.library folders; the Gallery doesn't index or touch them.")),
    (lib.kinds || []).includes("illustrations") ? downloads(s, inbox, () => renderSettings(root, ctx)) : null,
    phones(remote, () => renderSettings(root, ctx)),
    background(s, save),
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
    h("section.st-card.set", { "aria-label": "Eagle" },
      h("div.st-card-head", {}, h("h2", {}, "Eagle"), h("button.btn.small", { type: "button", onclick: () => eagleImport(folders, lb) }, "Bring in an Eagle library…")),
      h("p.note.set-lede", {}, "Bringing in a library only reads it. Its files are copied out, laid out by Eagle folder, into a new library of their own or into this one, with their tags, folders (as albums), ratings, notes, source links and colours; a file this library already has just gets the marks. Send to Eagle (in the selection's More menu) adds files to Eagle through Eagle itself."),
      field("Eagle's address", text("eagle_url", "http://localhost:41595", "Eagle address"), "Eagle has to be running for Send to Eagle."),
      field("API token", secret("eagle_token", "Eagle API token"), "Only if your Eagle asks for one (Eagle: Preferences, Developer). Kept in the Gallery's settings on this PC.")),
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
    updates(s, () => renderSettings(root, ctx), save),
  );
}

// ---------------------------------------------------------------- New downloads: the folders whose pictures are offered for filing
function downloads(s, inbox, again) {
  const set = async (body, what) => {
    try {
      await api("/inbox/folders", { method: "POST", body });
      if (what) toast(what);
      again();
    } catch (e) { toast(e.message, true); }
  };
  const dl = h("input", { type: "checkbox", checked: s.settings.inbox_downloads, id: "set-inbox-dl",
    onchange: (e) => set({ downloads: e.target.checked }, e.target.checked ? "Your Downloads folder is offered again." : "Your Downloads folder isn't offered any more.") });
  const others = inbox.filter((f) => !f.downloads);
  return h("section.st-card.set", { "aria-label": "New downloads" },
    h("div.st-card-head", {}, h("h2", {}, "New downloads"),
      h("button.btn.small", { type: "button", onclick: async () => { const p = await pickFolder("Another folder for new downloads", "Add"); if (p) set({ add: p }, `${baseName(p)} added.`); } }, "Add a folder")),
    h("p.note.set-lede", {}, "The pictures and videos in these folders are offered for filing (New downloads, in the sidebar): moved into the library with who made them and where they're from. Only the folders themselves are looked in, not the folders inside them."),
    field("Downloads", h("label.check", { for: "set-inbox-dl" }, dl, " Windows' Downloads folder"),
      inbox.find((f) => f.downloads)?.path || (s.settings.inbox_downloads ? "Not found on this PC." : null)),
    others.length ? h("ol.set-folders", {}, others.map((f) => h("li", {},
      h("span.sf-name", {}, baseName(f.path)), h("code", {}, f.path),
      f.ok ? null : h("span.st-none", {}, f.why),
      h("div.row", {}, h("button.btn.small", { type: "button", onclick: () => set({ remove: f.path }, `${baseName(f.path)} isn't offered any more.`) }, "Remove"))))) : null,
    inbox.some((f) => f.downloads && !f.ok) ? h("p.note", {}, inbox.find((f) => f.downloads).why) : null,
    field("Notifications", h("label.check", { for: "set-inbox-notify" },
      h("input", { type: "checkbox", id: "set-inbox-notify", checked: s.settings.inbox_notify,
        onchange: (e) => set({ notify: e.target.checked }, e.target.checked ? "You'll get a Windows notification when new pictures arrive." : "No more notifications for new downloads.") }),
      " Tell me when new pictures arrive"), "A Windows notification, while the Gallery is open. Under the Gallery's name once it's in the Start menu (Settings, The Gallery's data)."),
    field("Dismissed", h("button.btn.small", { type: "button", onclick: async () => {
      const r = await api("/inbox/undismiss", { method: "POST" }).catch((e) => { toast(e.message, true); return null; });
      if (r) toast(r.back ? `${plural(r.back, "dismissed download")} offered again.` : "None were dismissed.");
    } }, "Offer them again"), "Downloads you dismissed aren't offered again, unless they change."));
}

// ---------------------------------------------------------------- Phone and tablet: paired devices, on the home network or Tailscale
function phones(r, again) {
  const set = async (path, body, what) => {
    try {
      await api(path, { method: "POST", body });
      if (what) toast(what);
    } catch (e) { toast(e.message, true); }
    again();
  };
  const on = h("input", { type: "checkbox", id: "set-remote", checked: r.on,
    onchange: (e) => set("/remote", { on: e.target.checked }, e.target.checked ? "Phones and tablets you pair can open the library now." : "Phones and tablets can't open the library any more.") });
  const port = h("input", { type: "number", min: 1024, max: 65535, value: r.port, "aria-label": "Port" });
  const savePort = () => set("/remote", { port: Number(port.value) }, "Port saved.");
  port.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") savePort(); });
  const where = r.addresses.length ? h("ul.set-addrs", {}, r.addresses.map((a) => h("li", {}, h("code", {}, a.url), h("span.st-none", {}, KIND_NET[a.kind]))))
    : h("span.st-none", {}, "This PC has no home network or Tailscale address right now.");
  const devices = r.devices.length ? h("ol.set-folders.set-devices", {}, r.devices.map((d) => h("li", {},
    h("span.sf-name", {}, d.name),
    h("span.st-none", {}, `Paired ${fmtDate(d.added)}${d.seen ? ` · last here ${fmtDate(d.seen)}` : ""}`),
    h("div.row", {},
      h("button.btn.small", { type: "button", onclick: async () => {
        const name = await askText(`Rename ${d.name}`, "Name", d.name, { ok: "Rename" });
        if (name && name !== d.name) set("/remote/rename", { id: d.id, name });
      } }, "Rename"),
      h("button.btn.small", { type: "button", onclick: async () => {
        if (await ask(`Remove ${d.name}?`, "It can't open the library any more, unless you pair it again.", { ok: "Remove", danger: true })) set("/remote/forget", { id: d.id }, `${d.name} removed.`);
      } }, "Remove"))))) : h("p.st-none", {}, "None yet.");
  return h("section.st-card.set", { "aria-label": "Phone and tablet" },
    h("div.st-card-head", {}, h("h2", {}, "Phone and tablet"),
      r.on && r.running ? h("button.btn.small", { type: "button", onclick: () => pairDialog(new Set(r.devices.map((d) => d.id)), again) }, "Pair a device…") : null),
    h("p.note.set-lede", {}, "Look through the library on a phone or tablet: on your home Wi-Fi, or anywhere over Tailscale. It can rate, favourite, tag, put in albums, Swipe and delete to the Trash. Only devices you pair here are let in, and only from a private network, never the open internet. Settings, folders, moving and renaming files, Eagle and filing downloads stay on this PC."),
    field("Phones and tablets", h("label.check", { for: "set-remote" }, on, " Let paired devices open the library"),
      r.on && !r.running ? "On, but not listening: another program has the port. Pick another one below."
        : "The first time, Windows asks whether the Gallery may use the network: allow it on private networks."),
    field("Port", h("div.inline.set-port", {}, port, h("button.btn", { type: "button", onclick: savePort }, "Save")),
      "It stays the same, so paired devices keep their address. Change it only if another program uses it."),
    r.on ? field("Addresses", where, "Home Wi-Fi carries it unencrypted, like anything else on your network; over Tailscale it's encrypted.") : null,
    field("Paired devices", devices));
}

const KIND_NET = { home: "home network", tailscale: "Tailscale", other: "another network (a virtual one, often)" };

// the QR code to scan (and a code to type), good for 10 minutes and one device; closes once that device is paired
async function pairDialog(had, again) {
  let r;
  try { r = await api("/remote/code", { method: "POST" }); } catch (e) { toast(e.message, true); return; }
  if (!r.pair.length) { toast("This PC has no home network or Tailscale address to pair over right now.", true); return; }
  const code = `${r.code.slice(0, 4)}-${r.code.slice(4)}`;
  const qr = h("div.pair-qr"), url = h("code.pair-url"), typed = h("p.note");
  const show = (i) => {
    qr.innerHTML = qrSvg(r.pair[i], 5);  // (our own SVG of the address: no text from anywhere else)
    url.textContent = r.pair[i];
    typed.textContent = `Or open ${r.addresses[i].url} on it and type the code ${code}.`;
  };
  const pick = r.addresses.length > 1 ? h("select", { "aria-label": "Network", onchange: (e) => show(Number(e.target.value)) },
    r.addresses.map((a, i) => h("option", { value: i }, `${KIND_NET[a.kind]}: ${a.ip}`))) : null;
  show(0);
  const { dlg } = dialog("Pair a phone or tablet", h("div.pair", {},
    h("p", {}, `Scan this with the phone or tablet's camera and open the link: it's remembered from then on. The code works for ${r.minutes} minutes, for one device.`),
    pick ? h("label.field-row", {}, h("span", {}, "Over"), pick) : null, qr, url, typed), { submitLabel: "Done", submit: () => true });
  const watch = setInterval(async () => {  // paired: say so, and show it in the list
    if (!dlg.isConnected) return clearInterval(watch);
    const now = await api("/remote").catch(() => null);
    const added = now?.devices.find((d) => !had.has(d.id));
    if (added) {
      clearInterval(watch);
      dlg.close();
      toast(`${added.name} is paired.`);
      again();
    }
  }, 2000);
  dlg.addEventListener("close", () => { clearInterval(watch); again(); });
}

// ---------------------------------------------------------------- the tray: running with the window closed, and from sign-in
function background(s, save) {
  const tray = h("input", { type: "checkbox", id: "set-tray", checked: s.settings.tray, disabled: !s.tray_ok,
    onchange: (e) => save({ tray: e.target.checked }, "Tray setting") });
  const login = h("input", { type: "checkbox", id: "set-tray-login", checked: s.settings.tray_login, disabled: !s.tray_ok,
    onchange: (e) => save({ tray_login: e.target.checked }, "Sign-in setting") });
  return h("section.st-card.set", { "aria-label": "In the background" },
    h("div.st-card-head", {}, h("h2", {}, "In the background")),
    h("p.note.set-lede", {}, "In the tray, by the clock, the Gallery keeps working with its window closed: paired phones and tablets can open the library, new downloads are noticed (and told, if you like), and new files are indexed as they come. Click its icon to open the window; its menu has New downloads, Phone and tablet, Pause watching folders and Quit."),
    field("Closing the window", h("label.check", { for: "set-tray" }, tray, " Keep running in the tray"),
      s.tray_ok ? "Closing the window then only hides it: Quit from the tray icon's menu ends the Gallery." : "Only when the Gallery runs in its own window, on Windows."),
    field("When you sign in", h("label.check", { for: "set-tray-login" }, login, " Start the Gallery in the tray"),
      "With the library you opened last, its window closed until you open it. Turning this on keeps it running in the tray too."));
}

// ---------------------------------------------------------------- updates from GitHub Releases
function updates(s, again, save) {
  const u = s.update;
  const act = (path, label, busyText, okText) => {
    const b = h("button.btn.small", { type: "button", onclick: async () => {
      b.disabled = true;
      b.textContent = busyText;
      try {
        await api(path, { method: "POST" });
        if (okText) toast(okText);
        if (path !== "/update/restart") again();
      } catch (e) { toast(e.message, true); b.disabled = false; b.textContent = label; }
    } }, label);
    return b;
  };
  const on = h("input", { type: "checkbox", id: "set-updates", checked: s.settings.updates, onchange: (e) => save({ updates: e.target.checked }, "Update setting") });
  const v = u.newer && u.newer.version;
  let state;
  if (u.staged) {
    state = h("div.row", {}, h("span", {}, `${u.staged} is ready: it's used the next time the Gallery starts.`),
      u.can_restart ? act("/update/restart", "Restart now", "Restarting…", "Restarting with the new version…") : null);
  } else if (v) {
    state = h("div.row", {}, h("span", {}, `${v} is out (you have ${u.current}).`),
      u.portable ? act("/update/install", `Install ${v}`, "Downloading…", `${v} is ready: it's used the next time the Gallery starts.`) : null);
  } else {
    state = h("span", {}, u.checked && !u.error ? `${u.current} is the newest (looked ${fmtDate(u.checked)}).` : `${u.current}${u.checked ? "" : ", not checked yet"}.`);
  }
  const notes = v && !u.staged && u.newer.notes ? h("details.update-notes", {}, h("summary", {}, `What's new in ${v}`), h("pre", {}, u.newer.notes)) : null;
  const how = !u.portable ? "This Gallery runs from a checkout: bring it up to date with git pull, then start it again."
    : "Installing downloads the new version from GitHub (about 1 MB), checks it, and swaps it in when the Gallery next starts. A version that needs a full download says so.";
  return h("section.st-card.set", { "aria-label": "Updates" },
    h("div.st-card-head", {}, h("h2", {}, "Updates"), act("/update/check", "Check now", "Checking…")),
    field("Check for updates", h("label.check", { for: "set-updates" }, on, " Ask GitHub once a day whether there's a newer version"),
      "Only GitHub's list of releases is read: nothing about you or your library is sent."),
    field("This Gallery", h("div", {}, state, notes), u.error ? `The last check didn't work: ${u.error}` : how));
}

// ---------------------------------------------------------------- bring in an Eagle library
async function follow(title, note, line, stopping) {
  // a dialog that follows a job in the background (/eagle/read) until it's no longer running: a bar and the time left,
  // Stop, and Keep using the Gallery (it hides; the status bar carries on, and a click there opens it again)
  const status = h("p", { "aria-live": "polite" }, line({ done: 0 }));
  const bar = progressBar();
  bar.set(null);
  const left = h("p.p-left");
  const pace = new Pace();
  const stop = h("button.btn.danger", { type: "button", onclick: async () => {
    if (!await ask(stopping.title, stopping.text, { ok: "Stop", danger: true })) return;
    stop.disabled = true;
    await api("/eagle/stop", { method: "POST" }).catch((e) => toast(e.message, true));
  } }, "Stop");
  const hide = () => {
    if (!dlg.open) return;
    dlg.close();
    job.reopen = () => { job.reopen = null; dlg.showModal(); };
  };
  job.following = true;
  job.told = false;
  const dlg = h("dialog.dialog.follow", { "aria-label": title }, h("form", { method: "dialog", onsubmit: (e) => e.preventDefault() },
    h("h2", {}, title), status, bar, left, h("p.note", {}, note),
    h("div.row.end", {}, h("button.btn", { type: "button", onclick: hide }, "Keep using the Gallery"), stop)));
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); hide(); });  // (Esc: hidden, not stopped)
  dlg.addEventListener("keydown", (e) => { if (e.key !== "Escape" && e.key !== "Tab" && e.key !== "Enter" && e.key !== " ") e.preventDefault(); e.stopPropagation(); });
  document.body.append(dlg);
  dlg.showModal();
  let st;
  for (;;) {
    await new Promise((r) => setTimeout(r, 400));
    st = await api("/eagle/read").catch(() => ({ state: "failed", error: "The Gallery stopped answering." }));
    if (st.state !== "reading" && st.state !== "applying") break;
    status.textContent = st.stop ? "Stopping after the file in hand…" : line(st);
    const bytes = st.bytes_total > 0;  // copying: by the bytes, so a few big videos don't throw the time out
    const done = bytes ? st.bytes || 0 : st.done || 0, total = bytes ? st.bytes_total : st.total || 0;
    bar.set(total ? done / total : null);
    left.textContent = total && !st.stop ? fmtLeft(pace.update(done, total)) : "";
  }
  job.reopen = null;
  job.told = st.state === "done" || st.state === "failed";  // (told here: a poll before the server hears so doesn't tell it again)
  job.following = false;
  if (st.state === "done" || st.state === "failed") api("/eagle/seen", { method: "POST" }).catch(() => {});  // told here: not again by the status bar
  if (dlg.open) dlg.close();
  dlg.remove();
  return st;
}

async function eagleImport(folders, lb) {
  let path = "";
  try {
    path = (await api("/folders/pick", { method: "POST" })).path;
  } catch (e) {
    if (e.status !== 501) return toast(e.message, true);
    path = (await askText("Bring in an Eagle library", "Its folder", "", { ok: "Read it", placeholder: "D:\\Art.library" })) || "";
  }
  if (!path) return;
  try {
    await api("/eagle/read", { method: "POST", body: { path } });
  } catch (e) { return toast(e.message, true); }
  const st = await follow("Reading the Eagle library", "Only reading: nothing in it changes.",
    (x) => (x.total ? `Reading the library… ${(x.done || 0).toLocaleString()} of ${x.total.toLocaleString()} items` : `Reading the library… ${(x.done || 0).toLocaleString()} items so far`),
    { title: "Stop reading the library?", text: "Nothing has been brought in yet: reading it again starts from the beginning." });
  if (st.state === "stopped") return toast("Stopped reading the Eagle library: nothing was brought in.");
  if (st.state !== "ready") return toast(st.error || "Reading the library didn't finish.", true);
  const s = st.summary;
  const online = folders.filter((f) => f.online);
  // where it goes: a new library of its own (asked each time), or this library
  const radio = (v) => h("input", { type: "radio", name: "eagle-where", value: v, id: `eagle-${v}`, checked: v === "new", onchange: () => show() });
  const rNew = radio("new"), rHere = radio("here");
  let newPath = "";
  const chosen = h("code", {}, "No folder chosen yet");
  const name = h("input", { type: "text", value: s.name, "aria-label": "Name", spellcheck: false });
  const into = h("select", { "aria-label": "Copy into" }, online.map((f) => h("option", { value: f.id }, baseName(f.path))));
  const sub = h("input", { type: "text", value: `From Eagle - ${s.name}`, "aria-label": "Folder inside it", spellcheck: false });
  for (const i of [name, sub]) i.addEventListener("keydown", (e) => e.stopPropagation());
  const layout = `laid out by Eagle folder${s.unfiled ? ` (${plural(s.unfiled, "item")} in no folder go to Unfiled)` : ""}, under the names Eagle shows`;
  const newBox = h("div.eagle-opt", {},
    h("div.row", {}, h("button.btn.small", { type: "button", onclick: async () => {
      const p = await pickFolder("Folder for the new library", "Use it");
      if (p) { newPath = p; chosen.textContent = p; if (name.value === s.name) name.value = baseName(p); }
    } }, "Choose a folder…"), chosen),
    h("label.field-row", {}, h("span", {}, "Name"), name),
    h("p.note", {}, `All ${plural(s.all, "item")} (${fmtSize(s.all_size)}) are copied into it, ${layout}. Pick an empty folder, on an external drive for example.`));
  const hereBox = h("div.eagle-opt", {},
    h("p.note", {}, `${plural(s.matched, "item")} this library has already get Eagle's marks; no second copy is made.`),
    s.missing && online.length ? h("label.field-row", {}, h("span", {}, "Copy into"), into) : null,
    s.missing && online.length ? h("label.field-row", {}, h("span", {}, "Folder"), sub) : null,
    s.missing ? h("p.note", {}, online.length ? `${plural(s.missing, "item")} (${fmtSize(s.missing_size)}) it doesn't have are copied there, ${layout}.`
      : `${plural(s.missing, "item")} it doesn't have can't be copied: none of this library's folders is reachable.`) : null);
  const show = () => { newBox.hidden = !rNew.checked; hereBox.hidden = !rHere.checked; };
  show();
  const err = h("p.form-err", { role: "alert" });
  const go = h("button.btn.primary", { type: "submit" }, "Bring it in");
  const box = h("dialog.dialog", { "aria-label": `Bring in ${s.name}` }, h("form", { method: "dialog", onsubmit: async (e) => {
    e.preventDefault();
    if (rNew.checked && !newPath) { err.textContent = "Choose a folder for the new library."; return; }
    go.disabled = true;
    const body = rNew.checked ? { target: "new", path: newPath, name: name.value.trim() }
      : { target: "here", copy_to: s.missing && online.length ? Number(into.value) : null, sub: sub.value.trim() };
    try {
      await api("/eagle/apply", { method: "POST", body });
    } catch (ex) { err.textContent = ex.message; go.disabled = false; return; }
    box.close();
    const end = await follow(`Bringing in ${s.name}`, "Copied and checked one by one; Eagle's library isn't changed.",
      (x) => (x.total && x.done < x.total ? `Copying… ${(x.done || 0).toLocaleString()} of ${x.total.toLocaleString()}${x.bytes_total ? ` · ${fmtSize(x.bytes || 0)} of ${fmtSize(x.bytes_total)}` : ""}`
        : "Bringing in the tags, albums, ratings and notes…"),
      { title: `Stop bringing in ${s.name}?`, text: "What's copied so far stays, with its tags, albums, ratings and notes; the rest isn't copied. It's one step in History: Undo takes it all back." });
    if (end.state !== "done") return toast(end.error || "Bringing it in didn't finish.", true);
    const r = end.result;
    const marks = `${plural(r.tags, "tag")}, ${plural(r.albums, "album place")}, ${plural(r.ratings, "rating")} and ${plural(r.notes, "note")}`;
    const failed = (r.failed && r.failed.length ? ` ${plural(r.failed.length, "file")} couldn't be copied (${r.failed.slice(0, 3).join(", ")}${r.failed.length > 3 ? "…" : ""}).` : "")
      + (r.stopped ? ` Stopped: ${plural(r.stopped, "item")} not copied.` : "");
    if (r.library) {
      const msg = `${r.library.name}: ${plural(r.copied, "file")} copied from ${s.name}, with ${marks}.${failed}`;
      if (lb.can_switch && await ask(`${r.library.name} is ready`, `${msg} Open it now?`, { ok: "Open it" })) {
        api("/libraries/open", { method: "POST", body: { path: r.library.root } }).then(() => toast(`Opening ${r.library.name}…`)).catch((ex) => toast(ex.message, true));
      } else toast(msg);
      return;
    }
    done(r, `From ${s.name}: ${plural(r.files, "file")} got ${marks}${r.copied ? `; ${plural(r.copied, "file")} copied in` : ""}.${failed}`);
  } },
    h("h2", {}, `Bring in ${s.name}`),
    h("ul.plan-list", {},
      h("li", {}, `${plural(s.all, "item")}: ${plural(s.tags, "tag")}, ${plural(s.album_count, "folder")}${s.albums.length ? ` (${s.albums.slice(0, 6).join(", ")}${s.album_count > 6 ? "…" : ""})` : ""}, ${plural(s.rated, "rating")}, ${plural(s.notes, "note")}${s.urls ? `, ${plural(s.urls, "source link")}` : ""}.`),
      h("li", {}, "Folders become albums too; notes keep their source link on a line of their own; Eagle's colours come along.")),
    h("div.eagle-where", { role: "radiogroup", "aria-label": "Where it goes" },
      h("label.check", { for: "eagle-new" }, rNew, " Into a new library of its own"), newBox,
      h("label.check", { for: "eagle-here" }, rHere, ` Into this library${lb.current.root ? ` (${lb.current.name})` : ""}`), hereBox),
    h("p.note", {}, "Your own ratings and notes stay as they are. Eagle's library isn't changed: copied files are copies. It's one step in History: Undo takes the marks off again, and moves the copies to the Gallery's Trash."),
    err,
    h("div.row.end", {}, h("button.btn", { type: "button", onclick: () => box.close() }, "Cancel"), go)));
  box.addEventListener("keydown", (e) => { if (e.key !== "Escape") e.stopPropagation(); });
  box.addEventListener("close", () => box.remove());
  document.body.append(box);
  box.showModal();
}

// ---------------------------------------------------------------- libraries: a folder that carries its own index
async function pickFolder(title, ok) {
  try {
    return (await api("/folders/pick", { method: "POST" })).path || "";
  } catch (e) {
    if (e.status !== 501) { toast(e.message, true); return ""; }
    return (await askText(title, "Its folder", "", { ok, placeholder: "E:\\My Generations" })) || "";
  }
}

function libraries(lb, again, ctx) {
  const cur = lb.current;
  const open = async (body, what) => {
    try {
      await api("/libraries/open", { method: "POST", body });
      toast(`Opening ${what}…`);
    } catch (e) { toast(e.message, true); }
  };
  const btn = (label, onclick, disabled = false) => h("button.btn.small", { type: "button", disabled, onclick }, label);
  const openOther = async () => {
    const path = await pickFolder("Open a library", "Open");
    if (path) open({ path }, baseName(path));
  };
  const others = lb.recent.filter((r) => !r.open);
  const rows = [
    cur.root ? h("li", {}, h("span.sf-name", {}, "This PC's library"), h("span.st-none", {}, "Its index is kept on this PC"), h("span.st-none", {}, ""),
      h("div.row", {}, btn("Open", () => open({ local: true }, "this PC's library"), !lb.can_switch))) : null,
    ...others.map((r) => h("li", {}, h("span.sf-name", {}, r.name), h("code", {}, r.root),
      h("span.st-none", {}, r.reachable ? "" : "not reachable right now"),
      h("div.row", {}, btn("Open", () => open({ path: r.root }, r.name), !lb.can_switch || !r.reachable),
        btn("Forget", async () => { await api("/libraries/forget", { method: "POST", body: { path: r.root } }).catch((e) => toast(e.message, true)); again(); })))),
  ].filter(Boolean);
  const b = cur.brought;
  const brought = cur.bringing ? h("p.note", { role: "status" }, `Bringing ratings, tags, albums, characters and notes from ${cur.bringing} for the same files…`)
    : b && b.error ? h("p.note", {}, `Bringing marks from ${b.name} didn't work: ${b.error}`)
    : b ? h("p.note", {}, `From ${b.name}: ${plural(b.files, "file")} matched; ${plural(b.ratings, "rating")}, ${plural(b.tags, "tag")}, ${plural(b.albums, "album place")}, `
      + `${plural(b.notes, "note")}, ${plural(b.characters, "character")} and ${plural(b.decisions, "character decision")} came in. It's one step in History.`) : null;
  return h("section.st-card.set", { "aria-label": "Library" },
    h("div.st-card-head", {}, h("h2", {}, "Library"),
      h("div.row", {}, btn("New library…", () => newLibrary(lb, cur, again)), btn("Open library…", openOther, !lb.can_switch))),
    lb.note ? h("p.note.set-lede", { role: "status" }, lb.note) : null,
    field("Open now", h("div", {}, h("div", {}, cur.root ? cur.name : "This PC's library"), cur.root ? h("code", {}, cur.root) : null, brought),
      cur.root ? "Its index, thumbnails and backups are in the hidden .mlgallery folder inside it. Copy or move the whole folder to another drive or PC and open it there: its files keep their ratings, tags and albums."
        : "Its index is kept on this PC. A library of your own keeps its index inside its folder instead, so you can take it anywhere, on an external drive for example: New library."),
    field("What it's for", h("div.kind-set", { role: "group", "aria-label": "What this library is for" }, KINDS.map(([k, label, text]) =>
      h("label.check", { title: text }, h("input", { type: "checkbox", value: k, checked: (lib.kinds || ["ai"]).includes(k), onchange: async (e) => {
        const box = e.target.closest(".kind-set");
        const kinds = [...box.querySelectorAll("input:checked")].map((i) => i.value);
        if (!kinds.length) { e.target.checked = true; return toast("A library is for one thing at least.", true); }
        try {
          const r = await api("/library/kind", { method: "POST", body: { kinds } });
          ctx.setKinds(r.kinds);
          toast("Saved: the screens follow it now.");
          again();
        } catch (ex) { toast(ex.message, true); }
      } }), ` ${label}`))),
      "Tick one or more: what made each file and model statistics (AI generations), Live Photos, dates taken and People (Photos), Characters (AI generations, Illustrations). A library for more than one gets a shortcut to each part in the sidebar."),
    field("Other libraries", rows.length ? h("ol.set-folders", {}, rows) : h("p.st-none", {}, "None yet."),
      lb.can_switch ? "Opening one closes this window and opens the Gallery again with it." : "This Gallery runs as a server only, so it can't switch: start it with --library and the library's folder."));
}

async function newLibrary(lb, cur, again) {
  const path = await pickFolder("New library", "Next");
  if (!path) return;
  const name = h("input", { type: "text", value: baseName(path), "aria-label": "Name", spellcheck: false });
  const bring = h("input", { type: "checkbox", id: "lib-bring", checked: true });
  const from = cur.root ? cur.name : "this PC's library";
  const err = h("p.form-err", { role: "alert" });
  const go = h("button.btn.primary", { type: "submit" }, lb.can_switch ? "Make it and open it" : "Make it");
  const box = h("dialog.dialog", { "aria-label": "New library" }, h("form", { method: "dialog", onsubmit: async (e) => {
    e.preventDefault();
    go.disabled = true;
    try {
      const r = await api("/libraries/new", { method: "POST", body: { path, name: name.value.trim(), bring: bring.checked } });
      box.close();
      if (r.switched) toast(`Opening ${r.name}…`);
      else { toast(r.existing ? `${r.name} is a library already.` : `Made ${r.name}. Start the Gallery with --library to open it.`); again(); }
    } catch (ex) { err.textContent = ex.message; go.disabled = false; }
  } },
    h("h2", {}, "New library"),
    h("p.dialog-text", {}, "Everything in ", h("code", {}, path), " becomes the library. Your files stay where they are, as ordinary files; the Gallery keeps its index, thumbnails and backups in a hidden .mlgallery folder inside it, so the folder can be copied or moved anywhere, other drives included."),
    h("label.field-row", {}, h("span", {}, "Name"), name),
    h("label.check", { for: "lib-bring" }, bring, ` Bring the ratings, tags, albums, characters and notes the same files have in ${from}`),
    h("p.note", {}, "Matched by the files' bytes once they're indexed, so it works wherever they are. It's one step in the new library's History: Undo takes it back."),
    err,
    h("div.row.end", {}, h("button.btn", { type: "button", onclick: () => box.close() }, "Cancel"), go)));
  name.addEventListener("keydown", (e) => e.stopPropagation());
  box.addEventListener("keydown", (e) => { if (e.key !== "Escape") e.stopPropagation(); });
  box.addEventListener("close", () => box.remove());
  document.body.append(box);
  box.showModal();
}
