"""
ATR+MA50 자동 종목 스캐너

사용법:
    python -m backtest.scanner                 # 기본 (최근 2년)
    python -m backtest.scanner --top 30        # 상위 30개만 출력
    python -m backtest.scanner --days 365      # 최근 1년 기준

출력:
    - 조건 통과 종목 (ATR 2~8%, MA50 우상향, 유동성 기준)
    - MA50 기울기 순 정렬
    - 백테스트 Pool 생성용 코드 리스트
"""
import argparse
import logging
import sys
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.loader import load_multi

logging.basicConfig(level=logging.WARNING)
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

# ── 기본 후보 종목 (~100개) ────────────────────────────────────────────────
DEFAULT_CANDIDATES = [
    # 반도체/IT
    '005930','000660','012450','042700','000990','357780',
    '036200','054620','403870','058470','079550',
    # 2차전지/소재
    '006400','051910','247540','086520','010130','009830','011790','096770',
    # 자동차/기계
    '005380','000270','012330','064350',
    # 인터넷/게임/엔터
    '035420','035720','293490','251270','263750','035900','041510','352820',
    # 금융
    '086790','055550','316140','032830','000810',
    # 바이오/제약
    '068270','207940','000100','145020','196170',
    '128940','326030','263920',
    # 방산/중공업
    '329180','009540','034020','083450','012750',
    # 에너지/유틸
    '036460','051600','034730',
    # 유통/소비/기타
    '028260','018260','066570','047050','005490','373220','003490',
    # 코스닥 추가
    '086900','039030','236350','214420','122870',
    '036830','069620','039200','161390','950130','196300',
    '145720','067160','033290','094970','036540',
    '290650','035600','066970',
]


def scan_symbol(df: pd.DataFrame, symbol: str,
                atr_min: float = 0.02, atr_max: float = 0.08,
                ma50_slope_bars: int = 10,
                min_volume: int = 50000) -> dict:
    """
    단일 종목 적합성 평가.

    Returns:
        dict with 'pass', 'reason' (실패 시), 또는 metrics (통과 시)
    """
    if len(df) < 60:
        return {'symbol': symbol, 'pass': False, 'reason': '데이터부족'}

    close = df['close']
    high  = df['high']
    low   = df['low']

    # ── ATR% 범위 ─────────────────────────────────────────────────────────
    h15 = high.values[-15:]
    l15 = low.values[-15:]
    c15 = close.values[-15:]
    tr  = np.maximum(h15[1:] - l15[1:],
                     np.maximum(np.abs(h15[1:] - c15[:-1]),
                                np.abs(l15[1:] - c15[:-1])))
    atr_pct = float(np.mean(tr)) / float(close.iloc[-1]) if close.iloc[-1] > 0 else 0
    if atr_pct < atr_min:
        return {'symbol': symbol, 'pass': False, 'reason': f'ATR%={atr_pct*100:.1f}% 낮음(조용한종목)'}
    if atr_pct > atr_max:
        return {'symbol': symbol, 'pass': False, 'reason': f'ATR%={atr_pct*100:.1f}% 높음(고변동)'}

    # ── MA50 우상향 ────────────────────────────────────────────────────────
    if len(close) < 55:
        return {'symbol': symbol, 'pass': False, 'reason': 'MA50부족'}
    ma50      = close.rolling(50).mean()
    ma50_now  = float(ma50.iloc[-1])
    ma50_prev = float(ma50.iloc[-ma50_slope_bars - 1])
    if np.isnan(ma50_now) or np.isnan(ma50_prev):
        return {'symbol': symbol, 'pass': False, 'reason': 'MA50 NaN'}
    if not (close.iloc[-1] > ma50_now and ma50_now > ma50_prev):
        return {'symbol': symbol, 'pass': False, 'reason': 'MA50 하락/아래'}

    # ── 유동성 ────────────────────────────────────────────────────────────
    if 'volume' in df.columns:
        avg_vol = float(df['volume'].iloc[-20:].mean())
        if avg_vol < min_volume:
            return {'symbol': symbol, 'pass': False,
                    'reason': f'유동성부족(vol={avg_vol:.0f})'}
    else:
        avg_vol = None

    # ── 추가 지표 ─────────────────────────────────────────────────────────
    slope_pct = (ma50_now - ma50_prev) / ma50_prev * 100 if ma50_prev > 0 else 0
    ema20     = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema20_pos = (float(close.iloc[-1]) - ema20) / ema20 * 100

    return {
        'symbol':    symbol,
        'pass':      True,
        'atr_pct':   round(atr_pct * 100, 2),
        'slope_pct': round(slope_pct, 3),
        'ema20_pos': round(ema20_pos, 2),
        'close':     round(float(close.iloc[-1]), 0),
        'avg_vol':   round(avg_vol, 0) if avg_vol else None,
    }


