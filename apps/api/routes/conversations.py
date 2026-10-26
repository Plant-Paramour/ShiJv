from __future__ import annotations
import json
import time
import asyncio
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Header, HTTPException, Request, status
from ..local_user import local_user
from ..schemas import ConversationAutoCreateModel, ConversationCreateModel, ConversationMessageEditModel, ConversationPatchModel, FolderCreateModel, FolderPatchModel
from ..event_repository import EventRepository
from ..event_stream import event_stream_response
router = APIRouter(prefix='/v1/conversations', tags=['conversations'])

def _conversation_owner(request: Request, conversation_id: str, actor: dict) -> str:
    access = request.app.state.users.conversation_access(conversation_id, actor['id'])
    if access is None:
        raise HTTPException(status_code=404, detail='conversation not found')
    return access['user_id']

@router.get('')
def list_conversations(request: Request, include_deleted: bool=False, sort: str='updated'):
    user = local_user(request)
    return {'items': request.app.state.users.list_conversations(user['id'], include_deleted=include_deleted, sort=sort)}

@router.post('', status_code=201)
def create_conversation(body: ConversationCreateModel, request: Request):
    user = local_user(request)
    return request.app.state.users.create_conversation(user['id'], body.title)

@router.post('/auto', status_code=201)
def create_auto_conversation(body: ConversationAutoCreateModel, request: Request):
    """创建直连生成会话，并复用普通 Agent 会话的摘要标题逻辑。"""
    user = local_user(request)
    service = request.app.state.agent_service
    title_generator = getattr(service, 'generate_conversation_title', None)
    reservation = request.app.state.jobs.admission.reserve(user['id'], 'title') if callable(title_generator) else None
    try:
        title = title_generator(body.message) if callable(title_generator) else body.message[:15]
        conversation = request.app.state.users.create_conversation(user['id'], body.message[:15])
        request.app.state.users.rename_conversation(conversation['id'], user['id'], title)
        conversation['title'] = title[:15] or '新建对话'
        return conversation
    finally:
        if reservation:
            request.app.state.jobs.admission.release(reservation)

@router.post('/{conversation_id}/auto-title')
def auto_title_conversation(conversation_id: str, body: ConversationAutoCreateModel, request: Request):
    user = local_user(request)
    if request.app.state.users.get_conversation(conversation_id, user['id']) is None:
        raise HTTPException(status_code=404, detail='conversation not found')
    service = request.app.state.agent_service
    title_generator = getattr(service, 'generate_conversation_title', None)
    reservation = request.app.state.jobs.admission.reserve(user['id'], 'title') if callable(title_generator) else None
    try:
        title = title_generator(body.message) if callable(title_generator) else body.message[:15]
        request.app.state.users.rename_conversation(conversation_id, user['id'], title)
        return {'id': conversation_id, 'title': title[:15] or '新建对话'}
    finally:
        if reservation:
            request.app.state.jobs.admission.release(reservation)

@router.get('/folders')
def folders(request: Request):
    user = local_user(request)
    return {'items': request.app.state.users.list_folders(user['id'])}

@router.post('/folders', status_code=201)
def create_folder(body: FolderCreateModel, request: Request):
    user = local_user(request)
    return request.app.state.users.create_folder(user['id'], body.name)

@router.patch('/folders/{folder_id}')
def patch_folder(folder_id: str, body: FolderPatchModel, request: Request):
    user = local_user(request)
    if not request.app.state.users.update_folder(folder_id, user['id'], body.name):
        raise HTTPException(status_code=404, detail='folder not found')
    return {'id': folder_id, 'name': body.name}

