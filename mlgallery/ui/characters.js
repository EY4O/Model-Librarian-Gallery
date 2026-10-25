// Model Librarian Gallery: characters - the list, one character's page, the editor, and To review.

import { api, ask, fmtNum, h, has, icon, media, mixedWho, plural, stacks, toast, who } from "./util.js";

const KIND = { lora: "LORA", word: "WORD", tag: "TAG" };
const KIND_HELP = {
  lora: "A LoRA whose name contains this, any version or strength",
  word: "Words in the positive prompt, such as a trigger word",
  tag: "A part of the prompt, or one of your tags, exactly",
};

// ---------------------------------------------------------------- shared bits
// a picture, or (no confirmed file yet) the name's first letter on a quiet tint
function thumb(id, alt = "") {
  const none = () => h("div.c-thumb.none", { "aria-hidden": "true" }, h("span.c-initial", {}, (alt || "?").trim().charAt(0).toUpperCase() || "?"));
  if (!id) return none();
  const img = h("img", { src: media("thumb", id), alt, loading: "lazy", decoding: "async" });
  img.onerror = () => img.replaceWith(none());
  return h("div.c-thumb", {}, img);
}

// a quick look at one file, over everything (Esc or a click closes it)
export function preview(file) {
  const close = () => { box.remove(); document.removeEventListener("keydown", key, true); };
  const key = (e) => { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); } };
  const src = file.src || media("file", file.file_id ?? file.id);  // (a download: its own address)
  const box = h("div.preview", { role: "dialog", "aria-label": file.name || "Preview", onclick: close },
    file.kind === "video" ? h("video", { src, controls: true, autoplay: true, loop: true, onclick: (e) => e.stopPropagation() }) : h("img", { src, alt: file.name || "" }),
    h("div.preview-name", {}, file.name || ""));
  document.addEventListener("keydown", key, true);
  document.body.append(box);
}

function strengthBadge(rule, onToggle) {
  return h("button.strength-badge." + rule.strength, {
    type: "button", onclick: onToggle, "aria-pressed": String(rule.strength === "strong"),
    title: rule.strength === "strong" ? "Strong: a match counts straight away (click to make it weak)" : "Weak: a match waits in To review (click to make it strong)",
  }, rule.strength === "strong" ? "Strong" : "Weak");
}

// ---------------------------------------------------------------- the list
export async function renderCharacters(root, ctx) {
  root.setAttribute("aria-busy", "true");
  let data;
  try { data = await api("/characters"); } catch (e) { toast(e.message, true); return; } finally { root.removeAttribute("aria-busy"); }
  const cards = data.characters.map((c) => h("button.c-card", { type: "button", onclick: () => ctx.show("character", c.id) },
    thumb(c.cover, c.name),
    h("span.c-name", {}, c.name, mixedWho() ? h("span.c-who", {}, c.who === "person" ? "Person" : "Character") : null),
    h("span.c-n", {}, plural(c.n, "file")),
    c.review ? h("span.c-review", { title: "Waiting for a yes or no" }, `${c.review} to review`) : null));
  root.replaceChildren(...[
    h("header.st-head", {}, h("h1", {}, who()),
      h("div.row", {}, data.review ? h("button.btn", { type: "button", onclick: () => ctx.show("review") }, `To review (${data.review.toLocaleString()})`) : null,
        h("button.btn.primary", { type: "button", onclick: () => editCharacter(null, ctx) }, icon("plus"), `New ${who(false, false)}`))),
    data.suggestions.length ? h("section.st-card", { "aria-label": "From your workflows" },
      h("div.st-card-head", {}, h("h2", {}, "From your workflows"), h("span", {}, "Names your Gallery Info nodes used that aren't characters yet")),
      h("div.c-sugg", {}, data.suggestions.map((s) => h("button.btn", { type: "button", title: `Make ${s.name} a character (${plural(s.n, "file")})`,
        onclick: () => editCharacter(null, ctx, { name: s.name }) }, icon("plus"), s.name, h("span.lg-n", {}, s.n.toLocaleString()))))) : null,
    cards.length ? h("div.c-grid", {}, cards)
      : h("div.c-empty", {}, h("h2", {}, `No ${who(true, false)} yet`),
        h("p", {}, mixedWho()
          ? "Someone in your photos (a person) or someone made up (a character): a name you add to files, plus, for a character, what gives it away (its LoRA, trigger words or prompt tags). Each gets a page with every file they're in, and the Gallery asks you about the ones it isn't sure of."
          : has("photos")
            ? "A person is a name you add to photos (in a photo's details, or by selecting several). Each one gets a page with every photo they're in. A tag can also find them, and the Gallery asks you about the photos it isn't sure of."
            : "A character is a name plus what gives it away: its LoRA, its trigger words, or prompt tags. The Gallery then finds every image it's in, and asks you about the ones it isn't sure of."),
        h("button.btn.primary", { type: "button", onclick: () => editCharacter(null, ctx) }, `New ${who(false, false)}`)),
  ].filter(Boolean));
}

