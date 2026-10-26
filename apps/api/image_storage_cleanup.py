"""One-time removal of legacy inline images from stored agent job payloads."""
import hashlib
import json
from apps.agent.image_store import save_images, image_digest

def purge_legacy_chat_images(database):
    with database.connect() as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS image_storage_cleanup (id INTEGER PRIMARY KEY)')
        if connection.execute('SELECT id FROM image_storage_cleanup WHERE id=1').fetchone():
            return
        rows = connection.execute("SELECT id,status,request_stored,request_json FROM jobs WHERE kind='agent_turn'").fetchall()
        for row in rows:
            raw = row['request_stored'] if row.get('request_stored') else row.get('request_json') or '{}'
            payload = json.loads(raw)
            images = payload.pop('images', [])
            if images and row['status'] in {'queued', 'running'}:
                payload['images_ref'] = save_images(images)
            fingerprint = payload.get('idempotency_fingerprint') or {}
            if 'images' in fingerprint:
                fingerprint['image_digest'] = image_digest(fingerprint.pop('images'))
            for message in payload.get('history') or []:
                message.pop('images', None)
                message.pop('image_description', None)
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if json.loads(raw) != payload:
                connection.execute('UPDATE jobs SET request_json=NULL,request_stored=?,request_hash=? WHERE id=?', (canonical, hashlib.sha256(canonical.encode()).hexdigest(), row['id']))
        connection.execute('INSERT INTO image_storage_cleanup(id) VALUES (1)')
