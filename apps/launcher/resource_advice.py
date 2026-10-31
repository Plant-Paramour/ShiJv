"""Conservative deployment advice. Stdlib only; never a compatibility guarantee."""
from __future__ import annotations

import csv
import ctypes
import io
import os
import subprocess
import time

from .deployment_profiles import PROFILES, CUDA_ARCHITECTURES
from .runtime_manifest import version_tuple

GIB = 1024**3
MODEL_IDS = tuple('Qwen/Qwen3.5-' + size for size in ('4B', '2B', '0.8B'))
PROFILE_IDS = ('cpu', 'cpu-bf16', 'cuda-fp16', 'cuda-bnb', 'cuda-bnb8')
WEIGHT_BYTES = {'0.8B': 1746942600, '2B': 4548221488, '4B': 9319828096}

# peak_vram / peak_ram: observed GiB on one 14900HX + 4060 Laptop machine.
# vram / ram: observed reference values, not padded free-resource requirements.
# None means unmeasured, never a zero-resource requirement.
BUDGETS = {
    '0.8B': {
        # User-verified Windows Sandbox baseline: 10240 MiB, CPU FP32.
        'cpu': (None, 5.18, 0, 5.18, 10),
        'cpu-bf16': (None, 1.78, 0, 1.78, 8),
        'cuda-fp16': (None, None, 0, 0, 12),
        'cuda-bnb': (None, None, 0, 0, 12),
        'cuda-bnb8': (None, None, 0, 0, 12),
    },
    '2B': {
        'cpu': (None, None, 0, 9.54, 16),
        'cpu-bf16': (None, 3.88, 0, 3.88, 12),
        'cuda-fp16': (4.15, 5.93, 4.15, 5.93, 12),
        'cuda-bnb': (2.05, 4.98, 2.05, 4.98, 12),
        'cuda-bnb8': (2.59, 5.04, 2.59, 5.04, 12),
    },
    '4B': {
        'cpu': (None, None, 0, 0, 32),
        'cpu-bf16': (None, None, 0, 0, 16),
        'cuda-fp16': (None, None, 6.09, 0, 16),
        'cuda-bnb': (3.20, None, 3.20, 0, 16),
        'cuda-bnb8': (4.88, None, 4.88, 0, 16),
    },
}
GPU_CAPACITY = {
    '0.8B': {'cuda-fp16': 2, 'cuda-bnb': 2, 'cuda-bnb8': 2},
    '2B': {'cuda-fp16': 6, 'cuda-bnb': 3, 'cuda-bnb8': 4},
    '4B': {'cuda-fp16': 8, 'cuda-bnb': 4, 'cuda-bnb8': 6},
}


def model_size(model_id):
    return model_id.rsplit('-', 1)[-1] if model_id in MODEL_IDS else None


def named_official_model(name):
    """Use the model-name substring for local model selection and advice."""
    lowered = str(name).casefold()
    return next((model for model in MODEL_IDS if model.split('/')[-1].casefold() in lowered), None)


def gpu_budget_for_weights(model_type, weight_bytes, profile):
    """Recognize fixed Qwen3.5 weight sizes, never an arbitrary directory name."""
    if model_type == 'qwen3_5':
        for size, reference in WEIGHT_BYTES.items():
            if abs(weight_bytes - reference) <= reference * .001:
                value = BUDGETS[size][profile][2]
                return value * GIB if value else None
    return None


def cpu_reference_for_weights(model_type, weight_bytes, precision):
    if model_type == 'qwen3_5':
        for size, reference in WEIGHT_BYTES.items():
            if abs(weight_bytes - reference) <= reference * .001:
                peak = BUDGETS[size]['cpu-bf16' if precision == 'bf16' else 'cpu'][1]
                return peak * GIB if peak is not None else None
    return None


