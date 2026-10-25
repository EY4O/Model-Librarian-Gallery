"""Who made a picture and where it's from: found in the file, where it was downloaded from, its name and its folder.

    user      what you typed (an artist, a source; or that it has no artist)    for each field (the artist, the source)
    file      the file's own details: EXIF Artist, XMP creator, IPTC by-line,    the first of these that has it wins
              a PNG's Author; a source link written in it
    download  where Windows says it was downloaded from (the page and the picture's address, kept with the file)
    name      the name a site gives its downloads (12345678_p0.png from Pixiv, __..._drawn_by_artist__<md5> from
              Danbooru, title_by_artist_d1a2b3c from DeviantArt...), or a downloader's folders (gallery-dl's twitter\\<handle>)
    folder    a folder inside one called Artists (Artists\\<name>\\...)

Every artist name that wins becomes an artist in the Artists list - one the Gallery made, which goes again with its
last picture - matched by name or by another name you gave an artist, so a Pixiv name and an X handle can be one artist.
AI generations are left out: you made those.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .characters import norm

SRC = ("user", "file", "download", "name", "folder")  # who wins, first to last
SITES = {"pixiv": "Pixiv", "x": "X", "deviantart": "DeviantArt", "danbooru": "Danbooru", "gelbooru": "Gelbooru", "safebooru": "Safebooru",
         "yandere": "yande.re", "konachan": "Konachan", "e621": "e621", "artstation": "ArtStation", "bluesky": "Bluesky", "tumblr": "Tumblr",
         "instagram": "Instagram", "newgrounds": "Newgrounds", "furaffinity": "Fur Affinity", "reddit": "Reddit", "civitai": "Civitai"}
NAME_CAP, URL_CAP = 100, 2000
JUNK = {"", "unknown", "anonymous", "none", "n/a", "artist", "author", "owner", "camera owner", "copyright", "user", "admin", "administrator"}


# ---------------------------------------------------------------- tidying what's found
def clean_name(v, junk: bool = True) -> str | None:
    """An artist's name as found: one line, no © or year, not a placeholder (one you type may be anything: junk=False)."""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    if not isinstance(v, str):
        return None
    v = re.sub(r"\s+", " ", v.replace("\x00", " ")).strip()
    v = re.sub(r"^(?:©|\(c\)|copyright\b)\s*(?:\d{4}(?:\s*[-–]\s*\d{4})?)?[\s,.:]*", "", v, flags=re.I).strip()  # © 2024 Name
    v = re.sub(r"[\s,.:]*\ball rights reserved\.?$", "", v, flags=re.I).strip()  # Name, 2024. All rights reserved.
    v = re.sub(r"[\s,.:]+\d{4}(?:\s*[-–]\s*\d{4})?[.]?$", "", v).strip()  # (artist2024 is a name: only a year set apart goes)
    v = v.strip(" ,;:@")
    if not v or (junk and v.lower() in JUNK) or len(v) > NAME_CAP or not re.search(r"\w", v):
        return None
    return v


def clean_url(v) -> str | None:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    v = str(v or "").strip()
    return v[:URL_CAP] if re.match(r"https?://[^\s/]+\.[^\s]+$", v, re.I) else None


# ---------------------------------------------------------------- where it's from: a web address
def _int36(s: str) -> str | None:
    try:
        return str(int(s, 36))
    except ValueError:
        return None


def post_url(site: str | None, post: str | None, artist: str | None = None) -> str | None:
    """The page of a picture on its site, from what its name says (None when the site has no page to go by)."""
    if not site or not post:
        return None
    return {
        "pixiv": f"https://www.pixiv.net/artworks/{post}",
        "x": f"https://x.com/{artist or 'i'}/status/{post}",
        "deviantart": f"https://www.deviantart.com/deviation/{post}",
        "danbooru": f"https://danbooru.donmai.us/posts?tags=md5%3A{post}" if len(post) == 32 else f"https://danbooru.donmai.us/posts/{post}",
        "yandere": f"https://yande.re/post/show/{post}",
        "konachan": f"https://konachan.com/post/show/{post}",
        "furaffinity": f"https://www.furaffinity.net/view/{post}/",
        "e621": f"https://e621.net/posts/{post}",
        "gelbooru": f"https://gelbooru.com/index.php?page=post&s=view&id={post}",
        "safebooru": f"https://safebooru.org/index.php?page=post&s=view&id={post}",
    }.get(site)


