"""
analysis/filter_debug.py — SMC 진입 필터 레이어별 차단 추적기

각 필터 레이어가 얼마나 막는지 자동 집계:
  L0  시간 필터       (10:00 이전 / 12:30 이후)
  L1  MKT_CTX        (NO_TRADE_DAY)
  L2  Market Sensor  (RISK_OFF / AFTERNOON_BLOCKED)
  L3  prefilter      (HTF/Sweep/Reclaim 2/4 미달)
  L4  CHoCH 등급     (C급 차단 / HTF_B_BLOCK)
  L5  displacement   (atr/body 미달)
  L6  OB pullback    (되돌림 대기 실패)
  OK  진입 성공

사용법:
    python3 -m analysis.filter_debug              # 오늘
    python3 -m analysis.filter_debug 20260323     # 특정 날짜
    python3 -m analysis.filter_debug --days 7     # 최근 7일 누적
"""

import re
import os
import sys
from datetime import date, datetime, timedelta
from collections import Counter, defaultdict

LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')

# ─── 레이어 패턴 ──────────────────────────────────────────────────────────────

_LAYERS = [
    ("L0_TIME",         re.compile(r'OPENING_NOISE|진입 시간 외|TREND_TIME_BLOCK')),
    ("L1_MKT_CTX",      re.compile(r'\[MKT_CTX\].*NO_TRADE_DAY|MKT_CTX_BLOCK')),
    ("L2_SENSOR",       re.compile(r'RISK_OFF_DAY|AFTERNOON_BLOCKED|TRADING_HALT')),
    ("L3_PREFILTER",    re.compile(r'프리필터 차단')),
    ("L3_PASS",         re.compile(r'PREFILTER_PASS')),   # ← 통과 카운트
    ("L4_GRADE",        re.compile(r'HTF_B_BLOCK|CHoCH.*C급.*차단|최소.*급.*필요')),
    ("L5_DISP",         re.compile(r'DISP_BLOCK|\[DISP\]')),
    ("L6_OB",           re.compile(r'OB.*무효|ob_pullback.*실패|invalidate.*choch', re.I)),
    ("OK_ENTRY",        re.compile(r'SMC LONG|매수완료')),
]

# CHoCH 자체 감지 여부
_RE_CHOCH_DETECTED = re.compile(r'\[CHOCH\]|\[SMC\].*CHoCH감지|CHoCH.*감지|detect_choch.*bullish|detect_choch.*bearish', re.I)
_RE_RANGING        = re.compile(r'SMC 구조: ranging|structure.*ranging')
_RE_BEARISH_STRUCT = re.compile(r'SMC 구조: bearish')


def _read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8', errors='ignore') as f:
        return f.readlines()


# ─── 하루 분석 ────────────────────────────────────────────────────────────────

def analyze_day(target: date) -> dict:
    ds = target.strftime('%Y%m%d')
    lines = _read_lines(os.path.join(LOG_DIR, f'auto_trading_{ds}.log'))
    smc_lines = _read_lines(os.path.join(LOG_DIR, f'smc_decision_{ds}.log'))

    layer_counts = {label: 0 for label, _ in _LAYERS}
    ranging_count   = 0
    bearish_count   = 0
    choch_detected  = 0

    for line in lines + smc_lines:
        # 구조 판정
        if _RE_RANGING.search(line):
            ranging_count += 1
        if _RE_BEARISH_STRUCT.search(line):
            bearish_count += 1
        if _RE_CHOCH_DETECTED.search(line):
            choch_detected += 1

        # 레이어별 차단 카운트
        for label, pat in _LAYERS:
            if pat.search(line):
                layer_counts[label] += 1
                break  # 한 줄은 한 레이어만

    # MKT_CTX 상태
    mkt_ctx_blocked = any(
        '[MKT_CTX]' in l and 'NO_TRADE_DAY' in l
        for l in lines
    )

    return {
        'date':           target.isoformat(),
        'mkt_ctx_blocked': mkt_ctx_blocked,
        'layers':         layer_counts,
        'ranging_count':  ranging_count,
        'bearish_count':  bearish_count,
        'choch_detected': choch_detected,
    }


