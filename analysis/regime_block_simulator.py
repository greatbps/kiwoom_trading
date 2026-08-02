"""
REGIME_BLOCK Simulation (MVP)

장 종료 후 배치 — v1.4 Regime Gate([REGIME_BLOCK])와 SMC 오후 컷오프
([AFTERNOON_CUTOFF_BLOCK])로 진입하지 못한 후보를, 차단 시점 실제 가격을
Entry로 삼아 "현재 운영 중인 Exit Logic"(trading/exit_logic_optimized.py,
OptimizedExitLogic) 그대로 재생해서 정책이 유효했는지 정량 검증한다.

실거래 코드는 전혀 수정하지 않는다. 읽기 전용 배치 분석.
EC_HALT 등 운영 장애로 인한 차단(GLOBAL_GATE_BLOCKED)은 대상에서 제외 —
정책적 차단(REGIME_BLOCK/AFTERNOON_CUTOFF_BLOCK)만 분석한다.

실행:
    python3 -m analysis.regime_block_simulator          # 오늘
    python3 -m analysis.regime_block_simulator --date 2026-07-13
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from utils.config_loader import load_config
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from trading.exit_logic_optimized import OptimizedExitLogic
import trading.exit_logic_optimized as _exit_mod


_BLOCK_LINE_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - INFO - '
    r'\[(?P<tag>REGIME_BLOCK|AFTERNOON_CUTOFF_BLOCK)\] (?P<code>\d{6})'
    r'(?:.*?regime=(?P<regime>\S+) score=(?P<score>-?\d+))?'
)

# 🔧 2026-07-27: Regime Gate E3 Evidence — watchlist.json 기반 best-effort 종목명 조회
_STOCK_NAME_CACHE: Dict[str, str] = {}


def _lookup_stock_name(code: str) -> str:
    """data/watchlist.json에서 종목명 조회 (당일/최근 종목만 커버 — 과거 종목은 코드로 대체)."""
    if not _STOCK_NAME_CACHE:
        try:
            import json as _json
            wl = _json.loads((BASE / 'data' / 'watchlist.json').read_text(encoding='utf-8'))
            for s in wl:
                if s.get('stock_code'):
                    _STOCK_NAME_CACHE[s['stock_code']] = s.get('stock_name', s['stock_code'])
        except Exception:
            pass
    return _STOCK_NAME_CACHE.get(code, code)


class _FrozenDateTime(datetime):
    """exit_logic_optimized.py의 datetime.now() 호출만 시뮬레이션 시점으로 고정.
    다른 클래스메서드(fromisoformat 등)는 실제 datetime 그대로 상속돼서 동작함.
    실거래 코드(exit_logic_optimized.py) 자체는 건드리지 않고, 이 스크립트
    프로세스 안에서만 모듈 참조를 임시 교체한다."""
    _frozen_now: Optional[datetime] = None

    @classmethod
    def now(cls, tz=None):
        return cls._frozen_now


# ─── 로그에서 차단 후보 추출 ──────────────────────────────────────

def _parse_blocked_candidates(log_path: Path) -> Dict[str, Dict[str, Any]]:
    """종목별 '최초' 차단 시각 + 태그 추출 (같은 종목 반복 차단은 첫 건만 — 실제 거래는 1회 진입 가정)."""
    first_block: Dict[str, Dict[str, Any]] = {}
    if not log_path.exists():
        return first_block
    with open(log_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = _BLOCK_LINE_RE.match(line)
            if not m:
                continue
            code = m.group('code')
            if code in first_block:
                continue
            first_block[code] = {
                'tag': m.group('tag'),
                'time': datetime.strptime(m.group('ts'), '%Y-%m-%d %H:%M:%S'),
                'regime': m.group('regime'),
                'score': int(m.group('score')) if m.group('score') else None,
            }
    return first_block


# ─── 가격 데이터 ──────────────────────────────────────────────────

def _fetch_bars(code: str, target_date: date) -> Optional[pd.DataFrame]:
    from datetime import timedelta
    for suffix in ['.KS', '.KQ']:
        try:
            t = yf.Ticker(f'{code}{suffix}')
            df = t.history(start=str(target_date), end=str(target_date + timedelta(days=1)), interval='5m')
            if df.empty:
                continue
            df = df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low',
                                     'Close': 'close', 'Volume': 'volume'})
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df['datetime'] = df.index
            return df.reset_index(drop=True)
        except Exception:
            continue
    return None


# 🔧 2026-07-27: 고정 N거래일 후 종가 + MFE/MAE (Regime Gate E3 Evidence, 참고 지표)
def _fetch_daily_forward(code: str, block_date: date) -> Optional[Dict[str, Any]]:
    """block_date 포함 이후 일봉을 최대 30영업일 범위로 조회해
    1/3/5/10/20거래일 후 종가 및 그 구간의 MFE(최고가)/MAE(최저가)를 계산."""
    from datetime import timedelta
    end = block_date + timedelta(days=45)  # 20거래일 확보용 여유(주말/공휴일 감안)
    for suffix in ['.KS', '.KQ']:
        try:
            t = yf.Ticker(f'{code}{suffix}')
            df = t.history(start=str(block_date), end=str(end), interval='1d', auto_adjust=True)
            if df.empty or len(df) < 2:
                continue
            df = df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'})
            closes: Dict[int, float] = {}
            for horizon in (1, 3, 5, 10, 20):
                if horizon < len(df):
                    closes[horizon] = float(df['close'].iloc[horizon])
            window = df.iloc[1:21]  # 진입 다음날부터 최대 20거래일
            if window.empty:
                return {'closes': closes, 'max_high': None, 'min_low': None}
            return {
                'closes': closes,
                'max_high': float(window['high'].max()),
                'min_low': float(window['low'].min()),
            }
        except Exception:
            continue
    return None


# ─── Virtual Trade 재생 (실제 Exit Logic 그대로) ─────────────────

def _simulate_trade(
    code: str, block_time: datetime, tag: str, df: pd.DataFrame,
    analyzer: EntryTimingAnalyzer, exit_logic: OptimizedExitLogic, signal_config: dict,
    regime: Optional[str] = None, regime_score: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    bars_after = df[df['datetime'] >= block_time]
    if len(bars_after) < 2:
        return None

    entry_idx = bars_after.index[0]
    entry_price = float(df.loc[entry_idx, 'close'])
    entry_time = df.loc[entry_idx, 'datetime'].to_pydatetime()

    position = {
        'entry_price': entry_price,
        'entry_time': entry_time,
        'highest_price': entry_price,
        'partial_exit_stage': 0,
        'strategy': 'smc',
        'strategy_horizon': 'INTRADAY',
        'trailing_active': False,
        'trailing_stop_price': None,
    }

    for i in range(int(entry_idx) + 1, len(df)):
        window = df.loc[:i].copy()
        window = analyzer.calculate_vwap(window, use_rolling=True, rolling_window=20)
        window = analyzer.calculate_atr(window)
        window = analyzer.calculate_rsi(window)
        window = analyzer.generate_signals(window, **signal_config)

        current_price = float(df.loc[i, 'close'])
        sim_now = df.loc[i, 'datetime'].to_pydatetime()

        _FrozenDateTime._frozen_now = sim_now
        with patch.object(_exit_mod, 'datetime', _FrozenDateTime):
            try:
                should_exit, reason, _info = exit_logic.check_exit_signal(position, current_price, window)
            except Exception as e:
                should_exit, reason = False, f'SIM_ERROR: {e}'

        if should_exit:
            return _build_result(code, tag, block_time, entry_price, entry_time, current_price, sim_now, reason,
                                  regime, regime_score)

    # 데이터 끝까지 청산 신호 없으면 마지막 바로 강제 청산 (당일 데이터 한계)
    last_price = float(df['close'].iloc[-1])
    last_time = df['datetime'].iloc[-1].to_pydatetime()
    return _build_result(code, tag, block_time, entry_price, entry_time, last_price, last_time, 'EOD_DATA_END',
                          regime, regime_score)


def _build_result(code, tag, block_time, entry_price, entry_time, exit_price, exit_time, reason,
                   regime=None, regime_score=None) -> Dict[str, Any]:
    holding_min = round((exit_time - entry_time).total_seconds() / 60)
    return_pct = (exit_price - entry_price) / entry_price * 100
    return {
        'code': code, 'block_tag': tag, 'block_time': block_time.strftime('%H:%M:%S'),
        'entry_price': entry_price, 'exit_price': exit_price, 'holding_min': holding_min,
        'return_pct': return_pct, 'exit_reason': reason,
        'result': 'WIN' if return_pct > 0 else ('LOSS' if return_pct < 0 else 'FLAT'),
        'regime': regime, 'regime_score': regime_score,
        'stock_name': _lookup_stock_name(code),
    }


# ─── 리포트 ───────────────────────────────────────────────────────

def run_simulation(target_date: Optional[date] = None) -> Dict[str, Any]:
    target_date = target_date or date.today()
    log_path = BASE / 'logs' / f'auto_trading_{target_date.strftime("%Y%m%d")}.log'
    blocked = _parse_blocked_candidates(log_path)

    cfg = load_config(str(BASE / 'config' / 'strategy_hybrid.yaml'))
    analyzer = EntryTimingAnalyzer(**cfg.get_analyzer_config())
    exit_logic = OptimizedExitLogic(cfg)
    signal_config = cfg.get_signal_generation_config()

    trades: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for code, info in blocked.items():
        df = _fetch_bars(code, target_date)
        if df is None or len(df) < 3:
            skipped.append(code)
            continue
        result = _simulate_trade(code, info['time'], info['tag'], df, analyzer, exit_logic, signal_config,
                                  regime=info.get('regime'), regime_score=info.get('score'))
        if result:
            # 🔧 2026-07-27: 고정 N거래일 후 수익률 + MFE/MAE (참고 지표 — exit-logic 재생 결과를 대체하지 않음)
            fwd = _fetch_daily_forward(code, target_date)
            if fwd:
                entry_price = result['entry_price']
                for horizon in (1, 3, 5, 10, 20):
                    close_n = fwd['closes'].get(horizon)
                    result[f'ret_{horizon}d'] = round((close_n - entry_price) / entry_price * 100, 2) if close_n else None
                result['mfe_pct'] = round((fwd['max_high'] - entry_price) / entry_price * 100, 2) if fwd['max_high'] else None
                result['mae_pct'] = round((fwd['min_low'] - entry_price) / entry_price * 100, 2) if fwd['min_low'] else None
            trades.append(result)
        else:
            skipped.append(code)

    wins = [t for t in trades if t['result'] == 'WIN']
    losses = [t for t in trades if t['result'] == 'LOSS']
    avg_return = sum(t['return_pct'] for t in trades) / len(trades) if trades else 0.0
    opportunity_cost = sum(t['return_pct'] for t in wins)     # 놓친 수익 (양수 합)
    avoided_loss = sum(t['return_pct'] for t in losses)       # 막은 손실 (음수 합, 표시는 음수 그대로)
    net_policy_value = abs(avoided_loss) - opportunity_cost   # 양수=정책유효, 음수=과도차단

    if not trades:
        result_label = 'NO_DATA'
    elif net_policy_value >= 0:
        result_label = 'REGIME EFFECTIVE'
    else:
        result_label = 'REGIME TOO STRICT'

    return {
        'date': str(target_date),
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'blocked_trades': len(trades),
        'skipped_no_data': skipped,
        'virtual_wins': len(wins),
        'virtual_losses': len(losses),
        'win_rate_pct': (len(wins) / len(trades) * 100) if trades else 0.0,
        'avg_return_pct': avg_return,
        'opportunity_cost_pct': opportunity_cost,
        'avoided_loss_pct': avoided_loss,
        'net_policy_value_pct': net_policy_value,
        'result': result_label,
        'trades': trades,
    }


def print_report(report: Dict[str, Any]) -> None:
    print(f"\n{'='*41}")
    print("REGIME BLOCK SIMULATION")
    print(f"Date : {report['date']}")
    print(f"{'='*41}\n")

    print(f"Blocked Trades : {report['blocked_trades']}\n")
    print(f"Virtual Wins   : {report['virtual_wins']}")
    print(f"Virtual Losses : {report['virtual_losses']}\n")
    print(f"Win Rate       : {report['win_rate_pct']:.1f}%\n")
    print(f"Average Return : {report['avg_return_pct']:+.2f}%\n")
    print(f"Opportunity Cost : {report['opportunity_cost_pct']:+.2f}%\n")
    print(f"Avoided Loss     : {report['avoided_loss_pct']:+.2f}%\n")
    print(f"Net Policy Value : {report['net_policy_value_pct']:+.2f}%\n")
    print("Result\n")
    print(f"{report['result']}")
    print(f"{'='*41}")

    if report['skipped_no_data']:
        print(f"\n(가격 데이터 없어 제외된 종목: {', '.join(report['skipped_no_data'])})")

    if report['trades']:
        print("\n상세:")
        for t in sorted(report['trades'], key=lambda x: x['block_time']):
            print(
                f"  {t['code']} | 차단={t['block_time']}({t['block_tag']}) | "
                f"진입={t['entry_price']:,.0f} 청산={t['exit_price']:,.0f} | "
                f"{t['holding_min']}분 | {t['return_pct']:+.2f}% | {t['result']} | {t['exit_reason']}"
            )


# ─── 누적 KPI (CSV) ───────────────────────────────────────────────

HISTORY_CSV_PATH = BASE / 'analysis' / 'data' / 'regime_block_history.csv'
_CSV_FIELDS = [
    'trade_date', 'blocked_trades', 'virtual_wins', 'virtual_losses', 'win_rate',
    'average_return', 'opportunity_cost', 'avoided_loss', 'net_policy_value', 'result',
]


def append_to_history_csv(report: Dict[str, Any], path: Path = HISTORY_CSV_PATH) -> None:
    """오늘 결과를 CSV에 1행 추가. 같은 날짜로 재실행하면 기존 행을 교체(중복 방지)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, str]] = []
    if path.exists():
        with open(path, newline='', encoding='utf-8') as f:
            rows = [r for r in csv.DictReader(f) if r.get('trade_date') != report['date']]

    result_code = 'EFFECTIVE' if report['result'] == 'REGIME EFFECTIVE' else (
        'TOO_STRICT' if report['result'] == 'REGIME TOO STRICT' else report['result']
    )
    rows.append({
        'trade_date':       report['date'],
        'blocked_trades':   report['blocked_trades'],
        'virtual_wins':     report['virtual_wins'],
        'virtual_losses':   report['virtual_losses'],
        'win_rate':         f"{report['win_rate_pct']:.4f}",
        'average_return':   f"{report['avg_return_pct']:.4f}",
        'opportunity_cost': f"{report['opportunity_cost_pct']:.4f}",
        'avoided_loss':     f"{report['avoided_loss_pct']:.4f}",
        'net_policy_value': f"{report['net_policy_value_pct']:.4f}",
        'result':           result_code,
    })
    rows.sort(key=lambda r: r['trade_date'])

    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


