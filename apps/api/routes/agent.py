from __future__ import annotations
import json
import logging
import time
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from apps.agent.openai_client import ChatModelError
from apps.agent.framework import AgentTurnPaused
from apps.agent.image_store import save_images, image_digest
from ..schemas import AgentChatModel, AgentProposalSubmitModel
from ..local_user import local_user
from ..event_repository import EventRepository
from ..job_repository import IdempotencyConflict
from shiju.contracts import GeneratePoemRequest, PartialGeneratePoemRequest, RewritePoemRequest
from shiju.rewriting.validation import validate_rewrite_context
from shiju.generation.validation import validate_fixed_line_constraints
router = APIRouter(prefix='/v1/agent', tags=['agent'])
logger = logging.getLogger(__name__)

@router.post('/proposals/{proposal_id}/submit', status_code=status.HTTP_202_ACCEPTED)
def submit_proposal(proposal_id: str, body: AgentProposalSubmitModel, request: Request):
    user = local_user(request)
    conversation = request.app.state.users.get_conversation(body.conversation_id, user['id'])
    if conversation is None:
        raise HTTPException(status_code=404, detail='conversation not found')
    with request.app.state.jobs.database.connect() as connection:
        row = connection.execute('SELECT * FROM agent_proposals WHERE proposal_id=? AND conversation_id=? AND user_id=?', (proposal_id, body.conversation_id, user['id'])).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail='proposal not found')
    payload = json.loads(row.get('payload_stored') or row.get('payload_json') or '{}')
    payload.pop('source_lines', None)
    if body.sentence_inputs is not None:
        payload['sentence_inputs'] = body.sentence_inputs
    payload.update({'requirement': body.requirement, 'candidate_count': body.candidate_count, 'meter_type': body.meter_type, 'form_name': body.form_name, 'variant_name': body.variant_name, 'rhyme_dict_name': body.rhyme_dict_name, 'strict_polyphonic': body.strict_polyphonic, 'num_lines': body.num_lines, 'task_options': body.task_options, 'fixed_lines': body.fixed_lines})
    if body.theme is not None:
        payload['theme'] = body.theme
    if body.task_type is not None:
        payload['task_type'] = body.task_type
    if body.rhyme_mode is not None:
        payload['rhyme_mode'] = body.rhyme_mode
    if body.rhyme_parts is not None:
        payload['rhyme_parts'] = body.rhyme_parts
    try:
        if row['kind'] == 'rewrite' and (not payload.get('source_poem_id')):
            raise ValueError('改写必须提供作品 ID')
        if row['kind'] == 'partial_generate' and payload.get('original_text'):
            raise ValueError('部分生成只用于补完空缺句；已有作品的指定句重写请使用改写方案')
        if body.target_line_numbers is not None:
            payload['target_line_numbers'] = sorted(body.target_line_numbers)
        payload = request.app.state.users.lock_rewrite_source(payload, user['id'], request.app.state.project_root)
        if payload.get('original_text'):
            from shiju.rewriting.parser import parse_poem
            targets = set(payload.get('target_line_numbers') or [])
            if payload.get('sentence_inputs') is None:
                payload['fixed_lines'] = {str(line.number): line.text for line in parse_poem(payload['original_text']).lines if line.number not in targets}
        if row['kind'] in {'rewrite', 'partial_generate'} and (not payload.get('fixed_lines')):
            raise ValueError('部分生成必须至少指定一句需要原样保留的句子')
        if row['kind'] in {'rewrite', 'partial_generate'} and payload.get('original_text'):
            request_type = PartialGeneratePoemRequest if row['kind'] == 'partial_generate' or body.sentence_inputs is not None else RewritePoemRequest
            normalized = request_type.from_mapping(payload).to_dict()
            validate_rewrite_context(request_type.from_mapping(normalized), request.app.state.project_root)
        else:
            payload.pop('original_text', None)
            payload.pop('target_line_numbers', None)
            normalized = GeneratePoemRequest.from_mapping(payload).to_dict()
            if row['kind'] == 'partial_generate':
                from shiju.generation.validation import validate_partial_generation_request
                validate_partial_generation_request(GeneratePoemRequest.from_mapping(normalized), request.app.state.project_root)
                if not normalized.get('task_options', {}).get('allow_context_constraint_conflicts'):
                    validate_fixed_line_constraints(GeneratePoemRequest.from_mapping(normalized), request.app.state.project_root)
        if row['submitted_job_id']:
            job = request.app.state.jobs.get(row['submitted_job_id'])
            return request.app.state.jobs.snapshot(job.id)
        request.app.state.local_runtime.require_ready(f'http://127.0.0.1:{request.url.port or 18763}')
        job = request.app.state.jobs.submit('partial_generate' if body.sentence_inputs is not None and row['kind'] == 'rewrite' else row['kind'], normalized, idempotency_key=proposal_id, user_id=user['id'], conversation_id=body.conversation_id)
    except (ValueError, IdempotencyConflict) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with request.app.state.jobs.database.connect() as connection:
        connection.execute('UPDATE agent_proposals SET payload_json=NULL,payload_stored=?,submitted_job_id=?,updated_at=? WHERE proposal_id=?', (json.dumps(normalized, ensure_ascii=False, separators=(',', ':')), job.id, time.time(), proposal_id))
        if row['assistant_message_id']:
            connection.execute('UPDATE messages SET job_id=? WHERE id=? AND conversation_id=?', (job.id, row['assistant_message_id'], body.conversation_id))
    request.app.state.users.touch_conversation(body.conversation_id, user['id'])
    request.app.state.gpu_provider.ensure_running(job.id)
    result = request.app.state.jobs.snapshot(job.id)
    result['waiting_for_worker'] = not request.app.state.jobs.has_live_worker()
    return result

