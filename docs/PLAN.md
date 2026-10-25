# Model Librarian Gallery — plan

A desktop app for your own images and videos: browse tens of thousands of generations quickly, see what made each one (prompt, settings, models, LoRAs, workflow), find things by any of it, and see statistics over time — which models, LoRAs and characters you actually use.

## Principles

- **Yours alone.** No runtime dependency on ComfyUI, Model Librarian, a browser extension or any outside service. It starts, indexes and shows your library on its own. A small, pinned set of well-known libraries, MIT-licensed code, no build toolchain to keep alive.
- **Works better next to Model Librarian, never needs it.** When Model Librarian's shared data folder is there, the Gallery reads it (read-only) to link images to the model files you have. When ComfyUI is running, **Open in ComfyUI** works. Neither is required.
- **Your files stay as they are.** Indexing never writes to your media. Moving or deleting happens only when you ask, shows what will happen first, and can be undone. Deleting goes to the Recycle Bin.
- **Nothing leaves the PC.** No telemetry, no accounts, no network use unless you turn on an optional lookup.
- **Fast at your scale.** Designed for 250,000 images and videos (your current numbers to confirm).

## What it does (version 1)

| Screen | What you get |
|---|---|
| **Library** | One grid for every folder you add (output folders, the archive drive): thumbnails, videos playing on hover, newest first or any sort. Smooth scrolling at 250k items. Group by day, folder, model or character. |
| **Search and filters** | Full-text search over prompts and negative prompts; filters for model, LoRA (and its strength), character, date range, size and aspect, sampler, seed, rating, tags, folder, image or video, with or without generation data. Saved searches. |
| **Detail view** | The image or video full size (zoom, step through with the arrow keys). Beside it: prompt and negative prompt (copy), settings, the checkpoint and LoRAs with strengths (linked to your files when Model Librarian knows them), and the workflow. **Open in ComfyUI**, **Save as recipe**, **Show in Explorer**, **Copy seed**. |
| **Statistics** | Images per day and week; most used checkpoints and LoRAs over time (and the ones you have but never use, with Model Librarian's library); characters; top prompt tags; samplers, schedulers, steps and sizes you use; storage per folder and drive. Pick a time range; click any bar to see those images. |
| **Characters** | Your characters, each defined by its LoRA files, trigger words and prompt tags (plus aliases). Images are matched to characters automatically; uncertain ones wait in a review list. Each character has a page: its images, the LoRAs and strengths used, over time. |
| **Organize** | Ratings, favorites, your own tags and albums. Bulk actions on a selection: tag, add to album, move to another folder or drive, delete to the Recycle Bin. Each shows a preview and can be undone. Duplicates (same file, or the same picture saved twice) are found and listed. |
| **Settings** | Folders to watch, the archive drive, thumbnail cache location and size, Model Librarian's data folder (found automatically), ComfyUI address (for Open in ComfyUI). |

**Not in version 1:** face recognition, AI captioning or semantic ("looks like") search, editing images, sharing, phone access. All can come later and would run locally.

## How it fits with Model Librarian

| Model Librarian has | The Gallery uses it like this |
|---|---|
| Readers for PNG / JPEG / WebP metadata, ComfyUI videos, A1111 / CivitAI / NovelAI / InvokeAI / Fooocus / SwarmUI text (`recipe_parse`, `imagemeta`, `videometa`, no ComfyUI imports) | A copy, kept identical by a test (as the extension does with `fit.js`), so the Gallery never imports Model Librarian at run time |
| `registry.json`, `hash_cache.json`, `usage.json` in its shared data folder | Read, if present: which model file a LoRA or checkpoint name or hash is, its base model, CivitAI page |
| The recipe store (`recipes/`) | **Save as recipe** writes a recipe there, in the same format; Model Librarian picks it up |
| Archive (prompt families, thumbnails that carry their workflow, moving originals with undo) | The same ideas and move rules, so the two never disagree about a moved file |

## The Gallery Info node (sorting at generation time)

A node in Model Librarian's pack that you add to a workflow, just before its save node. It tags each image or video as it's made, so it arrives in the Gallery already sorted. The Gallery doesn't need it: without the node, everything works as described above.

**What you fill in** (all optional): albums, tags, characters, project, rating, favourite, a note. Any field can also be wired from another node, such as a wildcard or a prompt picker, so the value matches what was actually generated. Several values are separated by commas.

**What it adds on its own**, read from the run as it happens:

- the checkpoint and LoRAs that actually ran, with strengths and hashes (from Model Librarian's hash cache), so statistics survive renamed files;
- the open workflow's name (filled in when you queue; an unsaved workflow has none);
- the node and Model Librarian version.

**How it gets into the file.** ComfyUI gives every save node a shared metadata dictionary, and each one writes all of it into the file:

- Save Image writes PNG text chunks;
- Save Video and Save WebM write container tags;
- Video Combine (Video Helper Suite) writes both the PNG and the video metadata.

The node is a pass-through: image in, the same image out. When it runs, it adds one `mlgallery` entry to that dictionary, so the save node after it stores the entry with everything else, whichever save node it is. It needs no save node of its own.

```json
{ "v": 1,
  "albums": ["Lighthouses"], "tags": ["dusk", "paper cut"], "characters": ["Aurelia"],
  "project": "Coastline", "rating": 4, "favourite": false, "note": "",
  "models": { "checkpoint": [{ "name": "krea2_turbo_int4.safetensors", "sha256": "…" }],
              "loras": [{ "name": "aurelia_v3.safetensors", "model": 0.65, "clip": 0.65, "sha256": "…" }] },
  "source": { "ml": "1.22.0", "workflow": "lighthouse.json" } }
```

**Pre-sorting into folders.** The node also outputs a `filename_prefix` built from a pattern, e.g. `{project}/{character}/{date}/`. Wired into the save node's prefix, it saves each file straight into its folder, so nothing needs moving afterwards. Folder names are cleaned (no `..`, no drive letters, no characters Windows refuses).

**What the Gallery does with it**:

- **Albums and tags** are added and marked "from the workflow". Removing one by hand sticks: re-indexing never puts it back.
- **Characters** named by the node count as confirmed. Rule matches still apply on top, and a name the Gallery doesn't know yet is offered as a new character.
- **Ratings and favourites** apply only if you haven't set your own.

**Fallback.** Values typed into the node's own widgets are also in the `prompt` chunk that ComfyUI saves with every image, under the node's id. The Gallery reads them from there when the `mlgallery` entry is missing. Two cases need that:

- the node was cached and skipped while the save node re-ran (for example, after changing only the filename prefix);
- the image was saved by a custom node that keeps only `prompt` and `workflow`.

Wired-in values are only in the `mlgallery` entry.

**Limits:** text fields are capped (note 2 KB, 64 tags, 16 albums and characters), so a stray wired string can't bloat every file.

**Where and when:** shipped in Model Librarian 1.22.0 (installed in every ComfyUI you use), alongside Gallery M1. The format is written down in both repos, and a test keeps the writer and the reader in step.

**Tested by:** unit tests for the entry, the pattern and the caps; a run on the 8199 test instance with Save Image, Save Video and Video Combine, reading the entry back from the saved files; the Gallery's import rules on generated sample files.

## How it's built

- **One Python program** (3.12, its own private Python, so ComfyUI updates can't break it) with a small web server on `127.0.0.1` only.
- **The interface** is plain JavaScript modules and CSS, like Model Librarian's: no build step and no npm dependencies to update. It opens in its own window (`pywebview`, which uses Edge WebView2, already part of Windows 11), or in your browser if you prefer.
- **The index** is a single SQLite database (WAL mode) with full-text search (FTS5). It's a file you can back up; it's rebuilt from your media if lost.
- **Thumbnails** are WebP, 256 and 512 px, in a cache folder (roughly 30–50 KB per image). Videos get a poster frame and a short hover preview, made with a bundled `ffmpeg.exe` (no install).
- **Background work** (scanning, thumbnails, reading metadata) runs in resumable jobs: closing the app or unplugging a drive mid-scan loses nothing, and it carries on where it stopped. A folder watcher picks up new generations within seconds.
- **Dependencies, pinned:** aiohttp, Pillow, watchdog, pywebview, send2trash. That's the whole list for version 1.

### What it stores

| Table | Holds |
|---|---|
| `files` | Path, folder, drive, size, modified time, quick fingerprint (size + first and last 64 KB), full SHA-256 later for duplicates, width, height, duration, kind, offline flag |
| `generations` | Per file: prompt, negative, seed, sampler, scheduler, steps, CFG, size, denoise, source app, the workflow (compressed), parsed at index time |
| `uses` | Per file: each model used (checkpoint, diffusion model, VAE, LoRA with strengths, text encoder, upscaler), by name and hash, linked to Model Librarian's registry when known |
| `prompt_tags` | The prompt split into tags (`1girl`, `blue hair`, weights removed), for filters and statistics |
| `characters`, `character_rules`, `file_characters` | Your characters, what identifies them, and the matches (automatic or confirmed by you) |
| `ratings`, `tags`, `albums`, `file_tags`, `album_files` | Your own organisation |
| `jobs`, `undo_log` | Resumable background work; every file operation and how to undo it |

A file that moves or is renamed keeps its ratings, tags and characters (matched by fingerprint). A drive that isn't plugged in keeps its records, shown greyed out.

## Characters

A character is a name plus rules, any of which marks an image:

- **LoRA files**: e.g. `tifa_lockhart_v2.safetensors` (any version or strength).
- **Trigger words**: e.g. `tifa lockhart` (taken from the LoRA's metadata when you add the LoRA, editable).
- **Prompt tags**: e.g. `tifa_lockhart`, `final fantasy vii`.

A LoRA alone is a strong match. A tag alone is weaker and waits in the review list until you confirm or reject it, and the Gallery learns from what you reject. Statistics count confirmed and strong matches.

## Safety

- Indexing only reads.
- A move shows a preview with the count, size and destination, and only moves into your library folders (so a file keeps its ratings, tags and characters). Moves across drives copy, check the size and fingerprint, then remove the original. Every operation is logged with **Undo** (Ctrl+Z, or History).
- Delete moves files to the Gallery's Trash, a hidden `.mlg-trash` folder in the file's own library folder, so it's instant and **Put back** restores it. Emptying the Trash sends the files to the Windows Recycle Bin.
- The database is backed up daily (the last 7 kept).
- It never asks for, stores or sends any keys.

## Performance targets

- **First index:** about 2,000 images a minute on the HDD, with the grid usable after the first minute (thumbnails fill in as you scroll).
- **New generations:** in the library within 5 seconds of being saved.
- **Search and filters:** results in under 200 ms at 250,000 files.
- **Scrolling:** smooth (60 fps) at any size, because only what's on screen exists in the page.

## Milestones

Each one is usable on its own and tested before the next starts. Tests use generated sample images and videos, never your outputs.

| | Delivers | Tested by |
|---|---|---|
| **M0 — Skeleton** ✅ | Private repo, launcher and window, settings, the database, folder scan with fingerprints, thumbnail cache, the plain grid | Unit tests for the scanner and fingerprints; start-to-grid check in headless Edge on a sample library |
| **M1 — Viewer** ✅ | Metadata reading (the shared readers, plus the Gallery Info entry), the detail view, videos, search and filters, ratings, favorites, tags, keyboard navigation, the folder watcher | Parser tests on Model Librarian's fixtures; search tests; a UI walk-through |
| **M2 — Statistics** ✅ | Model and LoRA usage over time, prompt tags, settings, storage; Model Librarian's library linked (never-used models) | Statistics computed on a known sample set |
| **M3 — Characters** ✅ | Characters, rules, automatic matching, review list, character pages and statistics | Matching tests (LoRA, trigger word, tag, rejections) |
| **M4 — Organize** ✅ | Albums, bulk actions, move and delete with preview and undo, duplicates, daily backups | Move and undo tests on throwaway folders, including an offline drive |
| **M5 — Polish** ✅ | Portable package with its own Python and ffmpeg, Start menu shortcut, first-run setup, Open in ComfyUI, Save as recipe, performance at 250k | A 250k-file synthetic library for timing |

## Eagle bridge

The Gallery indexes your folders where they are; Eagle keeps its own copy of each file in a library folder. They meet through a bridge instead of a shared format:

- **Bring in an Eagle library** (Settings, Eagle) only reads its `metadata.json` files, then asks where it goes: a new library of its own (every item copied there) or the open library (items whose bytes match a file it has get the marks; the rest are copied into a folder of it). Copies are laid out by Eagle folder (the first, for an item in several; `Unfiled` for one in none), named as Eagle shows them (made safe for Windows, a clash gets " (2)"), dated as added to Eagle, and each checked against the original. Every item brings its tags, its folders as albums ("Characters / Aurelia" for a nested one), its star rating, its note with the source link on a line of its own, and Eagle's colours. Your own ratings and notes win. The library is never changed. It's one step in History, so Undo takes it all back (the copies go to the Gallery's Trash).

## Colour sets

Each picture's main colours (up to 8) and the share of it each covers, like Eagle's: worked out from its thumbnail (median cut, sampled without smoothing, near-twins merged), in the background next to the picture fingerprint, and again when the file changes; taken from Eagle for its items. Kept per colour with its CIE Lab value, a family name and a cube of a coarse Lab grid (indexed), so `color:#hex` (within a Lab distance of 22, covering 5%) and `color:<family>` (10%) are indexed lookups: on a made-up index of 250,000 files with 8 random colours each, a page of results takes 65–210 ms. Swatches in the viewer (find, or copy the value); colours over time in Statistics.
- **Send to Eagle** (the selection's More menu) asks a running Eagle to add copies through its own API (`/api/item/addFromPaths`), into a folder you pick, with the Gallery's tags and characters, and the prompt and settings as the note. An API token is only needed if your Eagle asks for one.
- Eagle libraries (`*.library`) are still never indexed as folders.

## Libraries you can take anywhere

A library is a folder that carries its own index, so it can live on an external drive and open on any PC (a way to leave a container like Eagle's behind without one: the files stay ordinary files).

- **What's in it:** your files, as they are, and a hidden `.mlgallery` folder: `library.db`, `thumbs\`, `backups\` and `library.json` (its name, and where it was last opened). Settings, the update check, the window's browser data and the recent libraries belong to the PC and stay in `%LOCALAPPDATA%\Model Librarian Gallery`, with this PC's own library.
- **Moving it:** folder paths are stored in full. When a library opens somewhere new (another drive letter, another folder), the folders inside it are re-anchored to where it is now. Files, marks and History refer to folders by number, so nothing else changes. Folders added from outside the library keep their full path.
- **Opening one:** `--library DIR`, Settings (New library, Open library, the recent ones), or the portable Gallery kept inside a library's folder (the launcher passes its parent). Otherwise the last library opens, or this PC's library when that one isn't reachable. Switching closes the window and starts the Gallery again with the other library, in front, where the window was. Each library has an id in `library.json`, so the recent list shows it once, where it was last opened (a copy still reachable elsewhere, a backup say, is listed too).
- **Bringing marks:** a new library can take the ratings, favourites, tags, albums (with their icons), characters (with their rules and your decisions), notes and folder looks of the library it was made from, for the same files by fingerprint, once its folder is indexed. One step in History.
- **Phases:** 1, the portable library; 2, leaving Eagle (above, Eagle bridge) with colour sets. Still to come: gathering outside folders in, and XMP sidecars.

## Updates

The portable folder can bring itself up to date from the repository's GitHub Releases. The switch is off by default (it's network use), and **Check now** works without it.

- **Checking:** with the switch on, the Gallery asks GitHub's API for the latest release once a day. Nothing is sent but the request itself.
- **Installing:** it downloads only `app\` (about 1 MB), checks its SHA-256, and unpacks it beside the running one. `Model Librarian Gallery.exe` swaps it in at the next start, while no Gallery is open; **Restart now** does that straight away.
- **Limits:** a release that needs other packages isn't installed this way; it points to the full portable download instead. A checkout updates with `git pull`.

## Decisions

1. **Where it lives:** its own private repo, `EY4O/Model-Librarian-Gallery`.
2. **Window or browser:** its own window (Edge WebView2); `--browser` opens it in a browser instead.
3. **Which folders:** yours to add in the app. Folders you add are the only ones it reads; Eagle libraries stay out.
4. **File operations:** allowed, each with a preview and Undo; deletes go to the Recycle Bin.
5. **Characters:** the rule-based matching above.
6. **Gallery Info node:** all the fields above, with the folder pattern on the node.

## Risks

- **Many metadata formats.** Some generators, and some ComfyUI custom save nodes, write data in their own way. The shared readers already cover the common ones; anything unread is still shown, marked "no generation data", and can be fixed later.
- **Very large workflows** (several MB of JSON) in thousands of files. They're stored compressed and only parsed when you open an image.
- **Videos** need ffmpeg for posters and duration. It's bundled, so there's no install, but it adds about 80 MB to the package.
- **Metadata turned off.** ComfyUI started with `--disable-metadata` writes no metadata at all, the Gallery Info entry included. Those files are still indexed, just without generation data.
- **OneDrive and long paths.** Paths are stored as they are, and long paths are handled. Folders under OneDrive (files online only) are skipped unless you choose to include them.
