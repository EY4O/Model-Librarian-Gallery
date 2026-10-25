// Model Librarian Gallery: swipe review - the pictures of a view, one at a time, big: swipe left (or A) to delete,
// right (or D) to keep, up (or W) to keep as a favourite, a star (or 1-5) to rate and keep, an album to put it in and keep,
// Z to take the last one back. The picker at the top switches what's gone through: the view it was opened from, recent
// days, an album or a folder. Kept pictures are remembered as reviewed, so the next review of a view starts with the ones
// you haven't seen; deleted ones go to the Gallery's Trash (one History step each, as ever).

import { albumName, api, ask, fmtDate, h, icon, leafName, media, menu, plural, store, toast } from "./util.js";

const FAR = 110;  // pixels a swipe must go to count
const quote = (v) => (/[\s"]/.test(v) ? `"${v.replaceAll('"', "")}"` : v);

export function swipeReview(opened, openedTitle, { onClose } = {}) {
  let view = opened, title = openedTitle;
  let queue = [], left = 0, reviewed = 0, done = 0, busy = false, loading = null;
  let decided = new Set();  // (ids swiped this time: not offered again while the server catches up)
  let history = [];         // what Z takes back: {item, kind: "delete" | "keep" | "album", op, was}
  const tally = { kept: 0, deleted: 0 };
  let albums = [], folders = [], dirs = {};
  const params = () => {
    const p = new URLSearchParams({ sort: view.sort || "newest", q: view.q || "", seed: String(view.seed || 0), limit: "40" });
    if (view.folder) p.set("folder", view.folder);
    if (view.kind) p.set("kind", view.kind);
    return p;
  };

  const stage = h("div.sw-stage");
  const where = h("p.sw-where", { "aria-live": "polite" });
  const pick = h("button.sw-pick", { type: "button", "aria-haspopup": "menu", onclick: () => pickView() });
  const close = () => { box.remove(); document.removeEventListener("keydown", key, true); onClose?.(tally); };
  const rates = h("div.sw-rate", { role: "group", "aria-label": "Rate and keep (1 to 5)" },
    [1, 2, 3, 4, 5].map((n) => h("button.sw-star", { type: "button", title: `Rate ${n} and keep (${n})`, "aria-label": `Rate ${n} and keep`, onclick: () => decide("rate", n) }, icon("star"))));
  const shelf = h("div.sw-albums", { role: "group", "aria-label": "Sort to album" });
  const box = h("div.swipe", { role: "dialog", "aria-label": `Swipe review: ${title}` },
    h("header.sw-bar", {},
      h("button.sw-icon", { type: "button", "aria-label": "Close the review", title: "Close (Esc)", onclick: close }, icon("remove")),
      pick,
      h("button.sw-icon", { type: "button", "aria-label": "Take the last one back", title: "Take the last one back (Z)", onclick: () => undo() }, icon("restore"))),
    where,
    stage,
    h("footer.sw-foot", {},
      h("div.sw-acts", {},
        rates,
        h("button.sw-act.fav", { type: "button", title: "Favourite and keep (W, or swipe up)", onclick: () => decide("fav") }, icon("heart"), h("span", {}, "Favourite"), h("kbd", {}, "W")),
        h("button.sw-act.del", { type: "button", title: "Delete (A, or swipe left)", onclick: () => decide("delete") }, icon("remove"), h("span", {}, "Delete"), h("kbd", {}, "A"))),
      h("div.sw-sort", {}, h("p.sw-sort-head", {}, "Sort to album", h("span.sw-hint", {}, "Swipe right or press D to keep as it is")), shelf)));

  const counter = () => {
    const item = queue[0];
    pick.replaceChildren(h("span", {}, title), icon("twist"));
    where.textContent = item ? [`${(done + 1).toLocaleString()} / ${(done + left).toLocaleString()}`, item.shot ? fmtDate(item.shot) : null].filter(Boolean).join(" · ") : "";
  };
  const fetchMore = () => {
    const at = view;
    loading ||= api(`/sort?${params()}`).then((d) => {
      if (at !== view) return;
      reviewed = d.reviewed;
      const have = new Set(queue.map((i) => i.id));
      queue.push(...d.items.filter((i) => !have.has(i.id) && !decided.has(i.id)));
      left = Math.max(queue.length, d.left);  // (each swipe is saved before the next fetch: the server's count is right)
    }).catch((e) => toast(e.message, true)).finally(() => { loading = null; });
    return loading;
  };

  // the card on top, and the next one waiting under it (its picture fetched ahead)
  function show() {
    counter();
    const item = queue[0];
    if (!item) return finished();
    const pic = (it) => (it.kind === "video"
      ? h("video", { src: media("file", it.id), autoplay: true, muted: true, loop: true, playsInline: true })
      : h("img", { src: media("file", it.id), alt: it.name, draggable: false }));
    const card = h("div.sw-card", { title: "Swipe right to keep, left to delete, up for a favourite" }, pic(item),
      h("span.sw-tag.keep", {}, "KEEP"), h("span.sw-tag.del", {}, "DELETE"), h("span.sw-tag.fav", {}, "FAVOURITE"),
      h("div.sw-name", {}, item.name, item.rating ? h("span.sw-had", {}, "★".repeat(item.rating)) : null, item.favorite ? icon("heart") : null));
    const next = queue[1] ? h("div.sw-card.under", { "aria-hidden": "true" }, pic(queue[1])) : null;
    if (queue[2] && queue[2].kind !== "video") new Image().src = media("file", queue[2].id);
    stage.replaceChildren(...[next, card].filter(Boolean));
    drag(card);
    if (queue.length < 8 && left > queue.length) fetchMore();
  }

  function drag(card) {
    let x0 = 0, y0 = 0, dx = 0, dy = 0, on = false;
    card.addEventListener("pointerdown", (e) => {
      if (busy) return;
      on = true; x0 = e.clientX; y0 = e.clientY;
      try { card.setPointerCapture(e.pointerId); } catch {}
      card.classList.add("held");
    });
    card.addEventListener("pointermove", (e) => {
      if (!on) return;
      dx = e.clientX - x0; dy = e.clientY - y0;
      card.style.transform = `translate(${dx}px, ${dy}px) rotate(${dx / 18}deg)`;
      card.style.setProperty("--keep", Math.max(0, Math.min(1, dx / FAR)));
      card.style.setProperty("--del", Math.max(0, Math.min(1, -dx / FAR)));
      card.style.setProperty("--fav", Math.max(0, Math.min(1, -dy / FAR)) * (Math.abs(dx) < FAR / 2 ? 1 : 0));
    });
    const up = () => {
      if (!on) return;
      on = false;
      card.classList.remove("held");
      if (dx > FAR) decide("keep");
      else if (dx < -FAR) decide("delete");
      else if (dy < -FAR && Math.abs(dx) < FAR / 2) decide("fav");
      else { card.style.transform = ""; for (const p of ["--keep", "--del", "--fav"]) card.style.setProperty(p, 0); }
      dx = dy = 0;
    };
    card.addEventListener("pointerup", up);
    card.addEventListener("pointercancel", up);
  }

  // kind: delete, keep, fav, rate (value: 1-5), album (value: its name)
  async function decide(kind, value) {
    const item = queue[0];
    if (!item || busy) return;
    busy = true;
    const card = stage.querySelector(".sw-card:not(.under)");
    card?.classList.add(kind === "delete" ? "out-left" : kind === "fav" ? "out-up" : kind === "album" ? "out-down" : "out-right");
    try {
      if (kind === "delete") {
        const r = await api("/ops/trash", { method: "POST", body: { selection: [item.id] } });
        if (!r.trashed) throw new Error(`It couldn't go to the Trash: ${r.failed?.[0]?.error || "it isn't reachable"}.`);  // (in use, or its drive's gone)
        history.push({ item, kind: "delete", op: r.op });
        tally.deleted++;
      } else {
        let op = null;
        if (kind === "album") {
          op = (await api("/ops/bulk", { method: "POST", body: { selection: [item.id], action: "album", value } })).op;
          used(value);
        }
        const r = await api("/sort", { method: "POST", body: { file: item.id, verdict: kind === "album" ? "keep" : kind, value } });
        history.push({ item, kind: kind === "album" ? "album" : "keep", op, was: r.was });
        tally.kept++;
      }
      decided.add(item.id);
      queue.shift();
      left = Math.max(0, left - 1);
      done++;
    } catch (e) {
      toast(e.message, true);
      card?.classList.remove("out-left", "out-up", "out-right", "out-down");
    }
    await new Promise((r) => setTimeout(r, 160));
    busy = false;
    show();
    if (kind === "album") shelve();
  }

  async function undo() {
    if (busy) return;  // (a swipe still going: the step it makes is the one to take back, once it's there)
    const last = history.pop();
    if (!last) return toast("Nothing to take back yet.");
    busy = true;
    try {
      if (last.kind === "delete") {
        await api(`/ops/${last.op}/undo`, { method: "POST" });
        tally.deleted--;
      } else {
        if (last.op) await api(`/ops/${last.op}/undo`, { method: "POST" });  // (out of the album again)
        await api("/sort/undo", { method: "POST", body: { file: last.item.id, was: last.was } });
        tally.kept--;
      }
      decided.delete(last.item.id);
      queue.unshift(last.item);
      left++;
      done = Math.max(0, done - 1);
    } catch (e) {
      history.push(last);  // it can be tried again
      toast(e.message, true);
    }
    busy = false;
    show();
  }

  function finished() {
    stage.replaceChildren(h("div.sw-done", {},
      h("h2", {}, tally.kept + tally.deleted ? "All reviewed" : "Nothing to review here"),
      h("p", {}, tally.kept + tally.deleted ? `This time: ${plural(tally.kept, "kept", "kept")}, ${plural(tally.deleted, "deleted", "deleted")} (in the Trash, to put back any time).`
        : `Every picture here has been reviewed${reviewed ? ` (${reviewed.toLocaleString()})` : ""}.`),
      h("div.row.center", {},
        reviewed ? h("button.btn", { type: "button", onclick: async () => {
          if (!await ask("Review these again?", "Every picture here counts as not reviewed yet, and the review starts from the first one.", { ok: "Start again" })) return;
          await api("/sort/reset", { method: "POST", body: { view } }).catch((e) => toast(e.message, true));
          decided.clear();
          done = 0;
          await fetchMore();
          show();
        } }, "Start again") : null,
        h("button.btn", { type: "button", onclick: () => pickView() }, "Something else…"),
        h("button.btn.primary", { type: "button", onclick: close }, "Done"))));
  }

  // ---------------------------------------------------------------- what's gone through: the view it came from, days, albums, folders
  async function lists() {
    const [a, f] = await Promise.all([api("/albums").catch(() => null), api("/folders").catch(() => null)]);
    albums = (a?.albums || []).filter((x) => x.query == null).map((x) => albumName(x.name)).sort((x, y) => x.localeCompare(y, undefined, { sensitivity: "base" }));
    folders = (f?.folders || []).filter((x) => x.online);
    await Promise.all(folders.map(async (x) => { try { dirs[x.id] = (await api(`/folders/${x.id}/dirs`)).dirs; } catch {} }));
  }

  function switchTo(v, t) {
    view = v;
    title = t;
    queue = []; left = 0; done = 0; reviewed = 0;
    decided = new Set();
    history = [];  // (what was done before stays in History)
    box.setAttribute("aria-label", `Swipe review: ${t}`);
    stage.replaceChildren(h("p.sw-wait", {}, "Gathering the pictures…"));
    counter();
    fetchMore().then(show);
  }

  function pickView() {
    const base = (q) => ({ sort: "newest", q });
    const day = (label, q) => ({ label, icon: "clock", onclick: () => switchTo(base(q), label) });
    const head = (text) => ({ node: h("p.sw-menu-head", {}, text) });
    const name = (p) => p.split(/[\\/]/).filter(Boolean).pop() || p;
    menu(pick, [
      { label: openedTitle, icon: "grid", onclick: () => switchTo(opened, openedTitle) },
      null, day("Today", "date:today"), day("Last 7 days", "date:7d"), day("Last 30 days", "date:30d"),
      { label: "Everything", icon: "grid", onclick: () => switchTo(base(""), "Everything") },
      ...(albums.length ? [null, head("Albums"), ...albums.map((n) => ({ label: n, icon: "album", onclick: () => switchTo(base(`album:${quote(n)}`), leafName(n)) }))] : []),
      ...(folders.length ? [null, head("Folders"), ...folders.flatMap((f) => [
        { label: name(f.path), icon: "folder", onclick: () => switchTo({ sort: "newest", folder: f.id, q: "" }, name(f.path)) },
        ...(dirs[f.id] || []).map((d) => ({ label: `${name(f.path)} / ${d.rel.replaceAll("/", " / ")}`, icon: "folder",
          onclick: () => switchTo({ sort: "newest", folder: f.id, q: `path:${quote(d.rel)}` }, name(d.rel)) }))])] : []),
    ], { cls: "sw-menu" });
  }

  // ---------------------------------------------------------------- sort to album: the ones used lately first, then the rest; Choose… for any
  const recent = () => store.get("swipe-albums", []);
  function used(name) {
    const n = albumName(name);
    if (!albums.some((x) => x.toLowerCase() === n.toLowerCase())) albums = [...albums, n].sort((x, y) => x.localeCompare(y, undefined, { sensitivity: "base" }));  // (just made)
    store.set("swipe-albums", [n, ...recent().filter((x) => x.toLowerCase() !== n.toLowerCase())].slice(0, 12));
  }
  function shelve() {
    const have = new Map(albums.map((n) => [n.toLowerCase(), n]));
    const first = recent().map((n) => have.get(n.toLowerCase())).filter(Boolean);
    const rest = albums.filter((n) => !first.some((x) => x.toLowerCase() === n.toLowerCase()));
    shelf.replaceChildren(...[...first, ...rest].slice(0, 30).map((n) => h("button.sw-album", { type: "button", title: `Put it in ${n} and keep it`, onclick: () => decide("album", n) },
      icon("album"), h("span", {}, leafName(n)))),
    h("button.sw-album.more", { type: "button", title: "Pick any album, or make one", onclick: chooseAlbum }, icon("more"), h("span", {}, "Choose…")));
  }

  function chooseAlbum() {
    const input = h("input", { type: "text", placeholder: "Find an album, or name a new one", "aria-label": "Album", autocomplete: "off", spellcheck: false });
    const list = h("div.sw-choose");
    const dlg = h("dialog.dialog.sw-choose-dlg", { "aria-label": "Sort to album" },
      h("form", { method: "dialog", onsubmit: (e) => { e.preventDefault(); const n = input.value.trim(); if (n) go(albums.find((x) => x.toLowerCase() === albumName(n).toLowerCase()) || n); } },
        h("h2", {}, "Sort to album"), input, list,
        h("div.row.end", {}, h("button.btn", { type: "button", onclick: () => dlg.close() }, "Cancel"))));
    const go = (n) => { dlg.close(); decide("album", n); };
    const fill = () => {
      const q = input.value.trim().toLowerCase();
      const found = albums.filter((n) => !q || n.toLowerCase().includes(q));
      list.replaceChildren(...found.slice(0, 200).map((n) => h("button.menu-item", { type: "button", onclick: () => go(n) }, icon("album"), h("span", {}, n))),
        q && !albums.some((n) => n.toLowerCase() === albumName(q).toLowerCase())
          ? h("button.menu-item", { type: "button", onclick: () => go(input.value.trim()) }, icon("plus"), h("span", {}, `New album: ${albumName(input.value)}`)) : null);
    };
    input.addEventListener("input", fill);
    dlg.addEventListener("close", () => dlg.remove());
    fill();
    document.body.append(dlg);
    dlg.showModal();
    input.focus();
  }

  // every key is the review's while it's open: none reaches the grid behind it (Delete there would trash a selection,
  // Enter would open a second review, Ctrl+Z would undo behind its back); Tab and Enter still work its own buttons
  function key(e) {
    if (document.querySelector("dialog[open]") || document.querySelector(".menu")) return;  // (a question, the album chooser, the picker)
    e.stopPropagation();
    const k = e.key.toLowerCase(), mod = e.ctrlKey || e.altKey || e.metaKey;
    if (e.key === "Escape") close();
    else if (mod) { if (k !== "c") e.preventDefault(); return; }
    else if (k === "a" || e.key === "ArrowLeft") decide("delete");
    else if (k === "d" || e.key === "ArrowRight") decide("keep");
    else if (k === "w" || e.key === "ArrowUp") decide("fav");
    else if (k === "z") undo();
    else if (/^[1-5]$/.test(e.key)) decide("rate", Number(e.key));
    else if (e.key === "Tab" || ((e.key === "Enter" || e.key === " ") && e.target.closest?.(".swipe button"))) return;
    e.preventDefault();
  }
  document.addEventListener("keydown", key, true);
  document.body.append(box);
  box.tabIndex = -1;
  box.focus();  // (not the sidebar's Swipe button: Enter there would open another)
  stage.replaceChildren(h("p.sw-wait", {}, "Gathering the pictures…"));
  counter();
  shelve();
  lists().then(shelve);
  fetchMore().then(show);
}