def from_url(url: str | None) -> dict:
    """{site, post, artist, url} from a page's or a picture's web address (what's in it, nothing fetched)."""
    url = clean_url(url)
    if not url:
        return {}
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        q = parse_qs(p.query)
    except ValueError:  # http://[x/: not an address after all
        return {}
    host = host[4:] if host.startswith("www.") else host
    path = p.path
    out = {"url": url}

    def hit(site, post=None, artist=None, page=True):
        out.update(site=site, post=post, artist=clean_name(artist) if artist else None)
        if not page:  # the picture itself (a CDN address): its page is better, when the post is known
            out["url"] = post_url(site, post, artist) or url
        return {k: v for k, v in out.items() if v}

    if host.endswith("pixiv.net"):
        m = re.search(r"/artworks/(\d+)", path)
        if m:
            return hit("pixiv", m.group(1))
        if "illust_id" in q:
            return hit("pixiv", q["illust_id"][0])
        return hit("pixiv")
    if host.endswith("pximg.net"):
        m = re.search(r"/(\d+)_p\d+", path)
        return hit("pixiv", m.group(1) if m else None, page=False)
    if host in ("x.com", "twitter.com", "mobile.twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com"):
        m = re.match(r"/([A-Za-z0-9_]{1,15})/status/(\d+)", path)
        if m:
            return hit("x", m.group(2), None if m.group(1).lower() == "i" else m.group(1))
        m = re.match(r"/([A-Za-z0-9_]{1,15})/?$", path)
        return hit("x", None, m.group(1) if m and m.group(1).lower() not in ("home", "i", "search", "explore") else None)
    if host == "pbs.twimg.com":
        return hit("x", page=False)
    if host.endswith("deviantart.com"):
        m = re.match(r"/([A-Za-z0-9-]+)/art/[^/]*?-?(\d+)/?$", path)
        if m:
            return hit("deviantart", m.group(2), m.group(1))
        sub = host.split(".")[0]
        return hit("deviantart", None, sub if sub not in ("deviantart", "www") else None)
    if host.endswith("wixmp.com") and "deviantart" in url.lower():
        return hit("deviantart", page=False)
    if host.endswith("donmai.us"):
        m = re.match(r"/posts/(\d+)", path)
        if m:
            return hit("danbooru", m.group(1))
        m = re.search(r"/([0-9a-f]{32})\.\w+$", path)
        return hit("danbooru", m.group(1) if m else None, page=not host.startswith("cdn"))
    if host in ("gelbooru.com", "safebooru.org"):
        site = host.split(".")[0]
        return hit(site, q["id"][0] if q.get("id") and q["id"][0].isdigit() else None)
    if host in ("yande.re", "konachan.com", "konachan.net"):
        m = re.match(r"/post/show/(\d+)", path)
        return hit("yandere" if host == "yande.re" else "konachan", m.group(1) if m else None)
    if host in ("files.yande.re", "assets.yande.re"):
        m = re.search(r"yande\.re%20(\d+)|yande\.re (\d+)", url)
        return hit("yandere", (m.group(1) or m.group(2)) if m else None, page=False)
    if host == "e621.net":
        m = re.match(r"/posts/(\d+)", path)
        return hit("e621", m.group(1) if m else None)
    if host.endswith("artstation.com"):
        m = re.match(r"/artwork/(\w+)", path)
        sub = host.split(".")[0]
        artist = sub if sub not in ("artstation", "www", "cdn") else None
        if not artist:
            m2 = re.match(r"/([A-Za-z0-9_-]+)/?$", path)
            artist = m2.group(1) if m2 and m2.group(1) not in ("artwork", "search", "learning") else None
        return hit("artstation", m.group(1) if m else None, artist)
    if host == "bsky.app":
        m = re.match(r"/profile/([^/]+)(?:/post/(\w+))?", path)
        if m:
            handle = re.sub(r"\.bsky\.social$", "", m.group(1)) if not m.group(1).startswith("did:") else None
            return hit("bluesky", m.group(2), handle)
        return hit("bluesky")
    if host.endswith("tumblr.com"):
        sub = host.split(".")[0]
        m = re.match(r"/post/(\d+)", path)
        if m and sub not in ("tumblr", "www", "64") and not re.match(r"^\d+$", sub):
            return hit("tumblr", m.group(1), sub)
        m = re.match(r"/([a-z0-9-]+)/(\d+)", path)
        if host == "tumblr.com" and m:
            return hit("tumblr", m.group(2), m.group(1))
        return hit("tumblr", page=host != "media.tumblr.com" and not host.startswith("64."))
    if host.endswith("instagram.com"):
        m = re.match(r"/(?:p|reel)/([\w-]+)", path)
        return hit("instagram", m.group(1) if m else None)
    if host.endswith("newgrounds.com"):
        m = re.match(r"/art/view/([A-Za-z0-9-]+)/([\w-]+)", path)
        sub = host.split(".")[0]
        return hit("newgrounds", m.group(2) if m else None, m.group(1) if m else (sub if sub not in ("newgrounds", "www", "art") else None))
    if host.endswith("furaffinity.net"):
        m = re.match(r"/(?:view|full)/(\d+)", path)
        return hit("furaffinity", m.group(1) if m else None)
    if host.endswith("reddit.com") or host == "redd.it":
        m = re.search(r"/comments/(\w+)", path)
        return hit("reddit", m.group(1) if m else None)
    if host.endswith("civitai.com"):
        m = re.match(r"/images/(\d+)", path)
        return hit("civitai", m.group(1) if m else None)
    return {"site": host, "url": url} if host else {}