// ---------------------------------------------------------------- one character
export async function renderCharacter(root, id, ctx) {
  root.setAttribute("aria-busy", "true");
  let c, queue, recent;
  try {
    [c, queue] = await Promise.all([api(`/characters/${id}`), api(`/characters/review?character=${id}`)]);
    recent = await api(`/files?limit=24&q=${encodeURIComponent(`character:"${c.name.replaceAll('"', "")}"`)}`);
  } catch (e) {
    toast(e.message, true);
    ctx.show("characters");
    return;
  } finally { root.removeAttribute("aria-busy"); }
  const filter = () => ctx.open([{ key: "character", value: c.name }]);
  const dates = c.first ? `${new Date(c.first * 1000).toLocaleDateString()} – ${new Date(c.last * 1000).toLocaleDateString()}` : "";
  const saveRules = async (rules) => {
    try {
      await api(`/characters/${id}`, { method: "POST", body: { rules } });
      ctx.refresh();
      renderCharacter(root, id, ctx);
    } catch (e) { toast(e.message, true); }
  };
  const rulesNow = () => c.rules.map((r) => ({ kind: r.kind, value: r.value, strength: r.strength, muted: r.muted }));
  root.replaceChildren(...[
    h("div.c-crumb", {}, h("button.link", { type: "button", onclick: () => ctx.show("characters") }, who()), " / ", c.name),
    h("header.c-head", {},
      h("div.c-portrait", {}, thumb(c.cover, c.name)),
      h("div.c-title", {},
        h("h1", {}, c.name, mixedWho() ? h("span.c-who", {}, c.who === "person" ? "Person" : "Character") : null),
        c.aliases.length ? h("p.c-aliases", {}, `Also: ${c.aliases.join(", ")}`) : null,
        h("p.c-facts", {}, [plural(c.n, "file"), c.counts.confirmed ? `${c.counts.confirmed.toLocaleString()} confirmed by you or a workflow` : "", dates].filter(Boolean).join(" · ")),
        h("div.row", {},
          h("button.btn.primary", { type: "button", onclick: filter, disabled: !c.n }, icon("grid"), "Show all files"),
          h("button.btn", { type: "button", onclick: () => editCharacter(c, ctx) }, "Edit"),
          h("button.btn.danger.c-del", { type: "button", onclick: async () => {
            if (!await ask(`Delete the ${who(false, false)} ${c.name}?`, "Its rules and your yes and no answers go; the files themselves are untouched.", { ok: `Delete the ${who(false, false)}`, danger: true })) return;
            await api(`/characters/${id}`, { method: "DELETE" }).catch((e) => toast(e.message, true));
            ctx.refresh();
            ctx.show("characters");
          } }, "Delete")))),
    stacks([
      h("section.st-card", { "aria-label": "How they're recognised" },
        h("div.st-card-head", {}, h("h2", {}, "How they're recognised"), h("span", {}, "Strong rules count at once; weak ones ask you first")),
        c.rules.length ? h("ol.rules", {}, c.rules.map((r, i) => h("li", {},
          h("span.rk." + r.kind, { title: KIND_HELP[r.kind] }, KIND[r.kind]),
          h("span.rv", {}, r.value),
          h("span.rs", {}, `${plural(r.matches, "match", "matches")}${r.confirmed ? ` · ${r.confirmed} yes` : ""}${r.rejected ? ` · ${r.rejected} no` : ""}`),
          strengthBadge(r, () => { const rs = rulesNow(); rs[i].strength = r.strength === "strong" ? "weak" : "strong"; saveRules(rs); }),
          h("button.icon-btn.small", { type: "button", "aria-label": `Remove the rule ${r.value}`, title: "Remove this rule",
            onclick: () => saveRules(rulesNow().filter((_, j) => j !== i)) }, icon("remove")),
          r.muted ? h("p.muted-note", {}, "Not suggesting files any more: you said no to most of its matches. ",
            h("button.link", { type: "button", onclick: () => { const rs = rulesNow(); rs[i].muted = false; saveRules(rs); } }, "Suggest again")) : null)))
          : h("p.st-none", {}, "No rules: only files a workflow's Gallery Info node names are counted."),
        ruleAdder(c, (rule) => saveRules([...rulesNow(), rule]))),
      h("section.st-card", { "aria-label": "LoRA strength" },
        h("div.st-card-head", {}, h("h2", {}, "LoRA strength"), h("span", {}, "How strongly its LoRA was used")),
        histogram(c.strengths),
        c.loras.length ? h("ol.bars.lora", {}, c.loras.map((l) => h("li", {}, h("div.bar-row", {},
          h("span.bar-label", {}, l.name), h("span.bar-extra", {}, l.avg != null ? `${fmtNum(l.lo)}–${fmtNum(l.hi)}, avg ${fmtNum(l.avg)}` : ""),
          h("span.bar-n", {}, l.n.toLocaleString()))))) : null),
      h("section.st-card", { "aria-label": "Made with" },
        h("div.st-card-head", {}, h("h2", {}, "Made with"), h("span", {}, "Checkpoints")),
        c.models.length ? h("ol.bars.ckpt", {}, c.models.map((m) => h("li", {}, h("button.bar-row", { type: "button", onclick: () => ctx.open([{ key: "character", value: c.name }, { key: "model", value: m.name }]) },
          h("span.bar-label", {}, m.family ? h("span.fam", { style: { color: m.color, borderColor: m.color + "88" } }, m.family.toUpperCase()) : null, m.name), h("span.bar-n", {}, m.n.toLocaleString()),
          h("span.bar-track", { "aria-hidden": "true" }, h("span.bar-fill", { style: { width: `${(m.n / c.models[0].n) * 100}%`, background: m.color || null } }))))))
          : h("p.st-none", {}, "No checkpoint recorded in its files."),
        c.timeline.length > 1 ? h("div.c-months", { "aria-label": "Files per month" }, months(c.timeline)) : null)]),
    queue.items.length ? h("section.st-card", { "aria-label": "To review" },
      h("div.st-card-head", {}, h("h2", {}, `To review (${queue.items.length.toLocaleString()})`), h("span", {}, `Is ${c.name} in these? Y for yes, N for no`)),
      reviewGrid(queue.items, ctx, () => renderCharacter(root, id, ctx), { single: true })) : null,
    recent.items.length ? h("section.st-card", { "aria-label": "Latest" },
      h("div.st-card-head", {}, h("h2", {}, "Latest"), h("button.link", { type: "button", onclick: filter }, `All ${c.n.toLocaleString()}`)),
      h("div.c-files", {}, recent.items.map((f) => h("button.c-file", { type: "button", title: f.name, onclick: () => preview(f),
        oncontextmenu: (e) => { e.preventDefault(); setCover(c, f.id, ctx, () => renderCharacter(root, id, ctx)); } }, thumb(f.id, f.name)))),
      h("p.st-none", {}, "Right-click a picture to use it as the portrait.")) : null,
  ].filter(Boolean));
}

async function setCover(c, fileId, ctx, done) {
  try {
    await api(`/characters/${c.id}`, { method: "POST", body: { cover: fileId } });
    toast(`That's ${c.name}'s portrait now.`);
    ctx.refresh();
    done();
  } catch (e) { toast(e.message, true); }
}

function months(tl) {
  const max = Math.max(...tl.map((m) => m.n), 1);
  return tl.slice(-18).map((m) => {
    const [y, mo] = m.month.split("-").map(Number);
    const label = new Date(y, mo - 1, 1).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
    return h("div.c-month", { title: `${label}: ${plural(m.n, "file")}` }, h("span.c-mbar", { style: { height: `${Math.max(4, (m.n / max) * 100)}%` } }), h("span.c-mlabel", {}, label));
  });
}

function histogram(values) {
  if (!values.length) return h("p.st-none", {}, "Its LoRA isn't in any of its files (or it has no LoRA rule).");
  const bins = Array.from({ length: 15 }, (_, i) => ({ lo: i / 10, n: 0 }));
  for (const v of values) bins[Math.max(0, Math.min(14, Math.floor(v * 10 + 1e-9)))].n++;
  const used = bins.filter((b) => b.n);
  const lo = Math.max(0, Math.min(...used.map((b) => bins.indexOf(b))) - 1), hi = Math.min(14, Math.max(...used.map((b) => bins.indexOf(b))) + 1);
  const shown = bins.slice(lo, hi + 1);
  const max = Math.max(...shown.map((b) => b.n));
  return h("div.histo", { role: "img", "aria-label": `LoRA strengths: ${shown.filter((b) => b.n).map((b) => `${b.lo.toFixed(1)} ${b.n}`).join(", ")}` },
    shown.map((b) => h("div.h-col", { title: `${b.lo.toFixed(1)}–${(b.lo + 0.1).toFixed(1)}: ${plural(b.n, "file")}` },
      h("span.h-bar", { style: { height: `${b.n ? Math.max(6, (b.n / max) * 100) : 0}%` } }), h("span.h-label", {}, b.lo.toFixed(1)))));
}

function ruleAdder(c, add) {
  const kind = h("select", { "aria-label": "Rule kind" }, Object.entries(KIND).map(([k, label]) => h("option", { value: k, title: KIND_HELP[k] }, label)));
  const value = h("input", { type: "text", placeholder: "lora name, trigger word or tag", "aria-label": "Rule", autocomplete: "off", spellcheck: false });
  const list = h("datalist", { id: `rule-sugg-${c.id}` });
  value.setAttribute("list", list.id);
  const go = () => {
    const v = value.value.trim();
    if (!v) return;
    add({ kind: kind.value, value: v, strength: kind.value === "tag" ? "weak" : "strong" });
  };
  value.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); go(); } });
  value.addEventListener("input", async () => {
    const field = kind.value === "lora" ? "lora" : kind.value === "tag" ? "tag" : null;
    if (!field) return;
    const { items } = await api(`/suggest?field=${field}&q=${encodeURIComponent(value.value)}`).catch(() => ({ items: [] }));
    list.replaceChildren(...items.map((it) => h("option", { value: it.name })));
  });
  return h("div.rule-add", {}, kind, value, list, h("button.btn", { type: "button", onclick: go }, icon("plus"), "Add rule"));
}

