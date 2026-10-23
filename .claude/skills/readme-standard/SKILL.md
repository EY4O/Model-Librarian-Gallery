---
name: readme-standard
description: The user's house style for project READMEs — centered icon/title/tagline and badge row, hero screenshot, emoji Key Features, screenshot grid, Option 1/2/3 installation, Getting Started steps, collapsible "How It Works" details, Development/Contributing/Credits/Ko-fi Support/License footer. Use this skill whenever writing, rewriting, restyling, reviewing or "cleaning up" a README.md for any of the user's projects, when creating a new repository or project scaffold, when preparing a project for GitHub, a package registry (Comfy Registry, PyPI, npm) or a public release, or when the user says a README is too long, too plain, or should look like the standard — even if they don't mention the word "standard".
---

# README standard

The user wants every project README to look and read like the **Model Librarian** README (https://github.com/EY4O/comfyui-model-librarian — a copy is in `assets/example-README.md`). It was modeled on LoRA Manager's README: scannable at a glance, visual, and short on the surface with depth tucked away. People decide in seconds whether a project is for them, so the top of the README sells and orients; everything a reader needs only once (internals, edge cases, safety details) folds away instead of making the page long.

## Workflow

1. **Learn the project first.** Read the code, existing README, `pyproject.toml`/`package.json`, and any docs. The README must describe what the project actually does today — never invent features, commands, package names, badges or URLs. If something the standard calls for doesn't exist (no registry listing yet, no screenshots), leave it out or say so, rather than faking it.
   - **Check that features are implemented**, not just promised in docstrings or plans. Stubbed or unfinished functionality goes under a short `## 🗺️ Roadmap` (or a `> **🚧 Work in progress**` note under the pitch), never in Key Features — and tell the user what you found.
2. **Start from `assets/README.template.md`.** Fill every section; delete sections that genuinely don't apply.
   - **No UI (CLI, library, service):** the hero is a short terminal example (command + the result it produces, e.g. the folder tree or output), and `## 📸 Screenshots` becomes `## 💻 Examples` with 2–3 more real invocations — or is dropped if one example says it all.
   - **No icon yet:** offer to make one (`references/screenshots.md` → Icon); until it exists, leave the `<img>` line out rather than pointing at a missing file.
3. **Move developer material out.** Tests, build/release steps, project layout and publishing go in `docs/DEVELOPMENT.md` (template: `assets/DEVELOPMENT.template.md`); the README just links to it.
4. **Screenshots.** Real screenshots of the real UI. If you need to produce them yourself, follow `references/screenshots.md` (headless Edge/Chrome against the real frontend with sample data). Label sample data as such.
5. **Run the checklist** at the bottom before finishing.

## Structure (in this order)

1. **Header, centered** — project icon (`docs/images/icon.png`, ~112px), `<h1>` title, one bold tagline (a single sentence of benefit, not a feature list), then a badge row: license, last commit, the package/registry badge if published, and the Ko-fi badge.
2. **Pitch** — one paragraph: what it is, who it's for, what it works with, and where to open/run it. Then the hero screenshot.
3. **Callout** — a `> **⚠ …**` blockquote for anything that blocks first use (API keys, required companion tools, hardware). Skip if there's nothing.
4. **`## ✨ Key Features`** — 6–9 bullets, each `- <emoji> **Name** — one sentence.` Lead with the user's benefit. One emoji per bullet, chosen to match the feature.
5. **`## 📸 Screenshots`** — a 2×2 table of images, each with a bold caption and a short dash description; `<sub>Screenshots use sample data.</sub>` when true.
6. **`## 📦 Installation`** — `### Option 1: **…** (Recommended)`, `### Option 2`, `### Option 3: **Manual Installation**` with copy-pasteable code blocks. Platform-specific setup goes in a `<details>` block underneath.
7. **`## 🚀 Getting Started`** — 4–6 numbered steps from "installed" to "first useful result", naming the actual buttons/commands.
8. **`## 📖 How It Works`** — one `<details><summary><b>emoji Topic</b></summary>` block per topic (integrations, algorithms, safety, data storage…). This is where depth lives; it may be as thorough as needed because it's collapsed.
9. **Footer** — `## 🧪 Development` (one line linking `docs/DEVELOPMENT.md`), `## 🤝 Contributing`, `## 🙏 Credits` (projects/APIs it builds on, linked), `## ☕ Support`, `## 📄 License`. Separate major groups with `---`.

## Support section (always included)

The user wants Ko-fi support on every project:

- Badge in the header row: `<a href="https://ko-fi.com/looneth"><img src="https://img.shields.io/badge/Ko--fi-Support-FF813F?logo=ko-fi&logoColor=white" alt="Support on Ko-fi"></a>`
- Section before License:
  ```markdown
  ## ☕ Support

  If <Project> <helps you in a project-specific way>, you can support its development:

  [![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/looneth)
  ```
- Apps with a UI can also carry a Ko-fi button (brand orange `#FF813F`, "☕ Ko-fi") in their header — offer it, don't add it unasked.

## Voice

- Plain, friendly, concrete. Short sentences; no marketing superlatives ("revolutionary", "ultimate").
- Name real UI labels in **bold** (`**Settings → Storage tiers**`) and real commands in code blocks.
- Explain *why* where it helps a reader trust the tool (e.g. "verified before the original is removed").
- The visible part of the README should fit in a few screens; if a section grows past ~8 lines, it probably belongs in a `<details>` block or in `docs/`.

## Badges

Use shields.io and only for things that exist: `github/license`, `github/last-commit`, package/registry (e.g. `https://img.shields.io/badge/Comfy%20Registry-<node-id>-6c47ff` linking to the registry page), Ko-fi. Don't add release/download/star badges for a project with no releases or traction yet — empty badges look worse than none.

## Checklist

- [ ] Every feature, command, package name and URL is real and current.
- [ ] Header: icon, title, tagline, badges (incl. Ko-fi) — centered.
- [ ] Hero screenshot near the top; screenshot grid captions describe what each view does.
- [ ] Install options are copy-pasteable and tested in order of recommendation.
- [ ] Getting Started gets a new user to a first result in ≤6 steps.
- [ ] Long explanations are in `<details>` blocks; dev material is in `docs/DEVELOPMENT.md`.
- [ ] Credits link the projects it builds on; License matches the LICENSE file.
- [ ] Image paths are relative (`docs/images/...`) so they render on GitHub and in forks.
- [ ] No personal paths, emails or secrets in examples (use placeholders like `C:/StabilityMatrix/...`).

## Files in this skill

- `assets/README.template.md` — the skeleton to fill in.
- `assets/DEVELOPMENT.template.md` — developer doc skeleton (layout, install for dev, tests, releasing).
- `assets/example-README.md` — the reference implementation (Model Librarian).
- `references/screenshots.md` — how to produce consistent screenshots and an icon when the project has none.

A GitHub template repository with this scaffold also exists: https://github.com/EY4O/project-template — suggest "Use this template" when the user starts a brand-new project on GitHub.
