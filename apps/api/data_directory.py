"""Resolve an optional workspace data copy without changing the original directory."""
import json
import os
from pathlib import Path


def default_data_directory():
    workspace_data = Path(__file__).resolve().parents[2] / 'var'
    selection = workspace_data / 'local-data.json'
    if selection.is_file():
        relative = Path(json.loads(selection.read_text(encoding='utf-8'))['directory'])
        chosen = (workspace_data / relative).resolve()
        if not chosen.is_relative_to(workspace_data.resolve()):
            raise ValueError('工作区数据副本必须位于 var 目录内')
        return chosen
    return Path(os.getenv('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'ShiJu'
