"""Launcher service entry point, including a persistent startup log."""
import argparse
import os
from pathlib import Path
import sys
import traceback
import json
import uuid
from datetime import datetime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18763)
    args = parser.parse_args()
    directory = Path(os.environ["SHIJU_DATA_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    instance_id = uuid.uuid4().hex
    os.environ["SHIJU_INSTANCE_ID"] = instance_id
    record = {"pid": os.getpid(), "port": args.port, "instance_id": instance_id}
    with (directory / "api-startup.log").open("a", encoding="utf-8", buffering=1) as output:
        original_stdout, original_stderr = sys.stdout, sys.stderr
        sys.stdout = output
        sys.stderr = output
        print(f"\n启动服务 {datetime.now().isoformat()} · Python: {sys.executable}", flush=True)
        from apps.launcher.build_info import build_info
        print('应用版本：' + json.dumps(build_info(), ensure_ascii=False), flush=True)
        snapshot = directory / 'startup-diagnostics/service.json'
        record.update(application='ShiJu', python=sys.executable, workspace=os.getenv('SHIJU_LAUNCHER_WORKSPACE_ROOT', str(Path(__file__).resolve().parents[2])),
                      data_root=str(directory), build=build_info(), started_at=datetime.now().isoformat())
        from apps.api.startup_diagnostics import write_report
        write_report(snapshot, dict(record, state='starting'))
        try:
            import uvicorn
            from apps.api.main import create_app
            app = create_app()
            app.router.on_startup.append(lambda: write_report(snapshot, dict(record, state='running')))
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                                  timeout_graceful_shutdown=10))
            app.state.launcher_shutdown = lambda: setattr(server, "should_exit", True)
            instance_file = directory / "instance.json"
            temporary = instance_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(record), encoding="utf-8")
            temporary.replace(instance_file)
            server.run()
            write_report(snapshot, dict(record, state='stopped'))
        except BaseException as exc:
            write_report(snapshot, dict(record, state='failed', error=str(exc)))
            traceback.print_exc()
            raise
        finally:
            instance_file = directory / "instance.json"
            try:
                if instance_file.exists() and json.loads(instance_file.read_text(encoding="utf-8")).get("instance_id") == instance_id:
                    instance_file.unlink()
            except (OSError, ValueError):
                pass
            sys.stdout, sys.stderr = original_stdout, original_stderr


if __name__ == "__main__":
    main()
