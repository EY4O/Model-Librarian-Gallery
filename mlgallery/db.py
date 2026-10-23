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
            c.execute("BEGIN")  # a migration applies whole or not at all
            for stmt in [s for s in sql.split(";") if s.strip()]:
                c.execute(stmt)
            c.execute(f"PRAGMA user_version={n}")
            c.execute("COMMIT")

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