@router.get('/{conversation_id}')
def get_conversation(conversation_id: str, request: Request):
    user = local_user(request)
    owner_id = _conversation_owner(request, conversation_id, user)
    conversation = request.app.state.users.get_conversation(conversation_id, owner_id)
    access = request.app.state.users.conversation_access(conversation_id, user['id'])
    branches = request.app.state.users.list_branches(conversation_id, owner_id)
    active_branch = next((item for item in branches if item['is_active']), None)
    head_message_id = active_branch.get('head_message_id') if active_branch else None
    if active_branch and (not request.app.state.users.message_exists(head_message_id, conversation_id)):
        head_message_id = active_branch.get('root_message_id')
    conversation['messages'] = request.app.state.users.list_path_messages(conversation_id, owner_id, head_message_id)
    from apps.agent.activity import attach_tool_activity
    attach_tool_activity(request.app.state.jobs.database, conversation_id, conversation['messages'])
    conversation['owner'] = {'id': access['user_id'], 'username': access['owner_username'], 'display_name': access['owner_display_name'] or access['owner_username'], 'avatar_url': access['owner_avatar_url']}
    conversation['read_only'] = owner_id != user['id']
    conversation['branches'] = branches
    conversation['active_branch_id'] = next((item['id'] for item in branches if item['is_active']), None)
    conversation['branch_path'] = conversation['messages']
    path_message_ids = {message['id'] for message in conversation['messages']}
    conversation['pending_proposal'] = _pending_proposal(request, conversation_id, owner_id, message_ids=path_message_ids)
    conversation['conversation_proposal'] = _pending_proposal(request, conversation_id, owner_id, include_submitted=True, message_ids=path_message_ids)
    return conversation

def _pending_proposal(request: Request, conversation_id: str, user_id: str, *, include_submitted: bool=False, message_ids: set[str] | None=None) -> dict | None:
    with request.app.state.jobs.database.connect() as connection:
        rows = connection.execute('SELECT proposal_id, kind, payload_json, payload_stored, submitted_job_id, assistant_message_id FROM agent_proposals WHERE conversation_id=? AND user_id=? ORDER BY updated_at DESC', (conversation_id, user_id)).fetchall()
    if not rows:
        return None
    event_message_ids = {}
    if any((not row['assistant_message_id'] for row in rows)):
        with request.app.state.jobs.database.connect() as connection:
            events = connection.execute("SELECT t.assistant_message_id, e.payload_json, e.payload_stored FROM agent_events e JOIN agent_turns t ON t.id=e.turn_id WHERE t.conversation_id=? AND e.event_type='tool.completed' ORDER BY e.created_at DESC", (conversation_id,)).fetchall()
        for event in events:
            event_payload = json.loads(event.get('payload_stored') or event.get('payload_json') or '{}')
            result = (event_payload.get('output') or {}).get('result') or {}
            proposal_id = result.get('proposal_id')
            if proposal_id and proposal_id not in event_message_ids:
                event_message_ids[proposal_id] = event['assistant_message_id']
    for row in rows:
        if row['submitted_job_id'] and (not include_submitted):
            continue
        assistant_message_id = row['assistant_message_id'] or event_message_ids.get(row['proposal_id'])
        if message_ids is not None and assistant_message_id not in message_ids:
            continue
        payload = json.loads(row.get('payload_stored') or row.get('payload_json') or '{}')
        if payload.get('original_text'):
            from shiju.rewriting.source import numbered_source
            payload['source_lines'] = numbered_source(payload['original_text'], payload, request.app.state.project_root)['lines']
        return {'proposal_id': row['proposal_id'], 'kind': row['kind'], 'proposal': payload, 'editable_prompt': payload.get('requirement', ''), 'candidate_count': payload.get('candidate_count', 1), 'assistant_message_id': assistant_message_id, 'submitted_job_id': row['submitted_job_id']}
    return None

@router.get('/{conversation_id}/messages')
def list_messages(conversation_id: str, request: Request):
    user = local_user(request)
    owner_id = _conversation_owner(request, conversation_id, user)
    branches = request.app.state.users.list_branches(conversation_id, owner_id)
    active = next((item for item in branches if item['is_active']), None)
    messages = request.app.state.users.list_path_messages(conversation_id, owner_id, active.get('head_message_id') if active else None)
    from apps.agent.activity import attach_tool_activity
    attach_tool_activity(request.app.state.jobs.database, conversation_id, messages)
    return {'items': messages}

