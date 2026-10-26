from __future__ import annotations
from pathlib import Path
import uuid
from fastapi import APIRouter, Header, HTTPException, Path as PathParameter, Request
from fastapi.responses import Response
from ..local_user import local_user
from ..user_repository import PoemNotFoundError
from ..schemas import PoemPatchModel, PoemRevisionCreateModel, CollectionCreateModel, CollectionPatchModel, CollectionItemModel, ProfilePatchModel
router = APIRouter(prefix='/v1/profile', tags=['profile'])

def _serve_avatar(filename: str, request: Request):
    local_user(request)
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail='avatar not found')
    if filename.startswith('db-'):
        owner = filename[3:].removesuffix('.jpg')
        with request.app.state.database.connect() as connection:
            row = connection.execute('SELECT avatar_stored FROM users WHERE id=?', (owner,)).fetchone()
        if not row or not row['avatar_stored']:
            raise HTTPException(status_code=404, detail='avatar not found')
        return Response(bytes(row['avatar_stored']), media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})
    path = request.app.state.settings.database_path.parent / 'avatars' / filename
    try:
        content = path.read_bytes()
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail='avatar not found')
    return Response(content=content, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})

@router.get('/avatar-file/{filename}')
def avatar_file(filename: str, request: Request):
    return _serve_avatar(filename, request)

def legacy_avatar_file(filename: str, request: Request):
    """读取本地档案中保存的头像 URL。"""
    return _serve_avatar(filename, request)

def _valid_image_bytes(content_type: str, content: bytes) -> bool:
    if content_type == 'image/png':
        return len(content) >= 24 and content.startswith(b'\x89PNG\r\n\x1a\n') and (content[12:16] == b'IHDR') and (int.from_bytes(content[16:20], 'big') > 0) and (int.from_bytes(content[20:24], 'big') > 0)
    if content_type == 'image/webp':
        return len(content) >= 20 and content[:4] == b'RIFF' and (content[8:12] == b'WEBP') and (content[12:16] in {b'VP8 ', b'VP8L', b'VP8X'})
    return len(content) >= 16 and content.startswith(b'\xff\xd8\xff') and content.endswith(b'\xff\xd9')

@router.get('/me')
def profile_me(request: Request):
    user = local_user(request)
    return user

@router.patch('/me')
def patch_profile(body: ProfilePatchModel, request: Request):
    user = local_user(request)
    try:
        request.app.state.users.update_profile(user['id'], body.model_dump(exclude_none=True, include={'display_name', 'bio'}))
        return profile_me(request)
    except Exception as exc:
        if 'unique' not in str(exc).lower() and 'duplicate' not in str(exc).lower():
            raise
        raise HTTPException(status_code=409, detail='这个昵称已被使用') from exc

@router.post('/me/avatar')
async def upload_avatar(request: Request):
    user = local_user(request)
    content_type = request.headers.get('content-type', '').split(';', 1)[0].lower()
    extensions = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}
    if content_type not in extensions:
        raise HTTPException(status_code=415, detail='头像仅支持 JPG、PNG 或 WebP')
    content = await request.body()
    if not content or len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='头像大小必须在 2MB 以内')
    if not _valid_image_bytes(content_type, content):
        raise HTTPException(status_code=422, detail='头像文件内容无效')
    from ..avatar import compress_avatar_bounded, AvatarBusy
    from starlette.concurrency import run_in_threadpool
    try:
        content = await run_in_threadpool(compress_avatar_bounded, request.app.state.database, content)
    except AvatarBusy as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail='头像无法处理') from exc
    with request.app.state.database.connect() as connection:
        connection.execute('UPDATE users SET avatar_stored=? WHERE id=?', (content, user['id']))
    return request.app.state.users.update_profile(user['id'], {'avatar_url': f"/v1/profile/avatar-file/db-{user['id']}.jpg"})

@router.get('/poems')
def poems(request: Request, work_type: str | None=None, favorite: bool=False, latest: bool=False, limit: int=24, offset: int=0):
    user = local_user(request)
    if latest and (not favorite):
        items, has_more = request.app.state.users.list_poem_cards(user['id'], limit=limit, offset=offset, work_type=work_type)
        return {'items': items, 'has_more': has_more, 'limit': max(1, min(limit, 100)), 'offset': max(0, offset)}
    items = request.app.state.users.list_favorite_poems(user['id']) if favorite else request.app.state.users.list_poems(user['id'])
    if work_type:
        items = [item for item in items if item.get('work_type') == work_type]
    return {'items': items}

