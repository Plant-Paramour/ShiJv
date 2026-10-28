from __future__ import annotations
from fastapi import APIRouter, Header, HTTPException, Request, status
import asyncio
import json
import time
from shiju.contracts import GeneratePoemRequest, JobKind, PartialGeneratePoemRequest, RewritePoemRequest, validate_sentence_inputs
from shiju.data import MeterTemplateRepository, RhymeLexicon
from shiju.tasks import parse_pailv_format, parse_tang_format
from shiju.rewriting.validation import validate_rewrite_context
from shiju.generation.validation import validate_fixed_line_constraints, validate_partial_generation_request
from shiju.rewriting.parser import parse_poem
from ..job_repository import IdempotencyConflict, JobNotFound
from ..schemas import CandidateEvaluationModel, GenerateJobModel, RewriteJobModel, PartialGenerateJobModel
from ..local_user import local_user
from ..event_stream import event_stream_response
from starlette.concurrency import run_in_threadpool
router = APIRouter(prefix='/v1/poetry/jobs', tags=['poetry-tools'])

def _poetry_shape(request: Request, payload: dict) -> tuple[int, int, dict[int, int], str | None]:
    """返回句数、句长、押韵句号和韵声；所有错误在入队前变成 422。"""
    meter_type = str(payload.get('meter_type') or '')
    form_name = str(payload.get('form_name') or '')
    if meter_type == '唐诗':
        length, count, rhyme_type = parse_tang_format(form_name)
        return (count, length, {line: 1 for line in ({1, 2, 4} if count == 4 else {1, 2, 4, 6, 8})}, '仄' if '仄' in rhyme_type else '平')
    if meter_type == '排律':
        length, count = parse_pailv_format(form_name, payload.get('num_lines'))
        if count > 128:
            raise ValueError('排律单次生成最多 128 句')
        return (count, length, {line: 1 for line in range(1, count + 1) if line == 1 or line % 2 == 0}, '平')
    if meter_type == '宋词':
        template = MeterTemplateRepository(request.app.state.meter_directory).get(form_name, payload.get('variant_name'))
        return (len(template.lines), max((line.layout.length for line in template.lines)), {index + 1: line.rhyme_group for index, line in enumerate(template.lines) if line.rhyme_group is not None}, None)
    if meter_type == '汉俳':
        pattern = str((payload.get('task_options') or {}).get('line_pattern', '5-7-5')).replace('五', '5').replace('七', '7')
        lengths = [int(item) for item in pattern.replace('－', '-').split('-') if item.isdigit()]
        if lengths not in ([5, 7, 5], [3, 5, 3]):
            raise ValueError('汉俳格式仅支持 5-7-5 或 3-5-3')
        return (len(lengths), max(lengths), {}, None)
    raise ValueError(f'不支持的诗体: {meter_type}')

