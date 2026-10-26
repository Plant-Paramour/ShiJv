from __future__ import annotations

import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask


router = APIRouter(prefix="/v1/backup", tags=["backup"])
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024


@router.get("")
def download_backup(request: Request):
    database_path = Path(request.app.state.settings.database_path)
    data_root = database_path.parent
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="shiju-backup-")
    snapshot = Path(temporary.name) / "shiju.db"
    with closing(sqlite3.connect(str(database_path))) as source, closing(sqlite3.connect(str(snapshot))) as target:
        source.execute("PRAGMA wal_checkpoint(FULL)")
        source.backup(target)
        target.commit()
    archive_path = Path(temporary.name) / "backup.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(snapshot, "shiju.db")
        for folder in ("avatars", "chat-images"):
            root = data_root / folder
            if root.exists():
                for item in root.rglob("*"):
                    if item.is_file():
                        archive.write(item, item.relative_to(data_root).as_posix())
    filename = f"shiju-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return FileResponse(archive_path, media_type="application/zip", filename=filename, background=BackgroundTask(temporary.cleanup))


@router.post("/restore")
async def restore_backup(request: Request):
    database_path = Path(request.app.state.settings.database_path)
    data_root = database_path.parent
    try:
        with tempfile.TemporaryDirectory(prefix="shiju-restore-") as temporary:
            temp_root = Path(temporary)
            archive_path = temp_root / "upload.zip"
            received = 0
            with archive_path.open("wb") as target:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > MAX_BACKUP_BYTES:
                        raise HTTPException(status_code=413, detail="备份文件超过 2 GB")
                    target.write(chunk)
            if received == 0:
                raise ValueError("备份文件为空")
            with zipfile.ZipFile(archive_path) as archive:
                total = 0
                for info in archive.infolist():
                    name = PurePosixPath(info.filename)
                    if name.is_absolute() or ".." in name.parts or "\\" in info.filename:
                        raise ValueError("备份包含无效路径")
                    total += info.file_size
                    if total > MAX_BACKUP_BYTES:
                        raise ValueError("解压后的备份超过 2 GB")
                if "shiju.db" not in archive.namelist():
                    raise ValueError("备份中缺少 SQLite 数据库")
                restored_db = temp_root / "shiju.db"
                restored_db.write_bytes(archive.read("shiju.db"))
                with closing(sqlite3.connect(str(restored_db))) as connection:
                    check = connection.execute("PRAGMA quick_check").fetchone()
                    if not check or check[0] != "ok":
                        raise ValueError("SQLite 数据库校验失败")
                    if connection.execute("PRAGMA user_version").fetchone()[0] != 100:
                        raise ValueError("数据库版本不受支持")
                    if connection.execute("PRAGMA foreign_key_check").fetchone():
                        raise ValueError("备份数据库的外键检查失败")
                database_path.parent.mkdir(parents=True, exist_ok=True)
                with request.app.state.database.connect() as connection:
                    connection.execute("PRAGMA wal_checkpoint(FULL)")
                for suffix in ("-wal", "-shm"):
                    Path(str(database_path) + suffix).unlink(missing_ok=True)
                replacement = data_root / "shiju.restore.db"
                os.replace(restored_db, replacement)
                os.replace(replacement, database_path)
                for folder in ("avatars", "chat-images"):
                    destination = data_root / folder
                    if destination.exists():
                        for child in destination.iterdir():
                            if child.is_file():
                                child.unlink()
                            elif child.is_dir():
                                import shutil
                                shutil.rmtree(child)
                    for info in archive.infolist():
                        prefix = folder + "/"
                        if info.filename.startswith(prefix) and not info.is_dir():
                            relative = PurePosixPath(info.filename).relative_to(folder)
                            target = destination.joinpath(*relative.parts)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(archive.read(info.filename))
    except (zipfile.BadZipFile, OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"恢复失败：{exc}") from exc
    return {"status": "restored", "restart_required": True}
