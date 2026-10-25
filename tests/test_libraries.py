"""Libraries that travel: making one, choosing which opens, following it to another place or drive letter,
bringing marks from another library, switching from Settings, and the launcher kept inside one.

Everything happens in made-up folders: never this PC's own library, never an Eagle library.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from conftest import image
from mlgallery import fileops, libraries, metadata, scan, update
from mlgallery.library import Library

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import build_portable  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """This PC's own folder and its own library's place, both throwaways."""
    monkeypatch.setenv("MLG_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.delenv("MLG_DATA", raising=False)
    return tmp_path / "home"


def media_lib(path, n=3):
    """A folder of n images."""
    for i in range(n):
        image(path / "sub" / f"{i}.png", (80, 60), seed=i)
    return path


def open_lib(root):
    lib = Library(libraries.data_dir(str(root)), libraries.home_dir(), str(root))
    libraries.prepare(lib)
    return lib


def scan_all(lib):
    for f in lib.folders():
        scan.scan_folder(lib.db, f)


def ids_by_name(lib):
    with lib.db.read() as c:
        return {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM files WHERE missing=0")}


# ---------------------------------------------------------------- making and finding one
def test_create_and_find(tmp_path, home):
    root = media_lib(tmp_path / "Gen")
    made = libraries.create(str(root), "My Generations")
    assert made == {"root": str(root), "name": "My Generations", "existing": False}
    assert libraries.is_library(str(root)) and libraries.find(str(root)) == str(root)
    assert libraries.find(str(root / ".mlgallery")) == str(root)  # picking the hidden folder works too
    assert libraries.info(str(root))["root"] == str(root)
    if os.name == "nt":
        import ctypes
        assert ctypes.windll.kernel32.GetFileAttributesW(str(root / ".mlgallery")) & 0x2  # hidden
    assert libraries.create(str(root))["existing"]  # making it again just finds it
    assert libraries.find(str(tmp_path)) is None


@pytest.mark.parametrize("where,msg", [("missing", "doesn't exist"), ("Art.library/images", "Eagle"), ("Gen/inner", "inside the library"),
                                       ("Gen/.mlgallery", "own data folder")])
def test_create_refuses(tmp_path, home, where, msg):
    libraries.create(str(media_lib(tmp_path / "Gen")))
    if where != "missing":
        (tmp_path / where).mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match=msg):
        libraries.create(str(tmp_path / where))


def test_a_new_library_indexes_its_own_folder_once(tmp_path, home):
    root = media_lib(tmp_path / "Gen")
    libraries.create(str(root))
    lib = open_lib(root)
    assert [f["path"] for f in lib.folders()] == [str(root)]
    scan_all(lib)
    assert len(ids_by_name(lib)) == 3  # the .mlgallery folder inside isn't media
    with lib.db.write() as c:
        c.execute("DELETE FROM folders")
    libraries.prepare(open_lib(root))  # removing it sticks: opening again doesn't add it back
    assert lib.folders() == []


def test_the_portable_gallery_inside_a_library_isnt_indexed(tmp_path):
    root = media_lib(tmp_path / "Gen")
    app = root / "Model Librarian Gallery"
    image(app / "app" / "mlgallery" / "ui" / "icon.png", (32, 32))
    (app / "Model Librarian Gallery.exe").write_bytes(b"MZ")
    assert sorted(rel for _, rel, _ in scan.walk(str(root))) == ["sub/0.png", "sub/1.png", "sub/2.png"]


# ---------------------------------------------------------------- which library opens
def test_choose(tmp_path, home, monkeypatch):
    local = os.path.join(str(tmp_path / "localappdata"), "Model Librarian Gallery")
    first = libraries.choose()
    assert first == {"data": local, "home": str(home), "root": None, "note": None, "remember": True}
    root = media_lib(tmp_path / "Gen")
    libraries.create(str(root))
    pick = libraries.choose(library=str(root))
    assert pick["root"] == str(root) and pick["data"] == str(root / ".mlgallery") and pick["home"] == str(home)
    with pytest.raises(ValueError, match="isn't a library"):
        libraries.choose(library=str(tmp_path))
    assert libraries.choose(data=str(tmp_path / "d")) == {"data": str(tmp_path / "d"), "home": str(tmp_path / "d"), "root": None, "note": None, "remember": False}
    # the portable Gallery kept inside a library opens it
    assert libraries.choose(app_root=str(root / "Model Librarian Gallery"))["root"] == str(root)
    # otherwise the one opened last, and this PC's library when its drive isn't there
    libraries.remember(str(root))
    assert libraries.choose()["root"] == str(root)
    assert libraries.choose(local=True)["root"] is None
    shutil.rmtree(root / ".mlgallery")
    gone = libraries.choose()
    assert gone["root"] is None and str(root) in gone["note"] and not gone["remember"]  # still the one to try next time
    monkeypatch.setenv("MLG_DATA", str(tmp_path / "env"))
    assert libraries.choose()["data"] == str(tmp_path / "env")


def test_recent_libraries(tmp_path, home):
    roots = []
    for i in range(10):
        r = tmp_path / f"L{i}"
        r.mkdir()
        libraries.create(str(r))
        roots.append(str(r))
    for r in roots[:3] + [roots[0]]:
        libraries.remember(r)
    assert [r["root"] for r in libraries.recent()] == [roots[0], roots[2], roots[1]]
    for r in roots:
        libraries.remember(r)
    assert len(libraries.recent()) == libraries.RECENT and libraries.recent()[0]["root"] == roots[-1]
    libraries.remember(None)  # this PC's library: opened next, the list kept
    assert libraries.choose()["root"] is None and len(libraries.recent()) == libraries.RECENT
    libraries.forget(roots[-1])
    assert roots[-1] not in [r["root"] for r in libraries.recent()]
    shutil.rmtree(os.path.join(roots[-2], ".mlgallery"))
    assert [r["reachable"] for r in libraries.recent() if r["root"] == roots[-2]] == [False]


# ---------------------------------------------------------------- following it to another place
def test_rebase_moves_only_the_folders_inside(tmp_path, home):
    root = media_lib(tmp_path / "Gen")
    libraries.create(str(root))
    lib = open_lib(root)
    outside, sibling = tmp_path / "elsewhere", tmp_path / "GenX"
    for p in (outside, sibling, root / "more"):
        p.mkdir(exist_ok=True)
    with lib.db.write() as c:
        c.execute("DELETE FROM folders")
        for p in (root, root / "more", outside, sibling):
            c.execute("INSERT INTO folders (path, added) VALUES (?, 0)", (str(p),))
    if os.name == "nt":  # recorded in other letter case: the same place, nothing to move
        libraries.update_info(str(root), root=str(root).upper())
        assert libraries.rebase(lib.db, str(root)) == 0 and libraries.info(str(root))["root"] == str(root)
    new = tmp_path / "Moved" / "Gen"
    shutil.copytree(root, new)
    assert libraries.rebase(lib.db, str(new)) == 2
    assert sorted(f["path"] for f in lib.folders()) == sorted([str(new), str(new / "more"), str(outside), str(sibling)])
    assert libraries.info(str(new))["root"] == str(new)


def test_a_copied_library_opens_with_its_marks(tmp_path, home):
    root = media_lib(tmp_path / "Gen")
    libraries.create(str(root))
    lib = open_lib(root)
    scan_all(lib)
    fid = ids_by_name(lib)["1.png"]
    with lib.db.write() as c:
        c.execute("INSERT INTO marks (file_id, rating, favorite, updated) VALUES (?, 4, 1, 0)", (fid,))
        c.execute("INSERT INTO file_tags (file_id, tag_id) VALUES (?, ?)", (fid, metadata.tag_id(c, "lighthouse")))
    other = tmp_path / "Drive2" / "My Generations"
    shutil.copytree(root, other)  # what copying it to another drive does
    lib2 = open_lib(other)
    assert [f["path"] for f in lib2.folders()] == [str(other)]
    counts = scan.scan_folder(lib2.db, lib2.folders()[0])
    assert counts["unchanged"] == 3 and counts["missing"] == 0 and counts["added"] == 0
    with lib2.db.read() as c:
        assert dict(c.execute("SELECT rating, favorite FROM marks WHERE file_id=?", (fid,)).fetchone()) == {"rating": 4, "favorite": 1}
        assert c.execute("SELECT f.rel FROM files f WHERE id=?", (fid,)).fetchone()[0] == "sub/1.png"


@pytest.mark.skipif(os.name != "nt", reason="drive letters")
def test_another_drive_letter(tmp_path, home):
    """The same library seen through two drive letters (subst), as a USB drive plugged into two PCs."""
    root = media_lib(tmp_path / "Drive" / "Gen")
    free = [d for d in "QRSTUVWXYZ" if not os.path.exists(f"{d}:\\")]
    if len(free) < 2:
        pytest.skip("no free drive letters")
    a, b = free[:2]
    try:
        subprocess.run(["subst", f"{a}:", str(tmp_path / "Drive")], check=True)
        libraries.create(f"{a}:\\Gen")
        lib = open_lib(f"{a}:\\Gen")
        scan_all(lib)
        fid = ids_by_name(lib)["2.png"]
        with lib.db.write() as c:
            c.execute("INSERT INTO marks (file_id, rating, favorite, updated) VALUES (?, 5, 0, 0)", (fid,))
        lib.db.close() if hasattr(lib.db, "close") else None
        subprocess.run(["subst", f"{a}:", "/d"], check=True)
        subprocess.run(["subst", f"{b}:", str(tmp_path / "Drive")], check=True)
        lib2 = open_lib(f"{b}:\\Gen")
        assert [f["path"] for f in lib2.folders()] == [f"{b}:\\Gen"]
        assert scan.scan_folder(lib2.db, lib2.folders()[0])["unchanged"] == 3
        with lib2.db.read() as c:
            assert c.execute("SELECT rating FROM marks WHERE file_id=?", (fid,)).fetchone()[0] == 5
    finally:
        for d in (a, b):
            subprocess.run(["subst", f"{d}:", "/d"], capture_output=True)


# ---------------------------------------------------------------- bringing marks from another library
def marked_source(tmp_path):
    """This PC's library (a made-up one) with every kind of mark on its copies of the files."""
    src_media = media_lib(tmp_path / "output")
    src = Library(str(tmp_path / "Model Librarian Gallery #2"))  # a name like this PC's library's, with a # too
    with src.db.write() as c:
        c.execute("INSERT INTO folders (path, added, icon, color) VALUES (?, 0, 'star', '#ef5350')", (str(src_media),))
    scan_all(src)
    ids = ids_by_name(src)
    with src.db.write() as c:
        c.execute("INSERT INTO marks (file_id, rating, favorite, updated) VALUES (?, 5, 1, 0)", (ids["0.png"],))
        c.execute("INSERT INTO marks (file_id, rating, favorite, updated) VALUES (?, 2, 0, 0)", (ids["1.png"],))
        c.execute("INSERT INTO file_tags (file_id, tag_id) VALUES (?, ?)", (ids["0.png"], metadata.tag_id(c, "dusk")))
        aid = c.execute("INSERT INTO albums (name, created, icon, color, cover) VALUES ('Lighthouses', 0, 'flag', '#60a5fa', ?)", (ids["1.png"],)).lastrowid
        c.execute("INSERT INTO album_files (album_id, file_id, added) VALUES (?, ?, 0)", (aid, ids["1.png"]))
        c.execute("INSERT INTO file_notes (file_id, note) VALUES (?, 'the good one')", (ids["0.png"],))
        cid = c.execute("INSERT INTO characters (name, aliases, created) VALUES ('Aurelia', '[\"Rel\"]', 0)").lastrowid
        c.execute("INSERT INTO character_rules (character_id, kind, value) VALUES (?, 'tag', 'aurelia')", (cid,))
        c.execute("INSERT INTO file_characters (file_id, character_id, state, source, at) VALUES (?, ?, 'confirmed', 'user', 0)", (ids["2.png"], cid))
        c.execute("INSERT INTO file_characters (file_id, character_id, state, source, at) VALUES (?, ?, 'rejected', 'user', 0)", (ids["1.png"], cid))
    return src, src_media


