import json
import sys

import pytest

from apps.launcher import deployer


def test_failed_process_includes_log_text_and_path(tmp_path):
    operation = deployer.Operation(tmp_path / 'report.json')
    with pytest.raises(deployer.DeploymentFailure) as caught:
        operation.run([sys.executable, '-c', "import sys;print('DLL load failure: VC++ runtime missing');sys.exit(1)"])
    details = caught.value.details
    assert 'DLL load failure: VC++ runtime missing' in details['log_content']
    assert details['log_path'].endswith('.log')
    assert details['log_truncated'] is False


def test_long_process_log_keeps_full_file_and_limits_preview(tmp_path):
    log = tmp_path / 'process.log'
    text = 'x' * 100_000 + '\nactual failure at the end'
    log.write_text(text, encoding='utf-8')
    failure = deployer.process_failure('test', log, 'exit 1')
    assert failure.details['log_truncated'] is True
    assert len(failure.details['log_content'].encode('utf-8')) <= 64 * 1024
    assert failure.details['log_content'].endswith('actual failure at the end')
    assert log.read_text(encoding='utf-8') == text


def test_missing_vc_runtime_stops_before_model_or_dependency_download(tmp_path, monkeypatch):
    monkeypatch.setattr(deployer, 'validate_plan', lambda plan: plan)
    def unexpected(*args, **kwargs):
        raise AssertionError('must stop before model validation or downloads')
    monkeypatch.setattr(deployer, 'verify_model', unexpected)
    operation = deployer.Operation(tmp_path / 'report.json')
    monkeypatch.setattr(operation, 'download', unexpected)
    def probe(command, **kwargs):
        deployer.save(tmp_path / 'vc-runtime.json', dict(ok=False, problems=['vcruntime140.dll missing']))
    monkeypatch.setattr(operation, 'run', probe)
    with pytest.raises(deployer.DeploymentFailure) as caught:
        deployer.check_vc_runtime(operation)
    assert caught.value.details['prerequisite'] == 'vc-runtime'
    assert caught.value.details['download_url'] == 'https://aka.ms/vc14/vc_redist.x64.exe'
    assert '自动安装' in str(caught.value)
    assert caught.value.details['instructions']
    assert not (tmp_path / 'environment').exists()


def test_reboot_is_not_treated_as_ready(tmp_path, monkeypatch):
    operation = deployer.Operation(tmp_path / 'report.json')
    monkeypatch.setattr(operation, 'run', lambda *args, **kwargs: deployer.save(
        tmp_path / 'vc-runtime.json', dict(ok=True, restart_required=True)))
    with pytest.raises(deployer.DeploymentFailure, match='重启'):
        deployer.check_vc_runtime(operation)


def test_cli_preserves_structured_log_details(tmp_path, monkeypatch):
    report = tmp_path / 'report.json'
    monkeypatch.setattr(sys, 'argv', ['deployer', 'install-vc-runtime', '--data-dir', str(tmp_path),
                                    '--cache', str(tmp_path / 'cache'), '--report', str(report)])
    def fail(*args):
        raise deployer.DeploymentFailure('failed', log_path='test.log', log_content='real DLL error', log_truncated=False)
    monkeypatch.setattr(deployer, 'install_vc_runtime', fail)
    assert deployer.main() == 1
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['log_content'] == 'real DLL error'


def test_empty_log_still_exposes_model_report_failure(tmp_path):
    log = tmp_path / 'process.log'
    log.touch()
    verification = tmp_path / 'verification.json'
    model = tmp_path / 'model-verification.json'
    deployer.save(verification, dict(ok=False, error='RuntimeError: Actual model acceptance failed'))
    reason = '系统可用内存不足：当前可用 0.88 GiB，估算需要超过 5.73 GiB'
    deployer.save(model, dict(ok=False, error=reason, checks=[dict(state='failed', detail=reason)]))
    error = deployer.verification_failure(deployer.process_failure('验证', log, '退出码 1'), [verification, model])
    assert str(error).startswith('本机验证未通过：' + reason)
    assert error.details['log_content'] == ''
    assert error.details['verification_error'].startswith(reason)
    assert len(error.details['verification_reports']) == 2


def test_missing_verification_report_provides_explanation(tmp_path):
    error = deployer.verification_failure(RuntimeError('process failed'), [tmp_path / 'absent.json'])
    assert '未留下可读错误报告' in error.details['verification_error']


@pytest.mark.parametrize('present', [True, False])
def test_runtime_is_installed_automatically_only_when_needed(tmp_path, monkeypatch, present):
    operation = deployer.Operation(tmp_path / 'report.json')
    monkeypatch.setattr(deployer, 'check_vc_runtime', lambda *args, **kwargs: dict(ok=present))
    installed = []
    def install(cache, operation):
        installed.append(cache)
        return dict(ok=True)
    monkeypatch.setattr(deployer, 'install_vc_runtime', install)
    assert deployer.ensure_vc_runtime(tmp_path / 'cache', operation)['ok']
    assert bool(installed) != present


def test_runtime_install_failure_stops_deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(deployer, 'validate_plan', lambda plan: plan)
    monkeypatch.setattr(deployer, 'check_vc_runtime', lambda *args, **kwargs: dict(ok=False))
    def fail(*args):
        raise deployer.DeploymentFailure('Windows permission declined')
    monkeypatch.setattr(deployer, 'install_vc_runtime', fail)
    monkeypatch.setattr(deployer, 'verify_model', lambda *args: pytest.fail('must not continue after runtime installation failure'))
    with pytest.raises(deployer.DeploymentFailure, match='permission declined'):
        deployer.install({}, tmp_path / 'model', tmp_path / 'target', tmp_path / 'cache',
                         deployer.Operation(tmp_path / 'report.json'), tmp_path / 'data')
