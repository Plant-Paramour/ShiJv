import hashlib
import ssl
import urllib.error
from pathlib import Path

import pytest

from apps.launcher import deployer


def test_certificate_failure_uses_windows_transport(tmp_path, monkeypatch):
    operation = deployer.Operation(tmp_path / 'report.json')
    error = urllib.error.URLError(ssl.SSLCertVerificationError('untrusted issuer'))
    monkeypatch.setattr(operation, '_download_once', lambda *args: (_ for _ in ()).throw(error))
    calls = []
    monkeypatch.setattr(operation, '_download_windows', lambda *args: calls.append(args))
    monkeypatch.setattr(deployer.os, 'name', 'nt')
    operation.download('https://example.com/file', tmp_path / 'file', 'digest', 3)
    assert len(calls) == 1


@pytest.mark.parametrize('payload,accepted', [(b'good', True), (b'bad!', False)])
def test_windows_transport_checks_content_before_accepting(tmp_path, monkeypatch, payload, accepted):
    operation = deployer.Operation(tmp_path / 'report.json')
    destination = tmp_path / 'package.whl'
    def fake_run(command, *, env, timeout):
        Path(env['SHIJU_DOWNLOAD_PATH']).write_bytes(payload)
        assert 'EncodedCommand' in ' '.join(map(str, command))
    monkeypatch.setattr(operation, 'run', fake_run)
    if accepted:
        operation._download_windows('https://example.com/file', destination, hashlib.sha256(b'good').hexdigest(), 4)
        assert destination.read_bytes() == b'good'
    else:
        with pytest.raises(ValueError, match='SHA256'):
            operation._download_windows('https://example.com/file', destination, hashlib.sha256(b'good').hexdigest(), 4)
        assert not destination.exists()
    assert not destination.with_name(destination.name + '.windows.part').exists()


def test_transient_failure_retries(tmp_path, monkeypatch):
    operation = deployer.Operation(tmp_path / 'report.json')
    calls = []
    def download(*args):
        calls.append(args)
        if len(calls) < 3:
            raise urllib.error.URLError(ConnectionResetError('reset'))
    monkeypatch.setattr(operation, '_download_once', download)
    monkeypatch.setattr(deployer.time, 'sleep', lambda seconds: None)
    operation.download('https://example.com/file', tmp_path / 'file', 'digest')
    assert len(calls) == 3


def test_bad_mirror_content_falls_back_to_official(tmp_path, monkeypatch):
    from apps.launcher import download_transport
    operation = deployer.Operation(tmp_path / 'report.json')
    item = dict(filename='test.whl', bytes=4, sha256=hashlib.sha256(b'good').hexdigest(), source_url='https://testserver/official/test')
    monkeypatch.setattr(download_transport, 'download_sources', lambda *args: ['https://testserver/mirror/test', 'https://testserver/official/test'])
    calls = []
    def download(url, destination, digest, size):
        calls.append(url)
        if 'mirror' in url:
            raise ValueError('wrong hash')
        Path(destination).write_bytes(b'good')
    monkeypatch.setattr(operation, 'download', download)
    operation.download_item(item, tmp_path)
    assert calls == ['https://testserver/mirror/test', 'https://testserver/official/test']
    assert (tmp_path / 'test.whl').read_bytes() == b'good'


def test_cancel_does_not_switch_sources(tmp_path, monkeypatch):
    from apps.launcher import download_transport
    operation = deployer.Operation(tmp_path / 'report.json')
    monkeypatch.setattr(download_transport, 'download_sources', lambda *args: ['https://testserver/mirror/test', 'https://testserver/official/test'])
    calls = []
    def download(*args):
        calls.append(args)
        raise deployer.Cancelled('cancel')
    monkeypatch.setattr(operation, 'download', download)
    with pytest.raises(deployer.Cancelled):
        operation.download_item(dict(filename='test.whl', bytes=4, sha256='hash'), tmp_path)
    assert len(calls) == 1


def test_short_download_keeps_partial_and_resumes(tmp_path, monkeypatch):
    import io
    from apps.launcher import download_transport
    operation = deployer.Operation(tmp_path / 'report.json')
    ranges = []
    class Response(io.BytesIO):
        def __init__(self, payload, status, headers):
            super().__init__(payload)
            self.status, self.headers = status, headers
    def open_url(request, **kwargs):
        ranges.append(request.get_header('Range'))
        if len(ranges) == 1:
            return Response(b'go', 200, {})
        return Response(b'od', 206, {'Content-Range': 'bytes 2-3/4'})
    monkeypatch.setattr(download_transport.urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(download_transport.time, 'sleep', lambda seconds: None)
    destination = tmp_path / 'test.whl'
    operation.download('https://testserver/file', destination, hashlib.sha256(b'good').hexdigest(), 4)
    assert ranges == [None, 'bytes=2-']
    assert destination.read_bytes() == b'good'
