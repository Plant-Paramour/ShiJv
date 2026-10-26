def resolve_contents(database, rows, connection=None):
    """Resolve shared bodies without changing the referenced version's metadata."""
    items = [dict(row) for row in rows]
    missing = [item['id'] for item in items if item.get('content') is None and (not item.get('content_stored'))]
    references = {}
    if missing:
        marks = ','.join(('?' for _ in missing))

        def fetch(db):
            return {row['id']: row for row in db.execute(f'SELECT p.id,p.content_reference_id,c.content,c.content_stored FROM poems p JOIN poems c ON c.id=p.content_reference_id WHERE p.id IN ({marks})', missing).fetchall()}
        if connection is not None:
            references = fetch(connection)
        else:
            with database.connect() as db:
                references = fetch(db)
    for item in items:
        body = references.get(item['id'], item)
        if body.get('content_stored'):
            item['content'] = body['content_stored']
        elif body is not item:
            item['content'] = body['content']
        if body is not item:
            item['content_reference_id'] = body['content_reference_id']
        item.pop('content_stored', None)
    return items
