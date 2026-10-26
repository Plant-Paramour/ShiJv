"""Native SQLite storage for one local workspace."""
import os
from pathlib import Path
import re
import sqlite3
import time
from .observability import record_query


class Database:
    def __init__(self, url):
        self.url = 'sqlite:///' + url.resolve().as_posix() if isinstance(url, Path) else str(url).strip()
        if not self.url.startswith('sqlite:///'):
            raise ValueError('单机版仅支持 sqlite:/// 本地文件')
        self.is_sqlite = True

    def connect(self):
        path = self.url.removeprefix('sqlite:///')
        if path == ':memory:':
            path, uri = 'file:shiju?mode=memory&cache=shared', True
        else:
            path = Path(path).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path, uri = str(path), False
        connection = sqlite3.connect(path, timeout=30, check_same_thread=False, uri=uri)
        connection.row_factory = _row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=30000')
        return _Connection(connection)

    def initialize(self):
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            has_tables = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
            if has_tables and version != 100:
                raise ValueError('这是旧版数据目录，请先转换数据副本或选择新的空目录；原文件未修改')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))

    def check(self):
        with self.connect() as db:
            db.execute('SELECT 1').fetchone()

    def close(self):
        pass


class _Connection:
    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, *args):
        try:
            return self._connection.__exit__(*args)
        finally:
            self._connection.close()

    def execute(self, sql, params=()):
        sql = re.sub(r'\bDELETE\s+FROM\s+(\w+)\s+(\w+)\s+WHERE\b', r'DELETE FROM \1 AS \2 WHERE', sql, flags=re.I)
        started = time.perf_counter()
        try:
            return self._connection.execute(sql, params)
        finally:
            record_query(sql, (time.perf_counter() - started) * 1000)

    def executescript(self, sql):
        return self._connection.executescript(sql)


class _Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def _row(cursor, values):
    return _Row(zip((field[0] for field in cursor.description), values))
