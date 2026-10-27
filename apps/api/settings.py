"""Local file paths and process settings; no account or server credentials."""
from dataclasses import dataclass
from pathlib import Path
import os
from .data_directory import default_data_directory, default_database_path


@dataclass(frozen=True)
class ApiSettings:
    database_path: Path
    database_url: str | None = None
    max_request_bytes: int = 65536
    agent_max_request_bytes: int = 12 * 1024 * 1024
    worker_lease_seconds: int = 300
    agent_turn_timeout_seconds: int = 900

    @classmethod
    def from_env(cls):
        directory = Path(os.getenv('SHIJU_DATA_DIR', str(default_data_directory())))
        database_url = os.getenv('DATABASE_URL', '').strip()
        if database_url and not database_url.startswith('sqlite:///'):
            raise ValueError('单机版仅支持 SQLite 本地文件')
        explicit_path = os.getenv('SHIJU_DATABASE_PATH', '').strip()
        path = (Path(database_url.removeprefix('sqlite:///')) if database_url else
                Path(explicit_path) if explicit_path else default_database_path(directory))
        path = path.expanduser().resolve()
        os.environ['SHIJU_DATABASE_PATH'] = str(path)
        return cls(database_path=path, database_url='sqlite:///' + path.as_posix(),
                   agent_turn_timeout_seconds=int(os.getenv('SHIJU_AGENT_TURN_TIMEOUT_SECONDS', '900')))