# ---------------------------------------------------------------- what its name says
_PIXIV = re.compile(r"^(\d{5,10})_p(\d+)(?:_(?:master|square|custom)\d+)?$")
_BOORU = re.compile(r"^__(.+?)__(?:sample[-_])?([0-9a-f]{32})$")
_DEVIANT = re.compile(r"^(?:.+?_)?by_([A-Za-z0-9][A-Za-z0-9-]*?)[_-]d([0-9a-z]{6,8})(?:-[a-z0-9]+)*$", re.I)
_YANDERE = re.compile(r"^yande\.re (\d+)\b")
_KONACHAN = re.compile(r"^Konachan\.com - (\d+)\b")
_TMD = re.compile(r"^([A-Za-z0-9_]{1,15})-(\d{15,20})-(?:\d{8}_\d{6})-(?:img|vid|gif)\d+$")  # Twitter Media Downloader
_FA = re.compile(r"^(\d{9,10})\.([A-Za-z0-9~.\-]+?)_")
_TUMBLR = re.compile(r"^tumblr_[A-Za-z0-9]{10,}(?:_r\d+)?_\d{2,4}$")
# a downloader's folders: <site>\<artist>\... (gallery-dl's own layout)
_LAYOUT = {"twitter": "x", "x": "x", "pixiv": "pixiv", "deviantart": "deviantart", "bluesky": "bluesky", "artstation": "artstation",
           "tumblr": "tumblr", "instagram": "instagram", "newgrounds": "newgrounds", "furaffinity": "furaffinity"}