def test_bring_marks_from_another_library(tmp_path, home):
    src, src_media = marked_source(tmp_path)
    root = tmp_path / "Drive" / "Gen"
    shutil.copytree(src_media, root)  # the same files, copied to the drive
    libraries.create(str(root), bring_from={"data": src.data_dir, "name": "this PC's library"})
    lib = open_lib(root)
    scan_all(lib)
    ids = ids_by_name(lib)
    with lib.db.write() as c:  # one rating of its own already: yours here win
        c.execute("INSERT INTO marks (file_id, rating, favorite, source, updated) VALUES (?, 3, 0, 'user', 0)", (ids["1.png"],))
    libraries.bring_when_scanned(lib, str(root))
    got = libraries.info(str(root))
    assert "bring_from" not in got and got["brought"]["name"] == "this PC's library"
    b = got["brought"]
    assert (b["files"], b["ratings"], b["tags"], b["albums"], b["notes"], b["characters"], b["decisions"]) == (3, 1, 1, 1, 1, 1, 2)
    with lib.db.read() as c:
        marks = {r["file_id"]: (r["rating"], r["favorite"]) for r in c.execute("SELECT * FROM marks")}
        assert marks == {ids["0.png"]: (5, 1), ids["1.png"]: (3, 0)}
        assert [r[0] for r in c.execute("SELECT t.name FROM file_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.file_id=?", (ids["0.png"],))] == ["dusk"]
        a = c.execute("SELECT id, icon, color, cover FROM albums WHERE name='Lighthouses'").fetchone()
        assert (a["icon"], a["color"], a["cover"]) == ("flag", "#60a5fa", ids["1.png"])
        assert c.execute("SELECT note FROM file_notes WHERE file_id=?", (ids["0.png"],)).fetchone()[0] == "the good one"
        ch = c.execute("SELECT id, aliases FROM characters WHERE name='Aurelia'").fetchone()
        assert json.loads(ch["aliases"]) == ["Rel"] and c.execute("SELECT COUNT(*) FROM character_rules WHERE character_id=?", (ch["id"],)).fetchone()[0] == 1
        states = dict(c.execute("SELECT file_id, state FROM file_characters WHERE character_id=?", (ch["id"],)).fetchall())
        assert states == {ids["2.png"]: "confirmed", ids["1.png"]: "rejected"}
        assert [r[0] for r in c.execute("SELECT rowid FROM search WHERE search MATCH 'dusk'")] == [ids["0.png"]]  # findable by its tag
        op = c.execute("SELECT id, kind, summary FROM ops").fetchone()
    assert op["kind"] == "carry" and "3 files matched" in op["summary"]
    # one step in History: Undo takes it all back, and the rating that was here stays
    fileops.undo(lib.db, op["id"])
    with lib.db.read() as c:
        assert dict(c.execute("SELECT file_id, rating FROM marks").fetchall()) == {ids["1.png"]: 3}
        for table in ("file_tags", "tags", "album_files", "albums", "file_notes", "characters", "file_characters", "character_rules"):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table


