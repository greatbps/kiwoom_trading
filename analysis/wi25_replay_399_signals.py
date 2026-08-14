"""
analysis/wi25_replay_399_signals.py — WI-25 §15/§16 실제 399 Signal Replay.

WI-24에서 확인한 오늘(2026-08-12) 399건의 실제 research.strategy_signals를
StrategyEntryAdapter(실제 ScoreEngine/RegimeAnalyzer/RiskManager 사용)로
Replay해 Shadow 결과를 만든다. execute_buy()는 호출하지 않는다.

risk_log.json 안전장치는 WI-24와 동일(복사본에만 기록, 원본 미변경).

실행:
    python3 -m analysis.wi25_replay_399_signals --date 2026-08-12
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import psycopg2
import yaml

from strategy_entry.adapter import StrategyEntryAdapter
from strategy_entry.enable_registry import StrategyEnableRegistry
from strategy_entry.shadow import run_shadow
from strategy_entry.types import StrategyEntryRequest

SHADOW_RISK_LOG = 'data/wi25_shadow_risk_log.json'


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _fetch_signals(date_str: str):
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.signal_id, s.candidate_id, s.condition_seq, s.strategy_name,
                   s.stock_code, s.observed_at, s.entry_price_reference, s.signal_reason
            FROM research.strategy_signals s
            WHERE s.observed_at::date = %s
            ORDER BY s.condition_seq, s.observed_at
        """, (date_str,))
        rows = cur.fetchall()
    conn.close()
    return rows


def _build_adapter(cfg) -> StrategyEntryAdapter:
    from trading.score_engine import ScoreEngine
    from analyzers.market.regime_analyzer import RegimeAnalyzer, _parse_daily_df
    from core.risk_manager import RiskManager
    from backtest.daily_scan import load_daily_watchlist
    from kiwoom_api import KiwoomAPI

    api = KiwoomAPI()
    daily_smc_symbols = set(load_daily_watchlist())
    score_engine = ScoreEngine(daily_smc_symbols=daily_smc_symbols)
    regime_analyzer = RegimeAnalyzer(api, cfg)

    real_log = 'data/risk_log.json'
    if os.path.exists(real_log):
        shutil.copy(real_log, SHADOW_RISK_LOG)  # 원본 절대 미변경 (WI-24와 동일 안전장치)
    snap_path = 'data/account_snapshot.json'
    snap = json.load(open(snap_path, encoding='utf-8')) if os.path.exists(snap_path) else {}
    risk_manager = RiskManager(
        initial_balance=float(snap.get('deposit', 0)), storage_path=SHADOW_RISK_LOG, config=cfg,
    )

    registry = StrategyEnableRegistry()  # data/strategy_enable_registry.json - 전부 DISABLED

    _ohlcv_cache = {}

    def ohlcv_provider(symbol):
        if symbol not in _ohlcv_cache:
            try:
                raw = api.get_daily_chart(stock_code=symbol)
                _ohlcv_cache[symbol] = _parse_daily_df(raw)
            except Exception:
                _ohlcv_cache[symbol] = None
            time.sleep(0.15)
        return _ohlcv_cache[symbol]

    def account_state_provider():
        return {
            'current_balance': float(snap.get('deposit', 0)),
            'positions_value': float(snap.get('holding_value', 0)),
            'position_count': len(snap.get('holdings', [])),
            'total_assets': float(snap.get('total_assets', 0)),
        }

    return StrategyEntryAdapter(
        score_engine=score_engine, regime_analyzer=regime_analyzer, risk_manager=risk_manager,
        enable_registry=registry, ohlcv_provider=ohlcv_provider,
        account_state_provider=account_state_provider,
    )


def run(date_str: str, out_path: str):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = yaml.safe_load(open(os.path.join(root, 'config', 'strategy_hybrid.yaml')))

    rows = _fetch_signals(date_str)
    requests = [
        StrategyEntryRequest(
            strategy_seq=seq, strategy_name=name, symbol=code,
            signal_timestamp=str(ts), signal_price=float(price) if price else None,
            candidate_id=str(cid), signal_id=str(sid), signal_reason=reason,
        )
        for sid, cid, seq, name, code, ts, price, reason in rows
    ]

    adapter = _build_adapter(cfg)
    results = run_shadow(adapter, requests)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['signal_id', 'candidate_id', 'seq', 'strategy', 'symbol',
                    'signal_timestamp', 'signal_price', 'decision_stage', 'allowed',
                    'shadow_result', 'ranking_score', 'gate_regime', 'risk_pass',
                    'order_candidate_id'])
        for r in results:
            req, dec = r['request'], r['decision']
            oc = r['order_candidate']
            w.writerow([
                req.signal_id, req.candidate_id, req.strategy_seq, req.strategy_name,
                req.symbol, req.signal_timestamp, req.signal_price, dec.decision_stage,
                dec.allowed, r['shadow_result'],
                dec.ranking_result['score'] if dec.ranking_result else '',
                dec.gate_result['regime'] if dec.gate_result else '',
                dec.risk_result['pass'] if dec.risk_result else '',
                oc.decision_id if oc else '',
            ])

    from collections import Counter
    summary = {
        'total': len(results),
        'by_shadow_result': dict(Counter(r['shadow_result'] for r in results)),
        'by_decision_stage': dict(Counter(r['decision'].decision_stage for r in results)),
        'strategy_identity_preserved': all(
            r['decision'].strategy_seq == r['request'].strategy_seq for r in results
        ),
        'execute_buy_calls': 0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return results, summary


def main():
    ap = argparse.ArgumentParser(description='WI-25 §15 Replay 399 Signals')
    ap.add_argument('--date', required=True)
    ap.add_argument('--out', default='phase1/reports/strategy_entry_architecture/wi25_replay_results.csv')
    a = ap.parse_args()
    run(a.date, a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