@router.patch('/{conversation_id}')
def patch_conversation(conversation_id: str, body: ConversationPatchModel, request: Request):
    user = local_user(request)
    ok = True
    if body.title is not None:
        ok = request.app.state.users.rename_conversation(conversation_id, user['id'], body.title) and ok
    if body.folder_id is not None or body.clear_folder:
        ok = request.app.state.users.move_conversation(conversation_id, user['id'], None if body.clear_folder else body.folder_id) and ok
    if not ok:
        raise HTTPException(status_code=404, detail='conversation not found')
    return request.app.state.users.get_conversation(conversation_id, user['id'])

@router.post('/{conversation_id}/messages/{message_id}/edit')
def edit_message(conversation_id: str, message_id: str, body: ConversationMessageEditModel, request: Request):
    user = local_user(request)
    owner_id = _conversation_owner(request, conversation_id, user)
    if owner_id != user['id']:
        raise HTTPException(status_code=403, detail='只能编辑自己的对话')
    if request.app.state.users.get_message(message_id, conversation_id, owner_id) is None:
        raise HTTPException(status_code=404, detail='user message not found')
    return {'conversation_id': conversation_id, 'message_id': message_id, 'content': body.content, 'preserved': True}

@router.get('/{conversation_id}/tree')
def conversation_tree(conversation_id: str, request: Request):
    user = local_user(request)
    owner_id = _conversation_owner(request, conversation_id, user)
    branches = request.app.state.users.list_branches(conversation_id, owner_id)
    messages = request.app.state.users.list_message_tree(conversation_id, owner_id)
    return {'conversation_id': conversation_id, 'messages': messages, 'roots': [item['id'] for item in messages if not item.get('parent_message_id')], 'branches': branches, 'active_branch_id': next((item['id'] for item in branches if item['is_active']), None)}

@router.post('/{conversation_id}/branches/{branch_id}/activate')
def activate_branch(conversation_id: str, branch_id: str, request: Request):
    user = local_user(request)
    owner_id = _conversation_owner(request, conversation_id, user)
    if owner_id != user['id']:
        raise HTTPException(status_code=403, detail='只能修改自己的对话')
    if not request.app.state.users.activate_branch(branch_id, conversation_id, owner_id):
        raise HTTPException(status_code=404, detail='branch not found')
    return {'conversation_id': conversation_id, 'active_branch_id': branch_id}

@router.post('/{conversation_id}/messages/{message_id}/activate-branch')
def activate_branch_for_message(conversation_id: str, message_id: str, request: Request):
    user = local_user(request)
    owner_id = _conversation_owner(request, conversation_id, user)
    if owner_id != user['id']:
        raise HTTPException(status_code=403, detail='只能修改自己的对话')
    branch_id = request.app.state.users.activate_branch_for_message(message_id, conversation_id, owner_id)
    if branch_id is None:
        raise HTTPException(status_code=404, detail='branch not found for message')
    return {'conversation_id': conversation_id, 'active_branch_id': branch_id}

@router.delete('/{conversation_id}')
def delete_conversation(conversation_id: str, request: Request):
    user = local_user(request)
    if not request.app.state.users.soft_delete_conversation(conversation_id, user['id']):
        raise HTTPException(status_code=404, detail='conversation not found')
    return {'status': 'deleted'}

@router.post('/{conversation_id}/restore')
def restore_conversation(conversation_id: str, request: Request):
    user = local_user(request)
    if not request.app.state.users.restore_conversation(conversation_id, user['id']):
        raise HTTPException(status_code=404, detail='conversation not found')
    return {'status': 'restored'}

