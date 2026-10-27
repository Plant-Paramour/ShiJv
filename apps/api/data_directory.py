"""Resolve an optional workspace data copy without changing the original directory."""
import json
import os
from pathlib import Path
import sqlite3


def default_database_path(directory):
    """Keep legacy data untouched while sharing installed environment settings."""
    path = Path(directory) / 'shiju.db'
    if path.is_file():
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            has_users = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if has_users and version != 100:
            import logging
            target = path.with_name('shiju-local.db')
            logging.getLogger(__name__).warning('检测到旧版数据库 %s；保留原文件，单机版使用 %s。旧数据尚未迁移。', path, target)
            return target
    return path


def default_data_directory():
    workspace_data = Path(__file__).resolve().parents[2] / 'var'
    selection = workspace_data / 'local-data.json'
    if selection.is_file():
        record = json.loads(selection.read_text(encoding='utf-8'))
        same_machine = not record.get('machine') or record['machine'].lower() == os.getenv('COMPUTERNAME', '').lower()
        same_user = not record.get('user_profile') or record['user_profile'].lower() == os.getenv('USERPROFILE', str(Path.home())).lower()
        if same_machine and same_user:
            relative = Path(record['directory'])
            chosen = (workspace_data / relative).resolve()
            if not chosen.is_relative_to(workspace_data.resolve()):
                raise ValueError('工作区数据副本必须位于 var 目录内')
            return chosen
    return Path(os.getenv('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'ShiJu'
