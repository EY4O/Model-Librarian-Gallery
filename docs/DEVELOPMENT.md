# Development

The repository root holds the app (`mlgallery/`), its tests and helper scripts. The plan is in [PLAN.md](PLAN.md).

```
mlgallery/        the app: server, index, scanner, thumbnails; ui/ is the interface (plain JS and CSS, no build step)
tests/            pytest suites, the sample media generator and the headless Edge driver
tools/            helper scripts (the icon)
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
| `test_server` | The token and Host checks, the API the interface uses, folder add and remove, Show in Explorer |
| `test_ui` | Start to grid in headless Edge, day headings, the viewer and its details panel, keyboard, sidebar, filters, Random, tile size, the empty state |

`MLG_SHOTS=<folder>` makes `test_ui` save screenshots there.

## Releasing

1. Bump `VERSION` in `mlgallery/__init__.py`.
2. Commit and push to `main`.
