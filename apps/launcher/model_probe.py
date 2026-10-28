"""Run acceptance in the chosen interpreter; never install dependencies."""
import argparse
import json
import os
import sys
import time
import subprocess
from pathlib import Path


def check_application_environment(python):
    from apps.api.environment import python_process_env
    from apps.launcher.environment_probe import probe
    path = Path(python)
    if not path.is_absolute() or not path.is_file():
        raise ValueError('所选软件环境的 Python 不存在：' + str(path))
    if path.resolve() == Path(sys.executable).resolve():
        return probe('core')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='shiju-core-') as temporary:
        output = Path(temporary) / 'report.json'
        process = subprocess.run([str(path), '-I', str(Path(__file__).with_name('environment_probe.py')),
                                  '--mode', 'core', '--output', str(output)],
                                 env=python_process_env(path), capture_output=True, timeout=600,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if not output.is_file():
            raise ValueError(f'软件环境检查未返回报告（退出码 {process.returncode}）：' +
                             process.stderr.decode('utf-8', errors='replace')[-4000:])
        return json.loads(output.read_text(encoding='utf-8'))


def check_runner(runner, engine, report):
    import torch
    from shiju.contracts import GeneratePoemRequest, SamplingOptions
    from shiju.end_tokens import eos_token_ids, pad_token_id
    from shiju.generation.protocol import parse_generation_protocol
    report('load', '模型权重加载', 'running', '正在读取权重')
    runner.load()
    report('load', '模型权重加载', 'passed', '权重、分词器与设备初始化成功')
    report('output', '模型调用与有效输出', 'running', '执行真实短推理')
    prompt = runner.render_chat([{'role': 'user', 'content': '你好，请用一句中文问候。'}], enable_thinking=False)
    inputs = runner.tokenizer(prompt, return_tensors='pt').to(runner.model.device)
    with torch.inference_mode():
        output = runner.model.generate(**inputs, max_new_tokens=32, do_sample=False,
            eos_token_id=eos_token_ids(runner.tokenizer, runner.model),
            pad_token_id=pad_token_id(runner.tokenizer, runner.model))
    text = runner.tokenizer.decode(output[0, inputs['input_ids'].shape[1]:], skip_special_tokens=True).strip()
    if not text or not any(c.isalnum() for c in text) or '\ufffd' in text:
        raise ValueError('短推理没有返回有效文本')
    report('output', '模型调用与有效输出', 'passed', text)
    report('constraints', '约束生成框架接入', 'running', '真实生成五言绝句，并校验协议、句数、字数与格律约束')
    deltas = []
    def on_event(event, payload):
        if event == 'candidate.started':
            deltas.clear()
        elif event == 'candidate.delta':
            deltas.append(payload.get('delta', ''))
    try:
        result = engine.generate_poem(GeneratePoemRequest(meter_type='唐诗', form_name='五言绝句',
            theme='春日山水', task_options={'skip_planning': True}, sampling=SamplingOptions(max_new_tokens=512)),
            on_candidate_event=on_event)
    except Exception as exc:
        raise RuntimeError(f"{exc}；最后输出：{''.join(deltas)[-800:]}") from exc
    candidate = result['candidates'][0]
    content = candidate.get('content') or candidate.get('poem') or candidate.get('text')
    # Engine itself parses the protocol and checks the constrained body.
    if not content:
        content = parse_generation_protocol(candidate['raw_output']).content
    lines = [line for line in content.replace('，', '\n').replace('。', '\n').splitlines() if line.strip()]
    if len(lines) != 4 or any(len(line.strip()) != 5 for line in lines):
        raise ValueError('约束生成未返回四句五言正文')
    runtime, _ = engine._runtime_for(GeneratePoemRequest(meter_type='唐诗', form_name='五言绝句', theme='春日山水'))
    runtime.profile.create_session().prime_fixed_lines({i: line.strip() for i, line in enumerate(lines)}, strict_polyphonic=False)
    report('constraints', '约束生成框架接入', 'passed', content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='')
    parser.add_argument('--output', required=True)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--scan', action='store_true')
    parser.add_argument('--root', default='')
    parser.add_argument('--precision', choices=['auto', 'none', '4bit'], default='auto')
    parser.add_argument('--device-uuid', default='')
    parser.add_argument('--application-python', default='')
    args = parser.parse_args()
    from apps.launcher.diagnostics import OperationDiagnostic
    diagnostic = OperationDiagnostic(args.data_dir, 'model-acceptance', os.getenv('SHIJU_DIAGNOSTIC_ID'))
    if args.device_uuid:
        os.environ['CUDA_VISIBLE_DEVICES'] = args.device_uuid
        os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
    from apps.launcher.environment_probe import probe
    from apps.launcher.model_catalog import validate_model, scan, report_path, fingerprint
    from apps.api.environment import assess_model, check_managed_runtime
    result = {'ok': False, 'python': sys.executable, 'model': args.model, 'checks': [], 'started_at': time.time(),
              'device_uuid': args.device_uuid or os.getenv('CUDA_VISIBLE_DEVICES', ''), 'diagnostics': diagnostic.metadata}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    runner = None
    initial_fingerprint = None
    def write():
        temporary = output.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(output)
    def report(key, name, state, detail):
        diagnostic.event('check', check=key, state=state)
        previous = next((c for c in result['checks'] if c['id'] == key), {})
        started = previous.get('started_at', time.time())
        result['checks'] = [c for c in result['checks'] if c['id'] != key] + [dict(id=key, name=name, state=state, detail=detail,
            started_at=started, elapsed_seconds=time.time() - started)]
        write()
    try:
        if args.application_python and not args.scan:
            report('application', '软件环境与部署文件', 'running', '检查所选软件环境和推理环境文件')
            check_managed_runtime(sys.executable)
            core = check_application_environment(args.application_python)
            result['application_environment'] = core
            if not core.get('ok'):
                raise ValueError('软件环境检查失败：' + '; '.join(core.get('errors', [])))
            from apps.api.launcher_memory import environment_stamp
            result['application_stamp'] = environment_stamp(args.application_python)
            result['inference_stamp'] = environment_stamp(sys.executable)
            report('application', '软件环境与部署文件', 'passed', '软件环境和推理环境文件可用')
        report('environment', '依赖、CUDA 与模型架构', 'running', '检查当前所选环境')
        environment = probe('inference')
        result['environment'] = environment
        if args.scan:
            roots = [args.root] if args.root else [str(Path.cwd()), str(Path.home()/'.cache/huggingface/hub'), str(Path.home()/'.cache/modelscope/hub')]
            result['models'] = []
            for path in scan(roots):
                try:
                    plan = assess_model(validate_model(path), environment)
                    result['models'].append(dict(path=path, supported=True, reason=plan['label']))
                except (ValueError, OSError) as exc:
                    result['models'].append(dict(path=path, supported=False, reason=str(exc)))
            result['ok'] = True
            write()
            diagnostic.save(dict(completed=True, ok=True, diagnostics=diagnostic.metadata))
            diagnostic.close()
            return 0
        model = validate_model(args.model)
        initial_fingerprint = fingerprint(model)
        result['plan'] = assess_model(model, environment)
        if args.precision != 'auto':
            if args.precision not in environment.get('quantization', []):
                raise ValueError(f'所选精度 {args.precision} 未通过环境运算检查')
            # FP16 must not silently fall back to 4bit in a baseline experiment.
            if args.precision == 'none' and result['plan']['quantization'] != 'none':
                raise ValueError('当前可用显存不足以执行 FP16 验收；未尝试加载，也未宣称 FP16 通过')
            result['plan']['quantization'] = args.precision
            result['plan']['label'] = ('指定 CPU FP32' if not environment.get('cuda') else '指定 FP16') if args.precision == 'none' else '指定 BNB 4bit'
        report('environment', '依赖、CUDA 与模型架构', 'passed', result['plan']['label'])
        from shiju.generation.model_runner import ModelRunner
        from shiju.generation.result import ModelSettings
        from shiju.generation.engine import GenerationEngine
        import torch
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        runner = ModelRunner(ModelSettings(str(model), quantization=result['plan']['quantization'], local_files_only=True))
        check_runner(runner, GenerationEngine(runner), report)
        if fingerprint(model) != initial_fingerprint:
            raise ValueError('模型、依赖或生成代码在测试期间发生变化，请重新完整测试')
        result['ok'] = True
    except Exception as exc:
        import traceback
        result['error'] = diagnostic.fail(exc, 'MODEL_ACCEPTANCE_FAILED')
        result['diagnostics'] = diagnostic.metadata
        diagnostic.save(dict(completed=True, ok=False, traceback=traceback.format_exc(), diagnostics=diagnostic.metadata))
        result['code'] = 'MODEL_OUT_OF_MEMORY' if 'out of memory' in str(exc).lower() else 'MODEL_ACCEPTANCE_FAILED'
        running = next((c for c in result['checks'] if c['state'] == 'running'), None)
        if running:
            report(running['id'], running['name'], 'failed', result['error'])
    finally:
        if runner is not None:
            try:
                import psutil
                import torch
                memory = psutil.Process().memory_info()
                result['measurements'] = dict(cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None,
                    cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved() if torch.cuda.is_available() else None, rss_bytes=memory.rss,
                    peak_working_set_bytes=getattr(memory, 'peak_wset', None),
                    elapsed_seconds=time.time() - result['started_at'])
            except Exception as exc:
                result['measurements_error'] = f'{type(exc).__name__}: {exc}'
            finally:
                try:
                    runner.unload()
                except Exception as exc:
                    result['ok'] = False
                    result['code'] = 'MODEL_ACCEPTANCE_FAILED'
                    result['error'] = f'模型资源释放失败：{type(exc).__name__}: {exc}'
    if result['ok'] and initial_fingerprint and fingerprint(args.model) != initial_fingerprint:
        result.update(ok=False, code='PAIR_INPUTS_CHANGED', error='模型、依赖或生成代码在测试期间发生变化，请重新完整测试')
    result['finished_at'] = time.time()
    write()
    if args.model:
        target = report_path(args.data_dir, args.model)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)
        if args.application_python and result['ok']:
            try:
                check_managed_runtime(sys.executable)
                from apps.api.launcher_memory import LauncherMemory
                LauncherMemory(args.data_dir).adopt_verified_pair(args.application_python, sys.executable, args.model)
                result['pair_saved'] = True
            except (OSError, ValueError) as exc:
                result.update(ok=False, error=diagnostic.fail(exc, 'PAIR_SAVE_FAILED'), code='PAIR_SAVE_FAILED', diagnostics=diagnostic.metadata)
                target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            write()
    diagnostic.event('completed', ok=result['ok'])
    diagnostic.save(dict(completed=True, ok=result['ok'], error=result.get('error'),
                         environment=result.get('environment'), plan=result.get('plan'), diagnostics=diagnostic.metadata))
    diagnostic.close()
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
