from __future__ import annotations
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from shiju.contracts import JobStatus
from shiju.data import MeterTemplateRepository
from .database import Database
from .event_repository import EventRepository
from .task_reservations import TaskReservations

class JobNotFound(KeyError):
    pass

class IdempotencyConflict(ValueError):
    pass

class JobOwnershipError(ValueError):
    pass

@dataclass(frozen=True)
class JobRecord:
    id: str
    kind: str
    status: str
    request: dict[str, Any]
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    attempts: int
    max_attempts: int
    worker_id: str | None
    lease_expires_at: float | None
    created_at: float
    updated_at: float
    started_at: float | None
    finished_at: float | None
    user_id: str | None = None
    conversation_id: str | None = None

    def to_dict(self, *, include_request: bool=False) -> dict[str, Any]:
        value = {'job_id': self.id, 'kind': self.kind, 'status': self.status, 'result': self.result, 'error': {'code': self.error_code, 'message': self.error_message} if self.error_code else None, 'attempts': self.attempts, 'created_at': self.created_at, 'updated_at': self.updated_at, 'started_at': self.started_at, 'finished_at': self.finished_at, 'user_id': self.user_id, 'conversation_id': self.conversation_id}
        if include_request:
            value['request'] = self.request
        return value

class JobRepository:

    def __init__(self, database: Database, *, clock=time.time, meter_source=None, settings=None):
        self.database = database
        self._clock = clock
        self.admission = TaskReservations(database, settings, clock=clock)
        self._meter_source = Path(meter_source) if meter_source else Path(__file__).resolve().parents[2] / 'Songci_Meter'

    def submit(self, kind: str, request: Mapping[str, Any], *, idempotency_key: str | None=None, max_attempts: int=2, user_id: str | None=None, conversation_id: str | None=None, agent_turn_id: str | None=None) -> JobRecord:
        request = dict(request)
        if kind == 'rewrite' or (kind == 'partial_generate' and request.get('original_text')):
            if not request.get('source_poem_id'):
                raise ValueError('改写必须提供作品 ID')
        if request.get('meter_type') in {'宋词', '汉俳'} and request.get('task_options'):
            request['task_options'] = dict(request['task_options'])
            request['task_options'].pop('allow_aojiu', None)
        if request.get('meter_type') == '宋词' and request.get('variant_name'):
            request['variant_name'] = MeterTemplateRepository(self._meter_source).get(request['form_name'], request.get('variant_name')).variant_name
        canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        request_hash = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        now = self._clock()
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if idempotency_key:
                existing = connection.execute('SELECT * FROM jobs WHERE idempotency_key = ? AND user_id IS NOT DISTINCT FROM ?', (idempotency_key, user_id)).fetchone()
                if existing:
                    if existing['request_hash'] != request_hash or existing['kind'] != kind or existing['conversation_id'] != conversation_id:
                        raise IdempotencyConflict('同一 Idempotency-Key 对应了不同请求')
                    return self._record(existing)
            job_id = str(uuid.uuid4())
            connection.execute('\n                INSERT INTO jobs(\n                    id, kind, status, request_json, request_stored, request_hash, idempotency_key,\n                    attempts, max_attempts, created_at, updated_at, user_id, conversation_id,\n                    agent_turn_id, forest_version\n                ) VALUES (?, ?, ?, NULL, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 1)\n                ', (job_id, kind, JobStatus.QUEUED.value, canonical, request_hash, idempotency_key, max_attempts, now, now, user_id, conversation_id, agent_turn_id))
            self.admission.reserve_in_transaction(connection, user_id, 'agent' if kind == 'agent_turn' else 'poetry', job_id=job_id)
            candidate_count = int(request.get('candidate_count', 0) or 0)
            if candidate_count > 0:
                for ordinal in range(1, candidate_count + 1):
                    connection.execute('INSERT INTO job_candidates(id,job_id,ordinal,updated_at) VALUES (?,?,?,?)', (str(uuid.uuid4()), job_id, ordinal, now))
            row = connection.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
            record = self._record(row)
        EventRepository(self.database, clock=self._clock).append_job_event(job_id, 'job.submitted', {'kind': kind, 'total': candidate_count})
        return record

    def get(self, job_id: str) -> JobRecord:
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return self._record(row)

    def cancel(self, job_id: str, *, user_id: str | None=None) -> JobRecord:
        """Cancel queued work or request cooperative cancellation from its worker."""
        now = self._clock()
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            if user_id is not None and row['user_id'] != user_id:
                raise JobNotFound(job_id)
            if row['status'] == JobStatus.QUEUED.value:
                connection.execute('UPDATE jobs SET status=?, cancel_requested=1, updated_at=?, finished_at=? WHERE id=? AND status=?', (JobStatus.CANCELLED.value, now, now, job_id, JobStatus.QUEUED.value))
            elif row['status'] == JobStatus.RUNNING.value:
                if row['cancel_requested']:
                    raise ValueError('任务取消请求已经提交')
                connection.execute('UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=? AND status=?', (now, job_id, JobStatus.RUNNING.value))
            else:
                raise ValueError('任务已经结束，当前不能取消')
            updated = connection.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        event_type = 'job.cancelled' if updated['status'] == JobStatus.CANCELLED.value else 'job.cancel_requested'
        EventRepository(self.database, clock=self._clock).append_job_event(job_id, event_type, {})
        return self._record(updated)

    def assign_user(self, job_id: str, user_id: str) -> None:
        self.assign_context(job_id, user_id, self.get(job_id).conversation_id)

    def assign_context(self, job_id: str, user_id: str, conversation_id: str | None) -> None:
        with self.database.connect() as connection:
            row = connection.execute('SELECT user_id,kind FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None or row['user_id'] not in (None, user_id):
                raise JobOwnershipError(job_id)
            if row['user_id'] is None:
                connection.execute('DELETE FROM generation_admissions WHERE job_id=?', (job_id,))
                self.admission.reserve_in_transaction(connection, user_id, 'agent' if row['kind'] == 'agent_turn' else 'poetry', job_id=job_id)
            connection.execute('UPDATE jobs SET user_id = ?, conversation_id = ? WHERE id = ?', (user_id, conversation_id, job_id))

    def list_for_user(self, user_id: str, conversation_id: str | None=None, *, active_only: bool=False, limit: int=20, offset: int=0) -> list[JobRecord]:
        clauses = ['user_id = ?']
        params: list[Any] = [user_id]
        if conversation_id:
            clauses.append('conversation_id = ?')
            params.append(conversation_id)
        if active_only:
            clauses.append("status IN ('queued', 'running')")
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT * FROM jobs WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ? OFFSET ?", (*params, min(max(limit, 1), 100), max(offset, 0))).fetchall()
        return [self._record(row) for row in rows]

    def claim(self, worker_id: str, capabilities: Mapping[str, Any], *, lease_seconds: int=300, kinds: tuple[str, ...] | None=None, job_id: str | None=None) -> JobRecord | None:
        now = self._clock()
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._touch_worker(connection, worker_id, capabilities, now)
            self._recover_expired(connection, now)
            allowed_kinds = kinds or ('generate', 'rewrite', 'partial_generate')
            placeholders = ','.join(('?' for _ in allowed_kinds))
            lock_clause = ''
            job_filter = ' AND id = ?' if job_id else ''
            parameters = (JobStatus.QUEUED.value, *allowed_kinds, *((job_id,) if job_id else ()))
            row = connection.execute(f'SELECT * FROM jobs WHERE status = ? AND kind IN ({placeholders}){job_filter} ORDER BY created_at LIMIT 1{lock_clause}', parameters).fetchone()
            if row is None:
                return None
            connection.execute('\n                UPDATE jobs\n                SET status = ?, worker_id = ?, attempts = attempts + 1,\n                    lease_expires_at = ?, started_at = COALESCE(started_at, ?), updated_at = ?\n                WHERE id = ? AND status = ?\n                ', (JobStatus.RUNNING.value, worker_id, now + lease_seconds, now, now, row['id'], JobStatus.QUEUED.value))
            claimed = connection.execute('SELECT * FROM jobs WHERE id = ?', (row['id'],)).fetchone()
            return self._record(claimed)

    def heartbeat(self, job_id: str, worker_id: str, *, lease_seconds: int=300) -> bool:
        now = self._clock()
        with self.database.connect() as connection:
            result = connection.execute('\n                UPDATE jobs SET lease_expires_at = ?, updated_at = ?\n                WHERE id = ? AND worker_id = ? AND status = ?\n                ', (now + lease_seconds, now, job_id, worker_id, JobStatus.RUNNING.value))
            if result.rowcount != 1:
                raise JobOwnershipError(job_id)
            connection.execute('UPDATE workers SET last_seen_at=? WHERE id=?', (now, worker_id))
            row = connection.execute('SELECT cancel_requested FROM jobs WHERE id=? AND worker_id=? AND status=?', (job_id, worker_id, JobStatus.RUNNING.value)).fetchone()
        return bool(row and row[0])

    def acknowledge_cancel(self, job_id: str, worker_id: str) -> None:
        now = self._clock()
        with self.database.connect() as connection:
            result = connection.execute('\n                UPDATE jobs SET status=?, worker_id=NULL, lease_expires_at=NULL,\n                    updated_at=?, finished_at=?\n                WHERE id=? AND worker_id=? AND status=? AND cancel_requested=1\n                ', (JobStatus.CANCELLED.value, now, now, job_id, worker_id, JobStatus.RUNNING.value))
        if result.rowcount != 1:
            raise JobOwnershipError(job_id)
        EventRepository(self.database, clock=self._clock).append_job_event(job_id, 'job.cancelled', {})

    def complete(self, job_id: str, worker_id: str, result: Mapping[str, Any]) -> None:
        row = self.get(job_id)
        result = dict(result)
        if row.request.get('original_text'):
            from shiju.rewriting.source import validate_rewrite_result
            candidates = []
            for candidate in result.get('candidates') or []:
                candidate = dict(candidate)
                key = next((key for key in ('content', 'text', 'full_text', 'display_text') if candidate.get(key)), None)
                if key:
                    candidate[key] = validate_rewrite_result(row.request, candidate[key])
                candidates.append(candidate)
            if candidates:
                result['candidates'] = candidates
            if result.get('full_text'):
                result['full_text'] = validate_rewrite_result(row.request, result['full_text'])
        now = self._clock()
        payload = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        with self.database.connect() as connection:
            updated = connection.execute('\n                UPDATE jobs\n                SET status = ?, result_json = NULL, result_stored = ?, error_code = NULL, error_message = NULL, error_message_stored = NULL,\n                    lease_expires_at = NULL, updated_at = ?, finished_at = ?\n                WHERE id = ? AND worker_id = ? AND status = ?\n                ', (JobStatus.SUCCEEDED.value, payload, now, now, job_id, worker_id, JobStatus.RUNNING.value))
            if updated.rowcount != 1:
                raise JobOwnershipError(job_id)
            if row.user_id and row.kind != 'agent_turn':
                from .user_repository import UserRepository
                candidates = result.get('candidates') or []
                if not candidates and result.get('full_text'):
                    candidates = [{'text': result['full_text']}]
                for ordinal, candidate in enumerate(candidates, start=1):
                    UserRepository._save_candidate_poem(connection, row.user_id, job_id, ordinal, row.request, candidate, candidate.get('evaluation'))
        EventRepository(self.database, clock=self._clock).append_job_event(job_id, 'job.completed', {'result': dict(result)})

    def fail_pending(self, job_id: str, code: str, message: str) -> bool:
        """Fail only a queued job; never overwrite a claim, cancel or result."""
        now = self._clock()
        with self.database.connect() as connection:
            changed = connection.execute("UPDATE jobs SET status='failed',error_code=?,error_message=NULL,error_message_stored=?,updated_at=?,finished_at=? WHERE id=? AND status='queued'", (code, message, now, now, job_id)).rowcount == 1
        if changed:
            EventRepository(self.database, clock=self._clock).append_job_event(job_id, 'job.failed', {'code': code, 'message': message, 'retrying': False})
        return changed

    def fail(self, job_id: str, worker_id: str, code: str, message: str, *, retryable: bool=False) -> None:
        now = self._clock()
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM jobs WHERE id = ? AND worker_id = ? AND status = ?', (job_id, worker_id, JobStatus.RUNNING.value)).fetchone()
            if row is None:
                raise JobOwnershipError(job_id)
            should_retry = retryable and row['attempts'] < row['max_attempts']
            connection.execute('\n                UPDATE jobs\n                SET status = ?, worker_id = NULL, lease_expires_at = NULL,\n                    error_code = ?, error_message = NULL, error_message_stored = ?, updated_at = ?, finished_at = ?\n                WHERE id = ?\n                ', (JobStatus.QUEUED.value if should_retry else JobStatus.FAILED.value, code, message, now, None if should_retry else now, job_id))
        EventRepository(self.database, clock=self._clock).append_job_event(job_id, 'job.failed', {'code': code, 'message': message, 'retrying': should_retry})

    def update_candidate(self, job_id: str, ordinal: int, *, worker_id: str | None=None, status: str | None=None, attempt: int | None=None, partial_text: str | None=None, raw_text: str | None=None, title: str | None=None, content: str | None=None, error: str | None=None, started_at: float | None=None, finished_at: float | None=None) -> dict[str, Any]:
        job = self.get(job_id)
        if status == 'succeeded' and job.request.get('source_poem_id'):
            with self.database.connect() as connection:
                source = connection.execute('SELECT title FROM poems WHERE id=?', (job.request['source_poem_id'],)).fetchone()
            if source:
                title = str(source['title'] or '').strip()
        if status == 'succeeded' and content and job.request.get('original_text'):
            from shiju.rewriting.source import validate_rewrite_result
            content = validate_rewrite_result(job.request, content)
        fields = {'status': status, 'attempt': attempt, 'partial_text': partial_text, 'raw_text': raw_text, 'title': title, 'content': content, 'error': error, 'started_at': started_at, 'finished_at': finished_at}
        fields = {key: value for key, value in fields.items() if value is not None}
        stored = {}
        for key in ('partial_text', 'raw_text', 'title', 'content', 'error'):
            if key in fields:
                stored[f'{key}_stored'] = str(fields.pop(key))
        fields['updated_at'] = self._clock()
        with self.database.connect() as connection:
            if worker_id is not None:
                owner = connection.execute('SELECT worker_id,status,lease_expires_at FROM jobs WHERE id=?', (job_id,)).fetchone()
                if owner is None or owner['worker_id'] != worker_id or owner['status'] != 'running' or (owner['lease_expires_at'] is None) or (owner['lease_expires_at'] <= self._clock()):
                    raise JobOwnershipError(job_id)
            existing = connection.execute('SELECT id FROM job_candidates WHERE job_id=? AND ordinal=?', (job_id, ordinal)).fetchone()
            if existing is None:
                connection.execute('INSERT INTO job_candidates(id,job_id,ordinal,updated_at) VALUES (?,?,?,?)', (str(uuid.uuid4()), job_id, ordinal, fields['updated_at']))
            assignments = ', '.join((f'{key}=?' for key in (*stored.keys(), *fields.keys())))
            connection.execute(f'UPDATE job_candidates SET {assignments} WHERE job_id=? AND ordinal=?', (*stored.values(), *fields.values(), job_id, ordinal))
            row = connection.execute('SELECT * FROM job_candidates WHERE job_id=? AND ordinal=?', (job_id, ordinal)).fetchone()
            if status == 'succeeded' and job.user_id and content:
                from .user_repository import UserRepository
                evaluation = json.loads(row['evaluation_json']) if row['evaluation_json'] else None
                UserRepository._save_candidate_poem(connection, job.user_id, job_id, ordinal, job.request, {'title': title, 'content': content}, evaluation)
        value = dict(row)
        for key in ('partial_text', 'raw_text', 'title', 'content', 'error'):
            if value.get(f'{key}_stored'):
                value[key] = value[f'{key}_stored']
            value.pop(f'{key}_stored', None)
        evaluation_json = value.pop('evaluation_json', None)
        value['evaluation'] = json.loads(evaluation_json) if evaluation_json else None
        EventRepository(self.database, clock=self._clock).append_job_event(job_id, 'candidate.updated', value)
        return value

    def save_candidate_evaluation(self, job_id: str, ordinal: int, evaluation: Mapping[str, Any], *, user_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job.user_id != user_id:
            raise JobNotFound(job_id)
        payload = json.dumps(dict(evaluation), ensure_ascii=False, separators=(',', ':'))
        now = self._clock()
        with self.database.connect() as connection:
            updated = connection.execute("UPDATE job_candidates SET evaluation_json=NULL,evaluation_stored=?,updated_at=? WHERE job_id=? AND ordinal=? AND status='succeeded'", (payload, now, job_id, ordinal))
            archived = connection.execute('UPDATE poems SET evaluation_json=NULL,evaluation_stored=? WHERE job_id=? AND candidate_ordinal=? AND user_id=?', (payload, job_id, ordinal, user_id))
            if updated.rowcount != 1 and archived.rowcount != 1:
                raise ValueError('候选尚未生成完成')
        EventRepository(self.database, clock=self._clock).append_job_event(job_id, 'candidate.evaluated', {'ordinal': ordinal, 'evaluation': dict(evaluation)})
        return dict(evaluation)

    def candidates(self, job_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute('SELECT ordinal,status,attempt,partial_text,partial_text_stored,raw_text,raw_text_stored,title,title_stored,content,content_stored,error,error_stored,started_at,finished_at,updated_at,evaluation_json,evaluation_stored FROM job_candidates WHERE job_id=? ORDER BY ordinal', (job_id,)).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            for key in ('partial_text', 'raw_text', 'title', 'content', 'error'):
                if value.get(f'{key}_stored'):
                    value[key] = value[f'{key}_stored']
                value.pop(f'{key}_stored', None)
            evaluation_json = value.pop('evaluation_json', None)
            evaluation_stored = value.pop('evaluation_stored', None)
            if evaluation_stored:
                evaluation_json = evaluation_stored
            value['evaluation'] = json.loads(evaluation_json) if evaluation_json else None
            values.append(value)
        return values

    def snapshot(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        candidates = self.candidates(job_id)
        total = len(candidates) or int(job.request.get('candidate_count', 0) or 0)
        completed = sum((1 for item in candidates if item['status'] == 'succeeded'))
        return {'job_id': job_id, 'kind': job.kind, 'status': job.status, 'created_at': job.created_at, 'total': total, 'completed': completed, 'request': job.request, 'candidates': candidates, 'result': job.result, 'error': job.to_dict()['error']}

    def has_live_worker(self, *, max_age_seconds: int=45) -> bool:
        threshold = self._clock() - max_age_seconds
        with self.database.connect() as connection:
            row = connection.execute('SELECT 1 FROM workers WHERE last_seen_at >= ? LIMIT 1', (threshold,)).fetchone()
        return row is not None

    def get_agent_turn_job(self, turn_id: str) -> JobRecord | None:
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM jobs WHERE agent_turn_id=? ORDER BY created_at DESC LIMIT 1', (turn_id,)).fetchone()
        return self._record(row) if row else None

    def get_by_idempotency_key(self, key: str, *, user_id: str | None=None) -> JobRecord | None:
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM jobs WHERE idempotency_key=? AND user_id IS NOT DISTINCT FROM ?', (key, user_id)).fetchone()
        return self._record(row) if row else None

    @staticmethod
    def _touch_worker(connection, worker_id, capabilities, now) -> None:
        payload = json.dumps(capabilities, ensure_ascii=False, separators=(',', ':'))
        connection.execute('\n            INSERT INTO workers(id, capabilities_json, last_seen_at) VALUES (?, ?, ?)\n            ON CONFLICT(id) DO UPDATE SET\n                capabilities_json = excluded.capabilities_json,\n                last_seen_at = excluded.last_seen_at\n            ', (worker_id, payload, now))

    @staticmethod
    def _recover_expired(connection, now: float) -> None:
        connection.execute('\n            UPDATE jobs\n            SET status = ?, worker_id = NULL, lease_expires_at = NULL,\n                updated_at = ?, finished_at = ?\n            WHERE status = ? AND lease_expires_at < ? AND cancel_requested = 1\n            ', (JobStatus.CANCELLED.value, now, now, JobStatus.RUNNING.value, now))
        connection.execute('\n            UPDATE jobs\n            SET status = ?, worker_id = NULL, lease_expires_at = NULL, updated_at = ?\n            WHERE status = ? AND lease_expires_at < ? AND attempts < max_attempts\n                AND cancel_requested = 0\n            ', (JobStatus.QUEUED.value, now, JobStatus.RUNNING.value, now))
        connection.execute("\n            UPDATE jobs\n            SET status = ?, error_code = 'WORKER_LEASE_EXPIRED',\n                error_message = 'Worker lease expired after maximum attempts',\n                worker_id = NULL, lease_expires_at = NULL, updated_at = ?, finished_at = ?\n            WHERE status = ? AND lease_expires_at < ? AND attempts >= max_attempts\n                AND cancel_requested = 0\n            ", (JobStatus.FAILED.value, now, now, JobStatus.RUNNING.value, now))

    @staticmethod
    def _record(row) -> JobRecord:
        request_raw = row.get('request_stored') if hasattr(row, 'get') else None
        result_raw = row.get('result_stored') if hasattr(row, 'get') else None
        error_raw = row.get('error_message_stored') if hasattr(row, 'get') else None
        request_json = request_raw if request_raw else row['request_json']
        result_json = result_raw if result_raw else row['result_json']
        error_message = error_raw if error_raw else row['error_message']
        return JobRecord(id=row['id'], kind=row['kind'], status=row['status'], request=json.loads(request_json), result=json.loads(result_json) if result_json else None, error_code=row['error_code'], error_message=error_message, attempts=row['attempts'], max_attempts=row['max_attempts'], worker_id=row['worker_id'], lease_expires_at=row['lease_expires_at'], created_at=row['created_at'], updated_at=row['updated_at'], started_at=row['started_at'], finished_at=row['finished_at'], user_id=row['user_id'] if 'user_id' in row.keys() else None, conversation_id=row['conversation_id'] if 'conversation_id' in row.keys() else None)
