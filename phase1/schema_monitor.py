"""
Iteration 7-1 — Position Risk Schema 운영 검증

`[POSITION_SCHEMA]` · `[SWING_STOP_MISSING]` · `[POS_RISK_SCHEMA]` 로그를
읽어 KPI 를 집계한다.

━━━ 이 검증은 시간이 흘러야 끝난다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  10거래일 또는 스윙 신규 진입 10건이 필요하다. 오늘 돌리면 "아직
  표본이 없다" 가 정답이고, 그렇게 보고한다. 표본 없이 PASS 를 찍으면
  검증한 적 없는 것을 검증했다고 말하는 것이다.

KPI (작업지시서)
    SWING_STOP_MISSING       0건
    strategy_horizon 누락     0건
    structure_stop_price 누락 0건
    fallback stop 발생        0건

사용법:
    python -m phase1.schema_monitor              # 전체 로그
    python -m phase1.schema_monitor --days 10    # 최근 10거래일
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_GLOB = os.path.join(ROOT, 'logs', 'auto_trading_2*.log')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'schema_monitor.json')

SCHEMA_RE = re.compile(
    r'\[POSITION_SCHEMA\]\s*(?P<rx>rx\s+)?'
    r'symbol=(?P<symbol>\S+)\s+'
    r'strategy_horizon=(?P<hz>\S+)\s+'
    r'entry_price=(?P<ep>\S+)\s+'
    r'structure_stop_price=(?P<ssp>\S+)\s+'
    r'position_type=(?P<pt>\S+)'
    r'(?:\s+source=(?P<src>\S+))?')
MISSING_RE = re.compile(r'\[SWING_STOP_MISSING\]\s+(?P<rx>rx\s+)?(?P<symbol>\S+)')
FALLBACK_RE = re.compile(r'\[SWING_NO_STRUCTURE_STOP\]\s+(?P<symbol>\S+)')
SWING_HARD_RE = re.compile(r'\[SWING_HARD_STOP\]')


def _day_of(path: str) -> str:
    m = re.search(r'(\d{8})', os.path.basename(path))
    if not m:
        return ''
    d = m.group(1)
    return f'{d[:4]}-{d[4:6]}-{d[6:]}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=0,
                    help='최근 N개 로그일만 (0=전체)')
    a = ap.parse_args()

    files = sorted(glob.glob(LOG_GLOB))
    if a.days:
        files = files[-a.days:]
    if not files:
        print('로그 파일이 없다.')
        return

    created, received = [], []
    missing, fallback = [], []
    swing_hard = 0
    per_day_swing = defaultdict(set)

    for fp in files:
        day = _day_of(fp)
        for line in open(fp, errors='replace'):
            m = SCHEMA_RE.search(line)
            if m:
                rec = {'day': day, 'symbol': m.group('symbol'),
                       'strategy_horizon': m.group('hz'),
                       'entry_price': m.group('ep'),
                       'structure_stop_price': m.group('ssp'),
                       'position_type': m.group('pt'),
                       'source': m.group('src') or '-'}
                (received if m.group('rx') else created).append(rec)
                if rec['strategy_horizon'] == 'SWING':
                    per_day_swing[day].add(rec['symbol'])
                continue
            m = MISSING_RE.search(line)
            if m:
                missing.append({'day': day, 'symbol': m.group('symbol'),
                                'side': 'rx' if m.group('rx') else 'create'})
                continue
            m = FALLBACK_RE.search(line)
            if m:
                fallback.append({'day': day, 'symbol': m.group('symbol')})
                continue
            if SWING_HARD_RE.search(line):
                swing_hard += 1

    days_with_data = sorted({r['day'] for r in created + received})
    swing_syms = {r['symbol'] for r in created + received
                  if r['strategy_horizon'] == 'SWING'}

    print('=' * 76)
    print('  Iteration 7-1 — Position Risk Schema 운영 검증')
    print('=' * 76)
    print(f'  로그 {len(files)}개  ({_day_of(files[0])} ~ {_day_of(files[-1])})')
    print(f'  [POSITION_SCHEMA] 생성측 {len(created)}건 · '
          f'수신측 {len(received)}건')
    print(f'  기록이 있는 날 {len(days_with_data)}일   '
          f'SWING 종목 {len(swing_syms)}개')

    # ── 표본 충족 여부 ───────────────────────────────────────────────────
    enough = len(days_with_data) >= 10 or len(swing_syms) >= 10
    print()
    print('-' * 76)
    print('  ① 표본 충족')
    print('-' * 76)
    print(f'  기준: 10거래일 또는 SWING 신규 10건')
    print(f'  현재: {len(days_with_data)}거래일 / SWING {len(swing_syms)}건'
          f'   → {"✅ 충족" if enough else "❌ 미달 — 판정 보류"}')

    # ── KPI ─────────────────────────────────────────────────────────────
    all_rec = created + received
    hz_missing = [r for r in all_rec if r['strategy_horizon'] in ('NONE', '')]
    swing_rec = [r for r in all_rec if r['strategy_horizon'] == 'SWING']
    ssp_missing = [r for r in swing_rec
                   if r['structure_stop_price'] in ('NONE', '')]

    print()
    print('-' * 76)
    print('  ② KPI')
    print('-' * 76)
    print(f'  {"항목":<34}{"목표":>6}{"실측":>8}   판정')
    kpis = [
        ('SWING_STOP_MISSING', 0, len(missing)),
        ('strategy_horizon 누락', 0, len(hz_missing)),
        ('structure_stop_price 누락 (SWING)', 0, len(ssp_missing)),
        ('fallback stop 발생', 0, len(fallback) + swing_hard),
    ]
    all_pass = True
    for name, goal, got in kpis:
        ok = got <= goal
        all_pass &= ok
        print(f'  {name:<34}{goal:>6}{got:>8}   '
              f'{"✅" if ok else "❌"}')

    verdict = ('PASS' if (all_pass and enough)
               else 'INSUFFICIENT' if not enough else 'FAIL')
    print()
    print(f'  판정: {verdict}')
    if verdict == 'INSUFFICIENT':
        print('    KPI 는 전부 0 이지만 표본이 없다. '
              '"위반이 없다" 와 "검증했다" 는 다르다.')

    # ── 상세 ────────────────────────────────────────────────────────────
    if created:
        print()
        print('-' * 76)
        print('  ③ 생성 경로별 분포')
        print('-' * 76)
        for src, n in Counter(r['source'] for r in created).most_common():
            swing_n = sum(1 for r in created
                          if r['source'] == src
                          and r['strategy_horizon'] == 'SWING')
            with_stop = sum(1 for r in created
                            if r['source'] == src
                            and r['strategy_horizon'] == 'SWING'
                            and r['structure_stop_price'] not in ('NONE', ''))
            print(f'  {src:<16}{n:>5}건   SWING {swing_n:>3}건 '
                  f'(손절 있음 {with_stop})')

    if missing:
        print()
        print('  ⚠️ SWING_STOP_MISSING 상세')
        for m in missing[:10]:
            print(f'    {m["day"]}  {m["symbol"]}  ({m["side"]})')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'log_files': len(files),
                   'range': [_day_of(files[0]), _day_of(files[-1])],
                   'schema_created': len(created),
                   'schema_received': len(received),
                   'days_with_data': days_with_data,
                   'swing_symbols': sorted(swing_syms),
                   'sample_sufficient': enough,
                   'kpi': {n: {'goal': g, 'actual': v, 'pass': v <= g}
                           for n, g, v in kpis},
                   'verdict': verdict,
                   'missing_detail': missing[:50],
                   'fallback_detail': fallback[:50]},
                  f, ensure_ascii=False, indent=2)
    print(f'\n  저장: {OUT}')


if __name__ == '__main__':
    main()
