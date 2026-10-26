"""Bound request bodies before parsers and worker signature verification run."""
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, *, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        limit = self.settings.max_request_bytes
        if path == "/v1/backup/restore":
            limit = 2 * 1024 * 1024 * 1024
        elif path == "/v1/profile/me/avatar":
            limit = 2 * 1024 * 1024
        elif path in {"/v1/agent/chat", "/v1/agent/chat/stream"}:
            limit = max(limit, self.settings.agent_max_request_bytes)
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                if not value or not value.isdigit():
                    return await JSONResponse({"detail": "invalid content-length"}, status_code=400)(scope, receive, send)
                if len(value) > 20 or int(value) > limit:
                    return await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)
        if path == "/v1/backup/restore":
            size = 0

            class RequestBodyTooLarge(Exception):
                pass

            async def limited_receive():
                nonlocal size
                message = await receive()
                if message["type"] == "http.request":
                    size += len(message.get("body", b""))
                    if size > limit:
                        raise RequestBodyTooLarge
                return message

            try:
                await self.app(scope, limited_receive, send)
            except RequestBodyTooLarge:
                return await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)
            return
        buffer = bytearray()
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                return await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)
            buffer.extend(chunk)
            if not message.get("more_body", False):
                break
        body = bytes(buffer)
        del buffer
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
