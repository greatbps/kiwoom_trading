"""
Iteration 6-1 — VWAP+AI 5분봉 데이터셋 구축

━━━ 유니버스는 하드코딩하지 않는다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━

  운영이 실제로 거래한 종목을 DB 에서 뽑는다.

  ⚠️ 조건검색을 지금 실행해 유니버스를 만들면 **오늘 시점의 매칭**이
     나온다. 2025-08~2026-07 백테스트에 그걸 쓰면 미래를 아는 셈이다
     (look-ahead). 그래서 **그 기간에 실제로 거래된 종목**을 쓴다.

━━━ 운영 프로세스와 토큰을 공유한다 ━━━━━━━━━━━━━━━━━━━━━━━━━━

  main_auto_trading 이 떠 있으면 조회 한도를 나눠 쓴다.
  장중이면 실행을 거부한다. 간격도 실측 안전선(0.2s)보다 넉넉히 둔다.

사용법:
    python -m phase1.build_minute_dataset            # 전체
    python -m phase1.build_minute_dataset --limit 5  # 소량 시험
    python -m phase1.build_minute_dataset --force    # 장중에도 강행
"""
from __future__ import annotations

import argparse
import csv
import io
import contextlib
import json
import os
import pickle
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))
OUT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(OUT, 'minute_cache', '5m')
os.makedirs(CACHE, exist_ok=True)

TIC = '5'
START_FLOOR = '20250801'      # 서버 보유 한계 (Iter6-0 실측)
REQ_GAP = 0.30                # 실측 안전선 0.2s + 여유
MAX_PAGES = 40                # 5분봉 전구간은 21p 였다. 여유 두 배.
MAX_RETRY = 5
TZ = 'Asia/Seoul'


def _px(v) -> float:
    """
    ⚠️ 키움은 가격에 부호를 붙인다 ('+262500'). 하락 시 '-' 가 붙어
       그대로 float 하면 **음수 가격**이 된다. Iter6-0 에서 실측 확인.
    """
    s = str(v).strip().replace('+', '').replace('-', '')
    return abs(float(s)) if s else float('nan')


def universe() -> list[tuple[str, str]]:
    """운영이 실제 거래한 종목. 하드코딩 없음."""
    c = psycopg2.connect(dbname='trading_system', user='postgres',
                         password=os.getenv('POSTGRES_PASSWORD'),
                         host='localhost')
    cur = c.cursor()
    cur.execute("""SELECT DISTINCT stock_code,
                          COALESCE(MAX(stock_name), stock_code)
                   FROM trades
                   WHERE stock_code ~ '^[0-9]{6}$'
                   GROUP BY stock_code ORDER BY stock_code""")
    rows = cur.fetchall()
    c.close()
    return rows


def _rows(resp) -> list:
    if not isinstance(resp, dict):
        return []
    for v in resp.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def fetch(api, code: str) -> tuple[list, dict]:
    """연속조회로 전 구간. 429 는 지수 백오프."""
    allrows, nk, cy, pages = [], '', 'N', 0
    stat = {'retries': 0, 'pages': 0, 'http429': 0, 'error': None}
    while pages < MAX_PAGES:
        delay = REQ_GAP
        for attempt in range(MAX_RETRY):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    r = api.get_minute_chart(
                        code, tic_scope=TIC,
                        cont_yn='Y' if pages else 'N', next_key=nk)
                break
            except Exception as e:
                msg = str(e)
                if '429' in msg:
                    stat['http429'] += 1
                stat['retries'] += 1
                if attempt == MAX_RETRY - 1:
                    stat['error'] = f'{type(e).__name__}: {msg[:60]}'
                    return allrows, stat
                # 지수 백오프
                time.sleep(delay * (2 ** attempt))
        rr = _rows(r)
        if not rr:
            break
        allrows += rr
        nk, cy = r.get('next_key', ''), r.get('cont_yn', 'N')
        pages += 1
        # 보유 한계 도달하면 멈춘다
        oldest = min(str(x.get('cntr_tm', '')) for x in rr if x.get('cntr_tm'))
        if cy != 'Y' or (oldest and oldest[:8] <= START_FLOOR):
            break
        time.sleep(REQ_GAP)
    stat['pages'] = pages
    return allrows, stat


def to_frame(rows: list) -> pd.DataFrame:
    recs = []
    for x in rows:
        t = str(x.get('cntr_tm', ''))
        if len(t) < 12:
            continue
        recs.append({
            'datetime': t, 'open': _px(x.get('open_pric')),
            'high': _px(x.get('high_pric')), 'low': _px(x.get('low_pric')),
            'close': _px(x.get('cur_prc')),
            'volume': int(str(x.get('trde_qty', '0')).replace('+', '')
                          .replace('-', '') or 0),
            'acc_volume': int(str(x.get('acc_trde_qty', '0'))
                              .replace('+', '').replace('-', '') or 0),
        })
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs)
    # ⚠️ KST 로 localize. UTC 변환 금지 — 장중 시각 판정이 로컬 기준이다.
    df['datetime'] = pd.to_datetime(df['datetime'],
                                    format='%Y%m%d%H%M%S').dt.tz_localize(TZ)
    df = df.set_index('datetime').sort_index()
    df = df[~df.index.duplicated(keep='last')]
    return df