def run_scanner(
    candidates: list = None,
    days:        int  = 730,   # 기본 2년
    atr_min:     float = 0.02,
    atr_max:     float = 0.08,
    min_volume:  int   = 50000,
    top:         int   = None,
    quiet:       bool  = False,
) -> list[dict]:
    """
    스캐너 실행.

    Returns:
        통과 종목 리스트 (slope 순 정렬)
    """
    symbols = candidates or DEFAULT_CANDIDATES
    end   = datetime.today().strftime('%Y-%m-%d')
    start = (datetime.today() - timedelta(days=days)).strftime('%Y-%m-%d')

    if not quiet:
        print(f"\n{'='*65}")
        print(f"  ATR+MA50 자동 스캐너  ({start} ~ {end})")
        print(f"  후보 {len(symbols)}개  ATR:{atr_min*100:.0f}~{atr_max*100:.0f}%  "
              f"유동성>={min_volume//10000}만주/일")
        print(f"{'='*65}")
        print("[데이터 로드 중...]")

    data = load_multi(symbols, start, end)

    if not quiet:
        print(f"  → {len(data)}종목 로드 완료\n")

    passed, rejected = [], []
    for sym, df in data.items():
        r = scan_symbol(df, sym, atr_min, atr_max, min_volume=min_volume)
        (passed if r['pass'] else rejected).append(r)

    passed.sort(key=lambda x: x['slope_pct'], reverse=True)
    if top:
        passed = passed[:top]

    if not quiet:
        print(f"  통과: {len(passed)}개  |  제외: {len(rejected)}개\n")
        print(f"  ✅ 통과 종목 (MA50기울기 순):")
        print(f"  {'코드':<8}  {'ATR%':>5}  {'MA50기울기':>10}  {'EMA20위치':>9}  {'현재가':>8}")
        print(f"  {'-'*55}")
        for r in passed:
            print(f"  {r['symbol']:<8}  {r['atr_pct']:>4.1f}%  "
                  f"{r['slope_pct']:>+9.3f}%  {r['ema20_pos']:>+8.2f}%  "
                  f"{r['close']:>8.0f}")

        rej_reasons: dict[str, int] = {}
        for r in rejected:
            k = r['reason'].split('(')[0].strip()
            rej_reasons[k] = rej_reasons.get(k, 0) + 1
        print(f"\n  ❌ 제외 이유:")
        for reason, cnt in sorted(rej_reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {cnt}개")

        codes = [r['symbol'] for r in passed]
        print(f"\n  백테스트 Pool 코드:")
        print(f"  {codes}")

    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='ATR+MA50 종목 스캐너')
    parser.add_argument('--days',    type=int,   default=730,   help='기준 기간 (일)')
    parser.add_argument('--top',     type=int,   default=None,  help='상위 N개만 출력')
    parser.add_argument('--atr-min', type=float, default=0.02,  help='ATR% 하한')
    parser.add_argument('--atr-max', type=float, default=0.08,  help='ATR% 상한')
    parser.add_argument('--min-vol', type=int,   default=50000, help='최소 일 평균 거래량')
    args = parser.parse_args()

    run_scanner(
        days       = args.days,
        atr_min    = args.atr_min,
        atr_max    = args.atr_max,
        min_volume = args.min_vol,
        top        = args.top,
    )