@router.post('/chat/stream')
def chat_stream(body: AgentChatModel, request: Request, idempotency_key: str | None=Header(default=None, alias='Idempotency-Key')):
    user = local_user(request)
    if request.app.state.agent_service is None:
        raise HTTPException(409, '请先在模型设置中保存对话端点并选择 Agent 模型')
    if not body.model:
        from ..local_runtime import read_config
        body.model = read_config(request.app.state.settings.database_path.parent)['default_model'] or None
    events = EventRepository(request.app.state.jobs.database)
    conversation_id = body.conversation_id
    resume_turn = None
    if body.turn_id:
        resume_turn = events.get_turn(body.turn_id, user['id'] if user else None)
        if resume_turn is None or not user:
            raise HTTPException(status_code=404, detail='turn not found')
        conversation_id = resume_turn.get('conversation_id')
    if user and conversation_id and (request.app.state.users.get_conversation(conversation_id, user['id']) is None):
        raise HTTPException(status_code=404, detail='conversation not found')
    if user and conversation_id:
        access = request.app.state.users.conversation_access(conversation_id, user['id'])

    def stream():
        nonlocal conversation_id
        turn_id = None
        user_message_id = None
        assistant_message_id = None
        branch_id = None
        try:
            jobs = request.app.state.jobs
            existing = jobs.get_by_idempotency_key(idempotency_key, user_id=user['id']) if idempotency_key and (not resume_turn) else None
            if existing:
                saved = existing.request
                fingerprint = saved.get('idempotency_fingerprint', {})
                if existing.kind != 'agent_turn' or fingerprint.get('message') != body.message or fingerprint.get('user_id') != (user['id'] if user else None) or (fingerprint.get('image_digest', image_digest([])) != image_digest([image.model_dump() for image in body.images])) or (fingerprint.get('model') != body.model) or (fingerprint.get('parent_message_id') != body.parent_message_id) or (body.conversation_id and fingerprint.get('conversation_id') != body.conversation_id):
                    raise IdempotencyConflict('同一 Idempotency-Key 对应了不同 Agent 请求')
                turn_id = saved['turn_id']
                last_seq = 0
            else:
                history = None
                if resume_turn:
                    turn_id = resume_turn['id']
                    branch_id = resume_turn.get('branch_id')
                    if events.active_turn(branch_id, exclude_turn_id=turn_id):
                        raise ValueError('该分支已有正在运行的 Agent turn')
                    user_message_id = resume_turn.get('user_message_id')
                    assistant_message_id = resume_turn.get('assistant_message_id')
                    history = [m for m in request.app.state.users.list_path_messages(conversation_id, user['id'], user_message_id) if m.get('status') == 'completed']
                    with jobs.database.connect() as connection:
                        connection.execute("UPDATE messages SET status='pending',updated_at=? WHERE id=?", (time.time(), assistant_message_id))
                    events.set_turn_status(turn_id, 'queued', cancel_requested=False)
                    last_seq = int(resume_turn.get('last_event_seq', 0) or 0)
                else:
                    if user and conversation_id is None:
                        conversation = request.app.state.users.create_conversation(user['id'], body.message[:80])
                        conversation_id = conversation['id']
                        request.app.state.users.rename_conversation(conversation_id, user['id'], body.message[:15] or '新建对话')
                    if user and conversation_id:
                        target = request.app.state.users.get_message(body.parent_message_id, conversation_id, user['id']) if body.parent_message_id else None
                        if body.parent_message_id and target is None:
                            raise ValueError('parent message not found')
                        branch = request.app.state.users.ensure_branch_for_message(conversation_id, user['id'], body.parent_message_id)
                        branch_id = branch['id']
                        if events.active_turn(branch_id):
                            raise ValueError('该分支已有正在运行的 Agent turn')
                        history_head = target.get('parent_message_id') if target else branch.get('head_message_id')
                        history = [m for m in request.app.state.users.list_path_messages(conversation_id, user['id'], history_head) if m.get('status') == 'completed']
                        user_message = request.app.state.users.add_message(conversation_id, user['id'], 'user', body.message, parent_message_id=target['parent_message_id'] if target and target.get('role') == 'user' else target['id'] if target else history_head)
                        request.app.state.users.update_branch_root_if_empty(branch_id, user_message['id'])
                        user_message_id = user_message['id']
                        pending = request.app.state.users.add_message(conversation_id, user['id'], 'assistant', '', status='pending', parent_message_id=user_message_id)
                        assistant_message_id = pending['id']
                    turn_id = events.start_turn(user_id=user['id'] if user else None, conversation_id=conversation_id, user_message_id=user_message_id, assistant_message_id=assistant_message_id, branch_id=branch_id, status='queued')
                    if user and conversation_id and assistant_message_id:
                        with jobs.database.connect() as connection:
                            connection.execute('UPDATE messages SET turn_id=?,updated_at=? WHERE id=?', (turn_id, time.time(), assistant_message_id))
                        request.app.state.users.update_branch_head(branch_id, assistant_message_id)
                    last_seq = 0
                payload = {'turn_id': turn_id, 'message': body.message, 'images_ref': save_images([image.model_dump() for image in body.images]), 'session_id': conversation_id or body.session_id, 'history': history, 'user_id': user['id'] if user else None, 'conversation_id': conversation_id, 'branch_id': branch_id, 'user_message_id': user_message_id, 'assistant_message_id': assistant_message_id, 'model': body.model, 'idempotency_fingerprint': {'message': body.message, 'user_id': user['id'] if user else None, 'conversation_id': body.conversation_id, 'parent_message_id': body.parent_message_id, 'model': body.model, 'image_digest': image_digest([image.model_dump() for image in body.images])}}
                job_key = f'agent-resume:{turn_id}:{time.time_ns()}' if resume_turn else idempotency_key or f'agent-turn:{turn_id}'
                events.append_agent_event(turn_id, 'turn.queued', {'turn_id': turn_id, 'branch_id': branch_id, 'conversation_id': conversation_id, 'user_message_id': user_message_id, 'assistant_message_id': assistant_message_id, 'resuming': bool(resume_turn)})
                jobs.submit('agent_turn', payload, idempotency_key=job_key, max_attempts=2, user_id=user['id'] if user else None, conversation_id=conversation_id, agent_turn_id=turn_id)
            yield None
            last_ping = time.monotonic()
            terminal = {'completed', 'failed', 'paused', 'cancelled', 'interrupted'}
            while True:
                batch = events.list_turn_events(turn_id, after=last_seq)
                for item in batch:
                    last_seq = item['seq']
                    if item['event_type'] == 'tool.delta':
                        continue
                    if item['event_type'] in {'tool.started', 'tool.completed', 'tool.failed'}:
                        from apps.agent.activity import tool_activity
                        activity = tool_activity(item['event_type'], item['payload'])
                        activity['updated_at'] = item['created_at']
                        yield f'event: tool.activity\ndata: {json.dumps(activity, ensure_ascii=False)}\n\n'
                        payload = item['payload']
                        if item['event_type'] != 'tool.completed' or payload.get('name') not in {'prepare_generation', 'prepare_partial_generation', 'prepare_rewrite'}:
                            continue
                        if not (payload.get('output') or {}).get('ok'):
                            continue
                    if item['event_type'] == 'assistant.activity':
                        item['payload'] = {'id': item['id'], 'text': item['payload'].get('text', ''), 'updated_at': item['created_at']}
                    yield f"id: {item['seq']}\nevent: {item['event_type']}\ndata: {json.dumps(item['payload'], ensure_ascii=False)}\n\n"
                turn = events.get_turn(turn_id) or {}
                job = jobs.get_agent_turn_job(turn_id)
                if turn.get('status') in terminal and (job is None or job.status not in {'queued', 'running'}):
                    if events.list_turn_events(turn_id, after=last_seq):
                        continue
                    return
                if not batch and time.monotonic() - last_ping >= 15:
                    yield ': keepalive\n\n'
                    last_ping = time.monotonic()
                if not batch:
                    time.sleep(0.1)
        except GeneratorExit:
            return
        except Exception as exc:
            logger.exception('Agent event stream failed')
            from apps.agent.errors import public_error, user_error_text
            error_code = 'AGT-TURN-FAILED'
            error = public_error(error_code)
            if turn_id:
                failed = events.append_agent_event(turn_id, 'turn.failed', {**error, 'error': error['message']})
                events.finish_turn(turn_id, 'failed')
                if user and conversation_id and assistant_message_id:
                    try:
                        request.app.state.users.update_message(assistant_message_id, conversation_id, user['id'], content=user_error_text(error_code, error['error_id']), status='failed')
                    except Exception:
                        pass
                yield f"id: {failed['seq']}\nevent: turn.failed\ndata: {json.dumps(failed['payload'], ensure_ascii=False)}\n\n"
            else:
                yield f"event: error\ndata: {json.dumps({**error, 'error': error['message']}, ensure_ascii=False)}\n\n"
    iterator = stream()
    first = next(iterator)

    def output():
        try:
            if first is not None:
                yield first
            yield from iterator
        finally:
            iterator.close()
    return StreamingResponse(output(), media_type='text/event-stream; charset=utf-8', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

def _turn_for_user(request: Request, turn_id: str):
    user = local_user(request)
    turn = EventRepository(request.app.state.jobs.database).get_turn(turn_id, user['id'])
    if turn is None:
        raise HTTPException(status_code=404, detail='turn not found')
    return (user, turn)

@router.get('/turns/{turn_id}')
def get_turn(turn_id: str, request: Request):
    _, turn = _turn_for_user(request, turn_id)
    return turn

@router.post('/turns/{turn_id}/pause')
def pause_turn(turn_id: str, request: Request):
    _, turn = _turn_for_user(request, turn_id)
    if turn['status'] not in {'queued', 'running', 'resuming'}:
        return turn
    events = EventRepository(request.app.state.jobs.database)
    events.set_turn_status(turn_id, 'pausing', cancel_requested=True)
    job = request.app.state.jobs.get_agent_turn_job(turn_id)
    if job is None:
        events.set_turn_status(turn_id, 'paused', cancel_requested=True)
    elif job.status == 'queued':
        request.app.state.jobs.cancel(job.id, user_id=turn.get('user_id'))
        events.finish_turn(turn_id, 'paused')
        events.append_agent_event(turn_id, 'turn.paused', {'turn_id': turn_id})
    elif job.status == 'running':
        request.app.state.jobs.cancel(job.id, user_id=turn.get('user_id'))
    return events.get_turn(turn_id)

@router.post('/turns/{turn_id}/cancel')
def cancel_turn(turn_id: str, request: Request):
    _, turn = _turn_for_user(request, turn_id)
    events = EventRepository(request.app.state.jobs.database)
    job = request.app.state.jobs.get_agent_turn_job(turn_id)
    if job is None:
        events.finish_turn(turn_id, 'cancelled')
    elif job.status == 'queued':
        request.app.state.jobs.cancel(job.id, user_id=turn.get('user_id'))
        events.finish_turn(turn_id, 'cancelled')
        events.append_agent_event(turn_id, 'turn.cancelled', {'turn_id': turn_id})
    elif job.status == 'running':
        events.set_turn_status(turn_id, 'cancelling', cancel_requested=True)
        request.app.state.jobs.cancel(job.id, user_id=turn.get('user_id'))
    return events.get_turn(turn_id)

@router.post('/turns/{turn_id}/resume')
def resume_turn(turn_id: str, request: Request):
    _, turn = _turn_for_user(request, turn_id)
    if turn['status'] not in {'paused', 'interrupted'}:
        return turn
    events = EventRepository(request.app.state.jobs.database)
    events.set_turn_status(turn_id, 'resuming', cancel_requested=False)
    return events.get_turn(turn_id)

@router.post('/chat')
def chat(body: AgentChatModel, request: Request):
    service = request.app.state.agent_service
    if service is None:
        from apps.agent.errors import public_error, user_error_text
        error = public_error('AGT-SERVICE-UNAVAILABLE')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=user_error_text(error['error_code'], error['error_id']))
    user = local_user(request)
    conversation_id = body.conversation_id
    if conversation_id and request.app.state.users.get_conversation(conversation_id, user['id']) is None:
        raise HTTPException(status_code=404, detail='conversation not found')
    admission = request.app.state.jobs.admission
    reservation = admission.reserve(user['id'], 'agent')
    stop_heartbeat = admission.start_heartbeat(reservation)
    pending_message_id: str | None = None
    turn_id: str | None = None
    event_store = EventRepository(request.app.state.jobs.database)
    try:
        history = None
        if user:
            if conversation_id is None:
                conversation = request.app.state.users.create_conversation(user['id'], body.message[:80])
                conversation_id = conversation['id']
            elif request.app.state.users.get_conversation(conversation_id, user['id']) is None:
                raise HTTPException(status_code=404, detail='conversation not found')
            history = [message for message in request.app.state.users.list_messages(conversation_id, user['id']) if message.get('status') == 'completed']
            user_message = request.app.state.users.add_message(conversation_id, user['id'], 'user', body.message, parent_message_id=body.parent_message_id)
            pending = request.app.state.users.add_message(conversation_id, user['id'], 'assistant', 'AI 正在生成回复……', status='pending')
            pending_message_id = pending['id']
            turn_id = event_store.start_turn(user_id=user['id'], conversation_id=conversation_id, user_message_id=user_message['id'], assistant_message_id=pending_message_id)
            event_store.append_agent_event(turn_id, 'turn.started', {'conversation_id': conversation_id})
        try:
            result = service.respond(body.message, conversation_id or body.session_id, history=history, user_id=user['id'] if user else None, conversation_id=conversation_id, model=body.model, images=[image.model_dump() for image in body.images])
        except TypeError:
            if body.images:
                raise
            result = service.respond(body.message, conversation_id or body.session_id)
        if user:
            for job in result.get('jobs') or []:
                request.app.state.jobs.assign_context(job['job_id'], user['id'], conversation_id)
            if result.get('job_id') and (not result.get('jobs')):
                request.app.state.jobs.assign_context(result['job_id'], user['id'], conversation_id)
        if user and conversation_id:
            request.app.state.users.update_message(pending_message_id, conversation_id, user['id'], content=result['reply'], status='completed', job_id=result.get('job_id'))
            result = {**result, 'conversation_id': conversation_id}
        if turn_id:
            event_store.append_agent_event(turn_id, 'assistant.delta', {'text': result.get('reply', '')})
            for job in result.get('jobs') or []:
                event_store.append_agent_event(turn_id, 'job.submitted', job)
            event_store.append_agent_event(turn_id, 'turn.completed', {'job_id': result.get('job_id')})
            event_store.finish_turn(turn_id)
        return result
    except ChatModelError as exc:
        logger.exception('Agent model request failed')
        from apps.agent.errors import public_error, user_error_text
        error = public_error('AGT-MODEL-UNAVAILABLE')
        message = user_error_text(error['error_code'], error['error_id'])
        if turn_id:
            event_store.append_agent_event(turn_id, 'turn.failed', {**error, 'error': error['message']})
            event_store.finish_turn(turn_id, 'failed')
        _mark_failed(request, user, conversation_id, pending_message_id, message)
        raise HTTPException(status_code=502, detail=message) from exc
    except ValueError as exc:
        from apps.agent.errors import public_error
        error = public_error('AGT-REQUEST-INVALID')
        logger.warning('Agent request rejected [%s] error_id=%s: %s', error['error_code'], error['error_id'], exc)
        message = f"{str(exc)}（错误码：{error['error_code']}，追踪号：{error['error_id']}）"
        _mark_failed(request, user, conversation_id, pending_message_id, message)
        raise HTTPException(status_code=422, detail=message) from exc
    except Exception as exc:
        logger.exception('Agent synchronous request failed')
        from apps.agent.errors import public_error, user_error_text
        error = public_error('AGT-TURN-FAILED')
        message = user_error_text(error['error_code'], error['error_id'])
        _mark_failed(request, user, conversation_id, pending_message_id, message)
        if turn_id:
            event_store.append_agent_event(turn_id, 'turn.failed', {**error, 'error': error['message']})
            event_store.finish_turn(turn_id, 'failed')
        raise HTTPException(status_code=500, detail=message) from exc
    finally:
        stop_heartbeat()
        admission.release(reservation)

def _mark_failed(request, user, conversation_id, message_id, error: str) -> None:
    if not user or not conversation_id or (not message_id):
        return
    request.app.state.users.update_message(message_id, conversation_id, user['id'], content=f'回复失败：{error}', status='failed')