def _validate_and_lock_payload(request: Request, payload: dict, *, partial: bool=False, allow_context_constraint_conflicts: bool=False) -> dict:
    count, default_length, rhyme_lines, rhyme_tone = _poetry_shape(request, payload)
    validate_sentence_inputs(payload.get('sentence_inputs'), payload.get('fixed_lines') or {}, payload.get('target_line_numbers') if payload.get('original_text') else None)
    if payload.get('sentence_inputs') is not None and set(payload['sentence_inputs']) != {str(number) for number in range(1, count + 1)}:
        raise ValueError('逐句输入句数与篇式不一致')
    fixed = {str(key): str(value).strip() for key, value in (payload.get('fixed_lines') or {}).items()}
    for key, text in fixed.items():
        if not key.isdigit() or not 1 <= int(key) <= count:
            raise ValueError(f"指定句序号超出范围: {key}（{payload.get('form_name')} 共 {count} 句）")
        if not text:
            raise ValueError(f'第 {key} 句不能为空')
        expected_length = default_length
        if payload.get('meter_type') == '汉俳':
            pattern = str((payload.get('task_options') or {}).get('line_pattern', '5-7-5')).replace('五', '5').replace('七', '7').replace('－', '-')
            expected_length = [int(value) for value in pattern.split('-')][int(key) - 1]
        if payload.get('meter_type') != '宋词' and len(text) != expected_length:
            raise ValueError(f'第 {key} 句应为 {expected_length} 字，实际为 {len(text)} 字')
    book = str(payload.get('rhyme_dict_name') or 'Xinyun')
    lexicon = RhymeLexicon(request.app.state.project_root / 'Rhyme' / f'{book}.json')
    requested = {str(key): str(value) for key, value in (payload.get('rhyme_parts') or {}).items()}
    valid_groups = {str(group) for group in rhyme_lines.values()}
    locked: dict[str, str] = {}
    songci_template = None
    if payload.get('meter_type') == '宋词':
        songci_template = MeterTemplateRepository(request.app.state.meter_directory).get(payload.get('form_name'), payload.get('variant_name'))
    for line, text in fixed.items():
        if int(line) not in rhyme_lines or not rhyme_tone:
            if int(line) not in rhyme_lines or songci_template is None:
                continue
        character = next((char for char in reversed(text) if '一' <= char <= '鿿'), '')
        if songci_template is not None:
            allowed_tones = songci_template.lines[int(line) - 1].tone_options[-1]
            parts = {part for tone in allowed_tones for part in lexicon.get_rhyme_part_by_tone(character, tone)} if character else set()
        else:
            parts = set(lexicon.get_rhyme_part_by_tone(character, rhyme_tone)) if character else set()
        if int(line) == 1 and payload.get('meter_type') in {'唐诗', '排律'} and (not parts):
            continue
        if not parts:
            if allow_context_constraint_conflicts:
                continue
            raise ValueError(f'第 {line} 句末字“{character}”没有可用于{rhyme_tone}韵的韵部')
        if len(parts) == 1:
            locked[str(rhyme_lines[int(line)])] = next(iter(parts))
    for group, part in requested.items():
        if group not in valid_groups:
            raise ValueError(f"{payload.get('form_name')} 不存在韵组 {group}")
        if group in locked:
            continue
        if str(part).lower() != 'random' and (not any((item_part == part and (not rhyme_tone or tone == rhyme_tone) for _, item_part, tone in lexicon.iter_rhyme_entries()))):
            raise ValueError(f"韵部 {part} 没有可用于{rhyme_tone or '当前'}韵的字")
    if locked:
        payload['rhyme_mode'] = 'fixed'
        payload['rhyme_parts'] = {**requested, **locked}
    payload['fixed_lines'] = fixed
    return payload

def _meter_catalog(request: Request):
    """返回前端方案编辑器可选的宋词词牌及其变体。"""
    from pathlib import Path
    base = getattr(request.app.state, 'meter_directory', None)
    if base is None:
        configured = getattr(getattr(request.app.state, 'settings', None), 'meter_source', None)
        base = Path(configured) if configured else Path(__file__).resolve().parents[3] / 'Songci_Meter'
        if not base.is_absolute():
            base = Path(__file__).resolve().parents[3] / base
    items = []
    if base.is_dir():
        for path in sorted(base.glob('*.json')):
            if path.name.lower() in {'index.json', 'meter-version.json'}:
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    variants = [str(item.get('name')) for item in data.get('variants') or [] if isinstance(item, dict) and item.get('name')]
                    name = str(data.get('name') or path.stem)
                    aliases = [str(item) for item in data.get('aliases') or [] if str(item).strip()]
                elif isinstance(data, list):
                    variants = [str(item.get('name')) for item in data if isinstance(item, dict) and item.get('name')]
                    name = path.stem
                    aliases = []
                else:
                    continue
                default_variant = MeterTemplateRepository(base).get(path.stem).variant_name
                items.append({'name': name, 'aliases': aliases, 'variants': variants, 'default_variant': default_variant})
            except (OSError, ValueError, TypeError):
                continue
    return {'items': items}

@router.get('/meters')
def list_meters(request: Request):
    return _meter_catalog(request)

@router.get('/meter-version')
def get_meter_version(request: Request):
    from fastapi.responses import JSONResponse
    response = JSONResponse({'meter_version': request.app.state.meter_version, 'rhyme_version': request.app.state.rhyme_version})
    response.headers['Cache-Control'] = 'public, max-age=300, stale-while-revalidate=86400'
    return response

@router.get('/meter-templates')
def get_meter_templates(request: Request, meter_version: str):
    from fastapi.responses import JSONResponse
    if meter_version != request.app.state.meter_version:
        raise HTTPException(status_code=409, detail='词谱版本已更新，请重新检查版本')
    payload = getattr(request.app.state, 'meter_payload', None)
    if payload is None:
        directory = request.app.state.meter_directory
        payload = {'meter_version': request.app.state.meter_version, 'items': [{'file': path.name, 'data': json.loads(path.read_text(encoding='utf-8'))} for path in sorted(directory.glob('*.json')) if path.name.lower() not in {'index.json', 'meter-version.json'}]}
        request.app.state.meter_payload = payload
    response = JSONResponse(payload)
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

@router.get('/rhyme-options', deprecated=True)
@router.get('/rhyme-part', deprecated=True)
@router.post('/validate-fixed-lines', deprecated=True)
def local_rhyme_only():
    raise HTTPException(status_code=410, detail='韵部查询和固定句预检已迁移至浏览器，请刷新页面')

