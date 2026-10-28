import ctypes
import os
from pathlib import Path
import sys

import pytest

from tools.probe_managed_candidate import classify_modules, loaded_module_paths


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows module loader')


def classify(paths, tmp_path):
    return classify_modules(paths, tmp_path / 'runtime', tmp_path / 'runtime',
                            tmp_path / 'Windows', tmp_path / 'cache',
                            tmp_path / 'ProgramData/Microsoft/Windows Defender/Platform',
                            tmp_path / 'Program Files/WindowsApps', tmp_path / 'ProgramData')


def test_loader_reports_current_python():
    paths = loaded_module_paths()
    assert any(Path(p).samefile(sys.executable) for p in paths)
    assert any(Path(p).name.lower().startswith('python3') for p in paths)


def test_unicode_directory_renamed_before_load(tmp_path):
    import shutil

    old = tmp_path / '诗矩旧目录'
    old.mkdir()
    source = Path(sys.executable).parent / 'vcruntime140.dll'
    assert source.is_file()
    shutil.copy2(source, old / 'probe_renamed_runtime.dll')
    new = tmp_path / 'runtime'
    old.rename(new)
    dll = ctypes.WinDLL(str(new / 'probe_renamed_runtime.dll'))
    try:
        paths = loaded_module_paths()
        matched = [p for p in paths if Path(p).name == 'probe_renamed_runtime.dll']
        assert matched == [str(new / 'probe_renamed_runtime.dll')]
        assert classify(matched, tmp_path)['external_loaded_modules'] == []
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.FreeLibrary.argtypes = [ctypes.c_void_p]
        kernel.FreeLibrary(dll._handle)


def test_narrow_injection_exception_and_real_leaks(tmp_path):
    injected = str(tmp_path / 'ProgramData/A-Volute/A-Volute.Nahimic/Modules/Scheduled/x64/AudioDevProps2.dll')
    external = [str(tmp_path / 'other/AudioDevProps2.dll'),
                str(tmp_path / 'other/torch.dll'), str(tmp_path / 'other/_ssl.pyd'),
                str(tmp_path / 'ProgramData/A-Volute/A-Volute.Nahimic/Modules/Scheduled/x64/other.dll')]
    result = classify([injected, *external], tmp_path)
    assert result['injected_modules'] == [injected]
    assert result['external_loaded_modules'] == external


def test_os_and_runtime_modules(tmp_path):
    os_paths = [str(tmp_path / 'ProgramData/Microsoft/Windows Defender/Platform/1/MpOAV.dll'),
                str(tmp_path / 'Program Files/WindowsApps/Microsoft.LanguageExperiencePackzh-CN_1/zh-CN/kernel.dll.mui')]
    paths = [str(tmp_path / 'runtime/DLLs/_ssl.pyd'), str(tmp_path / 'Windows/System32/kernel32.dll'),
             str(tmp_path / 'cache/kernel.pyd'), *os_paths]
    result = classify(paths, tmp_path)
    assert result['additional_os_modules'] == os_paths
    assert not result['external_loaded_modules']