// ---------------------------------------------------------------- the editor
export function editCharacter(c, ctx, preset = {}) {
  const isNew = !c;
  const rules = c ? c.rules.map((r) => ({ kind: r.kind, value: r.value, strength: r.strength, muted: r.muted })) : [];
  const name = h("input", { type: "text", value: c ? c.name : preset.name || "", placeholder: "Aurelia", "aria-label": "Name", required: true });
  const aliases = h("input", { type: "text", value: c ? c.aliases.join(", ") : "", placeholder: "Other names, separated by commas", "aria-label": "Also known as" });
  const lora = h("input", { type: "text", placeholder: "Its LoRA (optional)", "aria-label": "LoRA", autocomplete: "off", spellcheck: false });
  const loraList = h("datalist", { id: "char-lora-sugg" });
  lora.setAttribute("list", loraList.id);
  const words = h("div.word-picks");
  const rulesBox = h("ol.rules.edit");
  const showRules = () => rulesBox.replaceChildren(...(rules.length ? rules.map((r, i) => h("li", {},
    h("span.rk." + r.kind, { title: KIND_HELP[r.kind] }, KIND[r.kind]), h("span.rv", {}, r.value),
    strengthBadge(r, () => { r.strength = r.strength === "strong" ? "weak" : "strong"; showRules(); }),
    h("button.icon-btn.small", { type: "button", "aria-label": `Remove ${r.value}`, onclick: () => { rules.splice(i, 1); showRules(); } }, icon("remove"))))
    : [h("li.st-none", {}, "No rules yet: add its LoRA above, or a trigger word or tag below.")]));
  const addRule = (r) => {
    if (!rules.some((x) => x.kind === r.kind && x.value.toLowerCase() === r.value.toLowerCase())) rules.push(r);
    showRules();
  };
  lora.addEventListener("input", async () => {
    const { items } = await api(`/suggest?field=lora&q=${encodeURIComponent(lora.value)}`).catch(() => ({ items: [] }));
    loraList.replaceChildren(...items.map((it) => h("option", { value: it.name })));
  });
  const useLora = async () => {
    const v = lora.value.trim();
    if (!v) return;
    addRule({ kind: "lora", value: v.replace(/[_\-. ]?v\d+(\.\d+)?$/i, ""), strength: "strong" }); // any version of it
    lora.value = "";
    const { words: found } = await api(`/characters/words?lora=${encodeURIComponent(v)}`).catch(() => ({ words: [] }));
    words.replaceChildren(...(found.length ? [h("span.st-none", {}, "Trigger words found:"), ...found.map((w) => h("button.btn.small", { type: "button", title: "Add as a word rule",
      onclick: (e) => { addRule({ kind: "word", value: w, strength: "strong" }); e.currentTarget.disabled = true; } }, icon("plus"), w))] : []));
  };
  lora.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); useLora(); } });
  const err = h("p.form-err", { role: "alert" });
  // a library for photos and more: someone in your photos, or a character (made up, from a LoRA)
  const was = (c && c.who) || "character";  // (a new one: c is null)
  const whoPick = h("div.field-row", { role: "radiogroup", "aria-label": "Who" }, h("span", {}, "Who"),
    h("div.row", {}, [["person", "A person"], ["character", "A character"]].map(([v, label]) =>
      h("label.check", {}, h("input", { type: "radio", name: "who", value: v, checked: v === was }), ` ${label}`))));
  const dlg = h("dialog.dialog", { "aria-label": isNew ? `New ${who(false, false)}` : `Edit ${c.name}` },
    h("form", { method: "dialog", onsubmit: async (e) => {
      e.preventDefault();
      const body = { name: name.value.trim(), aliases: aliases.value.split(",").map((s) => s.trim()).filter(Boolean), rules };
      if (mixedWho()) body.who = whoPick.querySelector("input:checked").value;
      try {
        const saved = await api(isNew ? "/characters" : `/characters/${c.id}`, { method: "POST", body });
        dlg.close();
        ctx.refresh();
        ctx.show("character", saved.id);
      } catch (ex) { err.textContent = ex.message; }
    } },
      h("h2", {}, isNew ? `New ${who(false, false)}` : `Edit ${c.name}`),
      h("label.field-row", {}, h("span", {}, "Name"), name),
      mixedWho() ? whoPick : null,
      h("label.field-row", {}, h("span", {}, "Also known as"), aliases),
      h("div.field-row", {}, h("span", {}, "LoRA"), h("div.inline", {}, lora, loraList, h("button.btn", { type: "button", onclick: useLora }, "Use"))),
      words,
      h("div.field-row.top", {}, h("span", {}, "Rules"), h("div", {}, rulesBox, ruleAdder({ id: "new" }, addRule))),
      h("p.st-none", {}, "Strong rules count a match straight away; weak ones put it in To review for you to confirm. A LoRA or a trigger word is usually strong, a tag weak."),
      err,
      h("div.row.end", {}, h("button.btn", { type: "button", onclick: () => dlg.close() }, "Cancel"), h("button.btn.primary", { type: "submit" }, isNew ? "Create" : "Save"))));
  // the shortcuts of the screens below don't apply while typing here
  dlg.addEventListener("keydown", (e) => { if (e.key !== "Escape") e.stopPropagation(); });
  dlg.addEventListener("close", () => dlg.remove());
  showRules();
  document.body.append(dlg);
  dlg.showModal();
  name.focus();
}