@router.get('/{conversation_id}/state')
def conversation_state(conversation_id: str, request: Request):
    user = local_user(request)
    owner_id = _conversation_owner(request, conversation_id, user)
    branches = request.app.state.users.list_branches(conversation_id, owner_id)
    active = next((item for item in branches if item['is_active']), None)
    path_messages = request.app.state.users.list_path_messages(conversation_id, owner_id, active.get('head_message_id') if active else None)
    path_ids = {message['id'] for message in path_messages}
    job_messages: dict[str, set[str]] = {}

    def associate(job_id, message_id):
        if job_id and message_id:
            job_messages.setdefault(job_id, set()).add(message_id)
    for message in request.app.state.users.list_messages(conversation_id, owner_id):
        associate(message.get('job_id'), message['id'])
    with request.app.state.jobs.database.connect() as connection:
        turn_jobs = connection.execute('SELECT j.id, t.assistant_message_id FROM jobs j JOIN agent_turns t ON t.id=j.agent_turn_id WHERE j.conversation_id=?', (conversation_id,)).fetchall()
        proposals = connection.execute('SELECT submitted_job_id, assistant_message_id, proposal_id FROM agent_proposals WHERE conversation_id=?', (conversation_id,)).fetchall()
        events = connection.execute("SELECT t.assistant_message_id, e.event_type, e.payload_json, e.payload_stored FROM agent_events e JOIN agent_turns t ON t.id=e.turn_id WHERE t.conversation_id=? AND e.event_type IN ('job.submitted','tool.completed')", (conversation_id,)).fetchall()
    proposal_messages = {}
    for job in turn_jobs:
        associate(job['id'], job['assistant_message_id'])
    for event in events:
        payload = json.loads(event.get('payload_stored') or event.get('payload_json') or '{}')
        if event['event_type'] == 'job.submitted':
            associate(payload.get('job_id'), event['assistant_message_id'])
        else:
            result = (payload.get('output') or {}).get('result') or {}
            if payload.get('name') in {'submit_generation', 'submit_partial_generation', 'submit_rewrite'}:
                associate(result.get('job_id'), event['assistant_message_id'])
                for submitted in result.get('jobs') or []:
                    associate(submitted.get('job_id'), event['assistant_message_id'])
            if result.get('proposal_id'):
                proposal_messages[result['proposal_id']] = event['assistant_message_id']
    for proposal in proposals:
        associate(proposal['submitted_job_id'], proposal['assistant_message_id'] or proposal_messages.get(proposal['proposal_id']))
    jobs = request.app.state.jobs.list_for_user(owner_id, conversation_id)
    branch_jobs = []
    for job in jobs:
        anchors = job_messages.get(job.id, set())
        visible_anchors = anchors & path_ids
        if anchors and (not visible_anchors):
            continue
        snapshot = request.app.state.jobs.snapshot(job.id)
        snapshot['assistant_message_id'] = next((message['id'] for message in path_messages if message['id'] in visible_anchors), None)
        branch_jobs.append(snapshot)
    from apps.agent.activity import attach_tool_activity
    attach_tool_activity(request.app.state.jobs.database, conversation_id, path_messages)
    return {'conversation_id': conversation_id, 'messages': path_messages, 'jobs': branch_jobs}

@router.get('/{conversation_id}/events')
def conversation_events(conversation_id: str, request: Request, after: int=0):
    user = local_user(request)
    owner_id = _conversation_owner(request, conversation_id, user)
    events = EventRepository(request.app.state.jobs.database)

    async def stream():
        cursor = after
        deadline = time.time() + 25
        while time.time() < deadline:
            batch = await run_in_threadpool(events.list_agent_events, conversation_id, after=cursor)
            if batch:
                for event in batch:
                    cursor = max(cursor, int(event['seq']))
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"
                return
            yield ': keep-alive\n\n'
            await asyncio.sleep(1)
    return event_stream_response(request, stream(), user_id=user['id'])
