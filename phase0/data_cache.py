"""
Phase 0.1 — 데이터 · 신호 캐시

한 번 받아서 디스크에 두고 재사용한다. 4개 케이스(Baseline/Top3/Top5/Random5)가
**완전히 같은 입력**을 봐야 비교가 성립한다. 케이스마다 다시 받으면 yfinance 응답이
달라질 수 있고, 그러면 차이가 선별 규칙 때문인지 데이터 때문인지 알 수 없다.

워밍업:
    신호(window 60봉)와 ScoreEngine(MA50 55봉)이 과거 봉을 필요로 한다.
    START 이전 데이터를 함께 받아 두고, 집계할 때만 START 이후로 자른다.

사용법:
    python -m phase0.data_cache          # 캐시 생성 (없으면 다운로드)
    python -m phase0.data_cache --force  # 강제 재다운로드
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backtest.adapter import SMCAdapter
from backtest.daily_scan import BEST_ADAPTER_KWARGS, BEST_CONFIG
from backtest.loader import load_multi
from backtest.scanner import DEFAULT_CANDIDATES

logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('backtest.loader').setLevel(logging.CRITICAL)

log = logging.getLogger('phase0.cache')

# ── 실험 구간 (작업지시서 고정값) ──────────────────────────────────────────
START = '2024-08-01'
END = '2026-07-30'

# 워밍업 — 신호/스코어가 과거를 보는 만큼 앞에서 더 받는다
WARMUP_START = '2024-01-01'

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_cache')
OHLCV_PKL = os.path.join(CACHE_DIR, 'ohlcv.pkl')
SIGNAL_PKL = os.path.join(CACHE_DIR, 'signals.pkl')
META_JSON = os.path.join(CACHE_DIR, 'meta.json')


def load_ohlcv(force: bool = False) -> dict[str, pd.DataFrame]:
    """78종목 일봉. 캐시가 있으면 그대로 쓴다."""
    if not force and os.path.exists(OHLCV_PKL):
        with open(OHLCV_PKL, 'rb') as f:
            return pickle.load(f)

    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f'[CACHE] {len(DEFAULT_CANDIDATES)}종목 다운로드 '
          f'({WARMUP_START} ~ {END}) — 수 분 걸린다')
    data = load_multi(DEFAULT_CANDIDATES, WARMUP_START, END)

    # END 를 포함하도록 yfinance 는 exclusive 이므로 하루 더 요청해도
    # 여기서는 END 이하로만 자른다 (구간 밖 봉이 섞이면 결과가 흔들린다)
    data = {s: df[df.index <= END] for s, df in data.items()}
    data = {s: df for s, df in data.items() if len(df) > 0}

    with open(OHLCV_PKL, 'wb') as f:
        pickle.dump(data, f)
    print(f'[CACHE] 저장: {len(data)}종목')
    return data


def build_signals(data: dict[str, pd.DataFrame],
                  force: bool = False) -> dict[str, set]:
    """
    종목별 BUY 신호 발생일 집합.

    ⚠️ 신호 규칙은 운영과 동일한 것을 그대로 쓴다 (BEST_CONFIG /
       BEST_ADAPTER_KWARGS). Phase 0 은 **선별 규칙**만 비교하는 것이지
       신호 규칙을 바꾸는 자리가 아니다.
    """
    if not force and os.path.exists(SIGNAL_PKL):
        with open(SIGNAL_PKL, 'rb') as f:
            return pickle.load(f)

    adapter = SMCAdapter(BEST_CONFIG, **BEST_ADAPTER_KWARGS)
    out: dict[str, set] = {}
    for n, (sym, df) in enumerate(data.items(), 1):
        days = set()
        for i in range(len(df)):
            if adapter.get_signal(df, i) == 'BUY':
                days.add(df.index[i])
        out[sym] = days
        if n % 20 == 0:
            print(f'  신호 스캔 {n}/{len(data)}')

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(SIGNAL_PKL, 'wb') as f:
        pickle.dump(out, f)
    return out


def trading_days(data: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    """전 종목 인덱스 합집합 중 실험 구간에 드는 날."""
    idx = pd.DatetimeIndex([])
    for df in data.values():
        idx = idx.union(df.index)
    return [d for d in idx if str(d.date()) >= START and str(d.date()) <= END]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    data = load_ohlcv(a.force)
    sig = build_signals(data, a.force)
    days = trading_days(data)

    total = sum(len(v) for v in sig.values())
    in_range = sum(1 for s in sig.values() for d in s
                   if str(d.date()) >= START and str(d.date()) <= END)

    meta = {
        'built_at': datetime.now().isoformat(timespec='seconds'),
        'start': START, 'end': END, 'warmup_start': WARMUP_START,
        'universe_requested': len(DEFAULT_CANDIDATES),
        'universe_loaded': len(data),
        'trading_days': len(days),
        'signals_total': total,
        'signals_in_range': in_range,
    }
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(META_JSON, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(json.dumps(meta, ensure_ascii=False, indent=2))

    # 하루에 후보가 몇 개나 되는지 — 이게 3 미만이면 Top3/Top5/Random5 가
    # 사실상 같은 실험이 되어 버린다. 먼저 확인해야 할 값이다.
    per_day = pd.Series(
        [sum(1 for s in sig.values() if d in s) for d in days], index=days)
    print('\n일별 BUY 후보 수 분포')
    print(f'  평균 {per_day.mean():.2f}   중앙값 {per_day.median():.0f}   '
          f'최대 {per_day.max()}')
    for k in (0, 1, 2, 3, 5, 8):
        print(f'  후보 {k}개 초과인 날: {(per_day > k).sum():4d}일 '
              f'({(per_day > k).mean()*100:5.1f}%)')


if __name__ == '__main__':
    main()
