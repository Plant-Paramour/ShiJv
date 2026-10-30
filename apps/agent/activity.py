"""面向用户的执行轨迹投影，不包含调用参数及工具返回值。"""
TOOL_LABELS = {'understand_image': '理解图片内容', 'list_rhyme_parts': '查询韵书的韵部目录', 'list_rhyme_books': '查询韵书', 'lookup_rhyme': '查询韵部', 'get_rhyme_part': '查询韵部详情', 'list_ci_meters': '查询词牌', 'get_ci_meter': '查询词牌格律', 'list_supported_forms': '查询诗词体裁', 'check_pingze': '校验诗词平仄', 'prosody_checker': '校验诗词格律', 'search_allusions': '检索相关典故', 'query_poetry': '查询诗词资料', 'get_poem_source': '读取作品', 'prepare_generation': '编辑创作提示词', 'prepare_partial_generation': '编辑补写提示词', 'prepare_rewrite': '编辑改写提示词', 'submit_generation': '提交创作任务', 'submit_partial_generation': '提交补写任务', 'submit_rewrite': '提交改写任务', 'get_poetry_job': '查询创作进度', 'get_latest_poetry_job': '查询最近创作', 'list_poetry_jobs': '查询创作记录'}

def tool_label(name, arguments=None):
    import json
    import re
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            arguments = {}
    arguments = arguments if isinstance(arguments, dict) else {}

    def field(key):
        value = arguments.get(key)
        return re.sub('[\\x00-\\x1f<>]', '', value).strip()[:64] if isinstance(value, str) else ''
    meter, variant = (field('name'), field('variant_name'))
    if name == 'list_rhyme_parts' and field('rhyme_book'):
        return f"查询{field('rhyme_book')}的韵部目录"
    if name == 'list_ci_meters':
        return f'查询《{meter}》词牌的变体' if meter else '查询词牌目录及变体'
    if name == 'get_ci_meter' and meter:
        return f"查询《{meter}》词牌{variant or '默认体'}格律"
    if name in {'lookup_rhyme', 'get_rhyme_part'}:
        book, part = (field('rhyme_book'), field('rhyme_part'))
        if book or part:
            return f'查询{book}{part}韵部'
    return TOOL_LABELS.get(name, '处理诗词资料')

def tool_activity(event_type, payload):
    state = 'running' if event_type == 'tool.started' else 'failed' if event_type == 'tool.failed' or (payload.get('output') or {}).get('ok') is False else 'completed'
    return {'id': str(payload.get('tool_call_id') or 'unknown'), 'label': payload.get('label') or tool_label(payload.get('name'), payload.get('arguments')), 'state': state}

def attach_tool_activity(database, conversation_id, messages):
    import json
    targets = {message['id']: message for message in messages}
    records = {}
    previews = {}
    with database.connect() as connection:
        rows = connection.execute("SELECT t.assistant_message_id,t.status,e.id,e.created_at,e.event_type,e.payload_stored,e.payload_json FROM agent_events e JOIN agent_turns t ON t.id=e.turn_id WHERE t.conversation_id=? AND e.event_type IN ('tool.started','tool.completed','tool.failed','assistant.activity','assistant.preview.delta','assistant.preview.reset','assistant.delta','turn.retrying') ORDER BY e.seq", (conversation_id,)).fetchall()
    for row in rows:
        message_id = row['assistant_message_id']
        if message_id not in targets:
            continue
        payload = json.loads(row.get('payload_stored') or row.get('payload_json') or '{}')
        preview = previews.setdefault(message_id, {'reply': '', 'text': ''})
        if row['event_type'] == 'assistant.preview.delta':
            preview['text'] += str(payload.get('text') or '')
            continue
        if row['event_type'] == 'assistant.preview.reset':
            preview['text'] = ''
            continue
        if row['event_type'] == 'assistant.delta':
            preview['reply'] += preview['text'] + str(payload.get('text') or '')
            preview['text'] = ''
            continue
        if row['event_type'] == 'turn.retrying':
            preview.update(reply='', text='')
            continue
        if row['event_type'] == 'assistant.activity':
            preview['text'] = ''
            item = {'id': row['id'], 'kind': 'narration', 'text': str(payload.get('text') or ''), 'state': 'completed'}
        else:
            item = tool_activity(row['event_type'], payload)
        previous = records.get(message_id, {}).get(item['id'], {})
        if row.get('created_at') is not None:
            item['started_at'] = previous.get('started_at', row['created_at'])
            item['updated_at'] = row['created_at']
        if item['state'] == 'running' and row['status'] not in {'queued', 'running', 'resuming', 'pausing', 'cancelling'}:
            item['state'] = 'interrupted'
        records.setdefault(message_id, {})[item['id']] = item
    for message in messages:
        message['tool_activity'] = list(records.get(message['id'], {}).values())
        preview = previews.get(message['id'], {})
        text = preview.get('reply', '') + preview.get('text', '')
        if message.get('status') == 'pending' and text:
            message['tool_activity'].append({'id': 'live-preview', 'kind': 'narration', 'text': text, 'state': 'completed'})
