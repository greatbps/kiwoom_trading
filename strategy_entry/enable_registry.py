"""strategy_entry/enable_registry.py — WI-25 §20 Strategy Enable Registry.

전략별(seq32~39) 활성화 상태. 기본값은 전부 DISABLED다. 이번 WI에서는 실제
ENABLE 전환을 하지 않는다 — 파일도 전부 DISABLED로만 커밋한다(§20/§29).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

from strategy_entry.types import VALID_STRATEGY_SEQS

DEFAULT_REGISTRY_PATH = Path('data/strategy_enable_registry.json')

ENABLED = 'ENABLED'
DISABLED = 'DISABLED'


class StrategyEnableRegistry:
    """읽기 우선 레지스트리. set_enabled()는 존재하지만(§20 "향후 전략 단위로
    승인할 수 있어야 한다") 이번 WI 산출물 파일은 전부 DISABLED로만 저장한다
    — 실제 전환은 별도 WI의 명시적 승인 절차를 거친다."""

    def __init__(self, path: os.PathLike = DEFAULT_REGISTRY_PATH):
        self.path = Path(path)
        self._state: Dict[int, str] = self._load()

    def _load(self) -> Dict[int, str]:
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            state = {int(k): v for k, v in raw.items()}
        else:
            state = {}
        # 파일에 없는 seq는 안전하게 DISABLED로 채운다(Fail-Closed 기본값)
        for seq in VALID_STRATEGY_SEQS:
            state.setdefault(seq, DISABLED)
        return state

    def status(self, strategy_seq: int) -> str:
        return self._state.get(strategy_seq, DISABLED)

    def is_enabled(self, strategy_seq: int) -> bool:
        return self.status(strategy_seq) == ENABLED

    def set_enabled(self, strategy_seq: int, enabled: bool) -> None:
        """상태 변경 API. 이번 WI는 이 메서드를 호출하는 산출물을 만들지
        않는다 — 향후 KPI PASS 승인 절차용으로만 존재(§20/§29)."""
        if strategy_seq not in VALID_STRATEGY_SEQS:
            raise ValueError(f'알 수 없는 strategy_seq: {strategy_seq}')
        self._state[strategy_seq] = ENABLED if enabled else DISABLED

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({str(k): v for k, v in sorted(self._state.items())},
                       ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def snapshot(self) -> Dict[int, str]:
        return dict(self._state)
