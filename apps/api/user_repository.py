from __future__ import annotations
from pathlib import Path
import json
import time
import uuid
from difflib import SequenceMatcher
from typing import Any
from shiju.rewriting.parser import parse_poem
from .database import Database
from .poem_content import resolve_contents
from .local_visibility import visible_poem_ids, poem_access_sql
from .poem_context import public_poem_context

class PoemNotFoundError(Exception):
    """Raised only when the requested poem is missing or inaccessible."""

class UserRepository:

    def __init__(self, database: Database):
        self.database = database

    def ensure_local_profile(self):
        from .local_identity import ensure_identity
        self._local_user_id = ensure_identity(self)
        return self.local_profile()

    def local_profile(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_identity'").fetchone()
            row = connection.execute('SELECT u.* FROM users u JOIN local_identity l ON l.user_id=u.id WHERE l.singleton=1').fetchone() if exists else None
        if row is None:
            return self.ensure_local_profile()
        return self._user(row)

    def create_local_profile(self, display_name: str='请输入用户名') -> dict[str, Any]:
        return self.local_profile()

    @staticmethod
    def _next_public_id(connection) -> str:
        while True:
            value = uuid.uuid4().hex[:8]
            if not connection.execute('SELECT 1 FROM users WHERE public_id=?', (value,)).fetchone():
                return value

    def migrate_legacy_tree(self) -> None:
        """为旧线性对话补齐父链，并建立可继续使用的主分支。"""
        with self.database.connect() as connection:
            conversations = connection.execute('SELECT id FROM conversations').fetchall()
            for conversation in conversations:
                conversation_id = conversation[0]
                branch = connection.execute('SELECT id FROM conversation_branches WHERE conversation_id=? LIMIT 1', (conversation_id,)).fetchone()
                if branch is not None:
                    continue
                rows = connection.execute('SELECT id,parent_message_id FROM messages WHERE conversation_id=? ORDER BY created_at,id', (conversation_id,)).fetchall()
                previous = None
                for row in rows:
                    if row[1] is None and previous is not None:
                        connection.execute('UPDATE messages SET parent_message_id=?,updated_at=COALESCE(updated_at,created_at) WHERE id=?', (previous, row[0]))
                    previous = row[0]
                now = time.time()
                connection.execute('INSERT INTO conversation_branches(id,conversation_id,head_message_id,title,is_active,created_at,updated_at) VALUES (?,?,?,?,?,?,?)', (str(uuid.uuid4()), conversation_id, previous, '主线', 1, now, now))

    @staticmethod
    def _user(row) -> dict[str, Any]:
        return {'id': row['id'], 'public_id': row['public_id'] if 'public_id' in row.keys() else None, 'username': row['username'], 'display_name': row['display_name'] or row['username'], 'created_at': row['created_at'], 'role': 'user', 'bio': row['bio'] if 'bio' in row.keys() else '', 'avatar_url': row['avatar_url'] if 'avatar_url' in row.keys() else None}

    def update_profile(self, user_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {key: fields[key] for key in ('display_name', 'bio', 'avatar_url') if key in fields}
        if not allowed:
            return self.get(user_id)
        assignments = ','.join((f'{key}=?' for key in allowed))
        with self.database.connect() as connection:
            connection.execute(f'UPDATE users SET {assignments} WHERE id=?', (*allowed.values(), user_id))
            row = connection.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        return self._user(row) if row else None

    def get(self, user_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        return self._user(row) if row else None

    def get_by_public_id(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM users WHERE public_id = ?', (str(public_id).strip(),)).fetchone()
        return self._user(row) if row else None

    def list_conversations(self, user_id: str, *, include_deleted: bool=False, sort: str='updated') -> list[dict[str, Any]]:
        order = 'created_at ASC' if sort == 'created' else 'COALESCE(activity_at, created_at) DESC, created_at DESC'
        deleted_clause = '' if include_deleted else 'AND deleted_at IS NULL'
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT id, title, folder_id, deleted_at, created_at, updated_at, activity_at FROM conversations WHERE user_id = ? {deleted_clause} ORDER BY {order}", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def create_conversation(self, user_id: str, title: str='新建对话') -> dict[str, Any]:
        now = time.time()
        conversation_id = str(uuid.uuid4())
        with self.database.connect() as connection:
            connection.execute('INSERT INTO conversations(id, user_id, title, created_at, updated_at, activity_at) VALUES (?, ?, ?, ?, ?, ?)', (conversation_id, user_id, title[:15] or '新建对话', now, now, now))
            connection.execute('INSERT INTO conversation_branches(id, conversation_id, root_message_id, head_message_id, title, is_active, created_at, updated_at) VALUES (?, ?, NULL, NULL, ?, 1, ?, ?)', (str(uuid.uuid4()), conversation_id, '主线', now, now))
        return {'id': conversation_id, 'title': title[:15] or '新建对话', 'created_at': now, 'updated_at': now}

    def get_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute('SELECT id, title, folder_id, deleted_at, created_at, updated_at, source_conversation_id, user_id FROM conversations WHERE id = ? AND user_id = ? AND deleted_at IS NULL', (conversation_id, user_id)).fetchone()
        return dict(row) if row else None

    def add_message(self, conversation_id: str, user_id: str, role: str, content: str, *, status: str='completed', job_id: str | None=None, parent_message_id: str | None=None, turn_id: str | None=None) -> dict[str, Any]:
        now = time.time()
        message_id = str(uuid.uuid4())
        with self.database.connect() as connection:
            version_group_id = message_id
            version_number = 1
            if parent_message_id:
                parent = connection.execute('SELECT id, role, version_group_id, version_number FROM messages WHERE id = ? AND conversation_id = ?', (parent_message_id, conversation_id)).fetchone()
                if parent is None:
                    raise ValueError('parent message not found in conversation')
                archived = connection.execute('SELECT version_group_id, MAX(version_number) AS version_number FROM message_versions WHERE id = ? AND conversation_id = ? GROUP BY version_group_id', (parent_message_id, conversation_id)).fetchone()
                source = parent if parent and parent['role'] == role == 'user' else None
                if source:
                    version_group_id = source['version_group_id'] or parent_message_id
                    version_number = int(source['version_number'] or 1) + 1
            inserted = connection.execute('INSERT INTO messages(id, conversation_id, role, content, content_stored, created_at, status, job_id, version_group_id, version_number, parent_message_id, turn_id, updated_at, author_id) SELECT ?, id, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? FROM conversations WHERE id = ? AND user_id = ?', (message_id, role, content, now, status, job_id, version_group_id, version_number, parent_message_id, turn_id, now, user_id if role == 'user' else None, conversation_id, user_id))
            if inserted.rowcount != 1:
                raise ValueError('conversation not found')
            connection.execute("UPDATE conversations SET updated_at = ?, activity_at = CASE WHEN ? = 'user' THEN ? ELSE activity_at END, title = CASE WHEN title = '新建对话' AND ? = 'user' THEN substr(?, 1, 15) ELSE title END WHERE id = ? AND user_id = ?", (now, role, now, role, content, conversation_id, user_id))
            if version_group_id != message_id:
                connection.execute('INSERT INTO message_versions(id, conversation_id, version_group_id, version_number, role, content, content_stored, created_at, author_id) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)', (str(uuid.uuid4()), conversation_id, version_group_id, version_number, role, content, now, user_id if role == 'user' else None))
        return {'id': message_id, 'role': role, 'content': content, 'status': status, 'job_id': job_id, 'created_at': now, 'version_group_id': version_group_id, 'version_number': version_number, 'parent_message_id': parent_message_id, 'turn_id': turn_id, 'updated_at': now, 'author_id': user_id if role == 'user' else None}

    def update_message(self, message_id: str, conversation_id: str, user_id: str, *, content: str, status: str, job_id: str | None=None) -> None:
        now = time.time()
        with self.database.connect() as connection:
            updated = connection.execute('\n                UPDATE messages\n                SET content = NULL, content_stored = ?, status = ?, job_id = ?, updated_at = ?\n                WHERE id = ? AND conversation_id IN (\n                    SELECT id FROM conversations WHERE id = ? AND user_id = ?\n                )\n                ', (content, status, job_id, now, message_id, conversation_id, user_id))
            if updated.rowcount != 1:
                raise ValueError('message not found')
            connection.execute("UPDATE conversations SET updated_at = ?, activity_at = CASE WHEN EXISTS (SELECT 1 FROM messages WHERE id = ? AND role = 'user') THEN ? ELSE activity_at END WHERE id = ? AND user_id = ?", (now, message_id, now, conversation_id, user_id))

    def truncate_messages_from(self, message_id: str, conversation_id: str, user_id: str) -> bool:
        """Remove an edited user message and every response after it."""
        now = time.time()
        with self.database.connect() as connection:
            target = connection.execute("\n                SELECT m.created_at FROM messages m\n                JOIN conversations c ON c.id = m.conversation_id\n                WHERE m.id = ? AND m.conversation_id = ? AND c.user_id = ? AND m.role = 'user'\n                ", (message_id, conversation_id, user_id)).fetchone()
            if target is None:
                return False
            current = connection.execute('SELECT id, version_group_id, version_number, role, content, content_stored, created_at, author_id FROM messages WHERE id = ? AND conversation_id = ?', (message_id, conversation_id)).fetchone()
            if current:
                connection.execute('INSERT INTO message_versions(id, conversation_id, version_group_id, version_number, role, content, content_stored, created_at, author_id) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)', (current['id'], conversation_id, current['version_group_id'] or current['id'], current['version_number'], current['role'], current.get('content_stored') and current['content_stored'] or current['content'] or '', current['created_at'], current['author_id']))
            connection.execute('\n                DELETE FROM messages\n                WHERE conversation_id = ? AND (created_at > ? OR (created_at = ? AND id >= ?))\n                ', (conversation_id, target['created_at'], target['created_at'], message_id))
            connection.execute('UPDATE conversations SET updated_at = ?, activity_at = ? WHERE id = ? AND user_id = ?', (now, now, conversation_id, user_id))
        return True

    def list_messages(self, conversation_id: str, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('SELECT m.id, m.role, m.content, m.content_stored, m.status, m.job_id, m.created_at, m.updated_at, m.version_group_id, m.version_number, m.parent_message_id, m.turn_id, m.author_id, c.user_id AS conversation_owner_id FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE m.conversation_id = ? AND c.user_id = ? ORDER BY m.created_at, m.id', (conversation_id, user_id)).fetchall()
            versions = connection.execute('SELECT version_group_id, version_number, role, content, content_stored, created_at, author_id FROM message_versions WHERE conversation_id = ? ORDER BY version_group_id, version_number', (conversation_id,)).fetchall()
            branches = [dict(row) for row in connection.execute('SELECT id,root_message_id,head_message_id FROM conversation_branches WHERE conversation_id=?', (conversation_id,)).fetchall()]
        by_group: dict[str, list[dict[str, Any]]] = {}
        for version in versions:
            item = dict(version)
            if item.get('content_stored'):
                item['content'] = item['content_stored']
            item.pop('content_stored', None)
            by_group.setdefault(item['version_group_id'], []).append(item)
        result = []
        for row in rows:
            item = dict(row)
            if item.get('content_stored'):
                item['content'] = item['content_stored']
            item.pop('content_stored', None)
            group = item.get('version_group_id') or item['id']
            history = [version for version in by_group.get(group, []) if version['role'] == item['role']]
            if not any((v['version_number'] == item.get('version_number') for v in history)):
                if item['role'] == 'user':
                    history.append({'version_group_id': group, 'version_number': item.get('version_number', 1), 'role': item['role'], 'content': item['content'], 'created_at': item['created_at'], 'author_id': item['author_id']})
            history.sort(key=lambda v: v['version_number'])
            item['versions'] = history if item['role'] == 'user' else []
            result.append(item)
        with self.database.connect() as connection:
            authors = {row['id']: self._user(row) for row in connection.execute('SELECT * FROM users WHERE id IN (SELECT DISTINCT author_id FROM messages WHERE conversation_id=? AND author_id IS NOT NULL)', (conversation_id,)).fetchall()}
        for item in result:
            item['author'] = authors.get(item.get('author_id'))
        by_id = {item['id']: item for item in result}
        branch_members: dict[str, str] = {}
        for branch in branches:
            current_id = branch.get('head_message_id')
            visited: set[str] = set()
            while current_id and current_id not in visited:
                visited.add(current_id)
                current = by_id.get(current_id)
                if current is None:
                    break
                if current_id == branch.get('root_message_id') or current_id not in branch_members:
                    branch_members[current_id] = branch['id']
                current_id = current.get('parent_message_id')
        by_parent: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
        for item in result:
            by_parent.setdefault((item.get('parent_message_id'), item['role']), []).append(item)
        for item in result:
            siblings = by_parent.get((item.get('parent_message_id'), item['role']), [])
            item['branch_id'] = branch_members.get(item['id'])
            item['siblings'] = [{'id': sibling['id'], 'content': sibling['content'], 'created_at': sibling['created_at'], 'branch_id': branch_members.get(sibling['id'])} for sibling in siblings]
        return result

    def get_message(self, message_id: str, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute('SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=? AND m.conversation_id=? AND c.user_id=?', (message_id, conversation_id, user_id)).fetchone()
        return dict(row) if row else None

    def message_exists(self, message_id: str | None, conversation_id: str) -> bool:
        if not message_id:
            return False
        with self.database.connect() as connection:
            return connection.execute('SELECT 1 FROM messages WHERE id=? AND conversation_id=?', (message_id, conversation_id)).fetchone() is not None

    def resolve_conversation_access(self, conversation_id: str, actor_id: str) -> str | None:
        """返回本地会话所有者。"""
        with self.database.connect() as connection:
            row = connection.execute("SELECT c.user_id FROM conversations c WHERE c.id=? AND c.deleted_at IS NULL AND c.user_id=?", (conversation_id, actor_id)).fetchone()
        return row[0] if row else None

    def conversation_access(self, conversation_id: str, actor_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT c.*, u.username AS owner_username, u.display_name AS owner_display_name, u.avatar_url AS owner_avatar_url FROM conversations c JOIN users u ON u.id=c.user_id WHERE c.id=? AND c.deleted_at IS NULL AND c.user_id=?", (conversation_id, actor_id)).fetchone()
        return dict(row) if row else None

    def list_message_tree(self, conversation_id: str, user_id: str) -> list[dict[str, Any]]:
        messages = self.list_messages(conversation_id, user_id)
        children: dict[str | None, list[str]] = {}
        for message in messages:
            children.setdefault(message.get('parent_message_id'), []).append(message['id'])
        for message in messages:
            message['children_ids'] = children.get(message['id'], [])
            message['depth'] = 0
            parent_id = message.get('parent_message_id')
            seen: set[str] = set()
            while parent_id and parent_id not in seen:
                seen.add(parent_id)
                parent = next((item for item in messages if item['id'] == parent_id), None)
                if parent is None:
                    message['tree_error'] = 'missing_parent'
                    break
                message['depth'] += 1
                parent_id = parent.get('parent_message_id')
            if parent_id in seen:
                message['tree_error'] = 'cycle'
        return messages

    def create_branch(self, conversation_id: str, user_id: str, *, root_message_id: str | None=None, head_message_id: str | None=None, title: str='主线', activate: bool=True) -> dict[str, Any]:
        branch_id = str(uuid.uuid4())
        now = time.time()
        with self.database.connect() as connection:
            if connection.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=? AND deleted_at IS NULL', (conversation_id, user_id)).fetchone() is None:
                raise ValueError('conversation not found')
            if activate:
                connection.execute('UPDATE conversation_branches SET is_active=0 WHERE conversation_id=?', (conversation_id,))
            connection.execute('INSERT INTO conversation_branches(id,conversation_id,root_message_id,head_message_id,title,is_active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)', (branch_id, conversation_id, root_message_id, head_message_id, title[:80] or '主线', 1 if activate else 0, now, now))
        return {'id': branch_id, 'conversation_id': conversation_id, 'root_message_id': root_message_id, 'head_message_id': head_message_id, 'title': title[:80] or '主线', 'is_active': activate, 'created_at': now, 'updated_at': now}

    def list_branches(self, conversation_id: str, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('SELECT b.* FROM conversation_branches b JOIN conversations c ON c.id=b.conversation_id WHERE b.conversation_id=? AND c.user_id=? ORDER BY b.created_at,b.id', (conversation_id, user_id)).fetchall()
        return [dict(row) for row in rows]

    def get_active_branch(self, conversation_id: str, user_id: str) -> dict[str, Any]:
        branches = self.list_branches(conversation_id, user_id)
        active = next((item for item in branches if item['is_active']), None)
        if active:
            return active
        return self.create_branch(conversation_id, user_id)

    def ensure_branch_for_message(self, conversation_id: str, user_id: str, message_id: str | None=None) -> dict[str, Any]:
        branch = self.get_active_branch(conversation_id, user_id)
        if message_id:
            with self.database.connect() as connection:
                row = connection.execute('SELECT id FROM messages WHERE id=? AND conversation_id=?', (message_id, conversation_id)).fetchone()
            if row is None:
                raise ValueError('message not found')
            branch = self.create_branch(conversation_id, user_id, root_message_id=message_id, head_message_id=message_id, title='编辑分支')
        return branch

    def list_path_messages(self, conversation_id: str, user_id: str, head_message_id: str | None=None) -> list[dict[str, Any]]:
        messages = self.list_messages(conversation_id, user_id)
        by_id = {item['id']: item for item in messages}
        if head_message_id is None:
            return []
        path = []
        current = by_id.get(head_message_id)
        while current:
            path.append(current)
            current = by_id.get(current.get('parent_message_id'))
        path.reverse()
        return path

    def activate_branch(self, branch_id: str, conversation_id: str, user_id: str) -> bool:
        with self.database.connect() as connection:
            valid = connection.execute('SELECT 1 FROM conversation_branches b JOIN conversations c ON c.id=b.conversation_id WHERE b.id=? AND b.conversation_id=? AND c.user_id=?', (branch_id, conversation_id, user_id)).fetchone()
            if not valid:
                return False
            connection.execute('UPDATE conversation_branches SET is_active=0 WHERE conversation_id=?', (conversation_id,))
            connection.execute('UPDATE conversation_branches SET is_active=1,updated_at=? WHERE id=?', (time.time(), branch_id))
        return True

    def activate_branch_for_message(self, message_id: str, conversation_id: str, user_id: str) -> str | None:
        """根据目标消息所在的分支激活分支，避免前端使用过期 branch_id。"""
        branches = self.list_branches(conversation_id, user_id)
        messages = self.list_messages(conversation_id, user_id)
        by_id = {item['id']: item for item in messages}
        active = next((item for item in branches if item['is_active']), None)
        candidates = []
        for branch in branches:
            current_id = branch.get('head_message_id')
            visited = set()
            while current_id and current_id not in visited:
                visited.add(current_id)
                if current_id == message_id:
                    candidates.append(branch)
                    break
                current = by_id.get(current_id)
                if current is None:
                    break
                current_id = current.get('parent_message_id')
            if branch.get('root_message_id') == message_id and branch not in candidates:
                candidates.append(branch)
        candidate = next((item for item in candidates if not active or item['id'] != active['id']), None)
        if candidate is None:
            candidate = next(iter(candidates), None)
        if candidate is None or not self.activate_branch(candidate['id'], conversation_id, user_id):
            return None
        return candidate['id']

    def update_branch_head(self, branch_id: str, message_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute('UPDATE conversation_branches SET head_message_id=?,updated_at=? WHERE id=?', (message_id, time.time(), branch_id))

    def update_branch_root_if_empty(self, branch_id: str, message_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute('UPDATE conversation_branches SET root_message_id=? WHERE id=? AND root_message_id IS NULL', (message_id, branch_id))

    def get_rewrite_source(self, poem_id: str, user_id: str, project_root) -> dict:
        from shiju.rewriting.source import numbered_source
        value = self.get_poem_detail(poem_id, user_id)
        if not value or value.get('deleted'):
            raise ValueError('ID 不存在')
        context = {**(value.get('prosody_context') or {}), **{key: value[key] for key in ('meter_type', 'form_name', 'variant_name')}}
        context.setdefault('rhyme_dict_name', 'Xinyun')
        return {**numbered_source(value['content'], context, project_root, poem_id=value['id'], title=value['title']), 'task_options': context.get('task_options') or {}}

    def lock_rewrite_source(self, payload: dict, user_id: str, project_root) -> dict:
        from shiju.rewriting.source import select_rewrite_lines
        source_id = payload.get('source_poem_id')
        if not source_id:
            if payload.get('original_text'):
                raise ValueError('改写必须提供作品 ID')
            return payload
        source = self.get_rewrite_source(source_id, user_id, project_root)
        if payload.get('original_text') and payload['original_text'] != source['original_text']:
            raise ValueError('原文与来源作品不一致')
        for key in ('meter_type', 'form_name', 'variant_name'):
            if key == 'variant_name' and (not payload.get(key)) and (source.get('meter_type') == '宋词'):
                from shiju.data import MeterTemplateRepository
                default_variant = MeterTemplateRepository(Path(project_root) / 'Songci_Meter').get(source['form_name']).variant_name
                if default_variant == source.get(key):
                    payload[key] = default_variant
            if payload.get(key) != source.get(key):
                raise ValueError('重写不能改变来源作品的诗体、词牌或变体')
        targets = payload.get('target_line_numbers') or []
        chosen = [line for line in source['lines'] if line['number'] in targets]
        if len(chosen) != len(targets):
            raise ValueError('重写句号不存在或重复')
        selection = select_rewrite_lines(source, payload.get('source_version'), [line['line_id'] for line in chosen], {line['line_id']: line['text'] for line in chosen})
        locked = {**payload, **selection, 'original_text': source['original_text'], 'source_title': source['title']}
        if payload.get('sentence_inputs') is not None:
            locked['fixed_lines'] = payload.get('fixed_lines') or {}
        return locked

    def register_rewrite_original(self, content: str, context: dict, user_id: str, project_root, title: str='待改写原作') -> dict:
        from shiju.rewriting.source import numbered_source
        source = numbered_source(content, context, project_root, title=title)
        for existing in self.list_poems(user_id):
            if existing['content'] == content and existing['meter_type'] == source['meter_type'] and (existing['form_name'] == source['form_name']):
                existing_source = self.get_rewrite_source(existing['id'], user_id, project_root)
                if existing_source['source_version'] == source['source_version']:
                    return existing_source
        poem_id = str(uuid.uuid4())
        with self.database.connect() as db:
            db.execute('INSERT INTO poems(id,user_id,title,content_stored,meter_type,form_name,variant_name,created_at,work_type,prosody_context_stored) VALUES (?,?,?,?,?,?,?,?,?,?)', (poem_id, user_id, title, content, source['meter_type'], source['form_name'], source['variant_name'], time.time(), source['meter_type'], json.dumps(context, ensure_ascii=False)))
        return self.get_rewrite_source(poem_id, user_id, project_root)

    def save_poem(self, user_id: str, job_id: str, job_request: dict[str, Any], result: dict[str, Any]) -> None:
        candidates = result.get('candidates') or []
        if not candidates and result.get('full_text'):
            candidates = [{'text': result['full_text']}]
        with self.database.connect() as connection:
            for ordinal, candidate in enumerate(candidates, start=1):
                self._save_candidate_poem(connection, user_id, job_id, ordinal, job_request, candidate, candidate.get('evaluation'))

    def save_candidate_poem(self, user_id: str, job_id: str, ordinal: int, job_request: dict[str, Any], candidate: dict[str, Any], evaluation: dict[str, Any] | None=None) -> None:
        with self.database.connect() as connection:
            self._save_candidate_poem(connection, user_id, job_id, ordinal, job_request, candidate, evaluation)

    @staticmethod
    def _save_candidate_poem(connection, user_id, job_id, ordinal, job_request, candidate, evaluation) -> None:
        content = str(candidate.get('content') or candidate.get('text') or candidate.get('full_text') or candidate.get('display_text') or '').strip()
        if not content:
            return
        candidate_title = str(candidate.get('title') or '').strip()
        title = candidate_title or '未命名诗作'
        meter_type = str(job_request.get('meter_type') or '')
        work_type = str(job_request.get('work_type') or meter_type or '其他')
        evaluation_json = json.dumps(evaluation, ensure_ascii=False, separators=(',', ':')) if evaluation else None
        variant_name = str(job_request.get('variant_name') or '').strip() or None
        source_id = job_request.get('source_poem_id')
        root_id = None
        forest_id = None
        source_content = None
        if job_request.get('original_text'):
            from shiju.rewriting.source import validate_rewrite_result
            content = validate_rewrite_result(job_request, content)
        if source_id:
            source = connection.execute('SELECT * FROM poems WHERE id=?', (source_id,)).fetchone()
            if not source or source['deleted_at'] is not None or (not UserRepository._poem_visible_to(connection, source, user_id)):
                raise ValueError('无法访问来源作品，不能加入修改树')
            from shiju.rewriting.source import poem_body
            source_content = poem_body(resolve_contents(None, [source], connection)[0]['content'])
            if source_content != job_request.get('original_text'):
                raise ValueError('来源作品与提交时的原文不一致')
            title = str(source['title'] or '未命名诗作').strip() or '未命名诗作'
            root_id = source['root_poem_id'] or source['id']
            forest_id = source['forest_id']
        else:
            job = connection.execute('SELECT forest_version,kind,conversation_id FROM jobs WHERE id=?', (job_id,)).fetchone()
            if job and job['forest_version'] == 1:
                forest_id = job['conversation_id'] if job['kind'] == 'generate' and job['conversation_id'] else job_id
        saved = connection.execute('\n            INSERT INTO poems(id,user_id,job_id,title,content,content_stored,meter_type,form_name,variant_name,created_at,work_type,candidate_ordinal,evaluation_json,prosody_context_json,evaluation_stored,prosody_context_stored,parent_poem_id,root_poem_id,forest_id,derived_from_poem_id)\n            VALUES (?,?,?,?,NULL,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?,?)\n            ON CONFLICT(job_id,candidate_ordinal) DO UPDATE SET\n                title=excluded.title,content=excluded.content,content_stored=excluded.content_stored,meter_type=excluded.meter_type,\n                form_name=excluded.form_name,variant_name=excluded.variant_name,work_type=excluded.work_type,\n                evaluation_json=COALESCE(excluded.evaluation_json,poems.evaluation_json),\n                evaluation_stored=COALESCE(excluded.evaluation_stored,poems.evaluation_stored),\n                prosody_context_stored=excluded.prosody_context_stored,\n                parent_poem_id=excluded.parent_poem_id,root_poem_id=excluded.root_poem_id,forest_id=excluded.forest_id,derived_from_poem_id=excluded.derived_from_poem_id\n            RETURNING id\n            ', (str(uuid.uuid4()), user_id, job_id, title, content, meter_type, job_request.get('form_name', ''), variant_name, time.time(), work_type, ordinal, evaluation_json if evaluation_json else None, json.dumps(job_request, ensure_ascii=False), source_id, root_id, forest_id, source_id)).fetchone()
        if source_content is not None:
            old_lines, new_lines = (parse_poem(source_content).lines, parse_poem(content).lines)
            connection.execute('DELETE FROM poem_revision_lines WHERE child_poem_id=?', (saved['id'],))
            change_index = 0
            for old, new in zip(old_lines, new_lines):
                if old.source_text + old.separator == new.source_text + new.separator:
                    continue
                change_index += 1
                connection.execute('INSERT INTO poem_revision_lines(child_poem_id,change_index,old_line_number,new_line_number,old_text_stored,new_text_stored) VALUES (?,?,?,?,?,?)', (saved['id'], change_index, old.number, new.number, old.source_text + old.separator, new.source_text + new.separator))

    @staticmethod
    def _poem_visible_to(connection, poem, user_id: str) -> bool:
        return poem['id'] in visible_poem_ids(connection, [poem['id']], user_id)

    def create_poem_revision(self, source_id: str, user_id: str, title: str, content: str, evaluation: dict[str, Any], *, sequence: str | None=None, variant_name: str | None=None, variant_provided: bool=False, forest_reference: bool=False, revision_source_id: str | None=None) -> dict[str, Any]:
        title, content = (title.strip(), content.strip())
        if not title or not content:
            raise ValueError('标题和正文不能为空')
        if sequence is not None:
            sequence = sequence.strip()
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if forest_reference:
                if not revision_source_id:
                    raise ValueError('必须指定实际修改来源作品')
                source = connection.execute('SELECT * FROM poems WHERE id=? AND (forest_id=? OR id=? OR root_poem_id=?)', (revision_source_id, source_id, source_id, source_id)).fetchone()
                if source is not None and (source['deleted_at'] is not None or not self._poem_visible_to(connection, source, user_id)):
                    source = None
            else:
                source = connection.execute('SELECT * FROM poems WHERE id=?', (source_id,)).fetchone()
                if source is not None and source['deleted_at'] is not None:
                    raise ValueError('作者已删除该作品')
                if source is not None and (not self._poem_visible_to(connection, source, user_id)):
                    source = None
            if source is None:
                raise PoemNotFoundError(source_id)
            source_body = source
            if source['content_reference_id']:
                source_body = connection.execute('SELECT * FROM poems WHERE id=?', (source['content_reference_id'],)).fetchone()
            source_content = source_body['content_stored'] if source_body.get('content_stored') else source_body['content']
            source_sequence = source['sequence_stored'] if source.get('sequence_stored') else ''
            selected_sequence = source_sequence if sequence is None else sequence
            selected_variant = source['variant_name']
            if variant_provided:
                selected_variant = str(variant_name or '').strip() or None
                if selected_variant:
                    if source['meter_type'] != '宋词':
                        raise ValueError('只有宋词可以选择词牌变体')
                    from pathlib import Path
                    from shiju.data import MeterTemplateRepository
                    selected_variant = MeterTemplateRepository(Path(__file__).resolve().parents[2] / 'Songci_Meter').get(source['form_name'], selected_variant).variant_name
            if title == source['title'] and content == source_content and (selected_variant == source['variant_name']) and (selected_sequence == source_sequence):
                raise ValueError('标题和正文没有变化')
            root_id = source['root_poem_id'] or source['id']
            forest_id = source['forest_id'] or root_id
            if not source['forest_id']:
                connection.execute('UPDATE poems SET forest_id=? WHERE id=? OR root_poem_id=?', (forest_id, root_id, root_id))
            if forest_reference:
                nodes = connection.execute('SELECT * FROM poems WHERE forest_id=? ORDER BY created_at,id', (forest_id,)).fetchall()
            else:
                nodes = connection.execute('SELECT * FROM poems WHERE forest_id=? ORDER BY created_at,id', (forest_id,)).fetchall()
            content_reference_id = None
            by_id = {node['id']: node for node in nodes}
            for node in nodes:
                if node['deleted_at'] is not None or not self._poem_visible_to(connection, node, user_id):
                    continue
                body_node = node
                if node['content_reference_id']:
                    body_node = by_id.get(node['content_reference_id'])
                    if body_node is None:
                        body_node = connection.execute('SELECT * FROM poems WHERE id=?', (node['content_reference_id'],)).fetchone()
                    if body_node is None:
                        continue
                body = body_node['content_stored'] if body_node.get('content_stored') else body_node['content']
                if body == content and body_node['id'] != source['id']:
                    content_reference_id = body_node['id']
                    break
            context_json = source['prosody_context_stored'] if source.get('prosody_context_stored') else source['prosody_context_json']
            if not context_json and source['job_id']:
                job = connection.execute('SELECT request_json,request_stored FROM jobs WHERE id=?', (source['job_id'],)).fetchone()
                context_json = (job['request_stored'] if job['request_stored'] else job['request_json']) if job else None
            if source['user_id'] != user_id:
                context_json = json.dumps(public_poem_context(json.loads(context_json or '{}')), ensure_ascii=False)
            poem_id = str(uuid.uuid4())
            parent_poem_id = None
            revision_root_id = None
            if variant_provided:
                context = json.loads(context_json or '{}')
                context['variant_name'] = selected_variant
                context['task_options'] = {**(context.get('task_options') or {}), 'variant_name': selected_variant}
                context_json = json.dumps(context, ensure_ascii=False)
            connection.execute('INSERT INTO poems(id,user_id,job_id,title,content,content_stored,meter_type,form_name,variant_name,sequence_stored,created_at,work_type,candidate_ordinal,evaluation_json,parent_poem_id,root_poem_id,prosody_context_json,evaluation_stored,prosody_context_stored,content_reference_id,forest_id,derived_from_poem_id) VALUES (?, ?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, ?, ?, ?, ?, ?)', (poem_id, user_id, title, None if content_reference_id else content, source['meter_type'], source['form_name'], selected_variant, selected_sequence if selected_sequence else None, time.time(), source['work_type'], parent_poem_id, revision_root_id, json.dumps(evaluation, ensure_ascii=False), context_json if context_json else None, content_reference_id, forest_id, source['id']))
        return self.get_poem_detail(poem_id, user_id)

    def get_poem_detail(self, poem_id: str, viewer_id: str | None=None) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute('SELECT p.*,u.username AS author_username,u.display_name AS author_display_name,ou.id AS original_author_id,ou.display_name AS original_author_name FROM poems p JOIN users u ON u.id=p.user_id LEFT JOIN poems root ON root.id=COALESCE(p.root_poem_id,p.id) LEFT JOIN users ou ON ou.id=root.user_id WHERE p.id=?', (poem_id,)).fetchone()
            if row is None:
                return None
            if row['deleted_at'] is not None:
                return {'id': poem_id, 'deleted': True, 'message': '作者已删除该作品', 'original_author_name': row['original_author_name']}
            if viewer_id and (not self._poem_visible_to(connection, row, viewer_id)):
                return None
            value = self._poem_row(row, connection, viewer_id=viewer_id)
            value['favorite'] = bool(viewer_id and connection.execute('SELECT 1 FROM poem_favorites WHERE user_id=? AND poem_id=?', (viewer_id, poem_id)).fetchone())
            value['deleted'] = False
            parent_id = row['parent_poem_id']
            if parent_id:
                parent = connection.execute('SELECT p.*,u.display_name AS author_name FROM poems p JOIN users u ON u.id=p.user_id WHERE p.id=?', (parent_id,)).fetchone()
                parent_visible = bool(parent and parent['deleted_at'] is None and (not viewer_id or self._poem_visible_to(connection, parent, viewer_id)))
                value['source'] = {'id': parent_id, 'deleted': not parent_visible, 'author_name': parent['author_name'] if parent else row['original_author_name']}
                if parent_visible:
                    value['source']['title'] = parent['title']
                    value['changes'] = [{**dict(change), 'old_text': change['old_text_stored'] if change['old_text_stored'] else change['old_text'], 'new_text': change['new_text_stored'] if change['new_text_stored'] else change['new_text']} for change in connection.execute('SELECT old_line_number,old_text,new_line_number,new_text,old_text_stored,new_text_stored FROM poem_revision_lines WHERE child_poem_id=? ORDER BY change_index', (poem_id,)).fetchall()]
                    if row['content_reference_id']:
                        parent_body = parent
                        if parent['content_reference_id']:
                            parent_body = connection.execute('SELECT * FROM poems WHERE id=?', (parent['content_reference_id'],)).fetchone()
                        old_content = parent_body['content_stored'] if parent_body.get('content_stored') else parent_body['content']
                        old_lines = [line.source_text + line.separator for line in parse_poem(old_content).lines]
                        new_lines = [line.source_text + line.separator for line in parse_poem(value['content']).lines]
                        for operation, old_start, old_end, new_start, new_end in SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
                            if operation == 'equal':
                                continue
                            for offset in range(max(old_end - old_start, new_end - new_start)):
                                old_index, new_index = (old_start + offset, new_start + offset)
                                value['changes'].append({'old_line_number': old_index + 1 if old_index < old_end else None, 'old_text': old_lines[old_index] if old_index < old_end else None, 'new_line_number': new_index + 1 if new_index < new_end else None, 'new_text': new_lines[new_index] if new_index < new_end else None})
                else:
                    value['changes'] = []
            else:
                value['source'] = None
                value['changes'] = []
            if not value.get('prosody_context') and row['job_id']:
                job = connection.execute('SELECT request_json,request_stored FROM jobs WHERE id=?', (row['job_id'],)).fetchone()
                raw = (job['request_stored'] if job['request_stored'] else job['request_json']) if job else None
                value['prosody_context'] = json.loads(raw) if raw else {}
            if viewer_id is None or row['user_id'] != viewer_id:
                value['prosody_context'] = public_poem_context(value['prosody_context'])
            return value

    def list_poems(self, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('SELECT p.id,p.job_id,p.candidate_ordinal,p.title,p.content,p.content_stored,p.sequence_stored,p.work_type,p.meter_type,p.form_name,p.variant_name,p.created_at,p.evaluation_json,p.evaluation_stored,p.prosody_context_json,p.prosody_context_stored,p.is_public,p.parent_poem_id,p.root_poem_id,p.forest_id,EXISTS(SELECT 1 FROM poem_favorites f WHERE f.poem_id=p.id AND f.user_id=?) AS favorite,u.id AS author_id,u.username AS author_username,u.display_name AS author_display_name,ou.id AS original_author_id,ou.display_name AS original_author_name,p.user_id FROM poems p JOIN users u ON u.id=p.user_id LEFT JOIN poems root ON root.id=COALESCE(p.root_poem_id,p.id) LEFT JOIN users ou ON ou.id=root.user_id WHERE p.deleted_at IS NULL AND (p.user_id=? OR EXISTS (SELECT 1 FROM poems owned WHERE owned.user_id=? AND owned.deleted_at IS NULL AND ((p.forest_id IS NOT NULL AND owned.forest_id=p.forest_id) OR (p.forest_id IS NULL AND owned.forest_id IS NULL AND COALESCE(owned.root_poem_id,owned.id)=COALESCE(p.root_poem_id,p.id))))) ORDER BY p.created_at DESC,p.id DESC', (user_id, user_id, user_id)).fetchall()
            allowed = visible_poem_ids(connection, [row['id'] for row in rows], user_id)
            visible = [row for row in rows if row['id'] in allowed]
            return [self._poem_row(row, connection, viewer_id=user_id) for row in visible]

    def list_poem_cards(self, user_id: str, *, limit: int=24, offset: int=0, work_type: str | None=None) -> tuple[list[dict[str, Any]], bool]:
        """Return one lightweight, latest-version card per poem forest."""
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        work_type_filter = ''
        access, access_args = poem_access_sql('p', user_id)
        parameters: list[Any] = [user_id, user_id]
        if work_type:
            work_type_filter = ' AND p.work_type=?'
            parameters.append(work_type)
        parameters.extend((limit + 1, offset))
        with self.database.connect() as connection:
            rows = connection.execute('WITH accessible AS ( SELECT p.* FROM poems p WHERE p.deleted_at IS NULL AND (p.user_id=? OR EXISTS (SELECT 1 FROM poems owned WHERE owned.user_id=? AND owned.deleted_at IS NULL AND ((p.forest_id IS NOT NULL AND owned.forest_id=p.forest_id) OR (p.forest_id IS NULL AND owned.forest_id IS NULL AND COALESCE(owned.root_poem_id,owned.id)=COALESCE(p.root_poem_id,p.id))))) AND ' + access + '), ranked AS ( SELECT accessible.*, ROW_NUMBER() OVER ( PARTITION BY COALESCE(forest_id,root_poem_id,id) ORDER BY  CASE WHEN id=(SELECT marked_poem_id FROM poem_forest_markers WHERE user_id=? AND forest_key=COALESCE(forest_id,root_poem_id,id)) THEN 0  WHEN user_id=? THEN 1  WHEN user_id=COALESCE((SELECT c.user_id FROM conversations c WHERE c.id=accessible.forest_id), (SELECT j.user_id FROM jobs j WHERE j.id=accessible.forest_id), (SELECT r.user_id FROM poems r WHERE r.id=COALESCE(accessible.root_poem_id,accessible.id)),user_id) THEN 2 ELSE 3 END, CASE WHEN user_id=COALESCE((SELECT c.user_id FROM conversations c WHERE c.id=accessible.forest_id), (SELECT j.user_id FROM jobs j WHERE j.id=accessible.forest_id), (SELECT r.user_id FROM poems r WHERE r.id=COALESCE(accessible.root_poem_id,accessible.id)),user_id) THEN created_at END DESC, created_at DESC,id DESC ) AS version_rank FROM accessible) SELECT p.id,p.job_id,p.candidate_ordinal,p.title,p.content,p.content_stored,p.work_type,p.meter_type,p.form_name,p.variant_name,p.sequence_stored,p.created_at,p.evaluation_json,p.evaluation_stored,p.is_public,p.parent_poem_id,p.root_poem_id,p.forest_id,EXISTS(SELECT 1 FROM poem_favorites f WHERE f.poem_id=p.id AND f.user_id=?) AS favorite,u.id AS author_id,u.username AS author_username,u.display_name AS author_display_name,ou.id AS original_author_id,ou.display_name AS original_author_name,p.user_id FROM ranked p JOIN users u ON u.id=p.user_id LEFT JOIN poems root ON root.id=COALESCE(p.root_poem_id,p.id) LEFT JOIN users ou ON ou.id=root.user_id WHERE p.version_rank=1' + work_type_filter + ' ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?', (*parameters[:2], *access_args, user_id, user_id, user_id, *parameters[2:])).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            resolved = resolve_contents(self.database, rows, connection)
            items = []
            for row in resolved:
                raw_evaluation = row.get('evaluation_stored')
                evaluation_json = raw_evaluation if raw_evaluation else row.get('evaluation_json')
                evaluation = json.loads(evaluation_json) if evaluation_json else None
                if evaluation:
                    result = evaluation.get('result') if isinstance(evaluation.get('result'), dict) else {}
                    meter = result.get('meter') if isinstance(result.get('meter'), dict) else {}
                    evaluation_summary = {'_summary': True}
                    evaluation_summary.update({key: evaluation[key] for key in ('type', 'comprehensive_score', 'structure_score', 'tonal_score', 'rhyme_score', 'rhyme_book_name', 'rhyme_book_id', 'evaluated_at') if key in evaluation})
                    if meter.get('stanzaEnds'):
                        evaluation_summary['result'] = {'meter': {'stanzaEnds': meter['stanzaEnds']}}
                    evaluation = evaluation_summary
                row.pop('content_stored', None)
                sequence_stored = row.pop('sequence_stored', None)
                row['sequence'] = sequence_stored if sequence_stored else ''
                row.pop('evaluation_stored', None)
                row.pop('evaluation_json', None)
                row['evaluation'] = evaluation
                items.append(row)
            return (items, has_more)

    def update_poem(self, poem_id: str, user_id: str, fields: dict[str, Any]) -> bool:
        return False

    def delete_poem(self, poem_id: str, user_id: str) -> bool:
        with self.database.connect() as connection:
            result = connection.execute('UPDATE poems SET deleted_at=? WHERE id=? AND user_id=? AND deleted_at IS NULL', (time.time(), poem_id, user_id))
        return result.rowcount == 1

    def list_favorite_poems(self, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('WITH favorite_roots AS (\n                    SELECT DISTINCT COALESCE(p.root_poem_id,p.id) AS root_id\n                    FROM poem_favorites f JOIN poems p ON p.id=f.poem_id WHERE f.user_id=?\n                ), ranked AS (\n                    SELECT p.*, ROW_NUMBER() OVER (PARTITION BY COALESCE(p.root_poem_id,p.id) ORDER BY p.created_at DESC,p.id DESC) AS version_rank\n                    FROM poems p JOIN favorite_roots fr ON fr.root_id=COALESCE(p.root_poem_id,p.id) WHERE p.deleted_at IS NULL\n                )\n                SELECT p.id,p.user_id,p.job_id,p.candidate_ordinal,p.title,p.content,p.content_stored,p.sequence_stored,p.work_type,p.meter_type,p.form_name,p.variant_name,p.created_at,p.evaluation_json,p.evaluation_stored,p.prosody_context_json,p.prosody_context_stored,p.is_public,p.parent_poem_id,p.root_poem_id,1 AS favorite,u.id AS author_id,u.username AS author_username,u.display_name AS author_display_name,ou.id AS original_author_id,ou.display_name AS original_author_name\n                FROM ranked p JOIN users u ON u.id=p.user_id LEFT JOIN poems root ON root.id=COALESCE(p.root_poem_id,p.id) LEFT JOIN users ou ON ou.id=root.user_id\n                WHERE p.version_rank=1 ORDER BY p.created_at DESC', (user_id,)).fetchall()
            allowed = visible_poem_ids(connection, [row['id'] for row in rows], user_id)
            return [self._poem_row(row, connection, viewer_id=user_id) for row in rows if row['id'] in allowed]

    def _poem_row(self, row, connection=None, *, viewer_id: str | None=None) -> dict[str, Any]:
        value = resolve_contents(self.database, [row], connection)[0]
        sequence_stored = value.pop('sequence_stored', None)
        value['sequence'] = sequence_stored if sequence_stored else ''
        evaluation_stored = value.pop('evaluation_stored', None)
        evaluation_json = evaluation_stored if evaluation_stored else value.pop('evaluation_json', None)
        value.pop('evaluation_json', None)
        value['evaluation'] = json.loads(evaluation_json) if evaluation_json else None
        context_stored = value.pop('prosody_context_stored', None)
        context_json = context_stored if context_stored else value.pop('prosody_context_json', None)
        value.pop('prosody_context_json', None)
        context = json.loads(context_json) if context_json else {}
        if viewer_id is None or row.get('user_id') != viewer_id:
            context = public_poem_context(context)
        value['prosody_context'] = context
        return value

    def soft_delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self.database.connect() as connection:
            result = connection.execute('UPDATE conversations SET deleted_at=?, updated_at=? WHERE id=? AND user_id=? AND deleted_at IS NULL', (time.time(), time.time(), conversation_id, user_id))
        return result.rowcount == 1

    def restore_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self.database.connect() as connection:
            now = time.time()
            result = connection.execute('UPDATE conversations SET deleted_at=NULL, updated_at=?, activity_at=? WHERE id=? AND user_id=?', (now, now, conversation_id, user_id))
        return result.rowcount == 1

    def rename_conversation(self, conversation_id: str, user_id: str, title: str) -> bool:
        now = time.time()
        with self.database.connect() as connection:
            result = connection.execute('UPDATE conversations SET title=?, updated_at=?, activity_at=? WHERE id=? AND user_id=?', (title[:15] or '新建对话', now, now, conversation_id, user_id))
        return result.rowcount == 1

    def move_conversation(self, conversation_id: str, user_id: str, folder_id: str | None) -> bool:
        now = time.time()
        with self.database.connect() as connection:
            if folder_id is not None and connection.execute('SELECT 1 FROM conversation_folders WHERE id=? AND user_id=?', (folder_id, user_id)).fetchone() is None:
                return False
            result = connection.execute('UPDATE conversations SET folder_id=?, updated_at=?, activity_at=? WHERE id=? AND user_id=?', (folder_id, now, now, conversation_id, user_id))
        return result.rowcount == 1

    def list_folders(self, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('SELECT id,name,created_at,updated_at FROM conversation_folders WHERE user_id=? ORDER BY updated_at DESC', (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def create_folder(self, user_id: str, name: str) -> dict[str, Any]:
        now = time.time()
        folder_id = str(uuid.uuid4())
        with self.database.connect() as connection:
            connection.execute('INSERT INTO conversation_folders(id,user_id,name,created_at,updated_at) VALUES (?,?,?,?,?)', (folder_id, user_id, name[:120] or '未命名文件夹', now, now))
        return {'id': folder_id, 'name': name[:120] or '未命名文件夹', 'created_at': now, 'updated_at': now}

    def update_folder(self, folder_id: str, user_id: str, name: str) -> bool:
        with self.database.connect() as connection:
            result = connection.execute('UPDATE conversation_folders SET name=?,updated_at=? WHERE id=? AND user_id=?', (name[:120], time.time(), folder_id, user_id))
        return result.rowcount == 1

    def list_poem_collections(self, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('SELECT c.id,c.name,c.created_at,c.updated_at,\n                COUNT(p.id) AS count\n                FROM poem_collections c\n                LEFT JOIN poem_collection_items i ON i.collection_id=c.id\n                LEFT JOIN poems p ON p.id=i.poem_id AND p.deleted_at IS NULL\n                    AND EXISTS(SELECT 1 FROM poem_favorites f WHERE f.poem_id=p.id AND f.user_id=c.user_id)\n                WHERE c.user_id=? GROUP BY c.id ORDER BY c.updated_at DESC', (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def ensure_default_collection(self, user_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute('SELECT * FROM user_collections WHERE user_id=? AND name=?', (user_id, '默认合集')).fetchone()
            if row is None:
                legacy = db.execute('SELECT * FROM user_collections WHERE user_id=? AND name=?', (user_id, '默认收藏夹')).fetchone()
                if legacy is not None:
                    db.execute('UPDATE user_collections SET name=?,updated_at=? WHERE id=?', ('默认合集', time.time(), legacy['id']))
                    row = db.execute('SELECT * FROM user_collections WHERE id=?', (legacy['id'],)).fetchone()
            if row is None:
                now = time.time()
                cid = str(uuid.uuid4())
                db.execute('INSERT INTO user_collections(id,user_id,name,created_at,updated_at) VALUES (?,?,?,?,?)', (cid, user_id, '默认合集', now, now))
                row = db.execute('SELECT * FROM user_collections WHERE id=?', (cid,)).fetchone()
            return dict(row)

    def set_collection_item(self, user_id: str, item_type: str, item_id: str, collection_id: str | None=None) -> dict[str, Any]:
        collection = self.ensure_default_collection(user_id) if collection_id is None else None
        with self.database.connect() as db:
            if collection is None:
                row = db.execute('SELECT * FROM user_collections WHERE id=? AND user_id=?', (collection_id, user_id)).fetchone()
                if row is None:
                    raise KeyError(collection_id)
                collection = dict(row)
            db.execute('DELETE FROM user_collection_items WHERE collection_id IN (SELECT id FROM user_collections WHERE user_id=?) AND item_type=? AND item_id=?', (user_id, item_type, item_id))
            db.execute('INSERT INTO user_collection_items(collection_id,item_type,item_id,created_at) VALUES (?,?,?,?)', (collection['id'], item_type, item_id, time.time()))
        return collection

    def remove_collection_item(self, user_id: str, item_type: str, item_id: str) -> None:
        with self.database.connect() as db:
            db.execute('DELETE FROM user_collection_items WHERE collection_id IN (SELECT id FROM user_collections WHERE user_id=?) AND item_type=? AND item_id=?', (user_id, item_type, item_id))

    def touch_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self.database.connect() as connection:
            result = connection.execute('UPDATE conversations SET activity_at=? WHERE id=? AND user_id=? AND deleted_at IS NULL', (time.time(), conversation_id, user_id))
        return result.rowcount == 1

    def list_collection_items(self, collection_id: str, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('WITH collection_roots AS (\n                    SELECT DISTINCT COALESCE(p.root_poem_id,p.id) AS root_id\n                    FROM poem_collection_items i JOIN poem_collections c ON c.id=i.collection_id JOIN poems p ON p.id=i.poem_id\n                    WHERE i.collection_id=? AND c.user_id=?\n                ), ranked AS (\n                    SELECT p.*, ROW_NUMBER() OVER (PARTITION BY COALESCE(p.root_poem_id,p.id) ORDER BY p.created_at DESC,p.id DESC) AS version_rank\n                    FROM poems p JOIN collection_roots cr ON cr.root_id=COALESCE(p.root_poem_id,p.id) WHERE p.deleted_at IS NULL\n                )\n                SELECT p.id,p.user_id,p.job_id,p.candidate_ordinal,p.title,p.content,p.content_stored,p.sequence_stored,p.work_type,p.meter_type,p.form_name,p.variant_name,p.created_at,p.evaluation_json,p.evaluation_stored,p.prosody_context_json,p.prosody_context_stored,p.is_public,EXISTS(SELECT 1 FROM poem_favorites f WHERE f.poem_id=p.id AND f.user_id=?) AS favorite,u.id AS author_id,u.username AS author_username,u.display_name AS author_display_name\n                FROM ranked p JOIN users u ON u.id=p.user_id WHERE p.version_rank=1 ORDER BY p.created_at DESC', (collection_id, user_id, user_id)).fetchall()
            allowed = visible_poem_ids(connection, [row['id'] for row in rows], user_id)
            return [self._poem_row(row, connection, viewer_id=user_id) for row in rows if row['id'] in allowed]

    def get_poem_tree(self, poem_id: str, viewer_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            source = connection.execute('SELECT * FROM poems WHERE id=?', (poem_id,)).fetchone()
            if source is None or source['deleted_at'] is not None or (not self._poem_visible_to(connection, source, viewer_id)):
                return None
            root_id = source['root_poem_id'] or source['id']
            forest_id = source['forest_id']
            tree_node_count = connection.execute('SELECT COUNT(*) FROM poems WHERE id=? OR root_poem_id=?', (root_id, root_id)).fetchone()[0]
            clause = 'p.forest_id=?' if forest_id else '(p.id=? OR p.root_poem_id=?)'
            args = (forest_id,) if forest_id else (root_id, root_id)
            rows = connection.execute(f'SELECT p.*,u.display_name AS author_display_name FROM poems p JOIN users u ON u.id=p.user_id WHERE {clause} ORDER BY p.created_at,p.id', args).fetchall()
            forest_key = forest_id or root_id
            marker = connection.execute('SELECT marked_poem_id FROM poem_forest_markers WHERE forest_key=? AND user_id=?', (forest_key, viewer_id)).fetchone()
            marked_poem_id = marker['marked_poem_id'] if marker else None
            by_id = {row['id']: row for row in rows}
            visible = visible_poem_ids(connection, [row['id'] for row in rows], viewer_id)
            included = set(visible)
            for node_id in visible:
                parent_id = by_id[node_id]['parent_poem_id']
                while parent_id and parent_id in by_id:
                    included.add(parent_id)
                    parent_id = by_id[parent_id]['parent_poem_id']
            nodes = []
            for row in rows:
                if row['id'] not in included:
                    continue
                accessible = row['id'] in visible
                nodes.append({'id': row['id'], 'parent_poem_id': row['parent_poem_id'], 'title': row['title'] if accessible else '作者已删除该作品' if row['deleted_at'] is not None else '不可访问的作品', 'author_display_name': row['author_display_name'] if accessible else None, 'created_at': row['created_at'], 'available': accessible, 'can_delete': accessible and row['user_id'] == viewer_id, 'can_mark': accessible, 'is_marked': accessible and row['id'] == marked_poem_id, 'revision_type': self._poem_revision_type(row)})
            owner_id = self._forest_owner_id(connection, source)
            visible_latest_id = next((row['id'] for row in reversed(rows) if row['id'] in visible))
            owner_latest_id = next((row['id'] for row in reversed(rows) if row['id'] in visible and row['user_id'] == owner_id), None)
            latest_id = owner_latest_id or visible_latest_id
            viewer_latest_id = next((row['id'] for row in reversed(rows) if row['id'] in visible and row['user_id'] == viewer_id), source['id'])
            if marked_poem_id not in visible:
                marked_poem_id = None
            return {'root_poem_id': root_id, 'forest_id': forest_id, 'current_poem_id': poem_id, 'latest_poem_id': latest_id, 'owner_latest_poem_id': owner_latest_id, 'viewer_latest_poem_id': viewer_latest_id, 'marked_poem_id': marked_poem_id, 'tree_node_count': tree_node_count, 'nodes': nodes}

    @staticmethod
    def _forest_owner_id(connection, poem) -> str:
        forest_id = poem['forest_id']
        if forest_id:
            owner = connection.execute('SELECT user_id FROM jobs WHERE id=?', (forest_id,)).fetchone()
            if owner is None:
                owner = connection.execute('SELECT user_id FROM conversations WHERE id=?', (forest_id,)).fetchone()
            if owner is not None:
                return owner['user_id']
        root_id = poem['root_poem_id'] or poem['id']
        owner = connection.execute('SELECT user_id FROM poems WHERE id=?', (root_id,)).fetchone()
        return owner['user_id'] if owner is not None else poem['user_id']

    def mark_poem_forest_version(self, poem_id: str, user_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            poem = connection.execute('SELECT * FROM poems WHERE id=?', (poem_id,)).fetchone()
            if poem is None or poem['deleted_at'] is not None or (not self._poem_visible_to(connection, poem, user_id)):
                raise KeyError(poem_id)
            root_id = poem['root_poem_id'] or poem['id']
            forest_key = poem['forest_id'] or root_id
            connection.execute('INSERT INTO poem_forest_markers(forest_key,user_id,marked_poem_id,updated_at) VALUES (?,?,?,?) ON CONFLICT(user_id,forest_key) DO UPDATE SET marked_poem_id=excluded.marked_poem_id,updated_at=excluded.updated_at', (forest_key, user_id, poem_id, time.time()))
            return {'forest_key': forest_key, 'user_id': user_id, 'marked_poem_id': poem_id}

    def unmark_poem_forest_version(self, poem_id: str, user_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            poem = connection.execute('SELECT * FROM poems WHERE id=?', (poem_id,)).fetchone()
            if poem is None or poem['deleted_at'] is not None or (not self._poem_visible_to(connection, poem, user_id)):
                raise KeyError(poem_id)
            forest_key = poem['forest_id'] or poem['root_poem_id'] or poem['id']
            connection.execute('DELETE FROM poem_forest_markers WHERE forest_key=? AND user_id=?', (forest_key, user_id))
            return {'forest_key': forest_key, 'user_id': user_id, 'marked_poem_id': None}

    @staticmethod
    def _poem_revision_type(row) -> str:
        if row['parent_poem_id'] is not None:
            return 'ai_revision' if row['job_id'] else 'user_revision'
        return 'generated' if row['job_id'] else 'user_revision_root'

    def get_poem_sentence_variations(self, poem_id: str, sentence_number: int, viewer_id: str) -> dict[str, Any] | None:
        if sentence_number < 1:
            raise ValueError('句子编号必须从1开始')
        with self.database.connect() as connection:
            source = connection.execute('SELECT * FROM poems WHERE id=?', (poem_id,)).fetchone()
            if source is None or source['deleted_at'] is not None or (not self._poem_visible_to(connection, source, viewer_id)):
                return None
            source_content = resolve_contents(self.database, [source], connection)[0]['content']
            source_lines = parse_poem(source_content).lines
            if sentence_number > len(source_lines):
                raise ValueError(f'该作品没有第{sentence_number}句')
            root_id = source['root_poem_id'] or source['id']
            forest_id = source['forest_id']
            clause = 'p.forest_id=?' if forest_id else '(p.id=? OR p.root_poem_id=?)'
            args = (forest_id,) if forest_id else (root_id, root_id)
            rows = connection.execute(f'SELECT p.*,u.display_name AS author_display_name FROM poems p JOIN users u ON u.id=p.user_id WHERE {clause} ORDER BY p.created_at,p.id', args).fetchall()
            allowed = visible_poem_ids(connection, [row['id'] for row in rows], viewer_id)
            visible_rows = [row for row in rows if row['id'] in allowed]
            items = []
            roots = [row for row in visible_rows if row['parent_poem_id'] is None]
            root_bodies = {row['id']: row['content'] for row in resolve_contents(self.database, roots, connection)}
            for row in roots:
                body = root_bodies[row['id']]
                lines = parse_poem(body).lines
                if sentence_number <= len(lines):
                    line = lines[sentence_number - 1]
                    items.append({'poem_id': row['id'], 'parent_poem_id': None, 'root_poem_id': row['root_poem_id'] or row['id'], 'title': row['title'], 'author_display_name': row['author_display_name'], 'created_at': row['created_at'], 'revision_type': self._poem_revision_type(row), 'sentence_number': sentence_number, 'text': line.source_text + line.separator, 'parent_text': None})
            visible_ids = [row['id'] for row in visible_rows]
            if visible_ids:
                marks = ','.join(('?' for _ in visible_ids))
                revisions = connection.execute(f'SELECT r.child_poem_id,r.old_text,r.old_text_stored,r.new_text,r.new_text_stored FROM poem_revision_lines r JOIN poems p ON p.id=r.child_poem_id WHERE r.child_poem_id IN ({marks}) AND r.new_line_number=? ORDER BY p.created_at,p.id,r.change_index', (*visible_ids, sentence_number)).fetchall()
                by_id = {row['id']: row for row in visible_rows}
                for revision in revisions:
                    row = by_id[revision['child_poem_id']]
                    new_text = revision['new_text_stored'] if revision['new_text_stored'] else revision['new_text']
                    if new_text is None:
                        continue
                    old_text = revision['old_text_stored'] if revision['old_text_stored'] else revision['old_text']
                    items.append({'poem_id': row['id'], 'parent_poem_id': row['parent_poem_id'], 'root_poem_id': row['root_poem_id'] or row['id'], 'title': row['title'], 'author_display_name': row['author_display_name'], 'created_at': row['created_at'], 'revision_type': self._poem_revision_type(row), 'sentence_number': sentence_number, 'text': new_text, 'parent_text': old_text})
            items.sort(key=lambda item: (item['created_at'], item['poem_id']))
            return {'poem_id': poem_id, 'root_poem_id': root_id, 'forest_id': forest_id, 'sentence_number': sentence_number, 'items': items}

    def create_poem_collection(self, user_id: str, name: str) -> dict[str, Any]:
        now = time.time()
        collection_id = str(uuid.uuid4())
        with self.database.connect() as connection:
            connection.execute('INSERT INTO poem_collections(id,user_id,name,created_at,updated_at) VALUES (?,?,?,?,?)', (collection_id, user_id, name[:120] or '未命名合集', now, now))
        return {'id': collection_id, 'name': name[:120] or '未命名合集', 'created_at': now, 'updated_at': now, 'count': 0}

    def update_poem_collection(self, collection_id: str, user_id: str, name: str) -> bool:
        with self.database.connect() as connection:
            result = connection.execute('UPDATE poem_collections SET name=?,updated_at=? WHERE id=? AND user_id=?', (name[:120], time.time(), collection_id, user_id))
        return result.rowcount == 1

    def delete_poem_collection(self, collection_id: str, user_id: str) -> bool:
        with self.database.connect() as connection:
            result = connection.execute('DELETE FROM poem_collections WHERE id=? AND user_id=?', (collection_id, user_id))
        return result.rowcount == 1

    def set_favorite(self, user_id: str, poem_id: str, favorite: bool) -> bool:
        with self.database.connect() as connection:
            exists = connection.execute('SELECT 1 FROM poems WHERE id=?', (poem_id,)).fetchone()
            if exists is None:
                return False
            if favorite:
                if poem_id not in visible_poem_ids(connection, [poem_id], user_id):
                    return False
                connection.execute('INSERT INTO poem_favorites(user_id,poem_id,created_at) VALUES (?,?,?) ON CONFLICT(user_id,poem_id) DO NOTHING', (user_id, poem_id, time.time()))
            else:
                connection.execute('DELETE FROM poem_favorites WHERE user_id=? AND poem_id=?', (user_id, poem_id))
                connection.execute('DELETE FROM poem_collection_items WHERE poem_id=? AND collection_id IN (SELECT id FROM poem_collections WHERE user_id=?)', (poem_id, user_id))
        return True

    def collection_item(self, collection_id: str, poem_id: str, user_id: str, add: bool) -> bool:
        with self.database.connect() as connection:
            owned = connection.execute('SELECT 1 FROM poem_collections WHERE id=? AND user_id=?', (collection_id, user_id)).fetchone()
            poem = connection.execute('SELECT 1 FROM poems WHERE id=?', (poem_id,)).fetchone()
            if owned is None or poem is None:
                return False
            if add:
                if poem_id not in visible_poem_ids(connection, [poem_id], user_id):
                    return False
                connection.execute('INSERT INTO poem_favorites(user_id,poem_id,created_at) VALUES (?,?,?) ON CONFLICT(user_id,poem_id) DO NOTHING', (user_id, poem_id, time.time()))
                connection.execute('INSERT INTO poem_collection_items(collection_id,poem_id,created_at) VALUES (?,?,?) ON CONFLICT(collection_id,poem_id) DO NOTHING', (collection_id, poem_id, time.time()))
            else:
                connection.execute('DELETE FROM poem_collection_items WHERE collection_id=? AND poem_id=?', (collection_id, poem_id))
        return True
