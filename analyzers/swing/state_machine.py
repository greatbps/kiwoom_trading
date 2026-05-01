"""
스윙 포지션 상태 머신

상태 전이:
    WATCH → READY (패턴 형성 감지)
    READY → TRIGGER (돌파 발생)
    TRIGGER → HOLD (매수 완료)
    HOLD → ADD (MA5 눌림 추가매수 조건)
    HOLD → EXIT (MA5 이탈 또는 손절)
    ADD → HOLD (추가매수 완료)
    * → EXIT (어떤 상태에서도 강제 청산 가능)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import IntEnum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SwingState(IntEnum):
    WATCH = 0
    READY = 1
    TRIGGER = 2
    HOLD = 3
    ADD = 4
    EXIT = 5


@dataclass
class SwingPosition:
    stock_code: str
    stock_name: str
    state: SwingState = SwingState.WATCH
    pattern: str = ""
    score: float = 0.0
    entry_price: float = 0.0
    entry_date: Optional[date] = None
    add_count: int = 0
    holding_days: int = 0
    ma5_distance_pct: float = 0.0
    ma5_below_days: int = 0        # MA5 연속 이탈 일수 (2일 이상 → EXIT)
    max_profit_pct: float = 0.0
    drawdown_pct: float = 0.0
    allocated_size: float = 0.0
    position_lots: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['state'] = int(self.state)
        if self.entry_date is not None:
            d['entry_date'] = self.entry_date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'SwingPosition':
        d = dict(d)
        d['state'] = SwingState(d.get('state', 0))
        raw_date = d.get('entry_date')
        if isinstance(raw_date, str):
            d['entry_date'] = date.fromisoformat(raw_date)
        elif raw_date is not None:
            d['entry_date'] = None
        # 하위 호환: 이전 JSON에 없는 필드 기본값 보장
        d.setdefault('ma5_below_days', 0)
        return cls(**d)


class SwingStateManager:
    """
    스윙 포지션 상태를 JSON 파일로 저장/로드.

    다음날 러너가 이어받을 수 있도록 장 마감 후 상태 영속화.
    """

    def __init__(self, path: str = "data/swing_positions.json"):
        self._path = Path(path)
        self._positions: dict[str, SwingPosition] = {}
        self._loaded = False

    def load(self) -> dict[str, SwingPosition]:
        if not self._path.exists():
            logger.info(f"[SWING_SM] 상태 파일 없음, 빈 상태로 시작: {self._path}")
            self._positions = {}
            self._loaded = True
            return self._positions

        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
            self._positions = {
                code: SwingPosition.from_dict(data)
                for code, data in raw.items()
            }
            logger.info(f"[SWING_SM] 로드 완료: {len(self._positions)}개 포지션")
        except Exception as e:
            logger.error(f"[SWING_SM] 로드 실패: {e}", exc_info=True)
            self._positions = {}

        self._loaded = True
        return self._positions

    def save(self, positions: dict[str, SwingPosition]) -> None:
        self._positions = positions
        self._path.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {code: pos.to_dict() for code, pos in positions.items()}
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            logger.info(f"[SWING_SM] 저장 완료: {len(positions)}개 포지션 → {self._path}")
        except Exception as e:
            logger.error(f"[SWING_SM] 저장 실패: {e}", exc_info=True)

    def get(self, stock_code: str) -> Optional[SwingPosition]:
        if not self._loaded:
            self.load()
        return self._positions.get(stock_code)

    def set(self, pos: SwingPosition) -> None:
        if not self._loaded:
            self.load()
        self._positions[pos.stock_code] = pos

    def remove(self, stock_code: str) -> None:
        self._positions.pop(stock_code, None)

    @property
    def all(self) -> dict[str, SwingPosition]:
        if not self._loaded:
            self.load()
        return self._positions
