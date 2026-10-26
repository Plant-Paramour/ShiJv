import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.settings import ApiSettings


def test_local_start_profile_model_and_persisted_stream(tmp_path, monkeypatch):
    """Exercise the actual Agent queue and plain SQLite message storage."""
    monkeypatch.setenv("SHIJU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHIJU_ENV", "development")
    for name in ("DATABASE_URL", "SHIJU_DATABASE_PATH"):
        monkeypatch.delenv(name, raising=False)

    class ModelHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            if body.get("stream"):
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for delta, finish in (({"content": "本机运行验收通过。"}, None), ({}, "stop")):
                    chunk = {"choices": [{"delta": delta, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"choices": [{"message": {"role": "assistant", "content": "验收对话"}}]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = ApiSettings.from_env()
        app = create_app(settings)
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 200
            assert client.get("/v1/local-profile").json()["created"]
            assert client.get("/v1/local-profile").json()["profile"]["display_name"] == "请输入用户名"
            assert client.patch("/v1/local-profile", json={"display_name": "验收用户"}).status_code == 200
            endpoint = {"provider_id": "test", "provider": "openai-compatible",
                        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                        "model": "test-model", "api_key": "disposable-test-key"}
            assert client.put("/v1/model-config", json={"endpoints": [endpoint]}).status_code == 200
            response = client.post("/v1/agent/chat/stream", json={"message": "运行验收", "model": "test-model"})
            assert response.status_code == 200
            assert "event: turn.completed" in response.text, response.text
            conversations = client.get("/v1/conversations").json()["items"]
            assert conversations
            with app.state.database.connect() as connection:
                row = connection.execute("SELECT content_stored FROM messages WHERE role='assistant' AND status='completed' LIMIT 1").fetchone()
                assert row and row[0] == "本机运行验收通过。"
            for path in ("/v1/auth/login", "/v1/forum/sections", "/v1/admin/stats"):
                assert client.get(path).status_code == 404
        with TestClient(create_app(settings)) as client:
            assert client.get("/v1/local-profile").json()["profile"]["display_name"] == "验收用户"
            assert client.get("/v1/conversations").json()["items"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