def test_bringing_folder_looks_and_a_missing_source(tmp_path, home):
    src, src_media = marked_source(tmp_path)
    libraries.create(str(src_media), bring_from={"data": src.data_dir, "name": "this PC's library"})  # the same folder becomes a library
    lib = open_lib(src_media)
    scan_all(lib)
    libraries.bring_when_scanned(lib, str(src_media))
    assert dict(lib.folders()[0])["icon"] == "star"  # the same folder: its icon and colour come too
    root = media_lib(tmp_path / "Other")
    libraries.create(str(root), bring_from={"data": str(tmp_path / "gone"), "name": "a library"})
    lib2 = open_lib(root)
    scan_all(lib2)
    libraries.bring_when_scanned(lib2, str(root))
    assert "isn't there" in libraries.info(str(root))["brought"]["error"]


# ---------------------------------------------------------------- switching from Settings
def run_app(lib, fn, **hooks):
    from aiohttp.test_utils import TestClient, TestServer
    from mlgallery.server import make_app

    async def go():
        client = TestClient(TestServer(make_app(lib, "t", **hooks), host="127.0.0.1"))
        await client.start_server()
        try:
            return await fn(client)
        finally:
            await client.close()
    return asyncio.run(go())


H = {"X-MLG-Token": "t"}


