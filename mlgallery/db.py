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
]


class Database:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.write_lock = threading.Lock()
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            self._migrate(c)

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)  # autocommit; transactions are explicit
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

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
