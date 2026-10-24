# Development

The repository root holds the app (`mlgallery/`), its tests and helper scripts. The plan is in [PLAN.md](PLAN.md).

```
mlgallery/        the app: server, index, scanner, thumbnails, metadata, search, folder watcher;
                  meta/ holds Model Librarian's metadata readers (copies); ui/ is the interface (plain JS and CSS, no build step)
tests/            pytest suites, the sample media generator and the headless Edge driver
tools/            helper scripts: the icon, screenshots on sample media (shots.py), timings at 250k (perf.py), the portable build
docs/             the plan, this file, README images
```

## Run it locally

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
.venv/Scripts/python -m mlgallery            # its own window
.venv/Scripts/python -m mlgallery --browser  # in your browser
.venv/Scripts/python -m mlgallery --server --data <folder>   # server only, on a throwaway data folder
```

Its data (the index, thumbnails, settings) lives in `%LOCALAPPDATA%\Model Librarian Gallery`, or wherever `--data` / `MLG_DATA` points. Try changes on a data folder of your own and generated media (`python tests/sample_media.py <folder>`), not on your real library.

## Tests

Python 3.12 with `requirements-dev.txt`. The UI tests need Microsoft Edge; the video tests need `ffmpeg` on PATH (both are skipped without them).

```bash
.venv/Scripts/python -m pytest -q tests
```

| Suite | Covers |
|---|---|
| `test_scan` | Fingerprints, what's indexed and skipped (hidden, system, Eagle), changes, moves, missing files, offline folders, sorts, paging, day counts, the shuffle |
| `test_thumbs` | Thumbnail size and shape, transparency, EXIF orientation, animations, video posters, the cache, broken files |
| `test_meta` | The readers match Model Librarian's (set `MLG_ML_REPO` to its checkout), ComfyUI / A1111 / video metadata, Gallery Info entries, every search filter, marks and tags (yours win over the workflow's), Recently viewed, the folder watcher |
| `test_stats` | Statistics on known data, prompt tags (and their backfill), the date / size / CFG filters, Model Librarian's library and the models never used (on a made-up library: the tests never read yours; `MLG_ML_DATA` points them away) |
| `test_characters` | Rules (LoRA, trigger word, tag), strong and weak matches, To review, confirmations and rejections that stick, a rule you keep rejecting going quiet, names from Gallery Info nodes, new files matched as they arrive, the characters API |
| `test_organize` | Moves on one drive and across drives (copy, check, remove; a copy that doesn't match leaves the original), refusals (offline drive, `..`), the Trash, Put back, emptying (to a stand-in, never your Recycle Bin), Undo of each, bulk ratings / favourites / tags / albums, albums from Gallery Info, duplicates, backups |
| `test_eagle` | Reading an Eagle library (nested folders as albums, deleted items skipped), matching by bytes, what bringing it in adds (your own ratings and notes kept), copying in the files it doesn't have, Undo, Send to Eagle against a stand-in for Eagle's API; on made-up libraries only |
| `test_comfy` | Open in ComfyUI against a stand-in for Model Librarian's route (sent, not open in a browser, too old, not running), Save as recipe into a made-up library, the Start menu shortcut (in a temp folder) |
| `test_server` | The token and Host checks, the API the interface uses (files, details, marks, tags, workflows, suggestions), folder add and remove, Show in Explorer |
| `test_ui` | Start to grid in headless Edge, day headings, the viewer and its details panel, search chips and suggestions, ratings, favourites, tags, Recently viewed, Statistics and its click-through, characters (the editor, To review with the keyboard, the details panel), selecting and the bar (Rate and More menus), albums, notes, the shortcuts dialog, Delete and Ctrl+Z, the Trash, History, keyboard, sidebar, filters, Random, tile size, the empty states |

`MLG_SHOTS=<folder>` makes `test_ui` save screenshots there; `python tools/shots.py <folder>` takes the README's on a fresh sample library.

## The shared metadata readers

`mlgallery/meta/` holds copies of Model Librarian's `imagemeta.py`, `videometa.py` and `recipe_parse.py`. Change them in Model Librarian first, then copy them here; `test_meta` fails while they differ.

## Performance

```bash
.venv/Scripts/python tools/perf.py --files 250000
```

Builds a made-up index of 250,000 files (no image files; a few minutes) in a temp folder and times every query the screens make, cold and cached. The plan's target is under 200 ms for searching and filtering. Statistics, the characters list and Duplicates count over everything, so they show their last result at once and recount in the background when the index changes (and ahead of time once indexing goes quiet).

## The portable build

```bash
.venv/Scripts/python tools/build_portable.py
```

Makes `%LOCALAPPDATA%\Programs\Model Librarian Gallery`: its own Python (copied from a standalone CPython 3.12, `uv python find 3.12`), the packages from `requirements.txt` (through uv's cache), the app, and ffmpeg (copied from the one on PATH, without ffplay), about 270 MB. Start it with `Model Librarian Gallery.cmd`, or from the Start menu once Settings has added it. Its data stays in `%LOCALAPPDATA%\Model Librarian Gallery`, so a new build simply replaces the folder. Don't build into a OneDrive folder: it would sync all of it.

## Releasing

1. Bump `VERSION` in `mlgallery/__init__.py`.
2. Commit and push to `main`.
3. Build the portable folder again.
