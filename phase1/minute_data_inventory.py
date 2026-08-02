"""
Iteration 6-0 §2·§3 — 기존 분봉 데이터 재고 조사 + 로그 재생 가능성

⚠️ **추정 금지.** 파일을 실제로 열어 컬럼과 행수를 확인한다.
   이름이 `ohlcv` 여도 안 열어보고 "있다" 고 적지 않는다.

사용법:
    python -m phase1.minute_data_inventory
"""
from __future__ import annotations

import csv
import glob
import json
import os
import pickle
import re
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.dirname(os.path.abspath(__file__))

# OHLCV 로 인정할 컬럼 후보 (한/영/키움 표기)
OHLC = {
    'open': ['open', 'open_pric', '시가', 'o'],
    'high': ['high', 'high_pric', '고가', 'h'],
    'low': ['low', 'low_pric', '저가', 'l'],
    'close': ['close', 'cur_prc', '종가', 'c'],
    'volume': ['volume', 'trde_qty', '거래량', 'v'],
    'time': ['time', 'cntr_tm', 'datetime', 'timestamp', 'date', '일자', '시간'],
}
SKIP = ('/venv/', '/__pycache__/', '/node_modules/', '/.git/')


def _cols_ok(cols) -> dict:
    low = [str(c).lower() for c in cols]
    return {k: next((c for c in v if c in low), None) for k, v in OHLC.items()}


def scan_csv(p):
    try:
        with open(p, encoding='utf-8-sig', errors='replace') as f:
            r = csv.reader(f)
            head = next(r, [])
            n = sum(1 for _ in r)
        m = _cols_ok(head)
        return {'rows': n, 'columns': head[:12], 'match': m,
                'is_ohlcv': all(m[k] for k in ('open', 'high', 'low',
                                               'close', 'volume'))}
    except Exception as e:
        return {'error': f'{type(e).__name__}'}


def scan_pkl(p):
    try:
        if os.path.getsize(p) > 300 * 1024 * 1024:
            return {'error': 'too_large_skip'}
        with open(p, 'rb') as f:
            o = pickle.load(f)
        if hasattr(o, 'columns'):
            m = _cols_ok(list(o.columns))
            return {'rows': len(o), 'columns': [str(c) for c in o.columns][:12],
                    'match': m,
                    'is_ohlcv': all(m[k] for k in ('open', 'high', 'low',
                                                   'close', 'volume'))}
        if isinstance(o, dict) and o:
            k0 = next(iter(o))
            v0 = o[k0]
            if hasattr(v0, 'columns'):
                m = _cols_ok(list(v0.columns))
                return {'rows': sum(len(v) for v in o.values() if hasattr(v, '__len__')),
                        'symbols': len(o),
                        'columns': [str(c) for c in v0.columns][:12],
                        'match': m,
                        'is_ohlcv': all(m[k] for k in ('open', 'high', 'low',
                                                       'close', 'volume')),
                        'freq': _infer_freq(v0)}
        return {'type': type(o).__name__, 'is_ohlcv': False}
    except Exception as e:
        return {'error': f'{type(e).__name__}'}


def _infer_freq(df):
    """일봉인가 분봉인가 — 인덱스 간격으로 판단한다."""
    try:
        idx = df.index
        if len(idx) < 3:
            return 'unknown'
        d = (idx[1] - idx[0])
        sec = getattr(d, 'total_seconds', lambda: 0)()
        if sec <= 0:
            return 'unknown'
        return f'{int(sec/60)}분' if sec < 86400 else f'{int(sec/86400)}일'
    except Exception:
        return 'unknown'


def scan_sqlite(p):
    try:
        c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        cur = c.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tabs = [r[0] for r in cur.fetchall()]
        hit = []
        for t in tabs:
            cur.execute(f'PRAGMA table_info("{t}")')
            cols = [r[1] for r in cur.fetchall()]
            m = _cols_ok(cols)
            if all(m[k] for k in ('open', 'high', 'low', 'close', 'volume')):
                cur.execute(f'SELECT count(*) FROM "{t}"')
                hit.append({'table': t, 'rows': cur.fetchone()[0],
                            'match': m})
        c.close()
        return {'tables': len(tabs), 'ohlcv_tables': hit,
                'is_ohlcv': bool(hit)}
    except Exception as e:
        return {'error': f'{type(e).__name__}'}


