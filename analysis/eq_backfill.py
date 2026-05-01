"""
analysis/eq_backfill.py — trades.db BUY/SELL → entry_features 소급 입력

가용 피처 추출 규칙:
    BUY reason  → time_slot (timestamp), strategy hint
    SELL reason → choch_grade ("등급=B"/"CHoCH B급"), htf_trend (HTF❌/✅)
                  exit_reason 범주
    수치 피처   → 기록 없음 → NULL (모델 학습 시 LightGBM NaN 처리)

사용법:
    python -m analysis.eq_backfill [--dry-run] [--since 2026-01-01]
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT      = Path(__file__).parent.parent
_DB_PATH   = _ROOT / "data" / "trades.db"


# ── 이유 문자열 파싱 ──────────────────────────────────────────────

def _parse_choch_grade(sell_reason: str) -> str | None:
    """'등급=B', 'CHoCH B급', 'CHoCH A급' 등에서 등급 추출"""
    m = re.search(r'등급=([ABC][+]?)', sell_reason)
    if m:
        return m.group(1)
    m = re.search(r'CHoCH\s+([ABC][+]?)급', sell_reason)
    if m:
        return m.group(1)
    return None


def _parse_htf_trend(sell_reason: str) -> int | None:
    if 'HTF✅' in sell_reason or 'HTF_OK' in sell_reason:
        return 1
    if 'HTF❌' in sell_reason or 'HTF_FAIL' in sell_reason:
        return 0
    return None


def _parse_exit_reason(sell_reason: str) -> str:
    r = sell_reason.lower()
    if 'hard stop' in r or 'hard_stop' in r:
        return 'hard_stop'
    if 'early failure[no_demand]' in r or 'ef_no_demand' in r:
        return 'ef_no_demand'
    if 'early failure[no_follow]' in r or 'ef_no_follow' in r:
        return 'ef_no_follow'
    if 'early failure' in r:
        return 'early_failure'
    if '트레일링' in sell_reason or 'trailing' in r or 'atr 트레일링' in sell_reason:
        return 'trailing_stop'
    if '오버나이트' in sell_reason or 'overnight' in r:
        return 'overnight_close'
    if 'take profit' in r or '익절' in sell_reason:
        return 'take_profit'
    if 'time' in r or '시간' in sell_reason:
        return 'time_exit'
    if 'hts_import' in r:
        return 'hts_import'
    return 'unknown'


def _time_slot(ts: str) -> int:
    """ISO timestamp → 분 (09:00 = 0 기준)"""
    try:
        dt = datetime.fromisoformat(ts)
        return (dt.hour - 9) * 60 + dt.minute
    except Exception:
        return 60


# ── BUY/SELL 매칭 ─────────────────────────────────────────────────

def _match_pairs(db_path: Path, since: str | None = None) -> list[dict]:
    """
    trades 테이블에서 BUY→SELL 쌍을 시계열 순으로 매칭.
    - 같은 stock_code, SELL.timestamp > BUY.timestamp
    - realized_pnl != 0 인 SELL만 대상
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    where = "WHERE realized_pnl != 0"
    if since:
        where += f" AND timestamp >= '{since}'"

    sells = conn.execute(f"""
        SELECT timestamp, stock_code, stock_name, quantity, price,
               realized_pnl, reason
        FROM trades
        WHERE trade_type = 'SELL' AND realized_pnl != 0
        {'AND timestamp >= ?' if since else ''}
        ORDER BY timestamp
    """, (since,) if since else ()).fetchall()

    buys_all = conn.execute("""
        SELECT timestamp, stock_code, stock_name, quantity, price, reason
        FROM trades
        WHERE trade_type = 'BUY'
        ORDER BY timestamp
    """).fetchall()
    conn.close()

    # stock_code → list of buy records (시간 순)
    from collections import defaultdict
    buy_map: dict[str, list] = defaultdict(list)
    for b in buys_all:
        buy_map[b['stock_code']].append(dict(b))

    # 이미 사용된 buy idx
    used_buys: dict[str, set] = defaultdict(set)

    pairs = []
    for sell in sells:
        code     = sell['stock_code']
        sell_ts  = sell['timestamp']
        buys     = buy_map.get(code, [])

        # sell보다 이전인 가장 최근 BUY
        matched_buy = None
        matched_idx = -1
        for idx, b in enumerate(buys):
            if idx in used_buys[code]:
                continue
            if b['timestamp'] < sell_ts:
                matched_buy = b
                matched_idx = idx
            # 시간 순이므로 더 뒤를 봤자 여기서 break하면 마지막 유효 BUY

        if matched_buy is None:
            logger.debug(f"[BACKFILL] 매칭 BUY 없음: {code} sell={sell_ts}")
            continue

        used_buys[code].add(matched_idx)

        buy_qty   = matched_buy['quantity'] or 1
        buy_price = matched_buy['price']
        pnl_pct   = sell['realized_pnl'] / (buy_price * buy_qty) * 100 if buy_price else 0.0

        pairs.append({
            'timestamp':   matched_buy['timestamp'],
            'stock_code':  code,
            'stock_name':  sell['stock_name'] or matched_buy['stock_name'],
            'entry_price': buy_price,
            'buy_reason':  matched_buy['reason'] or '',
            'sell_reason': sell['reason'] or '',
            'outcome_pnl_pct': round(pnl_pct, 4),
            'outcome_win':     1 if sell['realized_pnl'] > 0 else 0,
            'exit_timestamp':  sell['timestamp'],
        })

    return pairs