# 🔧 2026-07-27: 종목별 상세 CSV (regime_tracking_schema.md 참조) — 일별 집계 CSV와 별개, 누적만 함
DETAIL_CSV_PATH = BASE / 'analysis' / 'data' / 'regime_block_detail.csv'
_DETAIL_FIELDS = [
    'trade_date', 'code', 'stock_name', 'block_time', 'block_tag',
    'regime', 'regime_score', 'entry_price',
    'ret_1d', 'ret_3d', 'ret_5d', 'ret_10d', 'ret_20d',
    'mfe_pct', 'mae_pct', 'exit_logic_result', 'exit_logic_return_pct',
]


def append_to_detail_csv(report: Dict[str, Any], path: Path = DETAIL_CSV_PATH) -> None:
    """오늘 시뮬레이션된 종목별 상세를 CSV에 append (같은 날짜 재실행 시 그 날짜분만 교체)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, str]] = []
    if path.exists():
        with open(path, newline='', encoding='utf-8') as f:
            rows = [r for r in csv.DictReader(f) if r.get('trade_date') != report['date']]

    for t in report['trades']:
        rows.append({
            'trade_date': report['date'], 'code': t['code'], 'stock_name': t.get('stock_name', t['code']),
            'block_time': t['block_time'], 'block_tag': t['block_tag'],
            'regime': t.get('regime') or '', 'regime_score': t.get('regime_score') if t.get('regime_score') is not None else '',
            'entry_price': t['entry_price'],
            'ret_1d': t.get('ret_1d', ''), 'ret_3d': t.get('ret_3d', ''), 'ret_5d': t.get('ret_5d', ''),
            'ret_10d': t.get('ret_10d', ''), 'ret_20d': t.get('ret_20d', ''),
            'mfe_pct': t.get('mfe_pct', ''), 'mae_pct': t.get('mae_pct', ''),
            'exit_logic_result': t['result'], 'exit_logic_return_pct': round(t['return_pct'], 2),
        })
    rows.sort(key=lambda r: (r['trade_date'], r['code']))

    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=_DETAIL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def load_history(n: int, path: Path = HISTORY_CSV_PATH) -> List[Dict[str, Any]]:
    """최근 n거래일 이력을 날짜 오름차순으로 반환 (CSV 없으면 빈 리스트)."""
    if not path.exists():
        return []
    with open(path, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r['trade_date'])
    tail = rows[-n:] if n > 0 else rows
    for r in tail:
        r['blocked_trades'] = int(r['blocked_trades'])
        r['virtual_wins']   = int(r['virtual_wins'])
        r['virtual_losses'] = int(r['virtual_losses'])
        for k in ('win_rate', 'average_return', 'opportunity_cost', 'avoided_loss', 'net_policy_value'):
            r[k] = float(r[k])
    return tail


def aggregate_history(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """거래건수 가중 평균으로 여러 날짜를 하나의 누적 통계로 합산."""
    days = len(rows)
    blocked = sum(r['blocked_trades'] for r in rows)
    wins = sum(r['virtual_wins'] for r in rows)
    opportunity_cost = sum(r['opportunity_cost'] for r in rows)
    avoided_loss = sum(r['avoided_loss'] for r in rows)
    net_policy_value = abs(avoided_loss) - opportunity_cost
    avg_return = (
        sum(r['average_return'] * r['blocked_trades'] for r in rows) / blocked
        if blocked > 0 else 0.0
    )
    win_rate = (wins / blocked * 100) if blocked > 0 else 0.0
    status = 'EFFECTIVE' if net_policy_value >= 0 else 'TOO_STRICT'
    return {
        'days': days, 'blocked_trades': blocked, 'win_rate_pct': win_rate,
        'avg_return_pct': avg_return, 'opportunity_cost_pct': opportunity_cost,
        'avoided_loss_pct': avoided_loss, 'net_policy_value_pct': net_policy_value,
        'status': status,
    }


def print_summary(agg: Dict[str, Any], label: str) -> None:
    print(f"\n{'='*41}")
    print(f"{label}")
    print(f"{'='*41}\n")
    print(f"Days Analysed     : {agg['days']}\n")
    print(f"Blocked Trades    : {agg['blocked_trades']}\n")
    print(f"Virtual Win Rate  : {agg['win_rate_pct']:.1f}%\n")
    print(f"Average Return    : {agg['avg_return_pct']:+.2f}%\n")
    print(f"Opportunity Cost  : {agg['opportunity_cost_pct']:+.2f}%\n")
    print(f"Avoided Loss      : {agg['avoided_loss_pct']:+.2f}%\n")
    print(f"Net Policy Value  : {agg['net_policy_value_pct']:+.2f}%\n")
    print(f"Policy Status     : {agg['status']}")
    print(f"{'='*41}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='REGIME_BLOCK Simulation (MVP + 누적 KPI)')
    parser.add_argument('--date', default=None, help='날짜 (YYYY-MM-DD). 기본: 오늘')
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()
    report = run_simulation(target)
    print_report(report)

    append_to_history_csv(report)
    append_to_detail_csv(report)

    hist_20 = load_history(20)
    hist_5 = load_history(5)

    if hist_20:
        print_summary(aggregate_history(hist_20), f"{len(hist_20)} Trading Day Summary")
    if hist_5:
        print_summary(aggregate_history(hist_5), f"Recent {len(hist_5)} Trading Day Trend")