// ---------------------------------------------------------------- To review
export async function renderReview(root, ctx) {
  root.setAttribute("aria-busy", "true");
  let queue;
  try { queue = await api("/characters/review"); } catch (e) { toast(e.message, true); return; } finally { root.removeAttribute("aria-busy"); }
  const groups = new Map();
  for (const it of queue.items) {
    if (!groups.has(it.character_id)) groups.set(it.character_id, { name: it.character, items: [] });
    groups.get(it.character_id).items.push(it);
  }
  root.replaceChildren(...[
    h("header.st-head", {}, h("h1", {}, "To review"), h("div.keys", {}, h("kbd", {}, "Y"), " it's them ", h("kbd", {}, "N"), " it isn't ", h("kbd", {}, "← →"), " next")),
    queue.items.length ? null : h("div.c-empty", {}, h("h2", {}, "Nothing to review"), h("p", {}, "When a weak rule (a tag) matches a file, it waits here for a yes or no.")),
    ...[...groups.entries()].map(([cid, g]) => h("section.st-card", { "aria-label": g.name },
      h("div.st-card-head", {}, h("div", {}, h("h2", {}, h("button.link", { type: "button", onclick: () => ctx.show("character", cid) }, g.name), ` · ${g.items.length.toLocaleString()}`),
        h("p.st-none", {}, "A weak rule (a tag) matched these: is it them?")),
        h("div.row", {},
          h("button.btn.small", { type: "button", onclick: () => decideAll(cid, g, "confirm") }, `All are ${g.name}`),
          h("button.btn.small", { type: "button", onclick: () => decideAll(cid, g, "reject") }, `None are ${g.name}`))),
      reviewGrid(g.items, ctx, () => { ctx.refresh(); renderReview(root, ctx); }, { single: true }))),
  ].filter(Boolean));
  async function decideAll(cid, g, verdict) {
    if (!await ask(verdict === "confirm" ? `Are all ${g.items.length} ${g.name}?` : `Are none of these ${g.name}?`,
      verdict === "confirm" ? `They'll count as ${g.name} from now on.` : `They won't be suggested for ${g.name} again.`, { ok: verdict === "confirm" ? `Yes, all ${g.name}` : `None are ${g.name}` })) return;
    await api(`/characters/${cid}/review`, { method: "POST", body: { files: g.items.map((i) => i.file_id), verdict } }).catch((e) => toast(e.message, true));
    ctx.refresh();
    renderReview(root, ctx);
  }
}

