"""Network boundaries for the local edition; no server configuration lives here."""
from urllib.parse import urlsplit


def local_control_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', '::1', 'localhost'}
                 and parsed.port is not None and 1 <= parsed.port <= 65535
                 and not parsed.username and not parsed.password
                 and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('单机 Worker 控制地址必须为带端口的本机回环 HTTP 地址')
    return value.rstrip('/')


class LocalRequestBoundaryMiddleware:
    """Reject DNS rebinding and browser requests from another origin before routing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from starlette.datastructures import Headers, URL
        from starlette.responses import JSONResponse

        headers = Headers(scope=scope)
        url = URL(scope=scope)
        # Starlette's in-process TestClient has no network listener.
        testing = scope.get('client', ('',))[0] == 'testclient' and url.hostname == 'testserver'
        allowed = url.hostname in {'localhost', '127.0.0.1', '::1'} or testing
        origin = headers.get('origin')
        if origin:
            try:
                source = urlsplit(origin)
                source_port = source.port or (443 if source.scheme == 'https' else 80)
                target_port = url.port or (443 if url.scheme == 'https' else 80)
                allowed = allowed and (source.scheme == url.scheme and source.hostname == url.hostname
                                       and source_port == target_port and not source.username
                                       and not source.password and not source.path
                                       and not source.query and not source.fragment)
            except ValueError:
                allowed = False
        if headers.get('sec-fetch-site') in {'cross-site', 'same-site'}:
            allowed = False
        if not allowed:
            return await JSONResponse({'detail': '仅允许本机同源访问'}, status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)
