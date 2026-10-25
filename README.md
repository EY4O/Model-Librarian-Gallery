<p align="center"><img src="docs/images/icon.png" width="112" alt="Model Librarian Gallery"></p>

<h1 align="center">Model Librarian Gallery</h1>

<p align="center"><b>Browse every image and video you've generated, and see what made each one.</b></p>

<p align="center">
  <a href="https://github.com/EY4O/Model-Librarian-Gallery/blob/main/LICENSE"><img src="https://img.shields.io/github/license/EY4O/Model-Librarian-Gallery?color=blue" alt="License"></a>
  <a href="https://github.com/EY4O/Model-Librarian-Gallery/commits/main"><img src="https://img.shields.io/github/last-commit/EY4O/Model-Librarian-Gallery?color=green&logo=github" alt="Last commit"></a>
  <a href="https://ko-fi.com/looneth"><img src="https://img.shields.io/badge/Ko--fi-Support-FF813F?logo=ko-fi&logoColor=white" alt="Support on Ko-fi"></a>
</p>

A Windows app for large libraries of AI generations: it indexes the folders you choose where they are (nothing is copied), reads the prompt, settings, models and workflow saved in each file, and lets you search, rate, tag, sort and count them, in a fast grid grouped by day. It works on its own, and works better next to [Model Librarian](https://github.com/EY4O/ComfyUI-Model-Librarian) and ComfyUI.

![The library: a day-by-day grid of generations, with search and filters](docs/images/hero.png)

---

## ✨ Key Features

- ⚡ **Fast at any size** — a virtual grid that scrolls the same at 200 files or 250,000; searches and filters answer in a fraction of a second.
- 🖼️ **Grid or Waterfall** — square tiles or each picture at its own shape, a zoom bar (Ctrl + wheel), names and an info line under the tiles, and a viewer where the picture fills the stage: zoom, pan, actual size.
- ✂️ **Light editing** — flip, rotate and crop in the viewer; the prompt and workflow the file carries are kept, Save as copy keeps the original, or replace it (undoable).
- 🧬 **What made it** — the prompt, settings, checkpoint, LoRAs with their strengths and the ComfyUI workflow of every image and video (ComfyUI, A1111 / Forge, NovelAI, InvokeAI and more), read by Model Librarian's own readers.
- 🔍 **Search that knows generations** — words in prompts and names, plus `model:`, `lora:`, `tag:`, `character:`, `color:`, `rating:4`, `is:fav`, `seed:`, `after:` and more, suggested as you type.
- 🎨 **Colour sets** — each picture's main colours as swatches, like Eagle's: hover for the value and share, click to copy it or find every picture with that colour (`color:#140d17`, `color:red`).
- 📊 **Statistics** — what you made with over time (by model family, in Model Librarian's colours), the colours you make most, your most used checkpoints, LoRAs, prompt parts and settings, and the models you have but never use.
- 👤 **Characters** — a name plus its LoRA, trigger words or tags finds every image it's in; anything uncertain waits in **To review** for a yes or no.
- 🗂️ **Organise with Undo** — ratings, favourites, tags, albums, moving and deleting many files at once; every change is in **History** and Ctrl+Z undoes it. Deleting goes to the Gallery's own Trash first.
- 🎨 **Your sidebar** — give any folder or album an icon (or a number, or an emoji) and a colour, from its right-click menu.
- ♻️ **Duplicates** — exact copies, and the same picture saved twice in another format or size.
- 🔗 **Model Librarian and ComfyUI** — **Open in ComfyUI** opens a file's workflow in a new ComfyUI tab, **Save as recipe** puts it in Model Librarian's recipes, and the **Gallery Info** node tags images as they're made.
- 🦅 **Leaving Eagle** — bring an Eagle library in as plain files, laid out by its folders, into a library of its own or this one, with its tags, ratings, notes, links and colours (Eagle's library is only read); or send any selection to Eagle.
- 🔄 **Updates when you want them** — turn on **Updates** in Settings and the portable Gallery asks GitHub once a day for a new version, then installs it at its next start.
- 💼 **A library you can take anywhere** — make any folder a library and its index, thumbnails and backups live inside it: copy it to an external drive and open it on any PC, whatever drive letter it gets.
- 🛡️ **Your files stay yours** — indexing only reads; nothing leaves your PC.

## 📸 Screenshots

| | |
|---|---|
| ![The viewer](docs/images/viewer.png) **Viewer** — the file full size, with its prompt, settings, models, workflow, tags and characters beside it | ![Statistics](docs/images/stats.png) **Statistics** — what you made with over time, and your most used models, LoRAs and settings |
| ![A character](docs/images/character.png) **Characters** — how one is recognised, its LoRA strengths, what it's made with | ![Selection](docs/images/selection.png) **Organise** — select any number of files and rate, tag, file or move them at once |

<sub>Screenshots use generated sample data and made-up model names.</sub>

---

## 📦 Installation

### Option 1: **Download the portable folder** (Recommended)

1. From the [latest release](https://github.com/EY4O/Model-Librarian-Gallery/releases/latest), download **Model-Librarian-Gallery-&lt;version&gt;-portable.zip** (about 100 MB; it brings its own Python and ffmpeg).
2. Unzip it somewhere outside OneDrive, such as `%LOCALAPPDATA%\Programs`.
3. Start **Model Librarian Gallery.exe** in the unzipped folder. The program isn't signed, so Windows may ask first: choose **More info → Run anyway**.

Then use **Settings → Add the Gallery to the Start menu**, or right-click its taskbar button and choose **Pin to taskbar**. To keep it up to date, turn on **Settings → Updates**.

### Option 2: **Build the portable folder**

Build it once from a checkout. It copies a standalone Python 3.12 and ffmpeg from your PC; only the app's packages are fetched, if uv doesn't have them yet:

```bash
git clone https://github.com/EY4O/Model-Librarian-Gallery.git
cd Model-Librarian-Gallery
uv run --python 3.12 --no-project tools/build_portable.py
```

It lands in `%LOCALAPPDATA%\Programs\Model Librarian Gallery`. Start **Model Librarian Gallery.exe** there, then use **Settings → Add the Gallery to the Start menu**. To keep it up to date, turn on **Settings → Updates**.

### Option 3: **Run from the checkout**

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/), which fetches the right Python by itself:

```bash
git clone https://github.com/EY4O/Model-Librarian-Gallery.git
cd Model-Librarian-Gallery
run.bat
```

The first start sets up the app's own Python in `.venv`; later starts open straight away. `run.bat --browser` opens it in your browser instead of its own window.

<details>
<summary><b>Optional companions</b></summary>

- **ffmpeg** — for video posters and lengths. The portable folder (downloaded or built) brings its own; otherwise the one on PATH is used, or set its path in **Settings**.
- **Model Librarian** — found by itself in your models folder (`.sm_model_fetcher`). It links your files to the models you have (and shows the ones you never use), and receives **Save as recipe**.
- **ComfyUI with Model Librarian 1.23 or newer** — for **Open in ComfyUI**; set ComfyUI's address in **Settings** if it isn't `http://127.0.0.1:8188`.
</details>

## 🚀 Getting Started

1. Start the Gallery and click **Add a folder** (or **+** at the top of the sidebar). Pick a folder of generations, such as ComfyUI's `output` folder.
2. Your files appear as they're indexed, newest first, grouped by day; prompts and settings are read in the background.
3. Click a tile to open it and see what made it. **←** **→** step through, **1**–**5** rate, **F** favourites, **I** shows or hides the details, **Delete** moves it to the Trash, **Esc** goes back.
4. Search from the top: `lora:` or `model:` followed by a name narrows it down (names are suggested), `rating:4` and `is:fav` find your best.
5. Open **Statistics** to see what you make with, and **Characters** to have the Gallery find a character everywhere.

---

## 📖 How It Works

<details>
<summary><b>📁 Folders, the index and thumbnails</b></summary>

- The Gallery indexes the folders you add where they are. A rescan only looks at files that changed; a folder watcher picks up new generations within seconds.
- Each file gets a quick fingerprint (its size and first and last 64 KB), so a file you move or rename keeps its ratings, tags and characters. A drive that isn't plugged in keeps its files, shown again when it's back.
- Thumbnails are small WebP files kept by fingerprint, made in the background, newest first. Videos get a poster frame from ffmpeg.
- The index is one SQLite file, backed up daily (the last 7 kept). Your files' own data can always be read again from them; your ratings, tags, albums and characters live in the index. This PC's library keeps it in `%LOCALAPPDATA%\Model Librarian Gallery`.
- Hidden and system folders, and Eagle libraries (`*.library`), are never indexed.
</details>

<details>
<summary><b>🦅 Leaving Eagle</b></summary>

- **Settings → Eagle → Bring in an Eagle library** reads its `.library` folder (only reads: nothing in it changes) and asks where it goes: into a new library of its own, in a folder you pick, or into the library that's open.
- Each item is copied out, checked against the original, laid out by its Eagle folder (`Characters\Aurelia\…`; an item in several goes into the first, items in none into `Unfiled`), under the name Eagle shows, keeping the date it was added to Eagle.
- Its tags, star rating and note come along; its source link goes on a line of its own at the end of the note; every Eagle folder becomes an album too; Eagle's colours become its colour set. A file the library already has (the same bytes) just gets the marks, without a second copy.
- It's one step in History: Undo takes the marks off and moves the copies to the Gallery's Trash. Once you're happy, delete the Eagle library yourself.
</details>

<details>
<summary><b>🎨 Colour sets</b></summary>

- Every picture gets a colour set: its main colours and how much of it each covers, worked out from its thumbnail in the background (pictures from Eagle start with Eagle's).
- The viewer shows them as swatches under the file's name; hover for the value and share, click to find every picture with that colour or its family, or to copy its value.
- `color:#140d17` finds pictures with a colour that looks like it (covering at least 5%); `color:red` (or orange, yellow, green, cyan, blue, purple, pink, brown, black, gray, white) a colour of that family covering at least 10%.
- Statistics shows the colours of what you made, over time.
</details>

<details>
<summary><b>💼 Libraries you can take anywhere</b></summary>

- **Settings → Library → New library** makes any folder a library: everything in it is indexed, and the Gallery keeps the index, thumbnails and backups in a hidden `.mlgallery` folder inside it. Your files stay ordinary files in ordinary folders.
- Copy or move the whole folder to an external drive or another PC and open it there (**Open library**): its folders follow it to the new place or drive letter, with their ratings, tags, albums, characters, notes and History.
- A new library can bring the ratings, tags, albums, characters and notes the same files have in the library you're in, matched by their bytes. It's one step in History, so Undo takes it back.
- The Gallery opens the library you used last; if its drive isn't plugged in, this PC's library opens instead and says so. The portable Gallery kept inside a library's folder opens that library.
- Settings such as ComfyUI's address, ffmpeg and Updates belong to each PC and stay on it. Folders added from outside a library keep their full path, so another PC shows them as not reachable.
</details>

<details>
<summary><b>🧬 What's read from each file</b></summary>

The readers are Model Librarian's, kept identical by a test: ComfyUI's `prompt` and `workflow` (PNG, WebP, and videos from Save Video and Video Helper Suite), A1111 / Forge `parameters` (with LoRA tags and hashes), NovelAI, InvokeAI, SwarmUI and Fooocus. The workflow is stored compressed and unpacked only when you open it; prompts are split into their parts for statistics.
</details>

<details>
<summary><b>🏷️ The Gallery Info node</b></summary>

A node in Model Librarian's pack: put **Gallery Info (Model Librarian)** before your save node and fill in albums, tags, characters, a project, a rating or a note. Every save node writes it into the file with the workflow, and the Gallery files the image under it straight away: albums and tags are added, characters count as confirmed, and the rating applies unless you've set your own. Its folder pattern (`{project}/{character}/{date}/`) can also save each file straight into its folder.
</details>

<details>
<summary><b>👤 Characters</b></summary>

A character is a name, aliases, and rules: a **LoRA** (any version or strength) or **trigger words** are strong and count straight away; a **tag** is weak and sends matches to **To review**. Your yes and no stick: a file you rejected is never suggested again, and a weak rule you mostly reject goes quiet. Names from a workflow's Gallery Info node count as confirmed. Each character has a page with its LoRA strengths, the checkpoints it's made with, and its files by month.
</details>

<details>
<summary><b>📊 Statistics and model families</b></summary>

Statistics count the files made in a range (7 days to all time): what they were made with over time, by model family (Illustrious, Qwen, Flux, ...), your top checkpoints, LoRAs with their average strength, prompt parts, samplers, schedulers, steps, CFG and sizes, and storage per folder and drive. A model's family comes from where Model Librarian's organizer filed it (including folders of your own), the CivitAI base model next to it, or its name. With Model Librarian's library found, **Never used** lists the models no file was made with. Every bar opens those files.
</details>

<details>
<summary><b>🛡️ Safety</b></summary>

- Indexing only reads. Files are moved or deleted only when you ask, and everything you change is in **History** with **Undo** (Ctrl+Z).
- A move shows what will happen first and only goes into your library folders. Across drives it copies, checks the copy's size and fingerprint, then removes the original.
- **Delete** moves a file to the Gallery's Trash (a hidden `.mlg-trash` folder in its own library folder), from where **Put back** restores it. Emptying the Trash sends files to the Windows Recycle Bin.
- The Gallery listens on 127.0.0.1 only, with a new token each start; other programs and web pages can't use it. It sends nothing anywhere: Open in ComfyUI talks only to the ComfyUI address you set.
- **Updates** are off until you turn them on. Checking only reads GitHub's list of releases. An update is checked against its SHA-256 before it's unpacked beside the app, and the Gallery swaps it in at its next start, while it's closed.
</details>

---

## 🧪 Development

Tests, performance timings, the portable build and releasing are described in **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**. The plan is in **[docs/PLAN.md](docs/PLAN.md)**.

## 🤝 Contributing

Issues and pull requests are welcome; please run the test suite before opening a PR.

## 🙏 Credits

- [pywebview](https://pywebview.flowrl.com/) — the app's own window (Edge WebView2)
- [aiohttp](https://docs.aiohttp.org/), [Pillow](https://python-pillow.org/), [watchdog](https://github.com/gorakhargosh/watchdog), [Send2Trash](https://github.com/arsenetar/send2trash)
- [FFmpeg](https://ffmpeg.org/) — video posters and lengths
- [Model Librarian](https://github.com/EY4O/ComfyUI-Model-Librarian) — the metadata readers, model families and the Gallery Info node

## ☕ Support

If the Gallery helps you find your way around your generations, you can support its development:

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/looneth)

## 📄 License

[MIT](LICENSE)
