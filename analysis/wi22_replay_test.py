"""
analysis/wi22_replay_test.py — WI-22 §11 Replay Test

실제 오늘(DB에 저장된) HTS Candidate에 대해 "동일 시점 Market Data"를 다시
조회해 Monitor를 재실행하고, 당시 저장된 monitor_status와 비교한다.

주의(WI-22 §10): 과거 데이터로 HTS 후보를 새로 만들어내는 것이 아니다 — 이미
실제로 발생한 오늘의 Candidate(research.condition_candidates)만 대상으로 하고,
그 시점에 실제로 사용됐던 것과 동일한 API 호출(get_ohlcv_data(period='D',
count=30), main_auto_trading.py:3483)로 차트를 다시 받아 재평가한다. 일봉은
당일 장마감 후 값이 확정되므로, 같은 날 재조회해도 사용된 봉 구성이 동일하다
(단, count=30이라 스캔 시점에 따라 '오늘' 봉이 미완성 상태였을 수 있음 — 그
경우의 불일치는 §11의 "원인 조사 대상"으로 별도 표기한다).

Live 트레이딩 프로세스와 별개 프로세스로 실행한다(읽기 전용 API 호출만, 신규
주문/설정 변경 없음). API 부하 방지를 위해 표본을 제한한다(--limit).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import psycopg2
import yaml


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _fetch_sample_candidates(conn, date_str: str, limit: int):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.candidate_id, c.stock_code, c.condition_seq, c.observed_at,
                   me.monitor_status, me.monitor_state
            FROM research.condition_candidates c
            JOIN research.strategy_monitor_events me ON me.candidate_id = c.candidate_id
            WHERE c.observed_at::date = %s
            ORDER BY c.observed_at DESC
            LIMIT %s
        """, (date_str, limit))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def run(date_str: str, limit: int, out_path: Optional[str]):
    from kiwoom_api import KiwoomAPI
    from analyzers.strategy_monitors.router import route_from_chart_data

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = yaml.safe_load(open(os.path.join(root, 'config', 'strategy_hybrid.yaml')))

    conn = _get_conn()
    sample = _fetch_sample_candidates(conn, date_str, limit)
    conn.close()

    api = KiwoomAPI()
    rows = []
    for row in sample:
        stock_code = row['stock_code']
        seq = row['condition_seq']
        try:
            result = api.get_ohlcv_data(stock_code, period='D', count=30)
            chart_data = result.get('data', []) if result and result.get('return_code') == 0 else None
        except Exception as e:
            chart_data = None
            rows.append({**row, 'replay_status': 'API_ERROR', 'replay_monitor_status': None,
                         'replay_monitor_state': None, 'match': False, 'note': str(e)[:200]})
            continue

        if not chart_data:
            rows.append({**row, 'replay_status': 'NO_CHART_DATA', 'replay_monitor_status': None,
                         'replay_monitor_state': None, 'match': False, 'note': 'API 응답 없음'})
            time.sleep(0.3)
            continue

        replayed = route_from_chart_data(seq, stock_code, chart_data, cfg)
        if replayed.data_quality == 'NO_CODE':
            replay_status = 'NOT_IMPLEMENTED'
        elif replayed.data_quality == 'ERROR':
            replay_status = 'ERROR'
        elif replayed.signal:
            replay_status = 'SIGNAL'
        else:
            replay_status = 'NO_SIGNAL'

        match = (replay_status == row['monitor_status'])
        rows.append({
            **row, 'replay_status': replay_status,
            'replay_monitor_status': replay_status,
            'replay_monitor_state': replayed.monitor_state,
            'match': match,
            'note': '' if match else f"원본={row['monitor_status']}/{row['monitor_state']} 재현={replay_status}/{replayed.monitor_state}",
        })
        time.sleep(0.3)  # API 부하 방지

    total = len(rows)
    matched = sum(1 for r in rows if r['match'])
    determinism = round(matched / total * 100, 2) if total else None

    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            fieldnames = list(rows[0].keys()) if rows else [
                'candidate_id', 'stock_code', 'condition_seq', 'observed_at',
                'monitor_status', 'monitor_state', 'replay_status',
                'replay_monitor_status', 'replay_monitor_state', 'match', 'note']
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

    print(f"표본={total} 일치={matched} Determinism={determinism}%")
    return rows, determinism


def main():
    ap = argparse.ArgumentParser(description='WI-22 §11 Replay Test')
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (기본: 오늘)')
    ap.add_argument('--limit', type=int, default=15)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    from datetime import date
    date_str = a.date or date.today().isoformat()
    run(date_str, a.limit, a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