@router.get('/v1/poetry/meters')
def list_meters_alias(request: Request):
    return _meter_catalog(request)

def _local_user_id(request):
    return local_user(request)['id']

def _submit(request: Request, kind: str, payload: dict, idempotency_key: str | None, user_id: str | None, conversation_id: str | None=None):
    if conversation_id and (not user_id or request.app.state.users.get_conversation(conversation_id, user_id) is None):
        raise HTTPException(status_code=404, detail='conversation not found')
    if kind == JobKind.PARTIAL_GENERATE.value:
        try:
            if payload.get('original_text'):
                PartialGeneratePoemRequest.from_mapping(payload)
            else:
                contract = GeneratePoemRequest.from_mapping(payload)
                validate_partial_generation_request(contract, request.app.state.project_root)
                if not payload.get('task_options', {}).get('allow_context_constraint_conflicts'):
                    validate_fixed_line_constraints(contract, request.app.state.project_root)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.local_runtime.require_ready(f'http://127.0.0.1:{request.url.port or 18763}')
    repository = request.app.state.jobs
    try:
        job = repository.submit(kind, payload, idempotency_key=idempotency_key, user_id=user_id, conversation_id=conversation_id)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if conversation_id and user_id:
        request.app.state.users.touch_conversation(conversation_id, user_id)
    request.app.state.gpu_provider.ensure_running(job.id)
    from apps.launcher.diagnostics import OperationDiagnostic
    diagnostic = OperationDiagnostic(request.app.state.local_runtime.directory, 'submission')
    diagnostic.event('submitted', request_id=getattr(request.state, 'request_id', ''), job_id=job.id,
                     worker_diagnostics=request.app.state.local_runtime.diagnostics)
    diagnostic.save(dict(completed=True, ok=True, job_id=job.id, request_id=getattr(request.state, 'request_id', ''), diagnostics=diagnostic.metadata))
    request.state.diagnostics = diagnostic.metadata
    diagnostic.close()
    return {'job_id': job.id, 'status': job.status, 'waiting_for_worker': not repository.has_live_worker()}

def _job_response(request: Request, job) -> dict:
    value = job.to_dict(include_request=True)
    value['waiting_for_worker'] = job.status == 'queued' and (not request.app.state.jobs.has_live_worker())
    return value

def _check_job_access(request: Request, job, user_id: str | None) -> None:
    if user_id and job.user_id != user_id:
        raise HTTPException(status_code=404, detail='job not found')