def test_library_routes_without_a_window(tmp_path, home):
    lib = Library(str(tmp_path / "data"), str(home))
    root = media_lib(tmp_path / "Gen")

    async def fn(c):
        lb = await (await c.get("/api/libraries", headers=H)).json()
        assert lb["current"]["root"] is None and lb["can_switch"] is False and lb["recent"] == []
        r = await c.post("/api/libraries/new", headers=H, json={"path": str(root), "name": "Gen", "bring": True})
        assert r.status == 200 and (await r.json()) == {"root": str(root), "name": "Gen", "existing": False, "switched": False}
        assert libraries.info(str(root))["bring_from"]["data"] == lib.data_dir
        r = await c.post("/api/libraries/open", headers=H, json={"path": str(root)})
        assert r.status == 409 and "--library" in (await r.json())["error"]  # a server alone can't switch
        r = await c.post("/api/libraries/open", headers=H, json={"path": str(tmp_path)})
        assert r.status == 422 and "New library" in (await r.json())["error"]
        r = await c.post("/api/libraries/new", headers=H, json={"path": str(tmp_path / "nope")})
        assert r.status == 422
    run_app(lib, fn)


def test_switching_libraries(tmp_path, home):
    lib = Library(libraries.choose(local=True)["data"], str(home))  # this PC's library is open
    roots = [media_lib(tmp_path / n) for n in ("A", "B")]
    for r in roots:
        libraries.create(str(r))
        libraries.remember(str(r))
    started, closed = [], []

    async def fn(c):
        lb = await (await c.get("/api/libraries", headers=H)).json()
        assert [r["root"] for r in lb["recent"]] == [str(roots[1]), str(roots[0])] and lb["can_switch"]
        assert (await c.post("/api/libraries/open", headers=H, json={"path": str(roots[0])})).status == 200
        await asyncio.sleep(0.5)
        assert started == [["--library", str(roots[0])]] and closed == [1]  # the window closes once the answer has gone
        hold = libraries.Lock(libraries.data_dir(str(roots[1])))
        assert hold.acquire()
        try:
            r = await c.post("/api/libraries/open", headers=H, json={"path": str(roots[1])})
            assert r.status == 409 and "open in another" in (await r.json())["error"]
        finally:
            hold.release()
        assert (await c.post("/api/libraries/open", headers=H, json={"local": True})).status == 409  # it's the one open now
        r = await c.post("/api/libraries/new", headers=H, json={"path": str(tmp_path / "A"), "bring": False})
        assert (await r.json())["existing"] and started[-1] == ["--library", str(roots[0])]
        lb = await (await c.post("/api/libraries/forget", headers=H, json={"path": str(roots[1])})).json()
        assert [r["root"] for r in lb["recent"]] == [str(roots[0])]
    run_app(lib, fn, quit=lambda: closed.append(1), relaunch=started.append)