def from_name(name: str) -> dict:
    """{site, post, artist, url} from the name a site gives its downloads."""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    m = _PIXIV.match(stem)
    if m:
        return {"site": "pixiv", "post": m.group(1), "url": post_url("pixiv", m.group(1))}
    m = _BOORU.match(stem)
    if m:
        tags, md5 = m.group(1), m.group(2)
        out = {"site": "danbooru", "post": md5, "url": post_url("danbooru", md5)}
        if "_drawn_by_" in tags:  # <characters>_<series>_drawn_by_<artist>
            artist = clean_name(tags.rsplit("_drawn_by_", 1)[1].replace("_", " "))
            if artist:
                out["artist"] = artist
        return out
    m = _DEVIANT.match(stem)
    if m:
        post = _int36(m.group(2))
        return {k: v for k, v in {"site": "deviantart", "post": post, "artist": clean_name(m.group(1)), "url": post_url("deviantart", post)}.items() if v}
    for rx, site in ((_YANDERE, "yandere"), (_KONACHAN, "konachan")):
        m = rx.match(stem)
        if m:
            return {"site": site, "post": m.group(1), "url": post_url(site, m.group(1))}
    m = _TMD.match(stem)
    if m:
        return {"site": "x", "post": m.group(2), "artist": m.group(1), "url": post_url("x", m.group(2), m.group(1))}
    m = _FA.match(stem)
    if m and clean_name(m.group(2)):
        return {"site": "furaffinity", "artist": clean_name(m.group(2))}
    if _TUMBLR.match(stem):
        return {"site": "tumblr"}
    return {}


def from_layout(rel: str) -> dict:
    """A downloader's <site>\\<artist> folders around the file: {site, artist}."""
    parts = rel.split("/")[:-1]
    for i in range(len(parts) - 1, -1, -1):
        site = _LAYOUT.get(parts[i].lower())
        if site and i + 1 < len(parts):
            who = parts[i + 1]
            if site == "pixiv":  # "12345 account" (gallery-dl), "12345" alone says nothing
                m = re.match(r"^\d+ (.+)$", who)
                who = m.group(1) if m else None
            name = clean_name(who) if who else None
            return {"site": site, "artist": name} if name else {"site": site}
    return {}


def from_folder(rel: str) -> str | None:
    """Artists\\<name>\\...: the artist a folder of yours names."""
    parts = rel.split("/")[:-1]
    for i in range(len(parts) - 2, -1, -1):
        if parts[i].lower() in ("artists", "artist"):
            return clean_name(parts[i + 1])
    return None


# ---------------------------------------------------------------- what the file says
def _xmp_text(im) -> str:
    for k in ("xmp", "XML:com.adobe.xmp"):
        v = im.info.get(k)
        if v:
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
    tag = getattr(im, "tag_v2", None)
    if tag is not None and tag.get(700):  # TIFF
        v = tag.get(700)
        return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
    return ""


def _xmp(xmp: str, prop: str) -> str | None:
    m = re.search(rf"<{prop}\b[^>]*>(.*?)</{prop}>", xmp, re.S)
    if m:
        li = re.search(r"<rdf:li\b[^>]*>(.*?)</rdf:li>", m.group(1), re.S)
        return html.unescape((li.group(1) if li else m.group(1)).strip()) or None
    m = re.search(rf'\b{prop}="([^"]*)"', xmp)
    return html.unescape(m.group(1)) if m else None


def _xp(v) -> str | None:
    """A Windows XP* EXIF field (UTF-16, as bytes or a tuple of byte values)."""
    if isinstance(v, (tuple, list)):
        v = bytes(v)
    if isinstance(v, bytes):
        return v.decode("utf-16-le", "replace").rstrip("\x00")
    return v if isinstance(v, str) else None