# ─── 출력 ─────────────────────────────────────────────────────────────────────

def _bar(n: int, total: int, width: int = 20) -> str:
    if total == 0:
        return '─' * width
    filled = int(n / total * width)
    return '█' * filled + '░' * (width - filled)


def _print_day(r: dict):
    width = 62
    mkt = '🚫 NO_TRADE' if r['mkt_ctx_blocked'] else '✅ TRADE_OK'
    print(f"\n{'='*width}")
    print(f"  🔬 Filter Debug — {r['date']}  {mkt}")
    print(f"{'='*width}")

    layers = r['layers']
    total  = sum(layers.values()) or 1

    print(f"\n  {'레이어':<20} {'건수':>5}  {'비율':>6}  {'분포'}")
    print(f"  {'─'*58}")
    for label, _ in _LAYERS:
        n    = layers[label]
        pct  = n / total * 100
        bar  = _bar(n, sum(layers.values()) or 1)
        icon = '🟢' if label == 'OK_ENTRY' else ('🔴' if n > 0 else '⚪')
        print(f"  {icon} {label:<18} {n:>5}  {pct:>5.1f}%  {bar}")

    print(f"\n  [SMC 구조 분포]")
    total_struct = r['ranging_count'] + r['bearish_count']
    print(f"    ranging  : {r['ranging_count']:4d}  ({r['ranging_count']/max(total_struct,1)*100:.0f}%)")
    print(f"    bearish  : {r['bearish_count']:4d}  ({r['bearish_count']/max(total_struct,1)*100:.0f}%)")
    print(f"    CHoCH 감지: {r['choch_detected']:4d}건")
    print(f"{'='*width}\n")


def _print_multi(results: list[dict]):
    width = 70
    print(f"\n{'='*width}")
    print(f"  🔬 Filter Debug — {len(results)}일 누적")
    print(f"{'='*width}")
    print(f"  {'날짜':10}  {'MKT':10}  {'L0시간':>6}  {'L1CTX':>6}  {'L3PRE':>6}  {'L4GRD':>6}  {'OK':>5}  {'CHoCH':>5}")
    print(f"  {'─'*65}")
    for r in results:
        mkt = '🚫' if r['mkt_ctx_blocked'] else '✅'
        l   = r['layers']
        print(
            f"  {r['date']:10}  {mkt:10}  "
            f"{l['L0_TIME']:>6}  {l['L1_MKT_CTX']:>6}  "
            f"{l['L3_PREFILTER']:>6}  {l['L4_GRADE']:>6}  "
            f"{l['OK_ENTRY']:>5}  {r['choch_detected']:>5}"
        )

    # 누적 합계
    agg = defaultdict(int)
    for r in results:
        for k, v in r['layers'].items():
            agg[k] += v
        agg['choch'] += r['choch_detected']

    total = sum(agg[l] for l, _ in _LAYERS) or 1
    print(f"\n  [누적 레이어 비중]")
    for label, _ in _LAYERS:
        n   = agg[label]
        pct = n / total * 100
        print(f"    {label:<20} {n:>6}건  ({pct:>5.1f}%)")
    print(f"    {'CHoCH 총 감지':<20} {agg['choch']:>6}건")
    print(f"{'='*width}\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if '--days' in args:
        idx  = args.index('--days')
        days = int(args[idx + 1]) if idx + 1 < len(args) else 7
        today = date.today()
        results = [analyze_day(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
        _print_multi(results)
        return

    target = date.today()
    if args and re.fullmatch(r'\d{8}', args[0]):
        target = datetime.strptime(args[0], '%Y%m%d').date()

    r = analyze_day(target)
    _print_day(r)


if __name__ == '__main__':
    main()
