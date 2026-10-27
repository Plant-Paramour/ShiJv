"""Shared verified download transport for GUI deployment and Conda exports."""
from __future__ import annotations
import base64
import hashlib
import os
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
try:
    from .deployment_profiles import download_sources
except ImportError:
    from deployment_profiles import download_sources


class Cancelled(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class DownloadTransport:
    def download_item(self, item, directory):
        failures = []
        last_failure = None
        for url in download_sources(item, self.source_preference):
            self.check()
            self.context['source'] = urlsplit(url).hostname
            self.update(self.stage, file=item['filename'], source=urlsplit(url).hostname)
            try:
                self.download(url, Path(directory) / item['filename'], item['sha256'], item['bytes'])
                return
            except Cancelled:
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                last_failure = exc
                failures.append(f'{urlsplit(url).hostname}: {exc}')
        raise RuntimeError(f"{item['filename']} 的可用下载源均失败。已保留缓存，可切换来源或导入本地 wheel 后继续。" + '；'.join(failures)) from last_failure


    def download(self, url, destination, digest, size=None):
        for attempt in range(3):
            try:
                return self._download_once(url, destination, digest, size)
            except (urllib.error.URLError, OSError) as exc:
                reason = getattr(exc, 'reason', exc)
                if isinstance(reason, ssl.SSLCertVerificationError):
                    if os.name == 'nt':
                        try:
                            return self._download_windows(url, destination, digest, size)
                        except Cancelled:
                            raise
                        except Exception as native_error:
                            raise RuntimeError(f'{Path(destination).name} 下载失败（{urlsplit(url).hostname}）：Python 与 Windows HTTPS 均无法完成下载。请检查系统时间、Windows 更新及网络代理的证书配置。Windows 详情：{native_error}') from native_error
                    raise RuntimeError(f'{Path(destination).name} 下载证书验证失败（{urlsplit(url).hostname}）：请检查系统可信证书和代理配置') from exc
                if attempt == 2:
                    raise RuntimeError(f'{Path(destination).name} 下载失败（{urlsplit(url).hostname}），已重试 3 次：{exc}') from exc
                self.update(self.stage, file=Path(destination).name, retry=attempt + 1, message='网络连接中断，保留断点并自动重试')
                for _ in range(10 * (attempt + 1)):
                    self.check()
                    time.sleep(.1)


    def _download_windows(self, url, destination, digest, size):
        """Use Windows chain building; never bypass TLS or content validation."""
        destination = Path(destination)
        part = destination.with_name(destination.name + '.windows.part')
        self.update(self.stage, file=destination.name, message='正在使用 Windows 系统证书重新下载')
        script = """
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$client = New-Object Net.WebClient
try { $client.DownloadFile($env:SHIJU_DOWNLOAD_URL, $env:SHIJU_DOWNLOAD_PATH) }
finally { $client.Dispose() }
"""
        env = os.environ.copy()
        env.update(SHIJU_DOWNLOAD_URL=url, SHIJU_DOWNLOAD_PATH=str(part.resolve()))
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
        powershell = windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            self.run([powershell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], env=env, timeout=7200)
            self.check()
            if (size is not None and part.stat().st_size != size) or sha256_file(part) != digest:
                raise ValueError(f'{destination.name} Windows 下载的大小或 SHA256 校验失败')
            part.replace(destination)
        finally:
            if part.exists():
                part.unlink()


    def _download_once(self, url, destination, digest, size=None):
        """Hash every reused file. Resume only on a correct Content-Range."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.check()
        if destination.is_file() and (size is None or destination.stat().st_size == size) and sha256_file(destination) == digest:
            return
        part = destination.with_name(destination.name + '.part')
        offset = part.stat().st_size if part.exists() else 0
        if size is not None and offset >= size:
            if offset == size and sha256_file(part) == digest:
                part.replace(destination)
                return
            part.unlink()
            offset = 0
        request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-'} if offset else {})
        with urllib.request.urlopen(request, timeout=30) as source:
            if offset and (source.status != 206 or not source.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                if source.status != 200:
                    raise ValueError('服务器返回无效断点范围，请重试')
                offset = 0
            received = offset
            started = time.monotonic()
            last = 0
            with part.open('ab' if offset else 'wb') as target:
                while True:
                    self.check()
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    target.write(block)
                    received += len(block)
                    if size is not None and received > size:
                        raise ValueError('下载内容超过清单大小')
                    if time.monotonic() - last > .5:
                        self.update(self.stage, file=destination.name, received_bytes=received, total_bytes=size, speed_bytes_per_second=int((received-offset)/max(time.monotonic()-started, .001)))
                        last = time.monotonic()
        if size is not None and part.stat().st_size < size:
            raise OSError(f'{destination.name} 下载提前结束，保留断点继续')
        if (size is not None and part.stat().st_size != size) or sha256_file(part) != digest:
            part.unlink()  # corrupt partial data must not be appended on retry
            raise ValueError(f'{destination.name} 下载校验失败，请重试重新获取')
        part.replace(destination)

