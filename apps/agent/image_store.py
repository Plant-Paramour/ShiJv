"""Short-lived stored image handoff between API and executor; no image bytes in jobs."""
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

def directory():
    return Path(os.getenv('SHIJU_CHAT_IMAGE_DIR', str(Path(os.getenv('SHIJU_DATABASE_PATH', 'var/shiju.db')).parent / 'chat-images')))

def image_digest(images):
    return hashlib.sha256(json.dumps(images, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def save_images(images):
    if not images:
        return None
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    for path in root.glob('*.image'):
        if path.stat().st_mtime < time.time() - 1800:
            path.unlink(missing_ok=True)
    reference = uuid.uuid4().hex
    (root / f'{reference}.image').write_bytes(json.dumps(images, ensure_ascii=False).encode())
    return reference

def image_path(reference):
    if not re.fullmatch('[a-f0-9]{32}', str(reference)):
        raise ValueError('图片引用无效')
    return directory() / f'{reference}.image'

def load_images(reference):
    if not reference:
        return []
    path = image_path(reference)
    if not path.exists() or path.stat().st_mtime < time.time() - 1800:
        raise ValueError('图片已过期，请重新添加图片')
    return json.loads(path.read_bytes())

def remove_images(reference):
    if reference:
        image_path(reference).unlink(missing_ok=True)