def from_file(path: str) -> dict:
    """{artist, url} from the picture's own details."""
    from PIL import Image
    out: dict = {}
    with Image.open(path) as im:
        exif = im.getexif() if im.format != "PNG" or "exif" in im.info else {}  # a PNG without EXIF up front: getexif() would decode it all
        xmp = _xmp_text(im)
        iptc = {}
        if im.format in ("JPEG", "TIFF"):
            try:
                from PIL import IptcImagePlugin
                iptc = IptcImagePlugin.getiptcinfo(im) or {}
            except Exception:
                iptc = {}
        info = im.info
        artists = [exif.get(0x013B), _xp(exif.get(0x9C9D)), _xmp(xmp, "dc:creator") if xmp else None, iptc.get((2, 80)),
                   info.get("Author"), info.get("Artist"), info.get("author"), exif.get(0x8298), _xmp(xmp, "dc:rights") if xmp else None,
                   info.get("Copyright")]
        for v in artists:
            if isinstance(v, (list, tuple)) and v and isinstance(v[0], bytes):
                v = v[0]
            name = clean_name(v)
            if name:
                out["artist"] = name
                break
        for v in (_xmp(xmp, "dc:source") if xmp else None, _xmp(xmp, "photoshop:Source") if xmp else None,
                  _xmp(xmp, "xmpRights:WebStatement") if xmp else None, iptc.get((2, 115)), info.get("Source"), info.get("URL"), info.get("source")):
            url = clean_url(v)
            if url:
                out.update({k: x for k, x in from_url(url).items() if k != "artist" or "artist" not in out})
                break
    return out


def from_download(path: str) -> dict:
    """{site, post, artist, url} from where Windows says the file was downloaded from (its Zone.Identifier: the page,
    then the picture's own address). Gone if the file was copied to another drive: what was found stays in the index."""
    try:
        with open(path + ":Zone.Identifier", encoding="utf-8", errors="replace") as f:
            text = f.read(8192)
    except OSError:
        return {}
    got = dict(re.findall(r"^(ReferrerUrl|HostUrl)=(.+?)\s*$", text, re.M))
    page, pic = from_url(got.get("ReferrerUrl")), from_url(got.get("HostUrl"))
    if not page and not pic:
        return {}
    out = dict(page)
    for k, v in pic.items():  # the picture's address fills in what the page didn't say (the post, when the page was just the site)
        if k == "url":
            if not page.get("post") and pic.get("post"):
                out["url"] = v
            continue
        if not out.get(k) and (k != "site" or not out.get("site")):
            out[k] = v
    if page.get("site") and pic.get("site") and page["site"] != pic["site"] and page.get("post"):
        out["site"], out["post"] = page["site"], page["post"]
    return out


def found(path: str, rel: str, name: str, kind: str) -> dict[str, dict]:
    """What each source says about one file -> {src: {artist, site, post, url}} (only the ones that say something)."""
    out = {}
    if kind == "image":
        try:
            got = from_file(path)
        except Exception:  # unreadable, or a format Pillow doesn't read details from
            got = {}
        if got:
            out["file"] = got
    got = from_download(path)
    if got:
        out["download"] = got
    got = {**from_layout(rel), **{k: v for k, v in from_name(name).items() if v}}
    if got:
        out["name"] = got
    artist = from_folder(rel)
    if artist:
        out["folder"] = {"artist": artist}
    return out


# ---------------------------------------------------------------- artists, and each file's
def name_map(c) -> dict[str, int]:
    """Every artist's name and other names (compared loosely: case, spaces, _ and - alike) -> its id."""
    out = {}
    for r in c.execute("SELECT id, name, aliases FROM artists ORDER BY auto, id"):
        out.setdefault(norm(r["name"]), r["id"])
        for a in json.loads(r["aliases"] or "[]"):
            out.setdefault(norm(a), r["id"])
    return out