@router.patch('/poems/{poem_id}')
def patch_poem(poem_id: str, body: PoemPatchModel, request: Request):
    user = local_user(request)
    if any((value is not None for value in (body.title, body.content, body.work_type, body.meter_type, body.form_name))):
        raise HTTPException(status_code=409, detail='作品正文与标题不可覆盖，请使用修改版本接口保存到我的创作')
    if not request.app.state.users.update_poem(poem_id, user['id'], body.model_dump(exclude_none=True)):
        raise HTTPException(status_code=404, detail='poem not found')
    return {'status': 'updated'}

@router.get('/poems/by-job/{job_id}/{ordinal}')
def poem_by_job(job_id: str, ordinal: int, request: Request):
    user = local_user(request)
    with request.app.state.users.database.connect() as db:
        row = db.execute('SELECT id FROM poems WHERE job_id=? AND candidate_ordinal=? AND user_id=?', (job_id, ordinal, user['id'])).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail='作品尚未归档')
    return request.app.state.users.get_poem_detail(row['id'], user['id'])

@router.post('/poems/{poem_id}/revisions', status_code=201)
def create_poem_revision(poem_id: str, body: PoemRevisionCreateModel, request: Request):
    user = local_user(request)
    if body.source_poem_id is not None and body.source_poem_id != poem_id:
        raise HTTPException(status_code=422, detail='修改来源与作品地址不一致')
    evaluation = body.evaluation
    if body.evaluated_content != body.content:
        raise HTTPException(status_code=422, detail='正文已变化，请重新计算格律评分')
    if not isinstance(evaluation.get('result'), dict) or not isinstance(evaluation.get('comprehensive_score'), (int, float)) or (not 0 <= evaluation['comprehensive_score'] <= 100):
        raise HTTPException(status_code=422, detail='请先完成当前正文的格律评分')
    try:
        variant_options = {}
        if 'variant_name' in body.model_fields_set:
            variant_options = {'variant_name': body.variant_name, 'variant_provided': True}
        return request.app.state.users.create_poem_revision(poem_id, user['id'], body.title, body.content, evaluation, sequence=body.sequence, **variant_options)
    except PoemNotFoundError as exc:
        raise HTTPException(status_code=404, detail='作品不存在') from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post('/poem-forests/{forest_id}/roots', status_code=201)
def create_poem_forest_root(forest_id: str, body: PoemRevisionCreateModel, request: Request):
    user = local_user(request)
    if not body.source_poem_id:
        raise HTTPException(status_code=422, detail='新增森林节点必须指定实际修改来源 source_poem_id')
    evaluation = body.evaluation
    if body.evaluated_content != body.content:
        raise HTTPException(status_code=422, detail='正文已变化，请重新计算格律评分')
    if not isinstance(evaluation.get('result'), dict) or not isinstance(evaluation.get('comprehensive_score'), (int, float)) or (not 0 <= evaluation['comprehensive_score'] <= 100):
        raise HTTPException(status_code=422, detail='请先完成当前正文的格律评分')
    try:
        variant_options = {}
        if 'variant_name' in body.model_fields_set:
            variant_options = {'variant_name': body.variant_name, 'variant_provided': True}
        return request.app.state.users.create_poem_revision(forest_id, user['id'], body.title, body.content, evaluation, sequence=body.sequence, forest_reference=True, revision_source_id=body.source_poem_id, **variant_options)
    except PoemNotFoundError as exc:
        raise HTTPException(status_code=404, detail='作品森林不存在或不可访问') from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.delete('/poems/{poem_id}')
def delete_poem(poem_id: str, request: Request):
    user = local_user(request)
    if not request.app.state.users.delete_poem(poem_id, user['id']):
        raise HTTPException(status_code=404, detail='poem not found')
    return {'status': 'deleted'}

@router.post('/poems/{poem_id}/favorite')
def favorite_poem(poem_id: str, request: Request):
    user = local_user(request)
    if not request.app.state.users.set_favorite(user['id'], poem_id, True):
        raise HTTPException(status_code=404, detail='poem not found')
    return {'favorite': True}

