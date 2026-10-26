from __future__ import annotations
import json
import threading
import time
import uuid
from typing import Any, Mapping
from .database import Database

class EventRepository:
    """持久化 Agent turn、对话事件和任务进度事件。"""

    def __init__(self, database: Database, *, clock=time.time):
        self.database = database
        self._clock = clock

    def start_turn(self, *, user_id: str | None, conversation_id: str | None, user_message_id: str | None=None, assistant_message_id: str | None=None, branch_id: str | None=None, status: str='running') -> str:
        turn_id = str(uuid.uuid4())
        now = self._clock()
        with self.database.connect() as connection:
            connection.execute('INSERT INTO agent_turns(id,user_id,conversation_id,user_message_id,assistant_message_id,branch_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)', (turn_id, user_id, conversation_id, user_message_id, assistant_message_id, branch_id, status, now, now))
        return turn_id

    def append_agent_event(self, turn_id: str, event_type: str, payload: Mapping[str, Any] | None=None) -> dict[str, Any]:
        payload = dict(payload or {})
        now = self._clock()
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            lock_clause = ''
            row = connection.execute('SELECT conversation_id,last_event_seq FROM agent_turns WHERE id = ?' + lock_clause, (turn_id,)).fetchone()
            if row is None:
                raise KeyError(turn_id)
            if row[0]:
                connection.execute('SELECT id FROM conversations WHERE id = ?', (row[0],)).fetchone()
                latest = connection.execute('SELECT COALESCE(MAX(e.seq),0) FROM agent_events e JOIN agent_turns t ON t.id=e.turn_id WHERE t.conversation_id=?', (row[0],)).fetchone()
                seq = int(latest[0]) + 1
            else:
                seq = int(row[1]) + 1
            event_id = str(uuid.uuid4())
            connection.execute('INSERT INTO agent_events(id,turn_id,seq,event_type,payload_json,payload_stored,created_at) VALUES (?,?,?,?,NULL,?,?)', (event_id, turn_id, seq, event_type, json.dumps(payload, ensure_ascii=False), now))
            connection.execute('UPDATE agent_turns SET last_event_seq=?,updated_at=? WHERE id=?', (seq, now, turn_id))
        return {'id': event_id, 'turn_id': turn_id, 'seq': seq, 'event_type': event_type, 'payload': payload, 'created_at': now}

    def finish_turn(self, turn_id: str, status: str='completed') -> None:
        with self.database.connect() as connection:
            connection.execute('UPDATE agent_turns SET status=?,updated_at=? WHERE id=?', (status, self._clock(), turn_id))

    def get_turn(self, turn_id: str, user_id: str | None=None) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            if user_id:
                row = connection.execute('SELECT * FROM agent_turns WHERE id=? AND user_id=?', (turn_id, user_id)).fetchone()
            else:
                row = connection.execute('SELECT * FROM agent_turns WHERE id=?', (turn_id,)).fetchone()
        return dict(row) if row else None

    def set_turn_status(self, turn_id: str, status: str, *, cancel_requested: bool | None=None) -> bool:
        now = self._clock()
        with self.database.connect() as connection:
            if cancel_requested is None:
                updated = connection.execute('UPDATE agent_turns SET status=?,updated_at=? WHERE id=?', (status, now, turn_id))
            else:
                updated = connection.execute('UPDATE agent_turns SET status=?,cancel_requested=?,updated_at=? WHERE id=?', (status, 1 if cancel_requested else 0, now, turn_id))
        return updated.rowcount == 1

    def active_turn(self, branch_id: str | None, *, exclude_turn_id: str | None=None) -> dict[str, Any] | None:
        if not branch_id:
            return None
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM agent_turns WHERE branch_id=? AND status IN ('queued','running','pausing','resuming','cancelling') AND id<>? ORDER BY created_at DESC LIMIT 1", (branch_id, exclude_turn_id or '')).fetchone()
        return dict(row) if row else None

    def interrupt_unfinished_turns(self) -> int:
        with self.database.connect() as connection:
            updated = connection.execute("UPDATE agent_turns SET status='interrupted',updated_at=? WHERE status IN ('running','pausing','resuming','cancelling') AND id NOT IN (SELECT agent_turn_id FROM jobs WHERE agent_turn_id IS NOT NULL AND status IN ('queued','running'))", (self._clock(),))
            connection.execute("UPDATE messages SET status='interrupted',updated_at=? WHERE turn_id IN (SELECT id FROM agent_turns WHERE status='interrupted') AND status IN ('pending','paused')", (self._clock(),))
        return updated.rowcount

    def list_agent_events(self, conversation_id: str, *, after: int=0, limit: int=500) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('SELECT e.*, t.conversation_id FROM agent_events e JOIN agent_turns t ON t.id=e.turn_id WHERE t.conversation_id=? AND e.seq>? ORDER BY e.created_at,e.id LIMIT ?', (conversation_id, after, limit)).fetchall()
        return [self._event(row) for row in rows]

    def list_turn_events(self, turn_id: str, *, after: int=0, limit: int=500) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('SELECT * FROM agent_events WHERE turn_id=? AND seq>? ORDER BY seq LIMIT ?', (turn_id, after, limit)).fetchall()
        return [self._event(row) for row in rows]

    def append_job_event(self, job_id: str, event_type: str, payload: Mapping[str, Any] | None=None) -> dict[str, Any]:
        payload = dict(payload or {})
        now = self._clock()
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('SELECT id FROM jobs WHERE id=?', (job_id,)).fetchone()
            row = connection.execute('SELECT COALESCE(MAX(seq),0) FROM job_events WHERE job_id=?', (job_id,)).fetchone()
            seq = int(row[0]) + 1
            event_id = str(uuid.uuid4())
            connection.execute('INSERT INTO job_events(id,job_id,seq,event_type,payload_json,payload_stored,created_at) VALUES (?,?,?,?,NULL,?,?)', (event_id, job_id, seq, event_type, json.dumps(payload, ensure_ascii=False), now))
        return {'id': event_id, 'job_id': job_id, 'seq': seq, 'event_type': event_type, 'payload': payload, 'created_at': now}

    def list_job_events(self, job_id: str, *, after: int=0, limit: int=500) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('SELECT * FROM job_events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?', (job_id, after, limit)).fetchall()
        return [self._event(row) for row in rows]

    @staticmethod
    def _event(row) -> dict[str, Any]:
        payload_raw = row.get('payload_stored') or row.get('payload_json') or '{}'
        purpose = 'agent-event' if 'turn_id' in row.keys() else 'job-event'
        return {'id': row['id'], 'turn_id': row['turn_id'] if 'turn_id' in row.keys() else None, 'job_id': row['job_id'] if 'job_id' in row.keys() else None, 'seq': row['seq'], 'event_type': row['event_type'], 'payload': json.loads(payload_raw), 'created_at': row['created_at']}

class TurnController:
    """进程内 turn 取消信号；数据库保存对外可见状态。"""

    def __init__(self):
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def register(self, turn_id: str) -> threading.Event:
        with self._lock:
            event = self._events.setdefault(turn_id, threading.Event())
            event.clear()
            return event

    def request_stop(self, turn_id: str) -> bool:
        with self._lock:
            event = self._events.get(turn_id)
            if event is None:
                return False
            event.set()
            return True

    def unregister(self, turn_id: str) -> None:
        with self._lock:
            self._events.pop(turn_id, None)