def validate(df: pd.DataFrame) -> dict:
    """⚠️ 결측을 채우지 않는다. 거래 없음과 데이터 빠짐을 구분할 수 없다."""
    v = {'bars': len(df), 'time_reversal': 0, 'duplicate': 0,
         'ohlc_invalid': 0, 'negative_volume': 0, 'nan': 0,
         'last_bar_ok': False, 'pass': False}
    if df.empty:
        return v
    v['time_reversal'] = int((df.index.to_series().diff()
                              .dt.total_seconds() < 0).sum())
    v['duplicate'] = int(df.index.duplicated().sum())
    bad = ((df['low'] > df['open']) | (df['low'] > df['close'])
           | (df['high'] < df['open']) | (df['high'] < df['close'])
           | (df['high'] < df['low']) | (df[['open', 'high', 'low', 'close']]
                                         <= 0).any(axis=1))
    v['ohlc_invalid'] = int(bad.sum())
    v['negative_volume'] = int((df['volume'] < 0).sum())
    v['nan'] = int(df[['open', 'high', 'low', 'close', 'volume']]
                   .isna().sum().sum())
    last = df.iloc[-1]
    v['last_bar_ok'] = bool(last['low'] <= last['close'] <= last['high']
                            and last['close'] > 0)
    v['pass'] = (v['time_reversal'] == 0 and v['duplicate'] == 0
                 and v['ohlc_invalid'] == 0 and v['negative_volume'] == 0
                 and v['nan'] == 0 and v['last_bar_ok'])
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    now = datetime.now()
    hm = now.hour * 100 + now.minute
    if 900 <= hm <= 1530 and not a.force:
        print('⛔ 장중이다. 운영 프로세스와 토큰을 공유하므로 대량 조회를')
        print('   거부한다. 장 마감 후 실행하거나 --force 를 쓴다.')
        sys.exit(1)

    uni = universe()
    if a.limit:
        uni = uni[:a.limit]

    t0 = time.perf_counter()
    print('=' * 74)
    print('  VWAP+AI 5분봉 데이터셋 구축')
    print('=' * 74)
    print(f'  대상 {len(uni)}종목 (DB 실거래 기준 — 하드코딩 없음)')
    print(f'  틱 {TIC}분 · 요청간격 {REQ_GAP}s · 최대 {MAX_PAGES}p · '
          f'재시도 {MAX_RETRY}회 지수백오프')

    from kiwoom_api import KiwoomAPI
    with contextlib.redirect_stdout(io.StringIO()):
        api = KiwoomAPI()
        api.get_access_token()

    summary, valrows = [], []
    ok = failed = retries = 0
    for i, (code, name) in enumerate(uni, 1):
        rows, st = fetch(api, code)
        retries += st['retries']
        df = to_frame(rows)
        v = validate(df)
        if df.empty:
            failed += 1
            state = 'FAIL'
        else:
            with open(os.path.join(CACHE, f'{code}.pkl'), 'wb') as f:
                pickle.dump(df, f)
            ok += 1
            state = 'OK' if v['pass'] else 'WARN'
        rec = {'code': code, 'name': name, 'state': state,
               'bars': v['bars'], 'pages': st['pages'],
               'retries': st['retries'], 'http429': st['http429'],
               'start_date': str(df.index[0]) if len(df) else '',
               'end_date': str(df.index[-1]) if len(df) else '',
               'last_update': now.isoformat(timespec='seconds'),
               'error': st['error'] or ''}
        summary.append(rec)
        valrows.append({'code': code, **v})
        if i % 15 == 0 or i == len(uni):
            print(f'    {i}/{len(uni)}  성공 {ok} 실패 {failed} '
                  f'재시도 {retries}')

    el = time.perf_counter() - t0
    npass = sum(1 for v in valrows if v['pass'])
    nfail = len(valrows) - npass
    bars = sum(r['bars'] for r in summary)
    starts = [r['start_date'] for r in summary if r['start_date']]
    ends = [r['end_date'] for r in summary if r['end_date']]
    cov = ok / len(uni) * 100 if uni else 0

    with open(os.path.join(OUT, 'minute_dataset_summary.csv'), 'w',
              newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    with open(os.path.join(OUT, 'minute_validation_report.csv'), 'w',
              newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(valrows[0]))
        w.writeheader()
        w.writerows(valrows)
    meta = {'built_at': now.isoformat(timespec='seconds'), 'tic': TIC,
            'timezone': TZ, 'symbols': len(uni), 'collected': ok,
            'failed': failed, 'bars': bars,
            'start': min(starts) if starts else None,
            'end': max(ends) if ends else None,
            'validation_pass': npass, 'validation_fail': nfail,
            'elapsed_sec': round(el, 1)}
    with open(os.path.join(os.path.dirname(CACHE), 'meta.json'), 'w',
              encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    ready = (failed == 0 and nfail == 0 and ok > 0)
    print('\n==============================')
    print('VWAP DATASET BUILD')
    print('==============================')
    print(f'Target Symbols     {len(uni)}')
    print(f'Collected          {ok}')
    print(f'Failed             {failed}')
    print(f'Retry              {retries}')
    print(f'Bars               {bars:,}')
    print(f'Start Date         {min(starts) if starts else "-"}')
    print(f'End Date           {max(ends) if ends else "-"}')
    print(f'Coverage %         {cov:.1f}%')
    print(f'Validation PASS    {npass}')
    print(f'Validation FAIL    {nfail}')
    print(f'Elapsed Time       {el:.1f}s')
    print('==============================')
    print(f'BACKTEST READY     {"YES" if ready else "NO"}')
    print('==============================')

    if nfail:
        print('\n  검증 실패 상세')
        for v in valrows:
            if not v['pass']:
                bad = {k: x for k, x in v.items()
                       if k not in ('code', 'pass', 'bars') and x}
                print(f'    {v["code"]}  bars={v["bars"]}  {bad}')
    if failed:
        print('\n  수집 실패 상세')
        for r in summary:
            if r['state'] == 'FAIL':
                print(f'    {r["code"]} {r["name"][:12]}  {r["error"]}')


if __name__ == '__main__':
    main()