# ── 피처 딕셔너리 구성 ────────────────────────────────────────────

def _build_features(pair: dict) -> dict:
    br = pair['buy_reason']
    sr = pair['sell_reason']

    choch_grade = _parse_choch_grade(sr) or _parse_choch_grade(br)
    htf_trend   = _parse_htf_trend(sr)
    exit_reason = _parse_exit_reason(sr)
    time_slot   = _time_slot(pair['timestamp'])

    return {
        'choch_grade':      choch_grade,
        'htf_trend':        htf_trend,
        'time_slot':        time_slot,
        'exit_reason':      exit_reason,
        # 수치 피처는 NULL → LightGBM이 NaN으로 처리
        'eq_grade':         None,
        'entry_confidence': None,
        'r_pct':            None,
        'sweep':            None,
        'atr_pct':          None,
        'volume_ratio':     None,
        'rsi':              None,
        'squeeze_on':       None,
        'regime':           None,
        'guard_state':      'normal',
    }


# ── entry_features 삽입 ──────────────────────────────────────────

def _insert(db_path: Path, pairs: list[dict], dry_run: bool = False) -> int:
    from ml.feature_logger import FeatureLogger

    fl = FeatureLogger(db_path=str(db_path))

    inserted = 0
    for p in pairs:
        feat = _build_features(p)
        if dry_run:
            print(
                f"  [DRY] {p['timestamp'][:10]} {p['stock_code']} "
                f"{p['stock_name'][:8]:8s} "
                f"pnl={p['outcome_pnl_pct']:+.2f}% "
                f"win={p['outcome_win']} "
                f"choch={feat['choch_grade']} htf={feat['htf_trend']} "
                f"exit={feat['exit_reason']}"
            )
            inserted += 1
            continue

        row_id = fl.log_entry(
            stock_code  = p['stock_code'],
            stock_name  = p['stock_name'],
            entry_price = p['entry_price'],
            features    = feat,
        )
        if row_id:
            fl.log_outcome(
                stock_code = p['stock_code'],
                pnl_pct    = p['outcome_pnl_pct'],
                exit_reason= feat['exit_reason'],
            )
            # exit_timestamp 직접 업데이트 (log_outcome은 NOW() 사용)
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "UPDATE entry_features SET exit_timestamp=? WHERE id=?",
                (p['exit_timestamp'], row_id)
            )
            conn.commit()
            conn.close()
            inserted += 1
            logger.info(
                f"[BACKFILL] id={row_id} {p['stock_code']} "
                f"pnl={p['outcome_pnl_pct']:+.2f}% win={p['outcome_win']}"
            )

    return inserted


# ── 메인 ─────────────────────────────────────────────────────────

def run(db_path: Path = _DB_PATH, since: str | None = None, dry_run: bool = False):
    if not db_path.exists():
        print(f"DB 없음: {db_path}")
        return

    pairs = _match_pairs(db_path, since)
    if not pairs:
        print("매칭 가능한 BUY/SELL 쌍 없음")
        return

    wins = sum(p['outcome_win'] for p in pairs)
    print(f"\n{'[DRY RUN] ' if dry_run else ''}매칭 쌍: {len(pairs)}건  "
          f"승률: {wins}/{len(pairs)} ({wins/len(pairs)*100:.0f}%)")

    if dry_run:
        print()

    inserted = _insert(db_path, pairs, dry_run=dry_run)

    if not dry_run:
        from ml.feature_logger import FeatureLogger
        stats = FeatureLogger(db_path=str(db_path)).stats()
        print(f"\n입력 완료: {inserted}건")
        print(f"entry_features 현황: 전체={stats['total']}  "
              f"레이블={stats['labeled']}  승률={stats['win_rate']}%")


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    parser = argparse.ArgumentParser(description='EQ 모델 백필')
    parser.add_argument('--dry-run', action='store_true', help='실제 입력 없이 미리보기')
    parser.add_argument('--since',   type=str, default=None, help='이후 거래만 (예: 2026-01-01)')
    args = parser.parse_args()

    run(dry_run=args.dry_run, since=args.since)