function reviewGrid(items, ctx, after, { single = false } = {}) {
  const grid = h("div.r-grid", { role: "list" });
  const decide = async (it, card, verdict) => {
    card.classList.add(verdict === "confirm" ? "yes" : "no");
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      await api(`/characters/${it.character_id}/review`, { method: "POST", body: { files: [it.file_id], verdict } });
      const next = card.nextElementSibling || card.previousElementSibling;
      setTimeout(() => { card.remove(); next?.focus(); if (!grid.children.length) after(); else ctx.refresh(); }, 180);
    } catch (e) {
      toast(e.message, true);
      card.classList.remove("yes", "no");
    }
  };
  grid.replaceChildren(...items.map((it) => {
    const card = h("div.r-card", { role: "listitem", tabIndex: 0, "aria-label": `${it.name}: is ${it.character} in it? Y or N` });
    card.append(...[
      h("button.r-thumb", { type: "button", title: "Look closer", onclick: () => preview(it) }, thumb(it.file_id, it.name)),
      single ? null : h("div.r-q", {}, h("span.r-who", {}, it.character)),
      h("div.r-acts", {},
        h("button.btn.small.yes", { type: "button", onclick: () => decide(it, card, "confirm") }, h("kbd", {}, "Y"), it.character),
        h("button.btn.small.no", { type: "button", onclick: () => decide(it, card, "reject") }, h("kbd", {}, "N"), `Not ${it.character}`))].filter(Boolean));
    card.addEventListener("keydown", (e) => {
      const k = e.key.toLowerCase();
      if (k === "y") { e.preventDefault(); decide(it, card, "confirm"); }
      else if (k === "n") { e.preventDefault(); decide(it, card, "reject"); }
      else if (k === " " || k === "enter") { if (e.target === card) { e.preventDefault(); preview(it); } }
      else if (e.key === "ArrowRight") { e.preventDefault(); card.nextElementSibling?.focus(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); card.previousElementSibling?.focus(); }
      else return;
      e.stopPropagation();
    });
    return card;
  }));
  return grid;
}
