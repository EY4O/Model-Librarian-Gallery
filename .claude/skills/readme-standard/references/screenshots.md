# Screenshots and icons for READMEs

Real screenshots from the user's own setup are best — ask first whether they'd like to capture them. When you need to produce them yourself (new project, private data on screen, or the app can't run here), render the **real frontend code** with **sample data** and capture it headlessly. This is what produced the Model Librarian screenshots.

## 1. Build a sample-data page (scratch directory, not the repo)

- Copy the project's real frontend files into a scratch folder.
- Stub only the host environment the frontend imports (for a ComfyUI extension: `scripts/app.js` and `scripts/api.js`), and answer its API calls from a table of canned JSON responses. Make the sample data realistic and **internally consistent** — totals that add up, "last used" dates that match "idle days", search results that belong to the item they're shown under. Reviewers notice.
- Make the page open the right view from the URL (e.g. `index.html#tab=storage`) and click through to an interesting state (search results shown, a plan built, a dropdown expanded) before it's captured.
- Preview images: if the UI shows thumbnails, generate abstract placeholder art (soft gradients + a few shapes, e.g. with Pillow) rather than using anyone's real images.
- Serve it with `python -m http.server <port> --bind 127.0.0.1` (ES modules don't load from `file://`).

## 2. Capture with headless Edge or Chrome

Windows paths: `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` or `C:\Program Files\Google\Chrome\Application\chrome.exe`.

```powershell
& $browser --headless=new --disable-gpu --hide-scrollbars `
  --user-data-dir="$scratch\browser-profile" `
  --window-size=1440,900 --force-device-scale-factor=1 `
  --virtual-time-budget=6000 `
  --screenshot="$repo\docs\images\<view>.png" `
  "http://127.0.0.1:<port>/index.html#tab=<view>"
```

- `--virtual-time-budget` lets async rendering and scripted clicks finish before capture.
- A separate `--user-data-dir` keeps the user's real browser profile untouched.
- 1440×900 gives readable UI text; keep all screenshots the same size so the grid lines up.
- Look at every image after capturing (open it) and fix anything inconsistent, then re-shoot.
- Stop the local server afterwards.

## 3. Icon (if the project has none)

A 512×512 PNG at `docs/images/icon.png`: a rounded-square tile with a simple, bold symbol in the project's accent colours. Draw it at 4× with Pillow and downscale with LANCZOS for smooth edges. Keep it legible at 32px — it's used by registries and the README header. Show it to the user before committing.

## 4. In the README

- Relative paths: `docs/images/<name>.png`.
- Hero image right after the pitch; the 2×2 grid in `## 📸 Screenshots`.
- Add `<sub>Screenshots use sample data.</sub>` whenever sample data was used.