@router.delete('/poems/{poem_id}/favorite')
def unfavorite_poem(poem_id: str, request: Request):
    user = local_user(request)
    if not request.app.state.users.set_favorite(user['id'], poem_id, False):
        raise HTTPException(status_code=404, detail='poem not found')
    return {'favorite': False}

@router.get('/poem-collections')
def collections(request: Request):
    user = local_user(request)
    return {'items': request.app.state.users.list_poem_collections(user['id'])}

@router.post('/poem-collections', status_code=201)
def create_collection(body: CollectionCreateModel, request: Request):
    user = local_user(request)
    return request.app.state.users.create_poem_collection(user['id'], body.name)

@router.patch('/poem-collections/{collection_id}')
def patch_collection(collection_id: str, body: CollectionPatchModel, request: Request):
    user = local_user(request)
    if not request.app.state.users.update_poem_collection(collection_id, user['id'], body.name):
        raise HTTPException(status_code=404, detail='collection not found')
    return {'id': collection_id, 'name': body.name}

@router.delete('/poem-collections/{collection_id}')
def delete_collection(collection_id: str, request: Request):
    user = local_user(request)
    if not request.app.state.users.delete_poem_collection(collection_id, user['id']):
        raise HTTPException(status_code=404, detail='collection not found')
    return {'status': 'deleted'}

@router.post('/poem-collections/{collection_id}/items')
def add_collection_item(collection_id: str, request: Request, body: CollectionItemModel | None=None, poem_id: str | None=None):
    user = local_user(request)
    selected_poem_id = body.poem_id if body is not None else poem_id
    if not selected_poem_id or not request.app.state.users.collection_item(collection_id, selected_poem_id, user['id'], True):
        raise HTTPException(status_code=404, detail='collection or poem not found')
    return {'status': 'added'}

@router.get('/poem-collections/{collection_id}/items')
def collection_items(collection_id: str, request: Request):
    user = local_user(request)
    return {'items': request.app.state.users.list_collection_items(collection_id, user['id'])}

@router.get('/poems/{poem_id}')
def poem_detail(poem_id: str, request: Request):
    user = local_user(request)
    value = request.app.state.users.get_poem_detail(poem_id, user['id'])
    if not value:
        raise HTTPException(status_code=404, detail='poem not found')
    if value.get('deleted'):
        return value
    with request.app.state.users.database.connect() as db:
        value['favorite'] = db.execute('SELECT 1 FROM poem_favorites WHERE poem_id=? AND user_id=?', (poem_id, user['id'])).fetchone() is not None
    return value

@router.get('/poems/{poem_id}/tree')
def poem_tree(poem_id: str, request: Request):
    user = local_user(request)
    value = request.app.state.users.get_poem_tree(poem_id, user['id'])
    if value is None:
        raise HTTPException(status_code=404, detail='poem not found')
    return value

@router.post('/poems/{poem_id}/forest-marker')
def mark_poem_forest_version(poem_id: str, request: Request):
    user = local_user(request)
    try:
        return request.app.state.users.mark_poem_forest_version(poem_id, user['id'])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='作品不存在或不可访问') from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

@router.delete('/poems/{poem_id}/forest-marker')
def unmark_poem_forest_version(poem_id: str, request: Request):
    user = local_user(request)
    try:
        return request.app.state.users.unmark_poem_forest_version(poem_id, user['id'])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='作品不存在或不可访问') from exc

@router.get('/poems/{poem_id}/sentence-variations/{sentence_number}')
def poem_sentence_variations(request: Request, poem_id: str, sentence_number: int=PathParameter(ge=1)):
    user = local_user(request)
    try:
        value = request.app.state.users.get_poem_sentence_variations(poem_id, sentence_number, user['id'])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if value is None:
        raise HTTPException(status_code=404, detail='poem not found')
    return value

@router.get('/poems/{poem_id}/rewrite-source')
def poem_rewrite_source(poem_id: str, request: Request):
    user = local_user(request)
    try:
        return request.app.state.users.get_rewrite_source(poem_id, user['id'], request.app.state.project_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.delete('/poem-collections/{collection_id}/items/{poem_id}')
def remove_collection_item(collection_id: str, poem_id: str, request: Request):
    user = local_user(request)
    if not request.app.state.users.collection_item(collection_id, poem_id, user['id'], False):
        raise HTTPException(status_code=404, detail='collection or poem not found')
    return {'status': 'removed'}