def test_switching_from_a_library_to_this_pcs(tmp_path, home):
    root = media_lib(tmp_path / "Gen")
    libraries.create(str(root))
    lib = open_lib(root)
    started = []

    async def fn(c):
        lb = await (await c.get("/api/libraries", headers=H)).json()
        assert lb["current"]["root"] == str(root) and lb["current"]["name"] == "Gen"
        assert (await c.post("/api/libraries/open", headers=H, json={"local": True})).status == 200
        assert started == [["--local"]]
        r = await c.post("/api/libraries/open", headers=H, json={"path": str(root)})
        assert r.status == 409 and "open already" in (await r.json())["error"]
    run_app(lib, fn, quit=lambda: None, relaunch=started.append)


def test_settings_live_on_this_pc(tmp_path, home):
    root = media_lib(tmp_path / "Gen")
    libraries.create(str(root))
    lib = open_lib(root)
    lib.settings.update({"comfy_url": "http://127.0.0.1:9000"})
    assert os.path.isfile(home / "settings.json") and not os.path.exists(root / ".mlgallery" / "settings.json")
    assert lib.thumbs and os.path.isfile(root / ".mlgallery" / "library.db")


def test_launch_command(tmp_path, monkeypatch):
    cmd, cwd = libraries.launch_command(["--library", "X:\\Gen"])
    assert cmd[1:] == ["-m", "mlgallery", "--library", "X:\\Gen"] and os.path.isdir(os.path.join(cwd, "mlgallery"))
    cmd, _ = libraries.launch_command(["--local"], browser=True)
    assert cmd[-2:] == ["--local", "--browser"] and not cmd[0].lower().endswith("pythonw.exe")
    monkeypatch.setattr(update, "portable_root", lambda: str(tmp_path))
    cmd, cwd = libraries.launch_command(["--library", "X:\\Gen"])
    assert cmd == [str(tmp_path / "Model Librarian Gallery.exe"), "--wait", "--library", "X:\\Gen"] and cwd == str(tmp_path)


