"""Serve application assets independently of Windows file associations."""
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import FileResponse
from starlette.staticfiles import NotModifiedResponse, StaticFiles


class ApplicationStaticFiles(StaticFiles):
    MEDIA_TYPES = {
        '.js': 'text/javascript', '.mjs': 'text/javascript',
        '.css': 'text/css', '.html': 'text/html',
        '.json': 'application/json', '.svg': 'image/svg+xml',
        '.wasm': 'application/wasm',
    }

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = FileResponse(
            full_path, status_code=status_code, stat_result=stat_result,
            media_type=self.MEDIA_TYPES.get(Path(full_path).suffix.lower()),
            headers={'Cache-Control': 'no-cache'},
        )
        # A cached response from the old server may have an incorrect MIME type.
        # Give the corrected representation a distinct validator as well.
        response.headers['etag'] = response.headers['etag'].removesuffix('"') + '-mime1"'
        if self.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response
