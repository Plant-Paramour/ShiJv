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
    module = sys.argv[2]
    sys.argv = [module, *sys.argv[3:]]
    runpy.run_module(module, run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    main()
