// Smart folders: Edit Rules, as Eagle's. A name, "any / all of the following are true / false", rule rows (a field, how
// it compares, a value) with − and + to take one out or add one after it, groups of rules inside (the top's + adds
// one), and how many pictures they find as they're changed. Saved as a smart album whose search the rules come to
// (rules.py), so it keeps collecting what matches.
import { albumName, api, h, icon, plural } from "./util.js";

const NO_VALUE = new Set(["empty", "notempty", "any", "none", "unrated", "rated", "yes", "no"]);
let FIELDS = null;
const fields = async () => FIELDS || (FIELDS = (await api("/smart/fields")).fields);

export const newRule = (field = "name", op = "contains", value = "") => ({ field, op, value });
const newGroup = () => ({ match: "all", is: true, rules: [newRule()] });

// {album (to change; else a new one), name, rules, albums (for the Albums rule's names), done(album)}
export async function editRules({ album = null, name = "", rules = null, albums = [], done = () => {} } = {}) {
  const list = await fields();
  const byKey = new Map(list.map((f) => [f.field, f]));
  const root = structuredClone(rules || album?.rules || { match: "all", is: true, rules: [newRule()] });
  const lists = new Map();  // suggestions for a field (a datalist each)

  const nameIn = h("input", { type: "text", value: album ? album.name : name, "aria-label": "Smart Folder Name", placeholder: "Ink's T2I", autocomplete: "off", maxLength: 100 });
  const found = h("span.sr-found", { "aria-live": "polite" });
  const err = h("p.form-err", { role: "alert" });
  const body = h("div.sr-rules");
  const go = h("button.btn.primary", { type: "submit" }, album ? "Save Changes" : "Create");

  const datalist = (f) => {
    const id = `sr-list-${f.field}`;
    if (!lists.has(id)) {
      const dl = h("datalist", { id });
      if (f.suggest_from === "album") dl.append(...albums.map((a) => h("option", { value: albumName(a.name) })));
      else if (f.suggest) dl.append(...f.suggest.map((v) => h("option", { value: v })));
      lists.set(id, dl);
      dlg.append(dl);
    }
    return id;
  };
  const suggest = async (f, input) => {  // (tags, models and the rest: the names most used, as they're typed)
    if (!f.suggest_from || f.suggest_from === "album") return;
    const { items } = await api(`/suggest?field=${f.suggest_from}&q=${encodeURIComponent(input.value)}`).catch(() => ({ items: [] }));
    document.getElementById(input.getAttribute("list"))?.replaceChildren(...items.map((it) => h("option", { value: it.name })));
  };

  function valueBox(r, f) {
    if (NO_VALUE.has(r.op)) return null;
    const set = (v) => { r.value = v; recount(); };
    const between = r.op === "between";
    if (between && !Array.isArray(r.value)) r.value = ["", ""];
    if (!between && Array.isArray(r.value)) r.value = "";
    if (f.kind === "choice") {
      if (!f.options.some(([v]) => v === r.value)) r.value = f.options[f.field === "rating" ? 3 : 0][0];
      return h("select.sr-value", { "aria-label": `${f.label}: which`, onchange: (e) => set(e.target.value) },
        f.options.map(([v, label]) => h("option", { value: v, selected: v === r.value }, label)));
    }
    const kind = f.kind === "date" && r.op === "last" ? "days" : f.kind;
    const input = (val, label, onset) => {
      const el = h("input.sr-value", { type: { number: "number", days: "number", date: "date" }[kind] || "text", value: val ?? "", "aria-label": label,
        step: kind === "number" ? "any" : null, min: kind === "days" ? "1" : null, autocomplete: "off",
        placeholder: f.kind === "text" && r.op === "regex" ? "^ink-\\d+" : f.field === "color" ? "red, #3a7bd5" : "",
        oninput: (e) => { onset(e.target.value); if (f.suggest_from) suggest(f, e.target); } });
      if (f.suggest_from || f.suggest || f.field === "color") el.setAttribute("list", datalist(f.field === "color" ? { ...f, suggest: COLOURS } : f));
      return el;
    };
    if (between) {
      return h("span.sr-between", {}, input(r.value[0], `${f.label}: from`, (v) => set([v, r.value[1]])), h("span.sr-and", {}, "and"),
        input(r.value[1], `${f.label}: to`, (v) => set([r.value[0], v])), f.unit ? h("span.sr-unit", {}, f.unit) : null);
    }
    const one = input(r.value, `${f.label}: value`, set);
    if (kind === "days") return h("span.sr-between", {}, one, h("span.sr-unit", {}, "days"));
    if (f.field === "color") {
      const pick = h("input.sr-swatch", { type: "color", "aria-label": "Pick a colour", value: /^#[0-9a-f]{6}$/i.test(r.value) ? r.value : "#3a7bd5",
        oninput: (e) => { one.value = e.target.value; set(e.target.value); } });
      return h("span.sr-between", {}, one, pick);
    }
    return f.unit ? h("span.sr-between", {}, one, h("span.sr-unit", {}, f.unit)) : one;
  }

  function ruleRow(r, g, i) {
    const f = byKey.get(r.field) || list[0];
    const sections = [...new Set(list.map((x) => x.section))];
    const fieldSel = h("select.sr-field", { "aria-label": "Field", onchange: (e) => {
      const nf = byKey.get(e.target.value);
      g.rules[i] = { field: nf.field, op: nf.ops[0][0], value: nf.kind === f.kind && !Array.isArray(r.value) && nf.kind !== "choice" ? r.value : "" };
      draw();
    } }, sections.map((s) => h("optgroup", { label: s === "Eagle" ? "As in Eagle" : "The Gallery's own" },
      list.filter((x) => x.section === s).map((x) => h("option", { value: x.field, selected: x.field === f.field }, x.label)))));
    const opSel = h("select.sr-op", { "aria-label": "How it compares", onchange: (e) => { r.op = e.target.value; draw(); } },
      f.ops.map(([v, label]) => h("option", { value: v, selected: v === r.op }, label)));
    const only = g === root && g.rules.length === 1;
    const row = h("div.sr-row", {}, fieldSel, opSel, valueBox(r, f) || h("span.sr-value.none"),
      h("span.sr-btns", {},
        h("button.icon-btn.small.sr-btn", { type: "button", "aria-label": "Remove this rule", title: "Remove this rule", disabled: only,
          onclick: () => { g.rules.splice(i, 1); if (!g.rules.length) dropGroup(g); draw(); } }, icon("minus")),
        h("button.icon-btn.small.sr-btn", { type: "button", "aria-label": "Add a rule after this one", title: "Add a rule",
          onclick: () => { g.rules.splice(i + 1, 0, newRule(f.field, f.ops[0][0])); draw(g.rules[i + 1]); } }, icon("plus"))));
    row.rule = r;
    return row;
  }

  // a group that's lost its last rule goes too (the top one stays, with an empty rule)
  function dropGroup(g, from = root) {
    if (g === root) { root.rules = [newRule()]; return true; }
    const at = from.rules.indexOf(g);
    if (at >= 0) { from.rules.splice(at, 1); if (!from.rules.length) dropGroup(from); return true; }
    return from.rules.some((x) => x.rules && dropGroup(g, x));
  }

  function groupBox(g, depth) {
    const top = g === root;
    const head = h("div.sr-head", {},
      h("select", { "aria-label": "Any or all", onchange: (e) => { g.match = e.target.value; recount(); } },
        h("option", { value: "any", selected: g.match === "any" }, "any"), h("option", { value: "all", selected: g.match !== "any" }, "all")),
      h("span.sr-text", {}, "of the following are"),
      h("select", { "aria-label": "True or false", onchange: (e) => { g.is = e.target.value === "true"; recount(); } },
        h("option", { value: "true", selected: g.is !== false }, "true"), h("option", { value: "false", selected: g.is === false }, "false")),
      h("span.spacer"),
      h("span.sr-btns", {},
        h("button.icon-btn.small.sr-btn", { type: "button", "aria-label": top ? "Remove the last group" : "Remove this group",
          title: top ? "Remove the last group of rules" : "Remove this group", disabled: top && !g.rules.some((x) => x.rules),
          onclick: () => {
            if (top) { const at = g.rules.map((x) => !!x.rules).lastIndexOf(true); if (at >= 0) g.rules.splice(at, 1); } else dropGroup(g);
            draw();
          } }, icon("minus")),
        h("button.icon-btn.small.sr-btn", { type: "button", "aria-label": "Add a group of rules", title: "Add a group of rules (its own any / all)",
          disabled: depth >= 5, onclick: () => { g.rules.push(newGroup()); draw(); } }, icon("plus"))));
    return h("div.sr-group" + (top ? ".top" : ""), { role: "group", "aria-label": top ? "Rules" : "A group of rules" }, head,
      h("div.sr-list", {}, g.rules.map((r, i) => (r.rules ? groupBox(r, depth + 1) : ruleRow(r, g, i)))));
  }

  function draw(added = null) {
    body.replaceChildren(groupBox(root, 0));
    if (added) [...body.querySelectorAll(".sr-row")].find((row) => row.rule === added)?.querySelector("input.sr-value, select.sr-value, select")?.focus();  // (ready to type)
    recount();
  }

  let timer = 0, asked = 0;
  function recount() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++asked;
      try {
        const r = await api("/smart/count", { method: "POST", body: { rules: root } });
        if (mine !== asked) return;
        found.textContent = `Found ${plural(r.n, "item")}`;
        found.title = `The search these rules make: ${r.query}`;
        found.classList.remove("dim");
      } catch (e) {
        if (mine !== asked) return;
        found.textContent = e.status === 422 ? e.message : "Couldn't count them just now.";
        found.title = "";
        found.classList.add("dim");
      }
    }, 220);
  }

  const dlg = h("dialog.dialog.wide.sr-dialog", { "aria-label": "Edit Rules" },
    h("form", { method: "dialog", onsubmit: async (e) => {
      e.preventDefault();
      const n = nameIn.value.trim();
      if (!n) { err.textContent = "Give it a name."; nameIn.focus(); return; }
      go.disabled = true;
      try {
        const saved = album ? await api(`/albums/${album.id}`, { method: "POST", body: { name: n, rules: root } })
          : await api("/albums", { method: "POST", body: { name: n, rules: root } });
        dlg.close();
        done(saved);
      } catch (ex) { err.textContent = ex.message; }
      go.disabled = false;
    } },
      h("h2", {}, "Edit Rules"),
      h("label.sr-name", {}, h("span", {}, "Smart Folder Name"), nameIn),
      h("p.note", {}, "A smart folder keeps collecting the pictures that meet its rules, as pictures come, go and change."),
      body, err,
      h("div.row.sr-foot", {}, found, h("span.spacer"), h("button.btn", { type: "button", onclick: () => dlg.close() }, "Cancel"), go)));
  dlg.addEventListener("keydown", (e) => { if (e.key !== "Escape") e.stopPropagation(); });
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  draw();
  dlg.showModal();
  nameIn.focus();
  if (!album && name) nameIn.select();
  return dlg;
}

const COLOURS = ["red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink", "brown", "black", "gray", "white"];
