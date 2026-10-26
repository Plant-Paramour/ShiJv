"""Single-user visibility predicates for the local edition."""


def poem_access_sql(alias, user_id, *, include_derivatives=True):
    derivation = ''
    derivation_args = ()
    if include_derivatives and user_id is not None:
        # UNION (not UNION ALL) also terminates safely for malformed legacy cycles.
        derivation = (
            " OR EXISTS (WITH RECURSIVE ac_lineage(id,user_id,derived_from_poem_id) AS ("
            f"SELECT ac_seed.id,ac_seed.user_id,ac_seed.derived_from_poem_id FROM poems ac_seed WHERE ac_seed.id={alias}.derived_from_poem_id "
            "UNION SELECT ac_parent.id,ac_parent.user_id,ac_parent.derived_from_poem_id "
            "FROM poems ac_parent JOIN ac_lineage ac_child ON ac_parent.id=ac_child.derived_from_poem_id) "
            "SELECT 1 FROM ac_lineage WHERE user_id=?)"
        )
        derivation_args = (user_id,)
    if user_id is None:
        return f"({alias}.deleted_at IS NULL AND {alias}.is_public=1)", ()
    return (f"({alias}.deleted_at IS NULL AND ({alias}.user_id=?{derivation} OR {alias}.is_public=1))",
            (user_id, *derivation_args))


def visible_poem_ids(connection, poem_ids, user_id, *, include_derivatives=True):
    ids = list(set(poem_ids))
    if not ids:
        return set()
    access, args = poem_access_sql('p', user_id, include_derivatives=include_derivatives)
    marks = ','.join('?' for _ in ids)
    return {row[0] for row in connection.execute(f'SELECT p.id FROM poems p WHERE p.id IN ({marks}) AND {access}', (*ids, *args)).fetchall()}


def validate_poem_references(connection, poem_ids, user_id):
    ids = list(set(poem_ids or []))
    if not ids:
        return
    marks = ','.join('?' for _ in ids)
    # Serialize with visibility/deletion updates before authorizing the whole batch.
    rows = connection.execute(f'SELECT id FROM poems WHERE id IN ({marks})', ids).fetchall()
    # Ancestors may read descendants, but cannot publish their private text for them.
    if len(rows) != len(ids) or visible_poem_ids(connection, ids, user_id, include_derivatives=False) != set(ids):
        raise ValueError('引用作品不存在或无权分享')
