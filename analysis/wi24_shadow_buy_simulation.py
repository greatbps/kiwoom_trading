"""
analysis/wi24_shadow_buy_simulation.py — WI-24 §11 Shadow BUY Simulation

seq32~39 Strategy Signal이 (가상으로) Ranking → Market Regime Gate → Risk를
거친다면 어디까지 통과했을지 실제 프로덕션 클래스(ScoreEngine/RegimeAnalyzer/
RiskManager)를 그대로 호출해 계산한다. 실제 execute_buy()는 절대 호출하지
않는다(코드에 해당 호출 자체가 없음).

⚠️ risk_log.json 안전장치: RiskManager.load()는 날짜가 바뀌면 내부적으로
   self.save()를 호출해 storage_path 파일에 즉시 쓴다. 실제 운영 파일
   data/risk_log.json에 절대 쓰지 않기 위해, 이 스크립트는 그 파일을 읽기
   전용으로 복사한 임시 파일(data/wi24_shadow_risk_log.json)에 대해서만
   RiskManager를 실행한다 — CLAUDE.md 절대금지("risk_log.json 임의 조작") 위반
   방지.

실행:
    python3 -m analysis.wi24_shadow_buy_simulation --date 2026-08-12
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import psycopg2
import yaml

SHADOW_RISK_LOG = 'data/wi24_shadow_risk_log.json'
ASSUMED_POSITION_SIZE_PCT = 0.10  # 실측 EDT Sizer 값이 없어 총자산의 10%를 가정
                                    # (SMC 표준 사이즈 체계 문서상 B급 fallback 20%보다
                                    # 보수적인 값 — Shadow는 실제 Sizer를 호출하지 않으므로
                                    # 명시적 가정임을 결과에 함께 기록한다)


def classify_shadow_result(rank_pass: bool, gate_pass: bool, risk_pass: bool) -> str:
    """§8/§11 판정 로직 — 순수 함수로 분리해 단독 테스트 가능하게 함."""
    if not rank_pass:
        return 'SHADOW_RANK_BLOCK'
    if not gate_pass:
        return 'SHADOW_GATE_BLOCK'
    if not risk_pass:
        return 'SHADOW_RISK_BLOCK'
    return 'SHADOW_BUY_ALLOWED'


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _fetch_all_signals(conn, date_str: str):
    """399건 전체(§6) - signal_id 단위."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.signal_id, s.condition_seq, s.strategy_name, s.stock_code, s.observed_at
            FROM research.strategy_signals s
            WHERE s.observed_at::date = %s
            ORDER BY s.condition_seq, s.observed_at
        """, (date_str,))
        return cur.fetchall()


def _shadow_rank(symbols, cfg) -> Dict[str, dict]:
    from trading.score_engine import ScoreEngine
    from analyzers.market.regime_analyzer import _parse_daily_df
    from backtest.daily_scan import load_daily_watchlist
    from kiwoom_api import KiwoomAPI

    api = KiwoomAPI()
    daily_smc_symbols = set(load_daily_watchlist())
    engine = ScoreEngine(daily_smc_symbols=daily_smc_symbols)

    out = {}
    for sym in symbols:
        try:
            raw = api.get_daily_chart(stock_code=sym)
            df = _parse_daily_df(raw)
        except Exception:
            df = None
        s = engine.score(sym, df)
        out[sym] = s
        time.sleep(0.2)
    return out, engine.min_score


def _shadow_regime(cfg) -> dict:
    from analyzers.market.regime_analyzer import RegimeAnalyzer
    from kiwoom_api import KiwoomAPI

    api = KiwoomAPI()
    analyzer = RegimeAnalyzer(api, cfg)
    decision = analyzer.evaluate(force=True)
    return {
        'regime': decision.regime, 'allow_new_entries': decision.allow_new_entries,
        'allowed_min_grade': decision.allowed_min_grade,
        'size_multiplier': decision.size_multiplier,
        'reasons': decision.reasons,
    }


def _shadow_risk(cfg) -> dict:
    from core.risk_manager import RiskManager

    real_log = 'data/risk_log.json'
    if os.path.exists(real_log):
        shutil.copy(real_log, SHADOW_RISK_LOG)  # 읽기 전용 스냅샷 — 실제 파일 절대 미변경

    snap_path = 'data/account_snapshot.json'
    snap = json.load(open(snap_path, encoding='utf-8')) if os.path.exists(snap_path) else {}
    current_balance = float(snap.get('deposit', 0))
    positions_value = float(snap.get('holding_value', 0))
    position_count = len(snap.get('holdings', []))
    total_assets = float(snap.get('total_assets', current_balance + positions_value))
    position_size = total_assets * ASSUMED_POSITION_SIZE_PCT

    rm = RiskManager(initial_balance=current_balance, storage_path=SHADOW_RISK_LOG, config=cfg)
    can_enter, reason = rm.can_open_position(
        current_balance=current_balance, current_positions_value=positions_value,
        position_count=position_count, position_size=position_size,
    )
    return {
        'can_enter': can_enter, 'reason': reason,
        'account_snapshot_used': snap_path, 'current_balance': current_balance,
        'positions_value': positions_value, 'position_count': position_count,
        'assumed_position_size': position_size,
        'assumed_position_size_pct': ASSUMED_POSITION_SIZE_PCT,
        'shadow_risk_log_used': SHADOW_RISK_LOG,
    }


def run(date_str: str, out_path: Optional[str]):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = yaml.safe_load(open(os.path.join(root, 'config', 'strategy_hybrid.yaml')))

    conn = _get_conn()
    sig_rows = _fetch_all_signals(conn, date_str)
    conn.close()

    unique_symbols = sorted(set(r[3] for r in sig_rows))
    # Rank/Gate/Risk는 종목-일 단위 상태(같은 날 같은 종목이면 몇 번 신호가 나든
    # 동일 점수/게이트)이므로 종목별로 한 번만 계산한 뒤 399건 각각에 조인한다
    # (같은 계산을 399번 반복 호출하지 않음 — API 부하 방지, §7 "숫자를 추정하지
    # 않는다"는 원칙은 유지하면서 중복 호출만 피한 것).
    rank_scores, min_score = _shadow_rank(unique_symbols, cfg)
    regime = _shadow_regime(cfg)
    risk = _shadow_risk(cfg)

    rows = []
    for signal_id, seq, strategy_name, sym, observed_at in sig_rows:
        s = rank_scores.get(sym, {'total': 0, 'smc': 0, 'volume': 0, 'ma50': 0, 'pattern': 0})
        shadow_rank_pass = s['total'] >= min_score
        shadow_gate_pass = regime['allow_new_entries']  # 최소등급은 SMC grade 체계라 seq32-39엔 적용 불가(§16 기록)
        shadow_risk_pass = risk['can_enter']

        result = classify_shadow_result(shadow_rank_pass, shadow_gate_pass, shadow_risk_pass)

        rows.append({
            'signal_id': str(signal_id), 'seq': seq, 'strategy': strategy_name,
            'symbol': sym, 'signal_timestamp': str(observed_at),
            'shadow_rank_score_total': s['total'], 'shadow_rank_pass': shadow_rank_pass,
            'shadow_gate_regime': regime['regime'], 'shadow_gate_pass': shadow_gate_pass,
            'shadow_risk_pass': shadow_risk_pass, 'shadow_risk_reason': risk['reason'],
            'shadow_result': result,
        })

    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    summary = {
        'total_signals': len(rows),
        'shadow_rank_pass': sum(1 for r in rows if r['shadow_rank_pass']),
        'shadow_gate_pass': sum(1 for r in rows if r['shadow_gate_pass']),
        'shadow_buy_allowed': sum(1 for r in rows if r['shadow_result'] == 'SHADOW_BUY_ALLOWED'),
        'regime': regime, 'risk': risk,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return rows, summary


def main():
    ap = argparse.ArgumentParser(description='WI-24 §11 Shadow BUY Simulation')
    ap.add_argument('--date', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    run(a.date, a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
