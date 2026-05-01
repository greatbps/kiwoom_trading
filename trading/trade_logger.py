"""
거래 자동 기록 — CSV 저장

저장 위치: logs/trade_log_YYYY.csv
컬럼: date, symbol, entry_price, exit_price, pnl_pct, score, entry_reason, exit_reason, hold_min

사용법:
    from trading.trade_logger import TradeLogger

    tl = TradeLogger()
    tl.log_entry(symbol='005930', price=70000, score=3, reason='breakout')
    tl.log_exit(symbol='005930', entry_price=70000, exit_price=72800,
                exit_reason='TP', hold_min=45)
"""
import csv
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LOG_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / 'logs'

FIELDNAMES = [
    'date', 'symbol',
    'entry_price', 'exit_price',
    'pnl_pct',        # 소수점 (예: 0.04 = +4%)
    'pnl_won',        # 주당 손익
    'score',          # 진입 시 점수
    'smc_score',      # SMC 세부 (0 or 2)
    'vol_score',      # 거래량 세부
    'ma50_score',     # MA50 세부
    'entry_reason',   # 'breakout' | 'smc_only' | etc.
    'exit_reason',    # 'TP' | 'SL' | 'TIME' | etc.
    'hold_min',       # 보유 시간 (분)
    'entry_time',     # HH:MM
    'exit_time',      # HH:MM
]


class TradeLogger:
    """거래 기록 관리자."""

    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._pending: dict[str, dict] = {}   # symbol → entry 정보 임시 저장

    def _log_path(self) -> Path:
        year = datetime.today().year
        return self.log_dir / f'trade_log_{year}.csv'

    def _ensure_header(self) -> None:
        path = self._log_path()
        if not path.exists():
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                writer.writeheader()

    def log_entry(
        self,
        symbol:       str,
        price:        float,
        score:        int   = 0,
        score_detail: dict  = None,   # {'smc':2, 'volume':1, 'ma50':0}
        reason:       str   = 'breakout',
    ) -> None:
        """진입 기록 (임시 저장 — log_exit 호출 시 CSV에 저장)."""
        self._pending[symbol] = {
            'entry_price':  price,
            'score':        score,
            'smc_score':    (score_detail or {}).get('smc', 0),
            'vol_score':    (score_detail or {}).get('volume', 0),
            'ma50_score':   (score_detail or {}).get('ma50', 0),
            'entry_reason': reason,
            'entry_time':   datetime.now().strftime('%H:%M'),
            'entry_dt':     datetime.now(),
        }
        logger.info(f'[TRADE_LOG] 진입 기록: {symbol}  price={price:.0f}  score={score}  reason={reason}')

    def log_exit(
        self,
        symbol:      str,
        entry_price: float,
        exit_price:  float,
        exit_reason: str = '',
        hold_min:    int = 0,
    ) -> None:
        """청산 기록 → CSV 행 저장."""
        self._ensure_header()

        entry = self._pending.pop(symbol, {})
        pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
        pnl_won = exit_price - entry_price

        # hold_min 자동 계산 (entry_dt 있으면)
        if entry.get('entry_dt') and hold_min == 0:
            hold_min = int((datetime.now() - entry['entry_dt']).total_seconds() / 60)

        row = {
            'date':         datetime.today().strftime('%Y-%m-%d'),
            'symbol':       symbol,
            'entry_price':  round(entry_price, 0),
            'exit_price':   round(exit_price, 0),
            'pnl_pct':      round(pnl_pct, 4),
            'pnl_won':      round(pnl_won, 0),
            'score':        entry.get('score', 0),
            'smc_score':    entry.get('smc_score', 0),
            'vol_score':    entry.get('vol_score', 0),
            'ma50_score':   entry.get('ma50_score', 0),
            'entry_reason': entry.get('entry_reason', ''),
            'exit_reason':  exit_reason,
            'hold_min':     hold_min,
            'entry_time':   entry.get('entry_time', ''),
            'exit_time':    datetime.now().strftime('%H:%M'),
        }

        with open(self._log_path(), 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(row)

        sign = '+' if pnl_pct >= 0 else ''
        logger.info(
            f'[TRADE_LOG] 청산 기록: {symbol}  '
            f'{entry_price:.0f}→{exit_price:.0f}  '
            f'pnl={sign}{pnl_pct*100:.2f}%  reason={exit_reason}  {hold_min}분'
        )

    def log_entry_from_position(self, symbol: str, position: dict, score: int = 0,
                                score_detail: dict = None) -> None:
        """main_auto_trading.py position dict에서 진입 기록."""
        price = float(position.get('entry_price', 0))
        reason = position.get('entry_reason', 'smc')
        self.log_entry(symbol, price, score, score_detail, reason)

    def log_exit_from_position(self, symbol: str, position: dict,
                               exit_price: float, exit_reason: str = '') -> None:
        """main_auto_trading.py position dict에서 청산 기록."""
        entry_price = float(position.get('entry_price', 0))
        self.log_exit(symbol, entry_price, exit_price, exit_reason)
