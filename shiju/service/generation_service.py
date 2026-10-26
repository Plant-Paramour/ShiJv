from __future__ import annotations

from typing import Any, Mapping

from ..contracts import GeneratePoemRequest, JobKind, PartialGeneratePoemRequest, RewritePoemRequest
from ..generation.engine import GenerationEngine


class GenerationService:
    def __init__(self, engine: GenerationEngine, event_callback=None):
        self._engine = engine
        self._event_callback = event_callback

    def execute(self, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if kind == JobKind.GENERATE.value:
            return self._engine.generate_poem(GeneratePoemRequest.from_mapping(payload), on_candidate_event=self._event_callback)
        if kind == JobKind.PARTIAL_GENERATE.value:
            request = (PartialGeneratePoemRequest.from_mapping(payload) if str(payload.get("original_text") or "").strip()
                       else GeneratePoemRequest.from_mapping(payload))
            return self._engine.partial_generate_poem(request, on_candidate_event=self._event_callback)
        if kind == JobKind.REWRITE.value:
            if not str(payload.get("original_text") or "").strip():
                return self._engine.generate_poem(
                    GeneratePoemRequest.from_mapping(payload),
                    on_candidate_event=self._event_callback,
                )
            return self._engine.rewrite_poem(RewritePoemRequest.from_mapping(payload), on_candidate_event=self._event_callback)
        raise ValueError(f"不支持的任务类型: {kind}")
