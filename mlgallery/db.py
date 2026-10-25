"""The index: one SQLite file (WAL, so the grid reads while a scan writes). Rebuilt from your media if lost.

Each thread opens its own connection (`Database.connect()`); writes are serialised by one lock, reads run
alongside them. The schema grows by numbered migrations (PRAGMA user_version).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager

MIGRATIONS = [
    # 1: folders you added, and the media files found in them
    """
    CREATE TABLE folders (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        added REAL NOT NULL,
        last_scan REAL,
        online INTEGER NOT NULL DEFAULT 1,
        file_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE files (
        id INTEGER PRIMARY KEY,
        folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
        rel TEXT NOT NULL,              -- path inside the folder, with forward slashes
        name TEXT NOT NULL,
        ext TEXT NOT NULL,
        kind TEXT NOT NULL,             -- image | video
        size INTEGER NOT NULL,
        mtime REAL NOT NULL,
        fp TEXT,                        -- quick fingerprint: size + first and last 64 KB
        width INTEGER,
        height INTEGER,
        duration REAL,
        first_seen REAL NOT NULL,
        last_seen REAL NOT NULL,
        missing INTEGER NOT NULL DEFAULT 0,
        thumb INTEGER NOT NULL DEFAULT 0, -- 0 not made yet, 1 made, -1 couldn't be made, -2 needs ffmpeg
        UNIQUE (folder_id, rel)
    );
    CREATE INDEX files_mtime ON files (missing, mtime DESC);
    CREATE INDEX files_fp ON files (fp);
    CREATE INDEX files_thumb_todo ON files (thumb, mtime DESC) WHERE thumb = 0;
    """,
    # 2: what made each file, the models it used, your own marks and tags, what you viewed, and search
    """
    ALTER TABLE files ADD COLUMN meta INTEGER NOT NULL DEFAULT 0;  -- 0 not read yet, 1 has generation data, 2 none, -1 unreadable
    CREATE INDEX files_meta_todo ON files (meta, mtime DESC) WHERE meta = 0;
    CREATE TABLE generations (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        source TEXT,                    -- comfyui, a1111, novelai, ...
        positive TEXT, negative TEXT,
        seed INTEGER, steps INTEGER, cfg REAL, sampler TEXT, scheduler TEXT,
        width INTEGER, height INTEGER, denoise REAL, clip_skip INTEGER,
        model TEXT,                     -- the checkpoint or diffusion model's name
        nodes INTEGER,                  -- how many nodes the workflow has
        workflow BLOB,                  -- {"prompt", "workflow"} as zlib-compressed JSON, read only when asked for
        info TEXT                       -- the Gallery Info node's entry (JSON), when the workflow had one
    );
    CREATE INDEX generations_seed ON generations (seed);
    CREATE TABLE uses (
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,             -- checkpoint, unet, lora, vae
        name TEXT NOT NULL, file TEXT, hash TEXT,
        strength REAL, clip REAL
    );
    CREATE INDEX uses_name ON uses (kind, name COLLATE NOCASE);
    CREATE INDEX uses_file ON uses (file_id);
    CREATE TABLE marks (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        rating INTEGER,                 -- 1..5, or NULL
        favorite INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'user',  -- 'workflow' while only the Gallery Info node has set them
        updated REAL
    );
    CREATE INDEX marks_rating ON marks (rating);
    CREATE INDEX marks_favorite ON marks (favorite) WHERE favorite = 1;
    CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
    CREATE TABLE file_tags (
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        source TEXT NOT NULL DEFAULT 'user',  -- or 'workflow'
        removed INTEGER NOT NULL DEFAULT 0,   -- a workflow tag you took off: re-reading the file doesn't put it back
        PRIMARY KEY (file_id, tag_id)
    );
    CREATE INDEX file_tags_tag ON file_tags (tag_id) WHERE removed = 0;
    CREATE TABLE views (file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE, at REAL NOT NULL);
    CREATE INDEX views_at ON views (at DESC);
    CREATE VIRTUAL TABLE search USING fts5(name, prompt, negative, models, tags, tokenize = "unicode61 remove_diacritics 2");
    CREATE TRIGGER files_search_gone AFTER DELETE ON files BEGIN DELETE FROM search WHERE rowid = old.id; END;
    """,
    # 3: the prompt split into tags, for statistics
    """
    CREATE TABLE prompt_tags (
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        tag TEXT NOT NULL
    );
    CREATE INDEX prompt_tags_tag ON prompt_tags (tag);
    CREATE INDEX prompt_tags_file ON prompt_tags (file_id);
    """,
    # 4: characters, the rules that recognise them, and which files they're in
    """
    CREATE TABLE characters (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        aliases TEXT NOT NULL DEFAULT '[]',
        cover INTEGER REFERENCES files(id) ON DELETE SET NULL,
        created REAL
    );
    CREATE TABLE character_rules (
        id INTEGER PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,                       -- lora, word, tag
        value TEXT NOT NULL,
        strength TEXT NOT NULL DEFAULT 'strong',  -- strong counts at once; weak waits in To review
        muted INTEGER NOT NULL DEFAULT 0          -- a weak rule you mostly said no to
    );
    CREATE TABLE file_characters (
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        state TEXT NOT NULL,                      -- auto, review, confirmed, rejected
        source TEXT,                              -- lora, word, tag, workflow, user
        at REAL,
        PRIMARY KEY (file_id, character_id)
    );
    CREATE INDEX file_characters_char ON file_characters (character_id, state);
    """,
    # 5: albums, the Gallery's Trash, every file operation (for Undo), and a picture fingerprint for duplicates
    """
    CREATE TABLE albums (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        cover INTEGER REFERENCES files(id) ON DELETE SET NULL,
        created REAL
    );
    CREATE TABLE album_files (
        album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        source TEXT NOT NULL DEFAULT 'user',   -- or 'workflow' (a Gallery Info node)
        removed INTEGER NOT NULL DEFAULT 0,    -- a workflow's album you took it out of: reading the file again doesn't put it back
        added REAL,
        PRIMARY KEY (album_id, file_id)
    );
    CREATE INDEX album_files_file ON album_files (file_id);
    ALTER TABLE files ADD COLUMN trashed REAL;      -- when it went to the Gallery's Trash
    ALTER TABLE files ADD COLUMN trash_rel TEXT;    -- where it is meanwhile (inside the folder's .mlg-trash)
    ALTER TABLE files ADD COLUMN phash TEXT;        -- the picture's fingerprint (from its thumbnail): the same picture saved twice
    CREATE INDEX files_trashed ON files (trashed) WHERE trashed IS NOT NULL;
    CREATE INDEX files_phash ON files (phash) WHERE phash IS NOT NULL;
    CREATE TABLE ops (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        kind TEXT NOT NULL,                     -- move, trash, restore, empty, marks, tags, albums
        summary TEXT NOT NULL,
        data TEXT NOT NULL,                     -- what's needed to undo it (JSON)
        undone REAL
    );
    """,
    # 6: indexes for a big library (the grid's order, filters by model, sampler, steps, review)
    """
    CREATE INDEX files_list ON files (missing, mtime DESC, id DESC);
    CREATE INDEX uses_kind_name ON uses (kind, name, file_id);
    CREATE INDEX generations_sampler ON generations (sampler COLLATE NOCASE);
    CREATE INDEX generations_scheduler ON generations (scheduler COLLATE NOCASE);
    CREATE INDEX generations_steps ON generations (steps);
    CREATE INDEX generations_model ON generations (model);
    CREATE INDEX file_characters_state ON file_characters (state, file_id);
    CREATE INDEX file_tags_file ON file_tags (file_id);
    CREATE INDEX files_name ON files (missing, name COLLATE NOCASE, id);
    CREATE INDEX files_size ON files (missing, size DESC, id DESC);
    CREATE INDEX files_fp_size ON files (fp, size);
    ANALYZE;
    """,
    # 7: a note per file (yours, or brought in from Eagle)
    """
    CREATE TABLE file_notes (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        note TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'user'   -- or 'eagle'
    );
    """,
    # 8: an icon and a colour of your choosing for each folder and album (in the sidebar; nothing on disk changes)
    """
    ALTER TABLE folders ADD COLUMN icon TEXT;
    ALTER TABLE folders ADD COLUMN color TEXT;
    ALTER TABLE albums ADD COLUMN icon TEXT;
    ALTER TABLE albums ADD COLUMN color TEXT;
    """,
    # 9: each picture's colour set (colors.py): its main colours, how much of it each covers, to find it by colour
    """
    CREATE TABLE colors (
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        rank INTEGER NOT NULL,          -- 0 covers the most
        hex TEXT NOT NULL,              -- #rrggbb
        ratio REAL NOT NULL,            -- the share of the picture, 0..1
        l REAL NOT NULL, a REAL NOT NULL, b REAL NOT NULL,  -- CIE Lab: colours that look alike are close
        family TEXT NOT NULL,           -- red, orange, yellow, green, cyan, blue, purple, pink, brown, black, gray, white
        cell INTEGER NOT NULL,          -- its cube in a coarse Lab grid: color:#hex looks in the few cubes around it
        PRIMARY KEY (file_id, rank)
    );
    CREATE INDEX colors_family ON colors (family, ratio);
    CREATE INDEX colors_cell ON colors (cell, ratio);
    ALTER TABLE files ADD COLUMN colors INTEGER NOT NULL DEFAULT 0;  -- 0 not worked out yet, 1 done
    CREATE INDEX files_colors_todo ON files (colors) WHERE colors = 0;
    """,
    # 10: photos and illustrations: the date a picture was taken, Live Photos, and what a library is for (dates.py)
    """
    ALTER TABLE files ADD COLUMN taken REAL;        -- when it was taken (EXIF, a video's recorded date, a date in its name), if known
    ALTER TABLE files ADD COLUMN shot REAL;         -- the date it's grouped and sorted by: taken, or else modified
    ALTER TABLE files ADD COLUMN dated INTEGER NOT NULL DEFAULT 0;  -- 0 its date taken not looked for yet, 1 done
    ALTER TABLE files ADD COLUMN motion INTEGER;    -- a Live Photo: the file id of its short video (which has missing = 3)
    UPDATE files SET shot = mtime;
    CREATE TRIGGER files_shot_default AFTER INSERT ON files WHEN NEW.shot IS NULL
    BEGIN UPDATE files SET shot = COALESCE(NEW.taken, NEW.mtime) WHERE id = NEW.id; END;
    CREATE INDEX files_shot ON files (missing, shot DESC, id DESC);  -- the grid's order, ties and all (as files_list was)
    DROP INDEX files_list;   -- the grid went by mtime: nothing lists by it any more
    DROP INDEX files_mtime;
    CREATE INDEX files_dated_todo ON files (dated, mtime DESC) WHERE dated = 0;  -- the date reader's queue, newest first
    CREATE INDEX files_mov ON files (folder_id) WHERE ext = '.mov';  -- Live Photos: the videos that may be one's
    CREATE INDEX files_motion ON files (motion) WHERE motion IS NOT NULL;
    CREATE TABLE library_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- kind: what it's for (ai, photos, illustrations; comma separated)
    ALTER TABLE characters ADD COLUMN who TEXT NOT NULL DEFAULT 'character';  -- character, or person (in photos)
    ANALYZE;  -- the new index has statistics, so the planner keeps using the right one for each sort (name, size...)
    """,
    # 11: artists, and each picture's artist and source, as found or as you typed them (credits.py); downloads you dismissed
    """
    CREATE TABLE artists (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        aliases TEXT NOT NULL DEFAULT '[]',    -- other names (their handle on another site)
        links TEXT NOT NULL DEFAULT '[]',      -- their pages
        cover INTEGER REFERENCES files(id) ON DELETE SET NULL,
        auto INTEGER NOT NULL DEFAULT 1,       -- 1 made by the Gallery from a name it found (gone again with its last picture); 0 yours
        created REAL
    );
    CREATE TABLE credits (
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        src TEXT NOT NULL,                     -- user, file, download, name, folder: for each field, the first of these that has it wins
        artist TEXT,                           -- the name as found or typed ('' from you: no artist)
        site TEXT,                             -- pixiv, x, deviantart, danbooru...
        post TEXT,                             -- the picture's id there
        url TEXT,                              -- the page it's from
        PRIMARY KEY (file_id, src)
    );
    CREATE INDEX credits_site ON credits (site) WHERE site IS NOT NULL;
    CREATE INDEX artists_cover ON artists (cover) WHERE cover IS NOT NULL;  -- (a file deleted: its artist's cover is found at once, not by a scan)
    ALTER TABLE files ADD COLUMN artist_id INTEGER REFERENCES artists(id) ON DELETE SET NULL;  -- the artist the credits come to
    ALTER TABLE files ADD COLUMN credited INTEGER NOT NULL DEFAULT 0;  -- 0 its artist and source not looked for yet, 1 done
    CREATE INDEX files_artist ON files (artist_id) WHERE artist_id IS NOT NULL;
    CREATE INDEX files_credited_todo ON files (credited, mtime DESC) WHERE credited = 0;
    CREATE TABLE inbox_dismissed (path TEXT PRIMARY KEY COLLATE NOCASE, size INTEGER NOT NULL, mtime REAL NOT NULL);  -- a download not to offer again
    """,
    # 12: smart albums (a search kept), the pictures a swipe review kept, and each file's source as it's shown (the one that wins)
    """
    ALTER TABLE albums ADD COLUMN query TEXT;  -- a smart album: this search, always up to date (NULL: the files you put in it)
    CREATE TABLE reviewed (file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE, at REAL NOT NULL);  -- kept in a swipe review
    ALTER TABLE files ADD COLUMN site TEXT;    -- where it's from, as its details show it (credits.py; site: and has:source go by it)
    UPDATE files SET site = (SELECT NULLIF(cr.site, '') FROM credits cr WHERE cr.file_id = files.id AND (cr.url IS NOT NULL OR cr.site IS NOT NULL)
                             ORDER BY CASE cr.src WHEN 'user' THEN 0 WHEN 'file' THEN 1 WHEN 'download' THEN 2 WHEN 'name' THEN 3 ELSE 4 END LIMIT 1)
     WHERE id IN (SELECT file_id FROM credits);
    CREATE INDEX files_site ON files (site) WHERE site IS NOT NULL;
    """,
]
CACHE = 256  # cached query results kept (each tied to the index as it was)


class Database:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.write_lock = threading.Lock()
        self.version = 0  # goes up with every write: a cached result from an older version is stale
        self._cache: dict = {}
        self._cache_lock = threading.Lock()
        self._lazy: dict = {}           # key -> (version, result): shown at once, recounted behind the scenes
        self._refreshing: set = set()
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            self._migrate(c)

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)  # autocommit; transactions are explicit
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-32768")    # 32 MB of pages per connection
        c.execute("PRAGMA mmap_size=268435456")  # reads straight from the file's pages
        c.execute("PRAGMA temp_store=MEMORY")
        return c

    def cached_lazy(self, key, fn):
        """For the big counts (Statistics, characters, duplicates): the last result at once, even if the index has
        changed since, while a fresh one is worked out in the background for next time. Only the very first
        call waits. -> (result, fresh)"""
        v = self.version
        with self._cache_lock:
            hit = self._lazy.get(key)
            stale = hit is not None and hit[0] != v
            if stale and key not in self._refreshing:
                self._refreshing.add(key)
                threading.Thread(target=self._refresh, args=(key, fn), daemon=True).start()
        if hit is None:
            out = fn()
            with self._cache_lock:
                self._lazy[key] = (v, out)
            return out, True
        return hit[1], not stale

    def user_change(self) -> None:
        """You changed something: the counts are worked out afresh when next asked for (no stale view of your own change)."""
        with self._cache_lock:
            self._lazy.clear()

    def _refresh(self, key, fn) -> None:
        try:
            v = self.version
            out = fn()
            with self._cache_lock:
                self._lazy[key] = (v, out)
        finally:
            with self._cache_lock:
                self._refreshing.discard(key)

    def cached(self, key, fn):
        """fn()'s result, reused until the next write (each write makes every cached result stale)."""
        v = self.version
        with self._cache_lock:
            hit = self._cache.get(key)
        if hit and hit[0] == v:
            return hit[1]
        out = fn()
        with self._cache_lock:
            if len(self._cache) >= CACHE:
                self._cache.clear()
            self._cache[key] = (v, out)
        return out

    def _migrate(self, c: sqlite3.Connection) -> None:
        have = c.execute("PRAGMA user_version").fetchone()[0]
        for n, sql in enumerate(MIGRATIONS[have:], start=have + 1):
            try:  # a migration applies whole or not at all
                c.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={n};\nCOMMIT;")
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise

    @contextmanager
    def write(self):
        """A write transaction on a fresh connection, one at a time."""
        with self.write_lock:
            c = self.connect()
            try:
                c.execute("BEGIN IMMEDIATE")
                yield c
                c.execute("COMMIT")
                if c.total_changes:
                    self.version += 1
            except BaseException:
                c.execute("ROLLBACK")
                raise
            finally:
                c.close()

    @contextmanager
    def read(self):
        c = self.connect()
        try:
            yield c
        finally:
            c.close()