# ---------------------------------------------------------------- the launcher kept inside a library
@pytest.fixture(scope="module")
def launcher(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("the launcher is a Windows program")
    try:
        build_portable.csc()
    except SystemExit:
        pytest.skip("no C# compiler (.NET Framework 4)")
    exe = str(tmp_path_factory.mktemp("launcher") / "Model Librarian Gallery.exe")
    build_portable.make_launcher(exe)
    return exe


def test_launcher_inside_a_library_checks_its_locks(tmp_path, home, launcher):
    import hashlib
    from test_update import NEXT, app_zip
    root = media_lib(tmp_path / "Gen")
    libraries.create(str(root))
    pkg = root / "Model Librarian Gallery"
    (pkg / "python").mkdir(parents=True)
    build_portable.make_app(str(pkg / "app"))
    shutil.copy2(launcher, pkg / "Model Librarian Gallery.exe")
    b = app_zip()
    update.stage(str(pkg), b, hashlib.sha256(b).hexdigest(), NEXT)

    def run():
        r = subprocess.run([str(pkg / "Model Librarian Gallery.exe"), "--apply-update-only"], capture_output=True, text=True, timeout=60)
        return r.stdout.strip()
    hold = libraries.Lock(libraries.data_dir(str(root)))  # the library this launcher opens is open
    assert hold.acquire()
    try:
        assert run() == "nothing applied"
    finally:
        hold.release()
    with open(pkg / "running.lock", "a+"):  # a Gallery from this folder is running (with another library)
        assert run() == "nothing applied"
    assert run() == NEXT


# ---------------------------------------------------------------- what a QA pass found
def test_moved_onto_one_of_its_own_outside_folders(tmp_path, home):
    root = media_lib(tmp_path / "D" / "Art")
    libraries.create(str(root))
    lib = open_lib(root)
    outside = media_lib(tmp_path / "E" / "Art")
    with lib.db.write() as c:
        c.execute("INSERT INTO folders (path, added) VALUES (?, 0)", (str(outside),))
    shutil.copytree(root, outside, dirs_exist_ok=True)  # the library moved to where that outside folder is
    assert libraries.rebase(lib.db, str(outside)) == 0  # no crash: the path is taken, so that folder stays put
    assert sorted(f["path"] for f in lib.folders()) == sorted([str(root), str(outside)])


def test_only_your_own_marks_travel_and_undo_survives_gone_files(tmp_path, home):
    src, src_media = marked_source(tmp_path)
    ids = ids_by_name(src)
    with src.db.write() as c:  # what the files' own data gave (a Gallery Info node): the new library reads that itself
        c.execute("INSERT INTO file_tags (file_id, tag_id, source) VALUES (?, ?, 'workflow')", (ids["2.png"], metadata.tag_id(c, "from-node")))
        c.execute("INSERT INTO file_tags (file_id, tag_id, source, removed) VALUES (?, ?, 'workflow', 1)", (ids["2.png"], metadata.tag_id(c, "taken-off")))
        c.execute("INSERT INTO marks (file_id, rating, favorite, source, updated) VALUES (?, 3, 0, 'workflow', 0)", (ids["2.png"],))
    root = tmp_path / "Drive" / "Gen"
    shutil.copytree(src_media, root)
    libraries.create(str(root), bring_from={"data": src.data_dir, "name": "this PC's library"})
    lib = open_lib(root)
    scan_all(lib)
    libraries.bring_when_scanned(lib, str(root))
    new = ids_by_name(lib)
    with lib.db.read() as c:
        tags = {r[0]: r[1] for r in c.execute("SELECT t.name, ft.removed FROM file_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.file_id=?", (new["2.png"],))}
        assert tags == {"taken-off": 1}  # your removal travels; the node's own tag doesn't (the file brings it)
        assert c.execute("SELECT COUNT(*) FROM marks WHERE file_id=?", (new["2.png"],)).fetchone()[0] == 0
        op = c.execute("SELECT id FROM ops WHERE kind='carry'").fetchone()[0]
    with lib.db.write() as c:
        c.execute("DELETE FROM files WHERE id=?", (new["0.png"],))  # a file gone from the index since
    fileops.undo(lib.db, op)  # still undoes the rest
    with lib.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0 and c.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0


def test_folder_looks_alone_are_a_step(tmp_path, home):
    src_media = media_lib(tmp_path / "out")
    src = Library(str(tmp_path / "src"))
    with src.db.write() as c:
        c.execute("INSERT INTO folders (path, added, icon) VALUES (?, 0, 'star')", (str(src_media),))
    scan_all(src)
    libraries.create(str(src_media), bring_from={"data": src.data_dir, "name": "src"})
    lib = open_lib(src_media)
    scan_all(lib)
    libraries.bring_when_scanned(lib, str(src_media))
    assert libraries.info(str(src_media))["brought"]["op"]


def test_the_watcher_leaves_the_portable_gallery_out(tmp_path):
    root = media_lib(tmp_path / "Gen")
    app = root / "Model Librarian Gallery"
    (app / "update" / "app.part" / "mlgallery" / "ui").mkdir(parents=True)
    image(app / "update" / "app.part" / "mlgallery" / "ui" / "icon.png", (32, 32))
    (app / "Model Librarian Gallery.exe").write_bytes(b"MZ")
    lib = Library(str(tmp_path / "data"))
    with lib.db.write() as c:
        fid = c.execute("INSERT INTO folders (path, added) VALUES (?, 0)", (str(root),)).lastrowid
    counts = scan.index_files(lib.db, {"id": fid, "path": str(root)}, ["Model Librarian Gallery/update/app.part/mlgallery/ui/icon.png", "sub/0.png"])
    assert counts["added"] == 1 and list(ids_by_name(lib)) == ["0.png"]


def test_two_galleries_keep_each_others_settings(tmp_path):
    from mlgallery.config import Settings
    a, b = Settings(str(tmp_path)), Settings(str(tmp_path))  # one PC, two Galleries (two libraries) open
    a.update({"comfy_url": "http://127.0.0.1:9000"})
    b.update({"thumb_size": 512})
    again = Settings(str(tmp_path))
    assert again.get("comfy_url") == "http://127.0.0.1:9000" and again.get("thumb_size") == 512


def test_bring_into_a_folder_that_is_a_library_already(tmp_path, home):
    lib = Library(str(tmp_path / "data"), str(home))
    root = media_lib(tmp_path / "Gen")
    libraries.create(str(root))

    async def fn(c):
        r = await c.post("/api/libraries/new", headers=H, json={"path": str(root), "bring": True})
        assert (await r.json())["existing"]
    run_app(lib, fn)
    assert libraries.info(str(root))["bring_from"]["data"] == lib.data_dir  # the marks come when it next opens


def test_launcher_reads_library_arguments_like_the_gallery(tmp_path, home, launcher):
    import hashlib
    from test_update import NEXT, app_zip
    root = media_lib(tmp_path / "Gen")
    libraries.create(str(root))
    pkg = tmp_path / "Elsewhere" / "Model Librarian Gallery"
    (pkg / "python").mkdir(parents=True)
    build_portable.make_app(str(pkg / "app"))
    shutil.copy2(launcher, pkg / "Model Librarian Gallery.exe")
    b = app_zip()
    update.stage(str(pkg), b, hashlib.sha256(b).hexdigest(), NEXT)
    hold = libraries.Lock(libraries.data_dir(str(root)))
    assert hold.acquire()
    try:  # --library=...\.mlgallery names the same library as --library ...: its open lock is found
        r = subprocess.run([str(pkg / "Model Librarian Gallery.exe"), "--apply-update-only", f"--library={root / '.mlgallery'}"],
                           capture_output=True, text=True, timeout=60)
        assert r.stdout.strip() == "nothing applied"
    finally:
        hold.release()