def artist_for(c, name: str, names: dict | None = None, auto: bool = True) -> int | None:
    """The artist with this name (or other name), made if there's none yet."""
    name = clean_name(name, junk=False)  # (found names were checked as they were stored; yours may be anything)
    if not name:
        return None
    names = name_map(c) if names is None else names
    got = names.get(norm(name))
    if got:
        if not auto:
            c.execute("UPDATE artists SET auto=0 WHERE id=?", (got,))  # you named them: they stay
        if not name.islower():  # the Gallery's own, found as "mika_art" first: "Mika Art" from a folder or a file reads better
            c.execute("UPDATE artists SET name=? WHERE id=? AND auto=1 AND name = lower(name) COLLATE BINARY AND name <> ? COLLATE BINARY", (name, got, name))
        return got
    aid = c.execute("INSERT INTO artists (name, created, auto) VALUES (?,?,?)", (name, time.time(), int(auto))).lastrowid
    names[norm(name)] = aid
    return aid


def winner(rows) -> dict:
    """The credit shown, field by field: {artist, artist_from, site, post, url, source_from}."""
    by = {r["src"]: r for r in rows}
    out = {"artist": None, "artist_from": None, "site": None, "post": None, "url": None, "source_from": None}
    for s in SRC:
        r = by.get(s)
        if not r:
            continue
        if out["artist_from"] is None and r["artist"] is not None:
            out["artist"], out["artist_from"] = r["artist"] or None, s  # '' (yours): no artist
        if out["source_from"] is None and (r["url"] is not None or r["site"] is not None):
            out.update(site=r["site"] or None, post=r["post"] or None, url=r["url"] or None, source_from=s)
    return out


def resolve(c, ids, names: dict | None = None) -> None:
    """Each file's artist, from its credits as they are now."""
    names = name_map(c) if names is None else names
    for fid in ids:
        rows = c.execute("SELECT src, artist, site, post, url FROM credits WHERE file_id=?", (fid,)).fetchall()
        w = winner(rows)
        aid = artist_for(c, w["artist"], names) if w["artist"] else None  # (one you typed goes with its last picture too, unless you edit them)
        c.execute("UPDATE files SET artist_id=? WHERE id=? AND artist_id IS NOT ?", (aid, fid, aid))


def prune(c) -> int:
    """The artists the Gallery made that no file has any more."""
    return c.execute("DELETE FROM artists WHERE auto=1 AND cover IS NULL AND aliases='[]' AND links='[]'"
                     " AND NOT EXISTS (SELECT 1 FROM files f WHERE f.artist_id=artists.id)").rowcount


def store(c, fid: int, got: dict[str, dict]) -> None:
    """What was found for one file (inside a write). Yours stays; what a download said stays too once found (the file
    may have been copied since, losing it)."""
    have = {r["src"] for r in c.execute("SELECT src FROM credits WHERE file_id=?", (fid,))}
    for s in ("file", "name", "folder"):
        if s not in got and s in have:
            c.execute("DELETE FROM credits WHERE file_id=? AND src=?", (fid, s))
    for s, v in got.items():
        if s != "user":
            upsert(c, fid, s, v)


def upsert(c, fid: int, src: str, v: dict) -> None:
    """What one source says about a file, the others left as they are."""
    c.execute("INSERT INTO credits (file_id, src, artist, site, post, url) VALUES (?,?,?,?,?,?)"
              " ON CONFLICT (file_id, src) DO UPDATE SET artist=excluded.artist, site=excluded.site, post=excluded.post, url=excluded.url",
              (fid, src, v.get("artist"), v.get("site"), v.get("post"), v.get("url")))


