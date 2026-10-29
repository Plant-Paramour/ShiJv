from __future__ import annotations
from pathlib import Path
import logging
import os
import time
import uuid
from .database import Database
from .job_repository import JobRepository
from .settings import ApiSettings
from .request_body import RequestBodyLimitMiddleware
from .user_repository import UserRepository
from .event_repository import EventRepository, TurnController
from .observability import begin, end

def create_app(settings: ApiSettings | None=None, *, agent_service=None):
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
    from .static_files import ApplicationStaticFiles
    from apps.agent.web_service import WebAgentService
    from .routes.agent import router as agent_router
    from .routes.conversations import router as conversations_router
    from .routes.folders import router as folders_router
    from .routes.profile import router as profile_router
    from .routes.profile import legacy_avatar_file
    from .routes.public import router as public_router
    from .routes.worker import router as worker_router
    from .routes.local_profile import router as local_profile_router
    from .routes.model_config import router as model_config_router
    from .routes.backup import router as backup_router
    active = settings or ApiSettings.from_env()
    database = Database(active.database_url or ('sqlite:///' + active.database_path.as_posix()))
    database.initialize()
    from .image_storage_cleanup import purge_legacy_chat_images
    purge_legacy_chat_images(database)
    EventRepository(database).interrupt_unfinished_turns()
    users = UserRepository(database)
    users.ensure_local_profile()
    app = FastAPI(title='诗矩工具服务', version='0.2.0')
    app.add_middleware(CORSMiddleware, allow_origin_regex='https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?$', allow_credentials=False, allow_methods=['GET', 'POST', 'PATCH', 'PUT', 'DELETE', 'OPTIONS'], allow_headers=['Content-Type', 'Idempotency-Key'])
    app.router.on_shutdown.append(database.close)
    app.state.settings = active
    from apps.launcher.build_info import build_info
    app.state.build_info = build_info()
    from .startup_diagnostics import router as diagnostics_router
    app.include_router(diagnostics_router)
    app.state.database = database
    app.state.users = users
    app.state.turn_controller = TurnController()
    project_root = Path(__file__).resolve().parents[2]
    app.state.project_root = project_root
    from apps.launcher.web_assets import check_web_assets
    try:
        import hashlib
        app.state.web_assets = {'state': 'ready', 'files': {name: hashlib.sha256((project_root / name).read_bytes()).hexdigest()
                                 for name in check_web_assets(project_root)}}
    except Exception as exc:
        from apps.launcher.diagnostics import OperationDiagnostic
        diagnostic = OperationDiagnostic(active.database_path.parent, 'web_assets')
        message = diagnostic.fail(exc, 'WEB_ASSETS_INVALID')
        app.state.web_assets = {'state': 'failed', 'error': message, 'diagnostics': diagnostic.metadata}
        diagnostic.save(dict(completed=True, ok=False, **app.state.web_assets))
        diagnostic.close()
    configured_meter_dir = getattr(active, 'meter_source', None)
    meter_dir = Path(configured_meter_dir) if configured_meter_dir else project_root / 'Songci_Meter'
    if not meter_dir.is_absolute():
        meter_dir = project_root / meter_dir
    app.state.meter_directory = meter_dir
    app.state.jobs = JobRepository(database, meter_source=meter_dir, settings=active)
    from .local_runtime import LocalRuntime, LocalPoetryProvider, read_config
    from .routes.local_runtime import router as local_runtime_router
    app.state.local_runtime = LocalRuntime(active.database_path.parent, project_root, active, app.state.jobs)
    app.state.gpu_provider = LocalPoetryProvider(app.state.local_runtime)
    app.router.on_shutdown.append(app.state.local_runtime.stop)

    def auto_start_local_worker():
        from .environment import selected_environment
        if read_config(active.database_path.parent)['auto_start'] and selected_environment(active.database_path.parent)['local_enabled']:
            try:
                app.state.local_runtime.start(os.getenv('SHIJU_CONTROL_URL', 'http://127.0.0.1:18763'), background=True)
            except Exception:
                logging.getLogger(__name__).exception('本地生成器自动启动失败')
    app.router.on_startup.append(auto_start_local_worker)
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def diagnostic_validation_error(request, exc):
        from fastapi.exception_handlers import request_validation_exception_handler
        from apps.launcher.diagnostics import OperationDiagnostic
        import json
        diagnostic = OperationDiagnostic(active.database_path.parent, 'request-validation')
        diagnostic.error_code = 'REQUEST_VALIDATION_FAILED'
        diagnostic.event('failed', request_id=getattr(request.state, 'request_id', ''), path=request.url.path,
                         errors=[dict(type=e['type'], loc=e['loc']) for e in exc.errors()])
        metadata = diagnostic.metadata
        diagnostic.save(dict(completed=True, ok=False, diagnostics=metadata))
        diagnostic.close()
        response = await request_validation_exception_handler(request, exc)
        payload = json.loads(response.body)
        payload['diagnostics'] = metadata
        request.state.diagnostics = metadata
        return JSONResponse(status_code=422, content=payload, headers={'X-ShiJu-Diagnostic-Id': metadata['diagnostic_id']})
    @app.exception_handler(StarletteHTTPException)
    async def diagnostic_http_error(request, exc):
        from apps.launcher.diagnostics import OperationDiagnostic
        diagnostic = OperationDiagnostic(active.database_path.parent, 'http-error')
        diagnostic.event('request', request_id=getattr(request.state, 'request_id', ''), method=request.method, path=request.url.path)
        message = diagnostic.fail(exc, f'HTTP_{exc.status_code}')
        metadata = diagnostic.metadata
        diagnostic.save(dict(completed=True, ok=False, diagnostics=metadata))
        diagnostic.close()
        request.state.diagnostics = metadata
        return JSONResponse(status_code=exc.status_code,
            content={'detail': exc.detail, 'diagnostics': metadata},
            headers={**(exc.headers or {}), 'X-ShiJu-Diagnostic-Id': metadata['diagnostic_id']})
    logger = logging.getLogger('shiju.http')

    @app.middleware('http')
    async def observe_requests(request: Request, call_next):
        app.state.local_runtime.control_url = f'http://127.0.0.1:{request.url.port or 18763}'
        request_id = request.headers.get('x-request-id') or str(uuid.uuid4())
        request.state.request_id = request_id
        source = request.headers.get('x-request-source')
        if source not in {'page_init', 'user_action', 'poll', 'background'}:
            source = 'poll' if request.url.path.endswith(('/state', '/events')) else 'user_action'
        started = time.perf_counter()
        token = begin()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception('request_failed request_id=%s method=%s path=%s source=%s', request_id, request.method, request.url.path, source)
            from apps.launcher.diagnostics import OperationDiagnostic
            diagnostic = OperationDiagnostic(active.database_path.parent, 'http-error')
            diagnostic.event('request', request_id=request_id, method=request.method, path=request.url.path)
            message = diagnostic.fail(exc, 'HTTP_INTERNAL_ERROR')
            metadata = diagnostic.metadata
            diagnostic.save(dict(completed=True, ok=False, diagnostics=metadata))
            diagnostic.close()
            end(token)
            return JSONResponse(status_code=500, content={'detail': message, 'diagnostics': metadata},
                headers={'X-Request-ID': request_id, 'X-ShiJu-Diagnostic-Id': metadata['diagnostic_id']},
                media_type='application/json; charset=utf-8')
        duration_ms = (time.perf_counter() - started) * 1000
        stats = end(token)
        response.headers['X-Request-ID'] = request_id
        if response.headers.get('content-type', '').lower() == 'application/json':
            response.headers['content-type'] = 'application/json; charset=utf-8'
        diagnostics = getattr(request.state, 'diagnostics', None) or getattr(app.state.local_runtime, 'diagnostics', None)
        if diagnostics:
            response.headers['X-ShiJu-Diagnostic-Id'] = diagnostics.get('diagnostic_id', '')
        if request.url.path.startswith(('/v1/poetry/jobs/', '/v1/conversations/')):
            response.headers['Cache-Control'] = 'private, no-store'
        elif request.url.path.startswith(('/Rhyme/', '/Songci_Meter/')):
            response.headers['Cache-Control'] = 'no-store'
        logger.info('request request_id=%s method=%s path=%s source=%s status=%s content_type=%s duration_ms=%.2f db_duration_ms=%.2f db_query_count=%s pool_wait_ms=%.2f', request_id, request.method, request.url.path, source, response.status_code, response.headers.get('content-type', ''), duration_ms, stats.db_duration_ms if stats else 0, stats.db_query_count if stats else 0, stats.pool_wait_ms if stats else 0)
        return response
    app.state.agent_service = agent_service

    def reload_agent_service():
        if agent_service is not None:
            return
        config_path = active.database_path.parent / 'models.toml'
        if not config_path.exists():
            app.state.agent_service = None
            return
        from apps.agent.gateway import ModelGateway
        from apps.agent.web_service import RepositoryPoetryJobs
        gateway = ModelGateway.from_toml(config_path)
        preferences = read_config(active.database_path.parent)
        app.state.agent_service = WebAgentService(gateway, RepositoryPoetryJobs(app.state.jobs, app.state.gpu_provider), project_root, max_tool_rounds=preferences['max_tool_rounds'], instructions=preferences['instructions'])
    app.state.reload_agent_service = reload_agent_service
    reload_agent_service()
    import threading
    from apps.agent.executor import AgentExecutor
    local_stop = threading.Event()
    executor = AgentExecutor(app.state.jobs, users, app.state.agent_service, turn_timeout_seconds=active.agent_turn_timeout_seconds)

    def run_local_agent():
        while not local_stop.is_set():
            if app.state.agent_service is not None:
                executor.service = app.state.agent_service
                try:
                    if executor.run_once():
                        continue
                except Exception:
                    logger.exception('Local Agent queue failed')
            local_stop.wait(0.25)
    local_thread = threading.Thread(target=run_local_agent, name='shiju-local-agent', daemon=True)
    app.router.on_startup.append(local_thread.start)

    def stop_local_agent():
        local_stop.set()
        executor.stop()
        local_thread.join(timeout=2)
    app.router.on_shutdown.append(stop_local_agent)

    @app.get('/health/live', tags=['health'])
    def live():
        import sys
        return {'status': 'ok', 'application': 'ShiJu', 'pid': os.getpid(), 'python': sys.executable, 'instance_id': os.getenv('SHIJU_INSTANCE_ID', ''), 'workspace': os.getenv('SHIJU_LAUNCHER_WORKSPACE_ROOT', str(project_root)), 'data_root': str(active.database_path.parent), 'build': app.state.build_info}

    @app.get('/health/ready', tags=['health'])
    def ready():
        database_status = 'ok'
        try:
            database.check()
        except Exception:
            database_status = 'error'
        from .environment import selected_environment
        worker = app.state.local_runtime.status()
        enabled = selected_environment(active.database_path.parent)['local_enabled']
        generation_ready = (not enabled or worker['state'] in {'ready', 'released'}) and app.state.web_assets['state'] == 'ready'
        payload = {'status': 'ok' if database_status == 'ok' and generation_ready else 'degraded', 'database': database_status,
                   'service': 'ready' if database_status == 'ok' and app.state.web_assets['state'] == 'ready' else 'failed', 'generator': worker, 'web_assets': app.state.web_assets,
                   'local_enabled': enabled, 'gpu_provider': app.state.gpu_provider.status(), 'worker_online': app.state.jobs.has_live_worker()}
        if database_status != 'ok' or not generation_ready:
            return JSONResponse(status_code=503, content=payload)
        return payload
    @app.get('/health/web', tags=['health'])
    def web_health():
        return app.state.web_assets
    app.include_router(public_router)
    app.include_router(worker_router)
    app.include_router(agent_router)
    app.include_router(local_profile_router)
    app.include_router(model_config_router)
    app.include_router(local_runtime_router)
    app.include_router(backup_router)
    app.include_router(conversations_router)
    app.include_router(folders_router)
    app.include_router(profile_router)
    app.add_api_route('/media/avatars/{filename}', legacy_avatar_file, methods=['GET'], include_in_schema=False)

    @app.get('/', include_in_schema=False)
    def website():
        return RedirectResponse('/web/prosody-checker/#chat')
    app.mount('/Rhyme', ApplicationStaticFiles(directory=project_root / 'Rhyme'), name='rhyme-data')
    app.mount('/Songci_Meter', ApplicationStaticFiles(directory=project_root / 'Songci_Meter'), name='meter-data')
    app.mount('/web', ApplicationStaticFiles(directory=project_root / 'web', html=True), name='web')
    avatar_dir = active.database_path.parent / 'avatars'
    avatar_dir.mkdir(parents=True, exist_ok=True)
    app.add_middleware(RequestBodyLimitMiddleware, settings=active)
    from apps.local_network import LocalRequestBoundaryMiddleware
    app.add_middleware(LocalRequestBoundaryMiddleware)
    return app

def main() -> None:
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=18763, workers=1)
if __name__ == '__main__':
    main()
