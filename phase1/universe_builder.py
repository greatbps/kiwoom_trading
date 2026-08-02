"""
Iteration 7-2 — 유니버스 확대 설계

━━━ 원천 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  pykrx 로 KRX 전체 목록을 받으려 했으나 이 환경에서 응답이 없다
  (`get_market_ticker_list` 0건). 따라서 **시스템이 실제로 관측한 종목**을
  원천으로 쓴다.

      trades · signal_rejections · blocked_trades · research.candidates
      signal_events · filtered_candidates · log_trade_events
      + DEFAULT_CANDIDATES 78

  합집합 529종목. 여기서 OHLCV 를 받을 수 있고 유동성 기준을 통과한
  종목만 남긴다.

  ⚠️ 이 원천 자체에 편향이 있다. 조건검색식이 뽑았거나 거래된 종목이라
     "시스템이 이미 관심을 가진" 집합이다. KRX 전체의 무작위 표본이
     아니다. Survivorship Bias 보고서에 이 한계를 명시한다.

━━━ 계층 기준 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  유동성(일평균 거래대금) 내림차순. 78 → 120 → 160 → 220.
  **DEFAULT_CANDIDATES 78 은 무조건 포함**한다 (기준선 재현 보장).

사용법:
    python -m phase1.universe_builder            # 계층 생성 + 캐시
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backtest.loader import load_multi
from backtest.scanner import DEFAULT_CANDIDATES

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'universe')
os.makedirs(OUT, exist_ok=True)
POOL_TXT = os.path.join(OUT, 'pool.txt')
CACHE_PKL = os.path.join(OUT, 'ohlcv_expanded.pkl')
TIERS_JSON = os.path.join(OUT, 'tiers.json')

START = '2024-01-01'      # 워밍업 포함
END = '2026-07-30'

TIERS = [78, 120, 160, 220]

# 유동성 하한 — 일평균 거래대금.
# ⚠️ 임의값이 아니다. DEFAULT_CANDIDATES 78종목의 하위 10% 수준에 맞춘다.
#    새 종목이 기존 기준선보다 유동성이 낮으면 체결 가정이 무너진다.
MIN_BARS = 400            # 구간 대부분에 데이터가 있어야 한다


def db_pool() -> set[str]:
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(OUT), '..', '.env'))
    load_dotenv('/home/greatbps/projects/kiwoom_trading/.env')
    c = psycopg2.connect(dbname='trading_system', user='postgres',
                         password=os.getenv('POSTGRES_PASSWORD'),
                         host='localhost')
    cur = c.cursor()
    out: set[str] = set()
    for t, col in (('trades', 'stock_code'),
                   ('signal_rejections', 'stock_code'),
                   ('blocked_trades', 'stock_code'),
                   ('research.candidates', 'stock_code'),
                   ('signal_events', 'stock_code'),
                   ('filtered_candidates', 'stock_code'),
                   ('log_trade_events', 'ticker')):
        try:
            cur.execute(f"SELECT DISTINCT {col} FROM {t} "
                        f"WHERE {col} ~ '^[0-9]{{6}}$'")
            out |= {r[0] for r in cur.fetchall()}
        except Exception:
            c.rollback()
    return out


def main():
    pool = sorted(db_pool() | set(DEFAULT_CANDIDATES))
    with open(POOL_TXT, 'w') as f:
        f.write('\n'.join(pool))
    print(f'  원천 풀 {len(pool)}종목 (DB 관측 + DEFAULT_CANDIDATES)')

    # ── OHLCV 확보 ──────────────────────────────────────────────────────
    if os.path.exists(CACHE_PKL):
        with open(CACHE_PKL, 'rb') as f:
            data = pickle.load(f)
        print(f'  캐시 사용: {len(data)}종목')
    else:
        print(f'  다운로드 시작 — {len(pool)}종목, 수십 분 걸린다')
        data = load_multi(pool, START, END)
        data = {s: df[df.index <= END] for s, df in data.items()}
        data = {s: df for s, df in data.items() if len(df) >= MIN_BARS}
        with open(CACHE_PKL, 'wb') as f:
            pickle.dump(data, f)
        print(f'  저장: {len(data)}종목 (봉 {MIN_BARS}개 이상)')

    # ── 유동성 산출 ─────────────────────────────────────────────────────
    liq = {}
    for s, df in data.items():
        sub = df[df.index >= '2024-08-01']
        if len(sub) < 200:
            continue
        liq[s] = float((sub['close'] * sub['volume']).mean())

    base = [s for s in DEFAULT_CANDIDATES if s in liq]
    base_liq = sorted(liq[s] for s in base)
    floor = base_liq[max(0, int(len(base_liq) * 0.10) - 1)] if base_liq else 0
    print(f'\n  DEFAULT_CANDIDATES 유동성 (일평균 거래대금)')
    print(f'    최소 {min(base_liq):>16,.0f}   중앙 '
          f'{base_liq[len(base_liq)//2]:>16,.0f}   최대 {max(base_liq):>16,.0f}')
    print(f'    하위10% 기준선 {floor:,.0f}  ← 신규 종목 유동성 하한')

    # ── 계층 구성 ───────────────────────────────────────────────────────
    #
    # ⚠️ 기준선 재현을 위해 DEFAULT_CANDIDATES 는 무조건 포함한다.
    #    나머지를 유동성 순으로 채운다.
    extra = sorted((s for s in liq
                    if s not in set(DEFAULT_CANDIDATES) and liq[s] >= floor),
                   key=lambda s: -liq[s])
    print(f'  유동성 하한 통과 신규 후보 {len(extra)}종목')

    tiers = {}
    for n in TIERS:
        need = max(0, n - len(base))
        tiers[str(n)] = base + extra[:need]
        got = len(tiers[str(n)])
        print(f'    Tier {n:>3}: {got:>3}종목' +
              ('' if got == n else f'  ⚠️ 목표 미달 (풀 부족)'))

    with open(TIERS_JSON, 'w', encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'source': 'DB 관측 종목 + DEFAULT_CANDIDATES',
                   'pool': len(pool), 'ohlcv_ok': len(data),
                   'liquidity_floor': floor,
                   'base_included': len(base),
                   'tiers': {k: v for k, v in tiers.items()}},
                  f, ensure_ascii=False, indent=2)
    print(f'\n  저장: {TIERS_JSON}')


if __name__ == '__main__':
    main()
