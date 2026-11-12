"""파라미터 레지스트리(확정값) + what-if 덮어쓰기 -> ModelSettings. what-if 는 표시를 남긴다."""
from __future__ import annotations

from dataclasses import fields

from dealcockpit.domain.parameters import ParameterRegistry

from .case import ModelSettings


def settings_from_registry(reg: ParameterRegistry, what_if: dict[str, float] | None = None) -> tuple[ModelSettings, tuple[str, ...]]:
    values = reg.what_if(what_if or {})
    kwargs = {}
    for f in fields(ModelSettings):
        v = values[f.name]
        kwargs[f.name] = int(v) if reg.spec(f.name).integer else float(v)
    notes = tuple(f"what-if: {k} = {v} (확정값 {reg.value(k)}) — 확정 계획 아님" for k, v in (what_if or {}).items())
    return ModelSettings(**kwargs), notes
