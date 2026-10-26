"""Use FLA's real Triton convolution kernels for Qwen3.5 on Windows."""
from __future__ import annotations

import sys
import logging


ADAPTER_VERSION = "strided-cache-v2"


def causal_conv1d_fn(x, weight, bias=None, activation=None, seq_idx=None):
    from fla.modules.conv.causal_conv1d import causal_conv1d

    if seq_idx is not None:
        raise NotImplementedError("Packed sequence convolution is not supported by this adapter")
    output, _ = causal_conv1d(
        x.transpose(1, 2).contiguous(), weight=weight, bias=bias,
        activation=activation, backend="triton",
    )
    return output.transpose(1, 2)


def causal_conv1d_update(x, conv_state, weight, bias=None, activation=None):
    from fla.modules.conv.triton.ops import causal_conv1d_update as update

    # The Triton update kernel processes one time step and mutates the cache.
    # Iterate explicitly for multi-token cached continuations as well.
    import torch
    # Transformers preserves the prefill projection's [B, W, D] storage in
    # its [B, D, W] cache. FLA's update kernel ignores cache strides and
    # addresses it as contiguous [B, D, W]. Passing that view directly mixes
    # channels and corrupts every following token. Copy back in-place to keep
    # the Transformers cache's identity/static address intact.
    kernel_state = conv_state.contiguous()
    kernel_weight = weight.contiguous()
    outputs = []
    for index in range(x.shape[-1]):
        output, _ = update(
            x[:, :, index].contiguous(), kernel_state,
            weight=kernel_weight, bias=bias, activation=activation,
        )
        outputs.append(output.unsqueeze(-1))
    if kernel_state.data_ptr() != conv_state.data_ptr():
        conv_state.copy_(kernel_state)
    return torch.cat(outputs, dim=-1)


def enable_windows_qwen35_kernels(model_type: str) -> bool:
    if sys.platform != "win32" or model_type != "qwen3_5":
        return False
    from transformers.utils.import_utils import (
        is_causal_conv1d_available, is_flash_linear_attention_available,
    )
    if is_causal_conv1d_available() or not is_flash_linear_attention_available():
        return False
    import torch
    if not torch.cuda.is_available():
        return False
    from transformers.models.qwen3_5 import modeling_qwen3_5 as modeling
    # Resolve the actual kernels before enabling the alternate backend.
    from fla.modules.conv.causal_conv1d import causal_conv1d
    from fla.modules.conv.triton.ops import causal_conv1d_update as update

    modeling.causal_conv1d_fn = causal_conv1d_fn
    modeling.causal_conv1d_update = causal_conv1d_update
    modeling.is_fast_path_available = all((
        causal_conv1d, update, modeling.chunk_gated_delta_rule,
        modeling.fused_recurrent_gated_delta_rule,
    ))
    logging.getLogger(__name__).info("Qwen3.5 Windows 卷积适配器已启用: %s", ADAPTER_VERSION)
    return modeling.is_fast_path_available
