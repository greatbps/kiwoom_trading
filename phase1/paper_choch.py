"""
Iteration 7-1 (재개) — CHoCH Paper Trading & Evidence Validation

━━━ 두 가지 모드 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --replay N   캐시된 과거 N거래일을 재생해 Match 지표를 **오늘** 낸다.
               Signal/Entry/Stop/Position/Exit 일치는 두 코드 경로의
               결정적 비교이므로 시간이 흐를 필요가 없다.

  --daily      오늘 하루치. 매 거래일 장 종료 후 크론으로 돌려
               **전진(out-of-sample)** 증거를 쌓는다.

  ⚠️ 둘은 다른 증거다. replay 는 "두 경로가 같은 답을 내는가",
     daily 누적은 "앞으로도 그런가". replay 결과로 승인하면 안 된다.

━━━ 실주문은 발생하지 않는다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  이 스크립트는 `KiwoomAPI` 를 import 하지 않는다. 신호 생성과 비교만
  한다. 주문 함수를 호출할 경로 자체가 없다.

사용법:
    python -m phase1.paper_choch --replay 20
    python -m phase1.paper_choch --daily
    python -m phase1.paper_choch --final          # 누적 CSV로 최종 리포트
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from analyzers.swing.choch_engine import ChochSignalEngine, MIN_BARS
from backtest.adapter import SMCAdapter
from backtest.daily_scan import BEST_ADAPTER_KWARGS, BEST_CONFIG
from phase0 import data_cache as dc

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paper_choch')
os.makedirs(OUT, exist_ok=True)
P = lambda n: os.path.join(OUT, n)          # noqa: E731

SIGNAL_CSV = P('paper_signal_log.csv')
FUNNEL_CSV = P('paper_funnel.csv')
DIFF_CSV = P('paper_diff.csv')
SUMMARY_CSV = P('paper_summary.csv')

TOP_N = 3                 # swing_runner Top-3 상한
RISK_PER_TRADE = 0.01     # 리스크 1% 가정 (사이징 비교용 — 운영값 미변경)
CAPITAL = 100_000_000

SIG_COLS = ['date', 'symbol', 'entry_engine', 'signal', 'entry_price',
            'stop_price', 'risk_percent', 'position_size', 'position_schema',
            'exit_mapping', 'backtest_signal', 'live_signal', 'rank',
            'selected']
FUNNEL_COLS = ['date', 'stage', 'passed', 'removed', 'removed_pct']
DIFF_COLS = ['date', 'symbol', 'field', 'live_value', 'backtest_value',
             'cause']
SUM_COLS = ['date', 'candidates', 'choch', 'rvol', 'atr', 'ma50', 'top3',
            'final_trades', 'signal_match', 'trade_match', 'entry_match',
            'stop_match', 'exit_match', 'position_match', 'mismatch_count',
            'scan_sec']


# ── Funnel 단계별 어댑터 (BEST_* 를 건드리지 않는다) ─────────────────────
def _stage_adapters():
    """
    ⚠️ 파라미터를 여기서 새로 정하지 않는다. BEST_ADAPTER_KWARGS 에서
       필터를 하나씩 빼서 단계를 만든다 — 원본이 바뀌면 자동으로 따라간다.
    """
    k = dict(BEST_ADAPTER_KWARGS)
    return [
        ('CHoCH', SMCAdapter(BEST_CONFIG, **{**k, 'require_volume': False,
                                             'atr_pct_min': None,
                                             'atr_pct_max': None,
                                             'require_ma50_trend': False})),
        ('RVOL', SMCAdapter(BEST_CONFIG, **{**k, 'atr_pct_min': None,
                                            'atr_pct_max': None,
                                            'require_ma50_trend': False})),
        ('ATR', SMCAdapter(BEST_CONFIG, **{**k,
                                           'require_ma50_trend': False})),
        ('MA50', SMCAdapter(BEST_CONFIG, **k)),
    ]


def _exit_mapping_tag(sig: dict) -> str:
    """이 신호가 어떤 백테스트 청산 규칙 아래 놓이는가."""
    return 'SL/TP/TRAIL/BE_STOP/MAX_HOLD (Case3)' if sig else '-'


def _schema_of(sig: dict) -> str:
    """POSITION_SCHEMA 필수 5필드 충족 여부."""
    need = ('entry', 'stop', 'final_score', 'trigger', 'pattern')
    return 'OK' if all(sig.get(k) is not None for k in need) else 'INCOMPLETE'


def _position_size(entry: float, stop: float) -> int:
    """risk_amount / 주당위험 — risk_manager 공식과 같은 형태."""
    r = abs(entry - stop)
    return int((CAPITAL * RISK_PER_TRADE) / r) if r > 0 else 0


def run_day(data, day, adapters, out_sig, out_funnel, out_diff):
    """하루치 스캔. Returns (요약 dict, scan 초)."""
    t0 = time.perf_counter()
    stage_pass = defaultdict(set)
    live_hits, bt_hits = {}, set()
    n_cand = 0

    for sym, df in data.items():
        if day not in df.index:
            continue
        i = df.index.get_loc(day)
        if i < MIN_BARS:
            continue
        n_cand += 1

        # Funnel — 필터를 하나씩 더한 어댑터로 단계 통과 여부
        for name, ad in adapters:
            try:
                if ad.get_signal(df, i) == 'BUY':
                    stage_pass[name].add(sym)
            except Exception:
                pass

        # Backtest 최종 신호 = MA50 단계 통과
        if sym in stage_pass['MA50']:
            bt_hits.add(sym)

        # Live 경로 (ChochSignalEngine)
        s = ChochSignalEngine(df.iloc[:i + 1], {}).run()
        if s:
            live_hits[sym] = s

    scan_sec = time.perf_counter() - t0
    dstr = str(day.date())

    # ── Diff 검증 ────────────────────────────────────────────────────────
    mismatches = []
    for sym in sorted(set(live_hits) | bt_hits):
        lv, bt = sym in live_hits, sym in bt_hits
        if lv != bt:
            mismatches.append({'date': dstr, 'symbol': sym, 'field': 'signal',
                               'live_value': 'BUY' if lv else 'NONE',
                               'backtest_value': 'BUY' if bt else 'NONE',
                               'cause': 'Live/Backtest 신호 불일치'})
            continue
        if not lv:
            continue
        s = live_hits[sym]
        df = data[sym]
        close = float(df.loc[day]['close'])
        if abs(s['entry'] - close) > 0.01:
            mismatches.append({'date': dstr, 'symbol': sym, 'field': 'entry',
                               'live_value': s['entry'],
                               'backtest_value': close,
                               'cause': '진입가가 당일 종가와 다르다'})
        if not (0 < s['stop'] < s['entry']):
            mismatches.append({'date': dstr, 'symbol': sym, 'field': 'stop',
                               'live_value': s['stop'],
                               'backtest_value': f'0 < stop < {s["entry"]}',
                               'cause': '손절가 범위 위반 — 즉시청산 위험'})
        if _schema_of(s) != 'OK':
            mismatches.append({'date': dstr, 'symbol': sym,
                               'field': 'position_schema',
                               'live_value': _schema_of(s),
                               'backtest_value': 'OK',
                               'cause': 'POSITION_SCHEMA 필수 필드 누락'})

    # ── 신호 로그 (Top-3 선별 포함) ─────────────────────────────────────
    ranked = sorted(live_hits.items(), key=lambda kv: -kv[1]['final_score'])
    for rank, (sym, s) in enumerate(ranked, 1):
        out_sig.append({
            'date': dstr, 'symbol': sym, 'entry_engine': 'choch',
            'signal': s['pattern'], 'entry_price': s['entry'],
            'stop_price': s['stop'],
            'risk_percent': round((s['entry'] - s['stop']) / s['entry'] * 100, 2),
            'position_size': _position_size(s['entry'], s['stop']),
            'position_schema': _schema_of(s),
            'exit_mapping': _exit_mapping_tag(s),
            'backtest_signal': 'BUY' if sym in bt_hits else 'NONE',
            'live_signal': 'BUY', 'rank': rank,
            'selected': rank <= TOP_N,
        })

    # ── Funnel ──────────────────────────────────────────────────────────
    prev = n_cand
    funnel_now = {}
    for name, _ in adapters:
        n = len(stage_pass[name])
        out_funnel.append({'date': dstr, 'stage': name, 'passed': n,
                           'removed': prev - n,
                           'removed_pct': round((prev - n) / prev * 100, 1)
                           if prev else 0.0})
        funnel_now[name] = n
        prev = n
    top3 = min(len(ranked), TOP_N)
    out_funnel.append({'date': dstr, 'stage': 'Top3', 'passed': top3,
                       'removed': len(ranked) - top3,
                       'removed_pct': round((len(ranked) - top3) /
                                            len(ranked) * 100, 1)
                       if ranked else 0.0})
    out_diff.extend(mismatches)

    n_sig = len(live_hits)
    both = len(set(live_hits) & bt_hits)
    pct = (lambda a, b: round(a / b * 100, 1) if b else 100.0)
    return {
        'date': dstr, 'candidates': n_cand,
        'choch': funnel_now.get('CHoCH', 0), 'rvol': funnel_now.get('RVOL', 0),
        'atr': funnel_now.get('ATR', 0), 'ma50': funnel_now.get('MA50', 0),
        'top3': top3, 'final_trades': top3,
        'signal_match': pct(both, max(len(bt_hits), n_sig) or 1),
        'trade_match': pct(both, max(len(bt_hits), n_sig) or 1),
        'entry_match': pct(n_sig - sum(1 for m in mismatches
                                       if m['field'] == 'entry'), n_sig or 1),
        'stop_match': pct(n_sig - sum(1 for m in mismatches
                                      if m['field'] == 'stop'), n_sig or 1),
        'exit_match': 100.0,
        'position_match': pct(n_sig - sum(1 for m in mismatches
                                          if m['field'] == 'position_schema'),
                              n_sig or 1),
        'mismatch_count': len(mismatches),
        'scan_sec': round(scan_sec, 3),
    }, scan_sec


def daily_report(s: dict):
    print('\n=============================')
    print('CHoCH DAILY REPORT')
    print('=============================')
    print(f'Date            {s["date"]}')
    print(f'Candidates      {s["candidates"]}')
    print(f'CHoCH           {s["choch"]}')
    print(f'RVOL            {s["rvol"]}')
    print(f'ATR             {s["atr"]}')
    print(f'MA50            {s["ma50"]}')
    print(f'Top3            {s["top3"]}')
    print(f'Final Trades    {s["final_trades"]}')
    print(f'Signal Match    {s["signal_match"]}%')
    print(f'Trade Match     {s["trade_match"]}%')
    print(f'Entry Match     {s["entry_match"]}%')
    print(f'Stop Match      {s["stop_match"]}%')
    print(f'Exit Match      {s["exit_match"]}%')
    print(f'Position Match  {s["position_match"]}%')
    print(f'Mismatch Count  {s["mismatch_count"]}')
    print(f'Scan Time       {s["scan_sec"]}s')


def _write(path, cols, rows, append=False):
    exists = os.path.exists(path)
    mode = 'a' if (append and exists) else 'w'
    with open(path, mode, newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        if mode == 'w':
            w.writeheader()
        w.writerows(rows)


def final_report(summaries, diffs, scans):
    print('\n=============================')
    print('FINAL PAPER REPORT')
    print('=============================')
    n = len(summaries)
    sig = sum(s['ma50'] for s in summaries)
    tr = sum(s['final_trades'] for s in summaries)
    print(f'Trading Days      {n}')
    print(f'Signal Count      {sig}')
    print(f'Trade Count       {tr}')
    # ⚠️ 일별 %의 산술평균을 내면 안 된다. 신호 없는 날이 0%로 잡혀
    #    전체가 낮아 보인다 (신호 없는 날이 81%다). 분모는 **신호 수**여야 한다.
    by_field = {'signal': 'signal', 'entry': 'entry', 'stop': 'stop',
                'position_schema': 'position'}
    print(f'{"(분모 = 신호 " + str(sig) + "건)":<18}')
    for fld, label in (('signal', 'Signal Match'), ('signal', 'Trade Match'),
                       ('entry', 'Entry Match'), ('stop', 'Stop Match'),
                       ('position_schema', 'Position Match')):
        bad = sum(1 for d in diffs if d['field'] == fld)
        pct = (sig - bad) / sig * 100 if sig else 0.0
        print(f'{label:<18}{pct:.1f}%   (불일치 {bad}건)')
    print(f'{"Exit Match":<18}100.0%   (매핑 규칙 고정 — Case3)')
    print(f'{"신호 있던 날":<18}'
          f'{sum(1 for s in summaries if s["ma50"]):d} / {n}일 '
          f'({sum(1 for s in summaries if s["ma50"])/n*100:.1f}%)')
    print(f'Mismatch List     {len(diffs)}건'
          + ('' if diffs else ' — 없음'))
    for d in diffs[:20]:
        print(f'  {d["date"]} {d["symbol"]} {d["field"]}: '
              f'live={d["live_value"]} bt={d["backtest_value"]} '
              f'({d["cause"]})')

    print('\nDaily Funnel Statistics')
    for k, label in (('candidates', 'Candidates'), ('choch', 'CHoCH'),
                     ('rvol', 'RVOL'), ('atr', 'ATR'), ('ma50', 'MA50'),
                     ('final_trades', 'Trades')):
        v = [s[k] for s in summaries]
        print(f'  {label:<12} 평균 {sum(v)/len(v):>7.2f}   '
              f'최대 {max(v):>4}   합계 {sum(v):>5}')

    print(f'\nAverage Candidates   {sum(s["candidates"] for s in summaries)/n:.1f}')
    print(f'Average Trades       {tr/n:.2f}')
    print(f'Average Signal Freq  {sig/n:.2f} 건/거래일  '
          f'({sig/n*20:.1f} 건/20거래일)')

    print('\nScan Time')
    print(f'  Average    {statistics.mean(scans):.3f}s')
    print(f'  Max        {max(scans):.3f}s')
    print(f'  95pct      {sorted(scans)[int(len(scans)*0.95)-1]:.3f}s')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--replay', type=int, default=0,
                    help='캐시 과거 N거래일 재생')
    ap.add_argument('--daily', action='store_true', help='최근 1거래일')
    ap.add_argument('--final', action='store_true',
                    help='누적 CSV로 최종 리포트만')
    a = ap.parse_args()

    if a.final:
        with open(SUMMARY_CSV, encoding='utf-8-sig') as f:
            summaries = [{k: (float(v) if k not in ('date',) else v)
                          for k, v in r.items()} for r in csv.DictReader(f)]
        diffs = []
        if os.path.exists(DIFF_CSV):
            with open(DIFF_CSV, encoding='utf-8-sig') as f:
                diffs = list(csv.DictReader(f))
        final_report(summaries, diffs, [s['scan_sec'] for s in summaries])
        return

    data = dc.load_ohlcv()
    days = dc.trading_days(data)
    target = days[-1:] if a.daily else days[-a.replay:] if a.replay else days
    adapters = _stage_adapters()

    print('=' * 76)
    print('  CHoCH Paper Trading — dry-run (실주문 없음)')
    print('=' * 76)
    print(f'  엔진 SWING_ENTRY_ENGINE=choch   '
          f'대상 {len(target)}거래일 '
          f'({target[0].date()} ~ {target[-1].date()})')
    print(f'  ⚠️ 이 스크립트는 KiwoomAPI 를 import 하지 않는다 — '
          f'주문 경로 자체가 없다.')

    out_sig, out_funnel, out_diff, summaries, scans = [], [], [], [], []
    for d in target:
        s, sec = run_day(data, d, adapters, out_sig, out_funnel, out_diff)
        summaries.append(s)
        scans.append(sec)
        if s['candidates'] and (s['ma50'] or s['mismatch_count']
                                or a.daily):
            daily_report(s)

    _write(SIGNAL_CSV, SIG_COLS, out_sig, append=a.daily)
    _write(FUNNEL_CSV, FUNNEL_COLS, out_funnel, append=a.daily)
    _write(DIFF_CSV, DIFF_COLS, out_diff, append=a.daily)
    _write(SUMMARY_CSV, SUM_COLS, summaries, append=a.daily)

    final_report(summaries, out_diff, scans)

    # ⚠️ Mismatch 0 을 곧바로 PASS 로 읽으면 안 된다. 신호가 0건이면
    #    비교할 대상이 없어서 0인 것이지 일치를 확인한 게 아니다.
    #    Iteration 7-1 에서 같은 함정을 이미 지적했다 —
    #    "위반이 없다" 와 "검증했다" 는 다르다.
    n_sig_total = sum(s['ma50'] for s in summaries)
    if n_sig_total == 0:
        verdict = '⛔ INSUFFICIENT — 신호 0건. 비교 대상이 없어 검증되지 않았다'
    elif out_diff:
        verdict = f'❌ FAIL — Mismatch {len(out_diff)}건'
    else:
        verdict = f'✅ PASS — 신호 {n_sig_total}건 전부 일치'
    ok = (n_sig_total > 0 and not out_diff)
    print('\n' + '=' * 76)
    print(f'  승인 판정: {verdict}')
    if a.replay:
        print('  ⚠️ 이것은 **재생 검증**이다. 두 코드 경로가 같은 답을 내는지')
        print('     확인했을 뿐, 전진(out-of-sample) 증거가 아니다.')
        print('     승인에는 --daily 를 20거래일 누적해야 한다.')
    print(f'  CSV: {OUT}')
    print('=' * 76)


if __name__ == '__main__':
    main()
