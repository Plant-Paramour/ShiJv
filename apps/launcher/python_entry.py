"""Run application modules from the launcher's source tree with the chosen Python.

Embedded Python ignores the working directory and PYTHONPATH via its _pth file.
Use an explicit entry script so probes, memory and workers import the same code.
"""
from pathlib import Path
import runpy
import sys


def main():
    if len(sys.argv) < 3 or sys.argv[1] != '-m':
        raise SystemExit('Expected -m module [arguments]')
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import os
    if os.environ.get('SHIJU_PROCESS_ENV_READY') != str(Path(sys.executable).resolve()):
        from apps.launcher.process_environment import python_environment
        env = python_environment(sys.executable)
        env['SHIJU_PROCESS_ENV_READY'] = str(Path(sys.executable).resolve())
        os.environ.clear()
        os.environ.update(env)
    module = sys.argv[2]
    sys.argv = [module, *sys.argv[3:]]
    from apps.launcher.diagnostics import OperationDiagnostic, prune
    import logging
    import tempfile
    directory = os.environ.get('SHIJU_DATA_DIR') or str(Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'ShiJu')
    diagnostic = OperationDiagnostic(directory, module.rsplit('.', 1)[-1], os.environ.get('SHIJU_DIAGNOSTIC_ID'))
    os.environ['SHIJU_DIAGNOSTIC_ID'] = diagnostic.id
    os.environ['SHIJU_DIAGNOSTIC_LOG'] = diagnostic.metadata['log_path'] or ''
    if diagnostic.handler:
        logging.getLogger().addHandler(diagnostic.handler)
        logging.getLogger().setLevel(logging.INFO)
    ok = False
    try:
        from apps.launcher.build_info import build_info
        diagnostic.event('identity', build=build_info(), model=os.getenv('SHIJU_MODEL_PATH'), device=os.getenv('CUDA_VISIBLE_DEVICES'))
        runpy.run_module(module, run_name='__main__', alter_sys=True)
        ok = True
    except SystemExit as exc:
        ok = exc.code in (None, 0)
        if not ok:
            print(diagnostic.fail(exc, 'PROCESS_EXIT_FAILED'), file=sys.stderr)
        raise
    except BaseException as exc:
        print(diagnostic.fail(exc), file=sys.stderr)
        raise
    finally:
        diagnostic.event('completed', ok=ok)
        diagnostic.save(dict(completed=True, ok=ok, diagnostics=diagnostic.metadata))
        if diagnostic.handler:
            logging.getLogger().removeHandler(diagnostic.handler)
        diagnostic.close()
        try:
            prune(directory)
        except OSError:
            pass


if __name__ == '__main__':
    main()
