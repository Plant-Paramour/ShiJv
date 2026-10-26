from __future__ import annotations
import json
import logging
import os
import signal
import socket
import threading
import time
from typing import Any
from apps.api.database import Database
from apps.api.event_repository import EventRepository
from apps.api.job_repository import JobOwnershipError, JobRepository
from apps.api.settings import ApiSettings
from apps.api.user_repository import UserRepository
from .framework import AgentTurnPaused
from .openai_client import ChatModelError
from .errors import new_error_id, public_error, user_error_text
from .web_service import WebAgentService
from .image_store import load_images, remove_images
logger = logging.getLogger(__name__)

class _LeaseCancellation:

    def __init__(self, jobs: JobRepository, job_id: str, worker_id: str, lease_seconds: int, timeout_seconds: int):
        self._jobs = jobs
        self._job_id = job_id
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._timeout_seconds = timeout_seconds
        self._started_at = time.monotonic()
        self.timed_out = False
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def is_set(self) -> bool:
        return self._cancel.is_set()

    def wait(self, timeout: float | None=None) -> bool:
        return self._cancel.wait(timeout)

    def _heartbeat(self) -> None:
        interval = max(0.05, min(5.0, self._lease_seconds / 3, self._timeout_seconds / 4))
        while not self._stop.wait(interval):
            if time.monotonic() - self._started_at >= self._timeout_seconds:
                self.timed_out = True
                self._cancel.set()
                return
            try:
                if self._jobs.heartbeat(self._job_id, self._worker_id, lease_seconds=self._lease_seconds):
                    self._cancel.set()
                    return
            except JobOwnershipError:
                self._cancel.set()
                return

