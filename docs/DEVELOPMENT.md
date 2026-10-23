# Development

The repository root holds the app (`mlgallery/`), its tests and helper scripts. The plan is in [PLAN.md](PLAN.md).

```
mlgallery/        the app: server, index, scanner, thumbnails, metadata, search, folder watcher;
                  meta/ holds Model Librarian's metadata readers (copies); ui/ is the interface (plain JS and CSS, no build step)
tests/            pytest suites, the sample media generator and the headless Edge driver
tools/            helper scripts: the icon, screenshots on sample media (shots.py)
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
| `test_server` | The token and Host checks, the API the interface uses (files, details, marks, tags, workflows, suggestions), folder add and remove, Show in Explorer |
| `test_ui` | Start to grid in headless Edge, day headings, the viewer and its details panel, search chips and suggestions, ratings, favourites, tags, Recently viewed, Statistics and its click-through, keyboard, sidebar, filters, Random, tile size, the empty states |

`MLG_SHOTS=<folder>` makes `test_ui` save screenshots there; `python tools/shots.py <folder>` takes the README's on a fresh sample library.

## The shared metadata readers

`mlgallery/meta/` holds copies of Model Librarian's `imagemeta.py`, `videometa.py` and `recipe_parse.py`. Change them in Model Librarian first, then copy them here; `test_meta` fails while they differ.

## Releasing

1. Bump `VERSION` in `mlgallery/__init__.py`.
2. Commit and push to `main`.
