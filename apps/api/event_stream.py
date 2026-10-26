"""Local task and conversation updates."""
from fastapi.responses import StreamingResponse


def event_stream_response(request, content, *, user_id):
    return StreamingResponse(content, media_type='text/event-stream; charset=utf-8',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
