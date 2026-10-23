<p align="center"><img src="docs/images/icon.png" width="112" alt="Model Librarian Gallery"></p>

<h1 align="center">Model Librarian Gallery</h1>

<p align="center"><b>Browse every image and video you've generated, and see what made each one.</b></p>

<p align="center">
  <a href="https://github.com/EY4O/Model-Librarian-Gallery/blob/main/LICENSE"><img src="https://img.shields.io/github/license/EY4O/Model-Librarian-Gallery?color=blue" alt="License"></a>
  <a href="https://github.com/EY4O/Model-Librarian-Gallery/commits/main"><img src="https://img.shields.io/github/last-commit/EY4O/Model-Librarian-Gallery?color=green&logo=github" alt="Last commit"></a>
  <a href="https://ko-fi.com/looneth"><img src="https://img.shields.io/badge/Ko--fi-Support-FF813F?logo=ko-fi&logoColor=white" alt="Support on Ko-fi"></a>
</p>

A Windows app for large libraries of AI generations. It indexes the folders you choose where they are (nothing is copied or changed), reads the prompt, settings, models and workflow saved in each file, and lets you search, rate, tag and browse them in a fast grid grouped by day.

> **🚧 Work in progress.** The library, the viewer, what made each file, search, ratings, favourites and tags work today. Statistics, characters and file organisation are next: see the [plan](docs/PLAN.md).

---

## 📦 Installation

Needs Windows 10 or 11 and [uv](https://docs.astral.sh/uv/getting-started/installation/) (which fetches the right Python by itself).

```bash
git clone https://github.com/EY4O/Model-Librarian-Gallery.git
cd Model-Librarian-Gallery
run.bat
```

The first start sets up the app's own Python in `.venv`; later starts open straight away.

## 🚀 Getting Started

1. Run **`run.bat`**.
2. Click **+** (top of the sidebar) and pick a folder of images and videos, such as ComfyUI's `output` folder.
3. Your files appear as they're indexed, newest first, grouped by day.
4. Click any tile to open it and see what made it; **←** and **→** step through, **1**–**5** rate, **F** favourites, **I** shows or hides the details, **Esc** goes back.
5. Search from the box at the top: words find prompts, file names, models and tags; `model:`, `lora:`, `tag:`, `rating:4`, `is:fav` and more narrow it down (they're suggested as you type).

---

## 🧪 Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## ☕ Support

If the Gallery helps you keep track of your generations, you can support its development:

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/looneth)

## 📄 License

[MIT](LICENSE)
