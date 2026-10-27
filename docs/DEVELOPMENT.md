# Development

The repository root holds the app (`mlgallery/`), its tests and helper scripts. The plan is in [PLAN.md](PLAN.md).

```
mlgallery/        the app: server, index, scanner, thumbnails, metadata, search, folder watcher;
                  meta/ holds Model Librarian's metadata readers (copies); ui/ is the interface (plain JS and CSS, no build step)
tests/            pytest suites, the sample media generator and the headless Edge driver
tools/            helper scripts: the icon, screenshots on sample media (shots.py), timings at 250k (perf.py), the portable build
                  and its launcher (launcher.cs), releases (release.py)
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

Its data (the index, thumbnails, settings) lives in `%LOCALAPPDATA%\Model Librarian Gallery`, or wherever `--data` / `MLG_DATA` points; `--library DIR` opens a library (its index in `DIR\.mlgallery`, the settings still on the PC). `MLG_HOME` moves the PC's own folder (settings, recent libraries); the tests point it at a throwaway. Try changes on a data folder of your own and generated media (`python tests/sample_media.py <folder>`), not on your real library.

## Tests

Python 3.12 with `requirements-dev.txt`. The UI tests need Microsoft Edge; the video tests need `ffmpeg` on PATH (both are skipped without them).

```bash
.venv/Scripts/python -m pytest -q tests
```

| Suite | Covers |
|---|---|
| `test_scan` | Fingerprints, what's indexed and skipped (hidden, system, Eagle), changes, moves, missing files, offline folders, sorts, paging, day counts, the shuffle |
| `test_photos` | The date taken (names from phones and screenshot tools, EXIF in JPEG and HEIC, a video's recorded date) and the grid, its days and date filters going by it; HEIC, TIFF, BMP and AVIF indexed with thumbnails, the viewer getting HEIC and TIFF as WebP, editing a HEIC; a day alone in a name, impossible dates, an edit date that isn't a date taken; TIFF edits (16-bit scans too) and a crop measured on a preview; Live Photos as one item, only in a photo library (the Trash and back, emptying, moving through a name clash, a half that can't go, a JPEG of it appearing, a .MOV too big to be one, a lone .MOV); what a library is for (one or more) and its route; what each file is in a mixed library (is:ai, is:photo, is:illustration, the counts in Statistics); people and characters (who) and their routes |
| `test_thumbs` | Thumbnail size and shape, transparency, EXIF orientation, animations, video posters, the cache, broken files |
| `test_meta` | The readers match Model Librarian's (set `MLG_ML_REPO` to its checkout), ComfyUI / A1111 / video metadata, Gallery Info entries, every search filter, marks and tags (yours win over the workflow's), Recently viewed, the folder watcher |
| `test_stats` | Statistics on known data, prompt tags (and their backfill), the date / size / CFG filters, Model Librarian's library and the models never used (on a made-up library: the tests never read yours; `MLG_ML_DATA` points them away) |
| `test_characters` | Rules (LoRA, trigger word, tag), strong and weak matches, To review (its answers one History step per run, Z taking back the last, a muted rule suggesting again on Undo), confirmations and rejections that stick, a rule you keep rejecting going quiet, names from Gallery Info nodes, new files matched as they arrive, the characters API |
| `test_organize` | Moves on one drive and across drives (copy, check, remove; a copy that doesn't match leaves the original), refusals (offline drive, `..`), the Trash, Put back, emptying (to a stand-in, never your Recycle Bin), Undo of each, bulk ratings / favourites / tags / albums, albums from Gallery Info, duplicates (and Keep both: not offered again until the group changes, Undo, offering them again), backups |
| `test_artists` | Artists and sources: the names sites give their downloads, web addresses, names tidied, a file's own details (EXIF, PNG text, a model name that isn't a source), where Windows says a file was downloaded from; who made each picture in a library for illustrations (a folder, a Danbooru name compared loosely, a Pixiv download), the file beating its folder and you beating both, two names one artist, many at once and Undo, none in a library without illustrations nor for generations, moving into an artist's folder; New downloads offered with who made them and copies marked, filing (and where the next one of theirs goes) and Undo, your artist and dismissing, a library folder refused as a downloads folder |
| `test_polish` | Swipe review (what's not reviewed yet, keep, favourite, rate, take back, the Trash and back, a search's own, starting again); smart albums (what the search finds, as it changes; taking no files; a search naming itself); renaming by a pattern (artist, date, number; Undo; names taken; a Live Photo's video with its photo; a plain name for one file only); site: and has:source going by the source shown; a notification only for downloads that arrive; a selection added to a character (and a new name making one) and its Undo; album names written one way (old ones with a / or a " tidied, long ones stable); moving an album renaming it in smart albums' searches (and Undo of a move or a rename, a name taken meanwhile left as it is); Tidy up (the albums holding exactly a folder's pictures, whatever either is called, never smart ones; deleting them as one step and Undo bringing them back with their pictures, icons and colours; one album deleted alone undoable too); nested albums (a parent holding what's inside it, each file once, smart ones too; moving and renaming a branch, never into itself or onto a name that's taken) and subfolders (listed with their counts, `path:` searches) |
| `test_tray` | The tray: how it starts at sign-in (the portable launcher or a checkout's Python, with `--tray`) and that setting added and taken out (against a stand-in for the registry, never this PC's), the settings (signing in turns the tray on, no tray turns signing in off, a server alone can't), pausing the watcher and starting again (every folder looked through), the API for both and Phone and tablet's switch; on Windows, a second start waking the Gallery that's in the tray (its own library's only), without ever showing an icon |
| `test_remote` | Phone and tablet (a device undoing only what a device can do, an address read as the router reads it, a device removed by another Gallery on this PC): the QR code against segno (every mask, versions up to 10, the version and mask it picks); only private addresses answered; what a paired device may ask; pairing (wrong, used and run-out codes, only a hash kept, renaming, removing); the server as a device sees it (off, unpaired, pairing and its cookie, the phone layout without the PC's token, the API's header, pictures without it, what's refused, a public address with the cookie, a removed device), and Phone and tablet's settings on the PC; keys that change and a copied one unpairing its device, devices forgotten after 30 days, 5 wrong codes cancelling the code; over Tailscale only this PC's account (another account, an unknown device, the Wi-Fi refused; signed in as someone else since), the way chosen the first time (Tailscale when it runs, else Home Wi-Fi waiting for a yes), and Phone and tablet's settings never through the general settings |
| `test_eagle` | Reading an Eagle library (nested folders as albums, deleted items skipped), matching by bytes, what bringing it in adds (your own ratings and notes kept), copying in the files it doesn't have laid out by Eagle folder under Eagle's names (made safe for Windows, clashes, dates kept), links in the note, Eagle's colours, a copy that doesn't match left out, bringing it in as a new library through the API, Undo, Send to Eagle against a stand-in for Eagle's API; on made-up libraries only |
| `test_colors` | Colour sets: the palette of known pictures, near-twins merged, the colour families, Eagle's palettes, colours made with the thumbnail and backfilled, a changed file's worked out again, `color:` searches and suggestions, colours in Statistics |
| `test_comfy` | Open in ComfyUI against a stand-in for Model Librarian's route (sent, not open in a browser, too old, not running), Save as recipe into a made-up library, the Start menu shortcut (in a temp folder) with the Gallery's app id, the window's taskbar identity (pinning it pins the launcher, by the Gallery's name and icon), and the window opening where it was (maximised or not; centred when that place is off every screen) and coming to the front |
| `test_update` | Updates against a stand-in for GitHub's API: a newer release found (or not, or GitHub unreachable), the daily check only with the switch on, installing (SHA-256 from GitHub's digest or a .sha256 file), refusals (a download that doesn't match, other packages, another version, a file outside its folder, not a zip, a checkout), the release zip, the routes; the launcher swapping in the update, and waiting while a Gallery is open (needs the C# compiler that comes with Windows); in made-up portable folders only |
| `test_libraries` | Libraries: making one (and refusals: inside an Eagle library, inside another library), which library opens (`--library`, `--data`, the Gallery kept inside one, the last one, this PC's when it's unreachable), the recent list (each library once, by its id, wherever it was opened from), re-anchoring folders after a move or a drive-letter change (with `subst`), marks kept, bringing marks from another library and its Undo, settings kept on the PC, the routes and switching, the launcher inside a library checking its locks; on made-up folders and a throwaway `MLG_HOME` only |
| `test_edit` | Flip, rotate and crop: ComfyUI's PNG text chunks and A1111's JPEG EXIF kept, the date kept, the result matching what the viewer showed, Save as copy, Undo of each |
| `test_edges` | The cases a bug hunt and the QA passes found (undo leaving out files gone from the index, a move undone after its folder left, To review per character, a library from a newer Gallery, a Statistics range in whole days): folder names Windows folds (case, trailing dots), names held by files in the Trash, a new file where a trashed one was, a cross-drive copy whose original can't go, undo that can't finish, words and filters left out of a search, values that can't be read, a character in more files than an IN list takes, requests and settings that make no sense |
| `test_server` | Dragging files out of the window (against a stand-in for its drag), the token and Host checks, the API the interface uses (files, details, marks, tags, workflows, suggestions), folder add and remove, Show in Explorer |
| `test_ui` | The sidebar's trees by keyboard (Right, Left, Enter, one row in the Tab order, no buttons inside rows) and the viewer and Swipe holding the focus; Swipe (keep, delete and back, a star, Sort to album and back, the picker); the phone layout at a phone's size (long-press selecting, the viewer and a swipe to the next, details, Browse); nested albums and subfolders in the sidebar (a parent's Delete, Tidy up); start to grid in headless Edge, day headings, the viewer and its details panel, search chips and suggestions, ratings, favourites, tags, Recently viewed, Statistics and its click-through, characters (the editor, To review with the keyboard, the details panel), the zoom bar and the View panel (Waterfall, names under the tiles), rotate in the viewer then Undo, crop mode, selecting and the bar (Rate and More menus), albums, notes, the shortcuts dialog, Delete and Ctrl+Z, the Trash, History, keyboard, sidebar, filters, Random, tile size, the empty states |

`test_remote` compares the QR code with [segno](https://pypi.org/project/segno/) (in `requirements-dev.txt`; skipped without it or without `node`). No test listens on the network: they act as a device by standing in for the address a request comes from, and `MLG_REMOTE_HOST=127.0.0.1` keeps Phone and tablet's listener on this PC when trying it by hand (so Windows Firewall doesn't ask). The tests never ask this PC's own Tailscale anything: `conftest.py` sets `MLG_NO_TAILSCALE`, and they stand in for what Tailscale would answer (`tailnet.status`, `tailnet.whois`).

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

Makes `%LOCALAPPDATA%\Programs\Model Librarian Gallery`: its own Python (copied from a standalone CPython 3.12, `uv python find 3.12`), the packages from `requirements.txt` (through uv's cache; `pillow-heif` reads iPhone HEIC), the app (`app\`: `mlgallery\`, `start.pyw` and `requirements.txt`), ffmpeg (copied from the one on PATH, without ffplay), about 270 MB, and `Model Librarian Gallery.exe`. Start it with the exe, or from the Start menu once Settings has added it.

The exe is `tools/launcher.cs`, compiled with the C# compiler that comes with Windows (.NET Framework 4's `csc.exe`; no SDK to install). It starts `python\pythonw.exe app\start.pyw`, after swapping in a downloaded update (`update\app`, marked by `update\ready`) while no Gallery has the library open and none runs from the folder (`running.lock`). Kept inside a library (the folder's parent has `.mlgallery\library.json`), it opens that library. Launchers from 1.1.4 and earlier know neither: they come with the next full download. Its data stays in `%LOCALAPPDATA%\Model Librarian Gallery`, so a new build simply replaces the folder. Don't build into a OneDrive folder: it would sync all of it.

## Releasing

1. Bump `VERSION` in `mlgallery/__init__.py`.
2. Commit and push to `main`. The commit message becomes the release notes.
3. `.venv/Scripts/python tools/release.py --full --publish` makes the GitHub release `v<version>` with three files:
   - `mlgallery-<version>.zip`, the portable folder's `app\`, which Galleries with **Updates** on download;
   - its `.sha256`;
   - `Model-Librarian-Gallery-<version>-portable.zip`, the whole folder, for a first install.

   Without `--publish`, the files are only made in `dist/`.
4. Build your own portable folder again (or let it update itself).

A Gallery only updates itself to a release with the same `requirements.txt`; one that needs other packages points to the full download instead. Changes to `tools/launcher.cs` also reach people only through the full download: the launcher sits beside `app\`, not in the update zip.

Updates need the repository to be public: GitHub's API doesn't show a private repository's releases to a Gallery that has no key, and the Gallery never asks for one.
