from __future__ import annotations

import json
import re
from pathlib import Path


VERSION_FILE = "meter-version.json"
VERSION_PATTERN = re.compile(r"v\d+\.\d+(?:\.\d+)?\Z")


def read_dataset_version(directory: Path, filename: str, field: str) -> str:
    version_path = directory / filename
    try:
        payload = json.loads(version_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取词谱版本文件: {version_path}") from error

    version = payload.get(field) if isinstance(payload, dict) else None
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version.strip()):
        raise RuntimeError(f"{version_path} 中 {field} 必须使用 v主版本.次版本[.修订版本] 格式")
    return version.strip()


def meter_version(directory: Path) -> str:
    return read_dataset_version(directory, VERSION_FILE, "meter_version")


def rhyme_version(directory: Path) -> str:
    return read_dataset_version(directory, "rhyme-version.json", "rhyme_version")