@router.post('/generate', status_code=status.HTTP_202_ACCEPTED)
def generate_poem(body: GenerateJobModel, request: Request, idempotency_key: str | None=Header(default=None, alias='Idempotency-Key'), conversation_id: str | None=None):
    user_id = _local_user_id(request)
    try:
        payload = _validate_and_lock_payload(request, body.model_dump(mode='json'))
        contract = GeneratePoemRequest.from_mapping(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _submit(request, JobKind.GENERATE.value, contract.to_dict(), idempotency_key, user_id, conversation_id)

@router.post('/rewrite', status_code=status.HTTP_202_ACCEPTED)
def rewrite_poem_lines(body: RewriteJobModel, request: Request, idempotency_key: str | None=Header(default=None, alias='Idempotency-Key'), conversation_id: str | None=None):
    user_id = _local_user_id(request)
    try:
        if not body.source_poem_id:
            raise ValueError('改写必须提供作品 ID')
        source_payload = request.app.state.users.lock_rewrite_source(body.model_dump(mode='json'), user_id, request.app.state.project_root)
        payload = _validate_and_lock_payload(request, source_payload, partial=True, allow_context_constraint_conflicts=bool(source_payload.get('task_options', {}).get('allow_context_constraint_conflicts')))
        contract = RewritePoemRequest.from_mapping(payload)
        validate_rewrite_context(contract, request.app.state.project_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _submit(request, JobKind.REWRITE.value, contract.to_dict(), idempotency_key, user_id, conversation_id)

@router.post('/partial-generate', status_code=status.HTTP_202_ACCEPTED)
def partial_generate(body: PartialGenerateJobModel, request: Request, idempotency_key: str | None=Header(default=None, alias='Idempotency-Key'), conversation_id: str | None=None):
    user_id = _local_user_id(request)
    payload = body.model_dump(mode='json')
    try:
        payload = request.app.state.users.lock_rewrite_source(payload, user_id, request.app.state.project_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.get('original_text'):
        poem = parse_poem(payload['original_text'])
        targets = set(payload.get('target_line_numbers') or [])
        if payload.get('sentence_inputs') is None:
            payload['fixed_lines'] = {str(line.number): line.text for line in poem.lines if line.number not in targets}
        try:
            payload = _validate_and_lock_payload(request, payload, partial=True, allow_context_constraint_conflicts=bool(payload.get('task_options', {}).get('allow_context_constraint_conflicts')))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        contract = PartialGeneratePoemRequest.from_mapping(payload)
        validate_rewrite_context(contract, request.app.state.project_root)
        normalized = contract.to_dict()
    else:
        if not payload.get('fixed_lines'):
            raise HTTPException(status_code=422, detail='部分生成必须至少指定一句需要原样保留的句子')
        generate_fields = {'meter_type', 'form_name', 'variant_name', 'rhyme_dict_name', 'rhyme_mode', 'rhyme_parts', 'requirement', 'theme', 'num_lines', 'strict_polyphonic', 'candidate_count', 'task_options', 'sampling', 'fixed_lines', 'sentence_inputs'}
        try:
            payload = _validate_and_lock_payload(request, payload, partial=True, allow_context_constraint_conflicts=bool(payload.get('task_options', {}).get('allow_context_constraint_conflicts')))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        normalized = GeneratePoemRequest.from_mapping({key: value for key, value in payload.items() if key in generate_fields}).to_dict()
    if not normalized.get('fixed_lines'):
        raise HTTPException(status_code=422, detail='部分生成必须至少指定一句需要原样保留的句子')
    return _submit(request, JobKind.PARTIAL_GENERATE.value, normalized, idempotency_key, user_id, conversation_id)

@router.get('')
def list_poetry_jobs(request: Request, conversation_id: str | None=None, active_only: bool=False, limit: int=20, offset: int=0):
    user_id = _local_user_id(request)
    if user_id is None:
        return {'items': []}
    jobs = request.app.state.jobs.list_for_user(user_id, conversation_id, active_only=active_only, limit=min(max(limit, 1), 100), offset=max(offset, 0))
    poetry_kinds = {JobKind.GENERATE.value, JobKind.REWRITE.value, JobKind.PARTIAL_GENERATE.value}
    return {'items': [_job_response(request, job) for job in jobs if job.kind in poetry_kinds]}

@router.get('/{job_id}')
def get_poetry_job(job_id: str, request: Request):
    user_id = _local_user_id(request)
    try:
        job = request.app.state.jobs.get(job_id)
        _check_job_access(request, job, user_id)
        return _job_response(request, job)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail='job not found') from exc

@router.delete('/{job_id}')
def cancel_poetry_job(job_id: str, request: Request):
    user_id = _local_user_id(request)
    try:
        _check_job_access(request, request.app.state.jobs.get(job_id), user_id)
        job = request.app.state.jobs.cancel(job_id, user_id=user_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail='job not found') from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_response(request, job)

@router.get('/{job_id}/state')
def get_poetry_job_state(job_id: str, request: Request):
    user_id = _local_user_id(request)
    try:
        job = request.app.state.jobs.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail='job not found') from exc
    _check_job_access(request, job, user_id)
    return request.app.state.jobs.snapshot(job_id)

@router.get('/{job_id}/snapshot')
def get_poetry_job_snapshot(job_id: str, request: Request):
    return get_poetry_job_state(job_id, request)

@router.put('/{job_id}/candidates/{ordinal}/evaluation')
def save_candidate_evaluation(job_id: str, ordinal: int, body: CandidateEvaluationModel, request: Request):
    user_id = _local_user_id(request)
    try:
        _check_job_access(request, request.app.state.jobs.get(job_id), user_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail='job not found') from exc
    try:
        evaluation = request.app.state.jobs.save_candidate_evaluation(job_id, ordinal, body.evaluation, user_id=user_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail='job not found') from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'job_id': job_id, 'ordinal': ordinal, 'evaluation': evaluation}

@router.get('/{job_id}/events')
def poetry_job_events(job_id: str, request: Request, after: int=0):
    user_id = _local_user_id(request)
    try:
        job = request.app.state.jobs.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail='job not found') from exc
    _check_job_access(request, job, user_id)
    from apps.api.event_repository import EventRepository
    events = EventRepository(request.app.state.jobs.database)

    async def stream():
        cursor = after
        deadline = time.time() + 25
        while time.time() < deadline:
            batch = await run_in_threadpool(events.list_job_events, job_id, after=cursor)
            if batch:
                for event in batch:
                    cursor = event['seq']
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"
                if any((event['event_type'] in {'job.completed', 'job.failed', 'job.cancelled'} for event in batch)):
                    return
                continue
            yield ': keep-alive\n\n'
            await asyncio.sleep(1)
    return event_stream_response(request, stream(), user_id=user_id)
