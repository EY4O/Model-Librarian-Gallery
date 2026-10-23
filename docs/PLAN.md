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
- the workflow's name, and its recipe if it was opened from one;
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
  "source": { "ml": "1.22.0", "workflow": "lighthouse.json", "recipe": null } }
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

**Where and when:** in Model Librarian (installed in every ComfyUI you use) as a minor release, alongside Gallery M1, where the Gallery starts reading metadata. The format is written down in both repos, and a test keeps the writer and the reader in step.

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
- A move or delete shows a preview with the count, size and destination. Moves across drives copy, check the size, then remove the original. Every operation is logged with **Undo**, and deletes go to the Recycle Bin.
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
| **M2 — Statistics** | Model and LoRA usage over time, prompt tags, settings, storage; Model Librarian's library linked (never-used models) | Statistics computed on a known sample set |
| **M3 — Characters** | Characters, rules, automatic matching, review list, character pages and statistics | Matching tests (LoRA, trigger word, tag, rejections) |
| **M4 — Organize** | Albums, bulk actions, move and delete with preview and undo, duplicates, daily backups | Move and undo tests on throwaway folders, including an offline drive |
| **M5 — Polish** | Portable package with its own Python and ffmpeg, Start menu shortcut, first-run setup, Open in ComfyUI, Save as recipe, performance at 250k | A 250k-file synthetic library for timing |

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
