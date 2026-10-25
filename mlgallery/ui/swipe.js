// Model Librarian Gallery: swipe review - the pictures of a view, one at a time, big: swipe left (or A) to delete,
// right (or D) to keep, up (or W) to keep as a favourite, 1-5 to rate and keep, Z to take the last one back.
// Kept pictures are remembered as reviewed, so the next review of the view starts with the ones you haven't seen;
// deleted ones go to the Gallery's Trash (one History step each, as ever).

import { api, ask, h, icon, media, plural, stars, toast } from "./util.js";

const FAR = 110;  // pixels a swipe must go to count

export function swipeReview(view, title, { onClose } = {}) {
  const params = new URLSearchParams({ sort: view.sort || "newest", q: view.q || "", seed: String(view.seed || 0), limit: "40" });
  if (view.folder) params.set("folder", view.folder);
  if (view.kind) params.set("kind", view.kind);
  let queue = [], left = 0, reviewed = 0, busy = false, loading = null;
  const decided = new Set();  // (ids swiped this time: not offered again while the server catches up)
  const history = [];         // what Z takes back: {item, kind: "delete" | "keep", op, was}
  const tally = { kept: 0, deleted: 0 };

  const stage = h("div.sw-stage");
  const count = h("span.sw-count", { "aria-live": "polite" });
  const close = () => { box.remove(); document.removeEventListener("keydown", key, true); onClose?.(tally); };
  const btn = (cls, label, hint, ico, fn) => h(`button.sw-btn.${cls}`, { type: "button", title: `${label} (${hint})`, "aria-label": label, onclick: fn }, icon(ico), h("kbd", {}, hint));
  const rates = h("div.sw-rate", { role: "group", "aria-label": "Rate and keep (1 to 5)" },
    [1, 2, 3, 4, 5].map((n) => h("button.sw-star", { type: "button", title: `Rate ${n} and keep (${n})`, "aria-label": `Rate ${n} and keep`, onclick: () => decide("rate", n) }, stars(n, { size: 10 }))));
  const box = h("div.swipe", { role: "dialog", "aria-label": `Swipe review: ${title}` },
    h("header.sw-bar", {},
      h("button.icon-btn", { type: "button", "aria-label": "Close the review", title: "Close (Esc)", onclick: close }, icon("remove")),
      h("span.sw-title", {}, `Swipe review · ${title}`), count,
      h("span.spacer"),
      h("button.btn.small", { type: "button", title: "Take the last one back (Z)", onclick: () => undo() }, h("kbd", {}, "Z"), "Undo")),
    stage,
    h("footer.sw-acts", {},
      btn("del", "Delete", "A", "trash", () => decide("delete")),
      btn("fav", "Favourite and keep", "W", "heart", () => decide("fav")),
      btn("keep", "Keep", "D", "check", () => decide("keep")),
      rates));

  const counter = () => { count.textContent = left ? `${plural(left, "to go", "to go")} · ${reviewed.toLocaleString()} reviewed` : ""; };
  const fetchMore = () => {
    loading ||= api(`/sort?${params}`).then((d) => {
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
    const card = h("div.sw-card", {}, pic(item), h("span.sw-tag.keep", {}, "KEEP"), h("span.sw-tag.del", {}, "DELETE"), h("span.sw-tag.fav", {}, "FAVOURITE"),
      h("div.sw-name", {}, item.name, item.rating ? stars(item.rating, { size: 10 }) : null, item.favorite ? icon("heart") : null));
    const next = queue[1] ? h("div.sw-card.under", { "aria-hidden": "true" }, pic(queue[1])) : null;
    if (queue[2] && queue[2].kind !== "video") new Image().src = media("file", queue[2].id);
    stage.replaceChildren(...[next, card].filter(Boolean));
    drag(card);
    if (queue.length < 8 && left > queue.length) fetchMore();
  }

  function drag(card) {
    let x0 = 0, y0 = 0, dx = 0, dy = 0, on = false;
    card.addEventListener("pointerdown", (e) => { if (busy) return; on = true; x0 = e.clientX; y0 = e.clientY; card.setPointerCapture(e.pointerId); card.classList.add("held"); });
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

  async function decide(kind, value) {
    const item = queue[0];
    if (!item || busy) return;
    busy = true;
    const card = stage.querySelector(".sw-card:not(.under)");
    card?.classList.add(kind === "delete" ? "out-left" : kind === "fav" ? "out-up" : "out-right");
    try {
      if (kind === "delete") {
        const r = await api("/ops/trash", { method: "POST", body: { selection: [item.id] } });
        if (!r.trashed) throw new Error(`It couldn't go to the Trash: ${r.failed?.[0]?.error || "it isn't reachable"}.`);  // (in use, or its drive's gone)
        history.push({ item, kind: "delete", op: r.op });
        tally.deleted++;
      } else {
        const r = await api("/sort", { method: "POST", body: { file: item.id, verdict: kind, value } });
        history.push({ item, kind: "keep", was: r.was });
        tally.kept++;
        reviewed++;
      }
      decided.add(item.id);
      queue.shift();
      left = Math.max(0, left - 1);
    } catch (e) {
      toast(e.message, true);
      card?.classList.remove("out-left", "out-up", "out-right");
    }
    await new Promise((r) => setTimeout(r, 160));
    busy = false;
    show();
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
        await api("/sort/undo", { method: "POST", body: { file: last.item.id, was: last.was } });
        tally.kept--;
        reviewed = Math.max(0, reviewed - 1);
      }
      decided.delete(last.item.id);
      queue.unshift(last.item);
      left++;
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
          await fetchMore();
          show();
        } }, "Start again") : null,
        h("button.btn.primary", { type: "button", onclick: close }, "Done"))));
  }

  // every key is the review's while it's open: none reaches the grid behind it (Delete there would trash a selection,
  // Enter would open a second review, Ctrl+Z would undo behind its back); Tab and Enter still work its own buttons
  function key(e) {
    if (document.querySelector("dialog[open]")) return;  // (Start again's question)
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
  fetchMore().then(show);
}
