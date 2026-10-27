"""Download pinned wheels with mirrors, TLS verification, hashes and resumption."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess

from download_transport import DownloadTransport


class Download(DownloadTransport):
    def __init__(self, root, source):
        self.report = root / 'download-state.json'
        self.stage = '下载固定依赖'
        self.context = {}
        self.source_preference = source

    def check(self):
        pass

    def update(self, stage, **details):
        self.stage = stage
        record = dict(stage=stage, **(self.context | details))
        self.report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        if 'received_bytes' not in details:
            print(json.dumps(record, ensure_ascii=False), flush=True)

    def run(self, command, *, env, timeout):
        subprocess.run(list(map(str, command)), env=env, stdin=subprocess.DEVNULL,
                       check=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', choices=['auto', 'mirror', 'official'], default='auto')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    items = json.loads((root / 'plan.json').read_text(encoding='utf-8'))['dependencies']
    download = Download(root, args.source)
    for index, item in enumerate(items, 1):
        download.context = dict(file_index=index, file_count=len(items))
        download.download_item(item, root / 'wheels')
    download.context = {}
    download.update('依赖包下载和摘要核验完成')


if __name__ == '__main__':
    main()