class AgentExecutor:
    """独立执行 Agent turn；队列和取消信号均由数据库协调。"""

    def __init__(self, jobs: JobRepository, users: UserRepository, service: Any, *, worker_id: str | None=None, lease_seconds: int=300, turn_timeout_seconds: int=900, poll_seconds: float=1.0):
        self.jobs = jobs
        self.users = users
        self.service = service
        self.worker_id = worker_id or f'agent-{socket.gethostname()}-{os.getpid()}'
        self.lease_seconds = lease_seconds
        self.turn_timeout_seconds = max(1, turn_timeout_seconds)
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._stop.wait(self.poll_seconds)

    def run_once(self) -> bool:
        job = self.jobs.claim(self.worker_id, {'service': 'agent-executor'}, lease_seconds=self.lease_seconds, kinds=('agent_turn',))
        if job is None:
            return False
        self._execute(job)
        return True

    def _execute(self, job) -> None:
        request = job.request
        turn_id = str(request['turn_id'])
        events = EventRepository(self.jobs.database)
        cancellation = _LeaseCancellation(self.jobs, job.id, self.worker_id, self.lease_seconds, self.turn_timeout_seconds)
        cancellation.start()
        reply_parts: list[str] = []
        submitted_jobs: list[dict[str, Any]] = []
        stream_events = None
        retain_images = False

        def publish(event_type: str, payload: dict[str, Any]) -> None:
            payload.setdefault('turn_id', turn_id)
            payload.setdefault('branch_id', request.get('branch_id'))
            payload.setdefault('message_id', request.get('assistant_message_id'))
            if payload.get('name') == 'understand_image' and event_type == 'tool.completed':
                payload = {**payload, 'output': {'ok': (payload.get('output') or {}).get('ok', False)}}
            events.append_agent_event(turn_id, event_type, payload)
        try:
            events.set_turn_status(turn_id, 'running', cancel_requested=False)
            publish('turn.started', {'conversation_id': request.get('conversation_id'), 'user_message_id': request.get('user_message_id'), 'assistant_message_id': request.get('assistant_message_id')})
            if hasattr(self.service, 'respond_stream'):
                stream_events = self.service.respond_stream(request['message'], request.get('session_id'), history=request.get('history'), user_id=request.get('user_id'), conversation_id=request.get('conversation_id'), model=request.get('model'), cancellation_event=cancellation, **{'images': load_images(request['images_ref'])} if request.get('images_ref') else {})
            else:
                result = self.service.respond(request['message'], request.get('session_id'), history=request.get('history'), user_id=request.get('user_id'), conversation_id=request.get('conversation_id'), model=request.get('model'))
                stream_events = ({'event_type': 'assistant.delta', 'payload': {'text': result.get('reply', '')}}, *({'event_type': 'job.submitted', 'payload': item} for item in result.get('jobs') or []), {'event_type': 'turn.completed', 'payload': {'reply': result.get('reply', ''), 'jobs': result.get('jobs') or []}})
            completed = False
            for item in stream_events:
                event_type = item['event_type']
                payload = dict(item.get('payload') or {})
                if event_type == 'assistant.delta':
                    reply_parts.append(str(payload.get('text') or ''))
                    if request.get('user_id') and request.get('assistant_message_id'):
                        self.users.update_message(request['assistant_message_id'], request['conversation_id'], request['user_id'], content=''.join(reply_parts), status='pending')
                    publish(event_type, payload)
                elif event_type == 'job.submitted':
                    submitted_jobs.append(payload)
                    if request.get('user_id') and payload.get('job_id'):
                        self.jobs.assign_context(payload['job_id'], request['user_id'], request.get('conversation_id'))
                    publish(event_type, payload)
                elif event_type == 'tool.completed':
                    result = payload.get('output', {}).get('result', {})
                    result = result if isinstance(result, dict) else {}
                    proposal_id = result.get('proposal_id')
                    assistant_message_id = request.get('assistant_message_id')
                    if proposal_id and assistant_message_id:
                        with self.jobs.database.connect() as connection:
                            connection.execute('UPDATE agent_proposals SET assistant_message_id=?,updated_at=? WHERE proposal_id=?', (assistant_message_id, time.time(), proposal_id))
                    publish(event_type, payload)
                elif event_type == 'turn.completed':
                    reply = str(payload.get('reply') or ''.join(reply_parts))
                    result_jobs = payload.get('jobs') or submitted_jobs
                    job_id = result_jobs[-1].get('job_id') if result_jobs else None
                    if request.get('user_id') and request.get('assistant_message_id'):
                        self.users.update_message(request['assistant_message_id'], request['conversation_id'], request['user_id'], content=reply, status='completed', job_id=job_id)
                    publish(event_type, {'job_id': job_id, 'reply': reply, 'conversation_id': request.get('conversation_id'), 'session_id': payload.get('session_id')})
                    events.finish_turn(turn_id)
                    self.jobs.complete(job.id, self.worker_id, {'turn_id': turn_id, 'reply': reply})
                    completed = True
                    break
                else:
                    publish(event_type, payload)
            if not completed:
                raise RuntimeError('Agent executor 在未收到 turn.completed 时结束')
        except AgentTurnPaused:
            turn = events.get_turn(turn_id) or {}
            if cancellation.timed_out:
                events.finish_turn(turn_id, 'failed')
                error = public_error('AGT-TURN-TIMEOUT')
                publish('turn.failed', {**error, 'error': error['message']})
                if request.get('user_id') and request.get('assistant_message_id'):
                    self.users.update_message(request['assistant_message_id'], request['conversation_id'], request['user_id'], content=''.join(reply_parts) + '\n\n' + user_error_text(error['error_code'], error['error_id']), status='failed')
                self.jobs.fail(job.id, self.worker_id, 'AGENT_TIMEOUT', 'Agent turn timed out', retryable=False)
                return
            status = 'paused' if turn.get('status') == 'pausing' else 'cancelled'
            events.finish_turn(turn_id, status)
            if request.get('user_id') and request.get('assistant_message_id'):
                self.users.update_message(request['assistant_message_id'], request['conversation_id'], request['user_id'], content=''.join(reply_parts), status=status)
            publish('turn.' + status, {'text': ''.join(reply_parts)})
            self.jobs.acknowledge_cancel(job.id, self.worker_id)
        except Exception as exc:
            error_id = new_error_id()
            error_code = 'AGT-MODEL-UNAVAILABLE' if isinstance(exc, ChatModelError) else 'AGT-TURN-FAILED'
            logger.exception('Agent turn failed [%s] error_id=%s', error_code, error_id)
            try:
                self.jobs.fail(job.id, self.worker_id, error_code, str(exc), retryable=True)
            except JobOwnershipError:
                return
            latest = self.jobs.get(job.id)
            retrying = latest.status == 'queued'
            retain_images = retrying
            events.finish_turn(turn_id, 'queued' if retrying else 'failed')
            if retrying:
                publish('turn.retrying', {'message': '正在恢复连接并继续处理。'})
            else:
                message = user_error_text(error_code, error_id)
                if request.get('user_id') and request.get('assistant_message_id'):
                    self.users.update_message(request['assistant_message_id'], request['conversation_id'], request['user_id'], content=''.join(reply_parts) + '\n\n' + message, status='failed')
                public = public_error(error_code, error_id)
                publish('turn.failed', {**public, 'error': public['message']})
        finally:
            close = getattr(stream_events, 'close', None)
            try:
                if callable(close):
                    close()
            finally:
                cancellation.close()
                if not retain_images:
                    remove_images(request.get('images_ref'))