def eligible_gpu(host):
    if host.os != 'windows' or host.arch != 'x64' or host.windows_build < 19041:
        return None
    devices = [g for g in host.gpus if g.compute_capability in CUDA_ARCHITECTURES
               and version_tuple(g.driver_version) >= version_tuple('560.76')
               and g.memory_bytes >= 2_000_000_000]
    return max(devices, key=lambda g: g.memory_bytes, default=None)


def collect_resources(host):
    result = dict(checked_at=time.time(), ram_total=None, ram_available=None,
                  gpu_total=None, gpu_available=None, gpu_name=None, gpu_uuid=None, errors=[])
    if os.name == 'nt':
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    'total_phys', 'avail_phys', 'total_page', 'avail_page',
                    'total_virtual', 'avail_virtual', 'avail_extended')]
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result.update(ram_total=memory.total_phys, ram_available=memory.avail_phys)
        else:
            result['errors'].append('无法读取系统内存，不能据此确认资源充足。')
    else:
        result['errors'].append('当前部署工具仅支持 Windows x64。')
    gpu = eligible_gpu(host)
    if host.gpus and gpu is None:
        result['errors'].append('已检测到显卡，但其系统、驱动或计算能力不在当前 GPU 组合适配范围内；因此按 CPU 路线评估。')
    if gpu:
        result.update(gpu_total=gpu.memory_bytes, gpu_uuid=gpu.uuid, gpu_name=gpu.name)
        try:
            from .runtime_manager import _nvidia_smi
            command = _nvidia_smi()
            if not command:
                raise ValueError('未找到 nvidia-smi')
            query = subprocess.run([command, '--query-gpu=uuid,memory.free',
                                    '--format=csv,noheader,nounits'], capture_output=True,
                                   text=True, timeout=10,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if query.returncode:
                raise ValueError('显存查询失败')
            for row in csv.reader(io.StringIO(query.stdout), skipinitialspace=True):
                if len(row) == 2 and row[0].strip() == gpu.uuid:
                    free = int(float(row[1].strip()) * 1024**2)
                    if not 0 <= free <= gpu.memory_bytes:
                        raise ValueError('可用显存数值异常')
                    result['gpu_available'] = free
                    break
            if result['gpu_available'] is None:
                raise ValueError('未取得所选显卡的可用显存')
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            result['errors'].append(f'无法读取当前可用显存：{exc}；不使用标称容量代替可用量。')
    return result


def fits(resources, size, profile):
    _, _, _, _, total = BUDGETS[size][profile]
    if resources.get('ram_total') is None or resources['ram_total'] < total * GIB * .97:
        return False
    if PROFILES[profile]['device'] == 'cuda':
        return bool(resources.get('gpu_uuid')) and (resources.get('gpu_total') or 0) >= GPU_CAPACITY[size][profile] * GIB * .97
    return True


def recommended(resources):
    capacity = (resources.get('gpu_total') or 0) / GIB
    # Product tiers are based on installed capacity. Current usage only adds warnings.
    for size, profile, minimum in [('4B', 'cuda-bnb', 11.8),
                                    ('2B', 'cuda-bnb', 3.8), ('0.8B', 'cuda-fp16', 3.2)]:
        if capacity >= minimum and fits(resources, size, profile):
            return dict(model_id='Qwen/Qwen3.5-' + size, profile=profile)
    if fits(resources, '0.8B', 'cpu'):
        return dict(model_id='Qwen/Qwen3.5-0.8B', profile='cpu')
    return None


def auto_profile(resources, model_id):
    size = model_size(model_id)
    if not size:
        return None
    rec = recommended(resources)
    if rec and rec['model_id'] == model_id:
        return rec['profile']
    profiles = ('cuda-fp16', 'cuda-bnb8', 'cuda-bnb')
    if resources.get('gpu_uuid'):
        for profile in profiles:
            if fits(resources, size, profile):
                return profile
    # The caller must show the risk before continuing a CPU fallback.
    return 'cpu'


def format_gib(value):
    return '未取得' if value is None else f'{value / GIB:.2f} GiB'


def describe_recommendation(resources):
    rec = recommended(resources)
    if rec:
        return f"本机推荐：{rec['model_id'].split('/')[-1]} / {PROFILES[rec['profile']]['label']}。"
    return '硬件容量未达到默认推荐档位，或硬件信息不完整。请查看具体评估。'


def assess_selection(resources, model_id, profile, *, known=True, weight_bytes=None):
    size = model_size(model_id) if known else None
    chosen = auto_profile(resources, model_id) if profile == 'auto' and size else profile
    label = PROFILES.get(chosen, {}).get('label', '自动选择（待核验）')
    lines = [describe_recommendation(resources), f'当前选择：{model_id} / {label}。',
             f"系统内存：总量 {format_gib(resources.get('ram_total'))}；当前可用 {format_gib(resources.get('ram_available'))}。"]
    if resources.get('gpu_uuid'):
        lines.append(f"所选显卡：{resources.get('gpu_name')}；总显存 {format_gib(resources.get('gpu_total'))}；当前可用 {format_gib(resources.get('gpu_available'))}。")
    lines.extend(resources.get('errors', []))
    state, title, budget = 'unknown', '待评估：尚无对应实测依据', None
    if size and chosen in PROFILES:
        peak_vram, peak_ram, reference_vram, reference_ram, total = BUDGETS[size][chosen]
        gpu = PROFILES[chosen]['device'] == 'cuda'
        capacity = GPU_CAPACITY[size][chosen] if gpu else 0
        budget = dict(ram_total_bytes=int(total * GIB), gpu_total_bytes=int(capacity * GIB),
                      observed_vram_peak_gib=peak_vram, observed_ram_peak_gib=peak_ram,
                      reference_vram_bytes=int(reference_vram * GIB), reference_ram_bytes=int(reference_ram * GIB))
        lines.append(f'配置档位参考：{total:g} GB 系统内存' + (f'、{capacity:g} GB 显存。' if gpu else '。'))
        if peak_vram is not None:
            lines.append(f'参考实测显存分配峰值约 {peak_vram:.2f} GiB，不含全部 CUDA、桌面及其他程序占用。')
        if peak_ram is not None:
            lines.append(f'参考实测进程物理内存峰值约 {peak_ram:.2f} GiB（含加载阶段），用于了解占用，不额外叠加固定空闲内存门槛。')
        elif gpu:
            lines.append('该组合的系统内存峰值尚无完整实测，不设置额外固定空闲内存门槛。')
        missing = resources.get('ram_total') is None or (gpu and resources.get('gpu_total') is None)
        if gpu and not resources.get('gpu_uuid'):
            state, title = 'risk', '高风险：缺少当前运行方式所需的受支持 GPU'
        elif missing:
            state, title = 'unknown', '待评估：硬件容量信息不完整'
        elif not fits(resources, size, chosen):
            state, title = 'risk', '高风险：硬件容量低于该方案的配置档位'
            lines.append('建议优先考虑与本机档位匹配的方案；若继续尝试，可能需要调整运行方式或接受较长等待时间。')
        else:
            rec = recommended(resources)
            matches = rec and (model_id, chosen) == (rec['model_id'], rec['profile'])
            state, title = ('recommended', '推荐配置：符合本机硬件档位') if matches else ('usable', '可用配置：可按速度、精度和占用偏好选择')
            if not matches and rec:
                levels = {'0.8B': .8, '2B': 2, '4B': 4}
                if levels[model_size(rec['model_id'])] > levels[size]:
                    lines.append('当前方案可以使用，资源占用较低。你的电脑也可以选择更大的模型；可以保留当前选择并开始安装，也可以调整模型。')
                else:
                    lines.append('当前选择与默认推荐不同，可以按精度、速度和资源占用偏好保留。')
            pressure = []
            if reference_ram and resources.get('ram_available') is not None and resources['ram_available'] < reference_ram * GIB:
                pressure.append(f'当前可用系统内存低于参考占用 {reference_ram:g} GiB')
            if gpu and reference_vram and resources.get('gpu_available') is not None and resources['gpu_available'] < reference_vram * GIB:
                pressure.append(f'当前可用显存低于参考占用 {reference_vram:g} GiB')
            if pressure:
                state, title = 'tight', '硬件档位可用，当前资源占用偏高'
                lines += [item + '。' for item in pressure]
                lines.append('建议关闭其他占用资源的程序后重试。这是当前占用提醒，不因此改变本机推荐档位，也不据此断言无法运行。')
            elif resources.get('ram_available') is None or (gpu and resources.get('gpu_available') is None):
                lines.append('当前可用资源未完整取得，保留按硬件容量给出的推荐；运行前请留意其他程序占用。')
        if chosen == 'cpu-bf16':
            lines.append('CPU BF16 参考测试约 0.08–0.12 tokens/s，速度随 CPU 而异；作为手动选项保留。')
        if size == '2B' and chosen == 'cpu':
            lines.append('16GB 主机曾在加载时物理内存达到约 9.54 GiB 后触发测试保护；最终峰值未知，该结果仅作占用提醒。')
        if size == '4B' and chosen == 'cuda-fp16':
            lines.append('16GB 系统内存＋8GB 显存默认推荐 4B 8bit；GPU FP16 略紧张但可尝试。专项复测中，补齐子模块预加载的 FP16 对照路径正常生成，显存峰值约 6.09 GiB；部分权重保存在 CPU 并由钩子调入 GPU 计算。原路径的加载兼容问题已定位，对照修正仅在测试脚本中验证，不用于否定此硬件档位。')
    else:
        lines.append('本地模型不能仅凭目录名称套用官方模型数据；请通过实际加载与生成评估，不将未经校准的估算作为推荐档位依据。')
    lines.append('推荐档位用于选择方案；实际占用受输入长度、其他程序和加载方式影响。部署后仍须通过本机生成验收；磁盘空间另行检查。')
    notice = {
        'recommended': '当前方案适合本机，可以继续。',
        'usable': '当前选择可用，可以保留。',
        'tight': '当前内存或显存占用偏高，建议先关闭占用资源的程序再继续。',
        'risk': '本机配置低于当前方案需求，建议选择更小的模型或更换运行方式。',
        'unknown': '暂时无法确认当前方案是否适合本机，安装后将通过实际生成检查。',
    }[state]
    if state == 'usable':
        rec = recommended(resources)
        if rec and model_size(rec['model_id']) and size:
            levels = {'0.8B': .8, '2B': 2, '4B': 4}
            if levels[model_size(rec['model_id'])] > levels[size]:
                notice = '当前方案可以使用，资源占用较低。你的电脑也可以选择更大的模型。你可以保留当前选择并开始安装，也可以调整模型。'
    if state == 'risk' and gpu and not resources.get('gpu_uuid'):
        notice = '未检测到当前方案所需的受支持显卡，建议改用 CPU 运行。'
    if not known:
        notice = '自带模型的兼容性尚未确认，安装后将通过实际加载和生成检查。'
    if size in ('2B', '4B') and chosen in ('cpu', 'cpu-bf16'):
        state = 'risk'
        title = '不推荐：CPU 仅推荐 0.8B'
        notice = 'CPU 运行 2B、4B 生成过慢，请选择 0.8B。仍可手动继续安装，但需要接受较长等待。'
    return dict(state=state, title=title, notice=notice, model_id=model_id, profile=chosen, requested_profile=profile,
                text=title + '\n' + '\n'.join(lines), budget=budget,
                requires_confirmation=state in ('tight', 'risk', 'unknown'))


def snapshot(host, resources=None):
    resources = collect_resources(host) if resources is None else resources
    return dict(resources=resources, recommendation=recommended(resources),
                summary=describe_recommendation(resources),
                options=[assess_selection(resources, model, profile)
                         for model in MODEL_IDS for profile in ('auto', *PROFILE_IDS)])