def set_user(c, fid: int, artist=..., url=...) -> None:
    """Your artist and source for a file: a value; '' for none; None to go back to what was found."""
    cur = c.execute("SELECT artist, site, post, url FROM credits WHERE file_id=? AND src='user'", (fid,)).fetchone()
    a = cur["artist"] if cur else None
    s, p, u = (cur["site"], cur["post"], cur["url"]) if cur else (None, None, None)
    if artist is not ...:
        a = None if artist is None else (clean_name(artist, junk=False) or "")
    if url is not ...:
        if url is None:
            s = p = u = None
        elif not str(url).strip():
            s, p, u = "", None, ""  # none
        else:
            u = clean_url(url)
            if not u:
                raise ValueError("A source is a web address, like https://www.pixiv.net/artworks/12345.")
            got = from_url(u)
            s, p = got.get("site") or "", got.get("post")
    if a is None and u is None and s is None:
        c.execute("DELETE FROM credits WHERE file_id=? AND src='user'", (fid,))
    else:
        c.execute("INSERT INTO credits (file_id, src, artist, site, post, url) VALUES (?, 'user', ?,?,?,?)"
                  " ON CONFLICT (file_id, src) DO UPDATE SET artist=excluded.artist, site=excluded.site, post=excluded.post, url=excluded.url",
                  (fid, a, s, p, u))


def of_file(c, fid: int) -> dict:
    """The details panel's artist and source: what's shown, where it came from, and what each source said."""
    rows = c.execute("SELECT src, artist, site, post, url FROM credits WHERE file_id=?", (fid,)).fetchall()
    w = winner(rows)
    a = c.execute("SELECT a.id, a.name FROM files f JOIN artists a ON a.id=f.artist_id WHERE f.id=?", (fid,)).fetchone()
    w["artist"] = dict(a) if a else None
    w["site_name"] = SITES.get(w["site"] or "", w["site"])
    w["found"] = [dict(r) for r in sorted(rows, key=lambda r: SRC.index(r["src"]) if r["src"] in SRC else 9)]
    w["mine"] = any(r["src"] == "user" for r in rows)
    return w


class CreditReader:
    """Looks for each file's artist and source in the background, newest first, in a library for illustrations
    (`wanted()`); an AI generation's metadata is read first, and it's left out."""

    def __init__(self, db, wanted=lambda: True):
        self.db, self.wanted = db, wanted
        self._wake = threading.Event()
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._work, name="credits", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def nudge(self) -> None:
        self._wake.set()

    def pending(self) -> int:
        if not self.wanted():
            return 0
        with self.db.read() as c:
            return c.execute("SELECT COUNT(*) FROM files f JOIN folders d ON d.id=f.folder_id WHERE f.credited=0 AND f.missing=0 AND d.online=1").fetchone()[0]

    def run_once(self, limit: int = 64) -> int:
        """One batch -> how many were looked at (0 when nothing's waiting, or the library isn't for illustrations)."""
        if not self.wanted():
            return 0
        with self.db.read() as c:
            rows = c.execute("SELECT f.id, f.folder_id, f.rel, f.name, f.kind, f.meta, d.path FROM files f JOIN folders d ON d.id=f.folder_id"
                             " WHERE f.credited=0 AND f.missing=0 AND d.online=1 AND f.meta <> 0 ORDER BY f.mtime DESC LIMIT ?", (limit,)).fetchall()
        if not rows:
            return 0
        done = []
        for r in rows:
            if self._stop.is_set():
                break
            try:
                got = {} if r["meta"] == 1 else found(os.path.join(r["path"], *r["rel"].split("/")), r["rel"], r["name"], r["kind"])
            except Exception:  # one file that can't be read doesn't hold up the rest
                got = {}
            done.append((r, got))
        with self.db.write() as c:
            names = name_map(c)
            kept = []
            for r, got in done:
                # moved or renamed meanwhile (another folder may name its artist): looked at again next time
                if c.execute("SELECT 1 FROM files WHERE id=? AND folder_id=? AND rel=? AND credited=0", (r["id"], r["folder_id"], r["rel"])).fetchone():
                    store(c, r["id"], got)
                    kept.append(r["id"])
            resolve(c, kept, names)
            c.executemany("UPDATE files SET credited=1 WHERE id=?", [(fid,) for fid in kept])
            prune(c)
        return len(done)

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                n = self.run_once()
            except Exception:
                n = 0
            if not n:
                self._wake.wait(5)
                self._wake.clear()
