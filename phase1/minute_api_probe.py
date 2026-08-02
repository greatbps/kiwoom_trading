"""
Iteration 6-0 §1 — 키움 분봉 API 실측

⚠️ **추정 금지.** 실제로 조회해서 나온 값만 기록한다.
   조회가 안 되면 "안 됐다" 를 기록하지, 문서상 스펙을 적지 않는다.

━━━ 읽기 전용 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  조회 API(ka10080)만 호출한다. 주문 함수는 건드리지 않는다.
  운영 프로세스가 돌고 있으면 토큰을 공유하므로 조회 한도에
  영향을 줄 수 있다 — 최소 횟수만 호출한다.

사용법:
    python -m phase1.minute_api_probe            # 기본 (1/3/5/10분)
    python -m phase1.minute_api_probe --deep     # 연속조회 한계까지
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))
OUT = os.path.dirname(os.path.abspath(__file__))

SYMBOL = '005930'          # 삼성전자 — 유동성 최상위, 데이터 결손 가능성 최소
SCOPES = ['1', '3', '5', '10']
MAX_CONT = 20              # 연속조회 상한 (API 부하 억제)


def _rows(resp: dict) -> list:
    """응답에서 분봉 리스트를 꺼낸다. 키 이름을 모르면 추정하지 않는다."""
    if not isinstance(resp, dict):
        return []
    for k, v in resp.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def probe(api, scope: str, deep: bool) -> dict:
    r = {'tic_scope': scope, 'ok': False, 'error': None,
         'first_call_rows': 0, 'columns': [], 'sample': None,
         'cont_pages': 0, 'total_rows': 0,
         'oldest': None, 'newest': None, 'elapsed_sec': 0.0}
    t0 = time.perf_counter()
    try:
        resp = api.get_minute_chart(SYMBOL, tic_scope=scope)
        rows = _rows(resp)
        r['ok'] = bool(rows)
        r['first_call_rows'] = len(rows)
        r['return_code'] = resp.get('return_code')
        r['return_msg'] = str(resp.get('return_msg'))[:80]
        if rows:
            r['columns'] = sorted(rows[0].keys())
            r['sample'] = rows[0]
            r['total_rows'] = len(rows)
            r['cont_yn'] = resp.get('cont_yn')

            if deep and resp.get('cont_yn') == 'Y':
                nk, cy, pages = resp.get('next_key', ''), 'Y', 0
                allrows = list(rows)
                while cy == 'Y' and pages < MAX_CONT:
                    time.sleep(0.35)      # API 부하 억제
                    rp = api.get_minute_chart(SYMBOL, tic_scope=scope,
                                              cont_yn='Y', next_key=nk)
                    rr = _rows(rp)
                    if not rr:
                        break
                    allrows += rr
                    nk, cy = rp.get('next_key', ''), rp.get('cont_yn', 'N')
                    pages += 1
                r['cont_pages'] = pages
                r['total_rows'] = len(allrows)
                rows = allrows

            # 시각 필드 추정 없이 — 후보 키를 찾아 최소/최대만 본다
            tk = next((k for k in rows[0]
                       if 'dt' in k.lower() or 'tm' in k.lower()
                       or 'time' in k.lower()), None)
            if tk:
                vals = sorted(str(x.get(tk, '')) for x in rows if x.get(tk))
                r['time_field'] = tk
                r['oldest'], r['newest'] = (vals[0], vals[-1]) if vals else (None, None)
    except Exception as e:
        r['error'] = f'{type(e).__name__}: {e}'
    r['elapsed_sec'] = round(time.perf_counter() - t0, 2)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--deep', action='store_true')
    a = ap.parse_args()

    print('=' * 72)
    print('  키움 분봉 API 실측 (ka10080)')
    print('=' * 72)
    print(f'  종목 {SYMBOL}   연속조회 상한 {MAX_CONT}페이지')
    print('  ⚠️ 조회 API 만 호출한다. 주문 경로 미사용.')

    out = {'probed_at': datetime.now().isoformat(timespec='seconds'),
           'symbol': SYMBOL, 'api_id': 'ka10080', 'results': []}

    try:
        from kiwoom_api import KiwoomAPI
        api = KiwoomAPI()
        tok = api.get_access_token()
        out['token_ok'] = bool(tok)
        print(f'\n  토큰 발급: {"성공" if tok else "실패"}')
    except Exception as e:
        out['token_ok'] = False
        out['fatal'] = f'{type(e).__name__}: {e}'
        print(f'\n  ❌ API 초기화 실패: {type(e).__name__}: {e}')
        print('  → 분봉 조회 자체가 불가능하다. 실측 결과로 기록한다.')
        with open(os.path.join(OUT, 'minute_api_probe.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=str)
        return

    print(f'\n  {"틱":<5}{"조회":>6}{"1회 건수":>10}{"총 건수":>9}'
          f'{"연속":>6}{"소요s":>8}   기간')
    for sc in SCOPES:
        r = probe(api, sc, a.deep)
        out['results'].append(r)
        if r['error']:
            print(f'  {sc:<5}{"실패":>6}   {r["error"][:44]}')
            continue
        span = (f'{r.get("oldest","?")} ~ {r.get("newest","?")}'
                if r.get('oldest') else '시각 필드 미확인')
        print(f'  {sc:<5}{("OK" if r["ok"] else "빈응답"):>6}'
              f'{r["first_call_rows"]:>10}{r["total_rows"]:>9}'
              f'{r["cont_pages"]:>6}{r["elapsed_sec"]:>8.2f}   {span}')
        time.sleep(0.4)

    ok = [r for r in out['results'] if r['ok']]
    if ok:
        print(f'\n  응답 컬럼 ({len(ok[0]["columns"])}개):')
        print(f'    {", ".join(ok[0]["columns"])}')
        print(f'\n  샘플 1건:')
        print(f'    {json.dumps(ok[0]["sample"], ensure_ascii=False)[:200]}')

    with open(os.path.join(OUT, 'minute_api_probe.json'), 'w',
              encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: minute_api_probe.json')


if __name__ == '__main__':
    main()