def main():
    inv = []
    pats = [('csv', '**/*.csv', scan_csv), ('pkl', '**/*.pkl', scan_pkl),
            ('sqlite', '**/*.db', scan_sqlite)]

    print('=' * 74)
    print('  §2 기존 데이터 재고 조사 (실제로 열어서 확인)')
    print('=' * 74)

    for kind, pat, fn in pats:
        files = [p for p in glob.glob(os.path.join(ROOT, pat), recursive=True)
                 if not any(s in p for s in SKIP)]
        n_ohlcv = 0
        for p in files:
            r = fn(p)
            rel = os.path.relpath(p, ROOT)
            rec = {'kind': kind, 'path': rel,
                   'size_kb': round(os.path.getsize(p) / 1024, 1), **r}
            inv.append(rec)
            if r.get('is_ohlcv'):
                n_ohlcv += 1
        print(f'  {kind:<8}{len(files):>5}개 스캔   OHLCV 형태 {n_ohlcv}개')

    ohlcv = [r for r in inv if r.get('is_ohlcv')]
    print(f'\n  OHLCV 로 인정된 파일 {len(ohlcv)}개')
    print(f'  {"경로":<46}{"행":>9}{"주기":>8}')
    for r in sorted(ohlcv, key=lambda x: -(x.get('rows') or 0))[:12]:
        print(f'  {r["path"][:45]:<46}{(r.get("rows") or 0):>9,}'
              f'{str(r.get("freq", "-")):>8}')

    minute = [r for r in ohlcv if '분' in str(r.get('freq', ''))]
    print(f'\n  그중 **분봉**: {len(minute)}개')
    if not minute:
        print('  → 저장된 분봉 데이터가 없다. (일봉 캐시만 존재)')

    # ── §3 로그 재생 가능성 ─────────────────────────────────────────────
    print('\n' + '=' * 74)
    print('  §3 로그에서 OHLCV 재생 가능한가')
    print('=' * 74)
    logs = [p for p in glob.glob(os.path.join(ROOT, 'logs', '*.log'))]
    fields = {'timestamp': 0, 'open': 0, 'high': 0, 'low': 0,
              'close': 0, 'volume': 0}
    pats2 = {
        'timestamp': re.compile(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}'),
        'open': re.compile(r'시가|open[_ ]?pric|\bopen\b', re.I),
        'high': re.compile(r'고가|high[_ ]?pric|\bhigh\b', re.I),
        'low': re.compile(r'저가|low[_ ]?pric|\blow\b', re.I),
        'close': re.compile(r'종가|cur_prc|\bclose\b', re.I),
        'volume': re.compile(r'거래량|trde_qty|\bvolume\b', re.I),
    }
    sampled = 0
    for p in logs[:40]:
        try:
            with open(p, errors='replace') as f:
                for i, line in enumerate(f):
                    if i > 3000:
                        break
                    sampled += 1
                    for k, rx in pats2.items():
                        if rx.search(line):
                            fields[k] += 1
        except Exception:
            pass
    print(f'  로그 {len(logs)}개 중 {min(40,len(logs))}개 · {sampled:,}줄 표본')
    print(f'  {"필드":<12}{"등장 줄":>10}')
    for k, v in fields.items():
        print(f'  {k:<12}{v:>10,}')
    have = sum(1 for k in ('open', 'high', 'low', 'close', 'volume')
               if fields[k] > 0)
    verdict = ('가능' if have == 5 and fields['timestamp'] > 0
               else '부분 가능' if have >= 2 else '불가능')
    print(f'\n  판정: **{verdict}**')
    print('  ⚠️ 필드 문자열이 등장한다고 OHLCV 가 재생되는 것은 아니다.')
    print('     같은 봉의 5개 값이 한 줄에 정형으로 있어야 한다.')

    # 정형 OHLCV 한 줄 존재 여부
    strict = re.compile(
        r'(시가|open).{0,40}(고가|high).{0,40}(저가|low).{0,40}(종가|close)',
        re.I)
    n_strict = 0
    for p in logs[:40]:
        try:
            with open(p, errors='replace') as f:
                for i, line in enumerate(f):
                    if i > 3000:
                        break
                    if strict.search(line):
                        n_strict += 1
        except Exception:
            pass
    print(f'  한 줄에 OHLC 4값이 함께 있는 줄: {n_strict}건')
    if n_strict == 0:
        verdict = '불가능'
        print('  → 정형 OHLCV 줄이 0건이다. **로그 재생 불가능.**')

    with open(os.path.join(OUT, 'minute_data_inventory.csv'), 'w',
              newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['kind', 'path', 'size_kb', 'rows',
                                          'symbols', 'freq', 'is_ohlcv',
                                          'error'],
                           extrasaction='ignore')
        w.writeheader()
        w.writerows(inv)
    with open(os.path.join(OUT, 'minute_data_inventory.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'scanned_at': datetime.now().isoformat(timespec='seconds'),
                   'total_files': len(inv), 'ohlcv_files': len(ohlcv),
                   'minute_files': len(minute),
                   'log_fields': fields, 'log_strict_lines': n_strict,
                   'replay_verdict': verdict,
                   'inventory': inv}, f, ensure_ascii=False, indent=2,
                  default=str)
    print(f'\n  저장: minute_data_inventory.csv · .json')


if __name__ == '__main__':
    main()
