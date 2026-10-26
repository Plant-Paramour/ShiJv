"""Launcher service entry point, including a persistent startup log."""
import argparse
import os
from pathlib import Path
import sys
import traceback
import json
import uuid


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
        sys.stdout = output
        sys.stderr = output
        try:
            import uvicorn
            from apps.api.main import create_app
            app = create_app()
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port))
            app.state.launcher_shutdown = lambda: setattr(server, "should_exit", True)
            instance_file = directory / "instance.json"
            temporary = instance_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(record), encoding="utf-8")
            temporary.replace(instance_file)
            server.run()
        except BaseException:
            traceback.print_exc()
            raise
        finally:
            instance_file = directory / "instance.json"
            try:
                if instance_file.exists() and json.loads(instance_file.read_text(encoding="utf-8")).get("instance_id") == instance_id:
                    instance_file.unlink()
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    main()
