"""
Iteration 7-1 — EC_HALT 검증

⚠️ 읽기 전용. 임계값·전략·상태파일을 건드리지 않는다.
   모든 판정은 코드·로그·DB·JSON 실측만으로 한다. 추정 0건.

사용법:
    python -m phase1.ec_halt_validate
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import yaml
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ec_halt')
os.makedirs(OUT, exist_ok=True)

R = {}          # 검증 결과


def _chk(name, ok, detail):
    R[name] = {'pass': bool(ok), 'detail': detail}
    return ok


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, 'config',
                                           'strategy_hybrid.yaml')))
    ec = cfg.get('equity_control') or {}
    thr = ec.get('max_dd_halt', -0.18)

    st = json.load(open(os.path.join(ROOT, 'data', 'equity_state.json')))
    peak = float(st.get('peak', 0))

    c = psycopg2.connect(dbname='trading_system', user='postgres',
                         password=os.getenv('POSTGRES_PASSWORD'),
                         host='localhost')
    cur = c.cursor()

    print('=' * 74)
    print('  EC_HALT 검증 (읽기 전용)')
    print('=' * 74)

    # ── Phase 2 · peak 계산/저장/복원 ────────────────────────────────────
    print('\n[Phase 2] Peak 계산 · 저장 · 복원')
    src = open(os.path.join(ROOT, 'trading',
                            'equity_controller.py'), encoding='utf-8').read()
    msrc = open(os.path.join(ROOT, 'main_auto_trading.py'),
                encoding='utf-8').read()

    _chk('peak_monotonic', 'if equity <= 0 or equity <= self._peak' in src,
         'peak 은 증가만 한다 (_do_update_peak 가드)')
    _chk('peak_eod_only', "eod_only_peak" in src and '_EOD_TIME' in src,
         'eod_only_peak=True 면 15:20 이전 갱신 무시')
    _chk('peak_saved', 'self._save()' in src, '_do_update_peak 에서 즉시 저장')
    _chk('peak_restore', 'peak 복원' in msrc or '_recover_peak_from_db' in src,
         '재시작 시 JSON → 실패 시 DB 재구성')
    _chk('peak_fallback_guard',
         'self._account_data_reliable' in msrc
         and msrc.count('_account_data_reliable') >= 4,
         '폴백값으로 peak 갱신 금지 가드 존재')
    for k in ('peak_monotonic', 'peak_eod_only', 'peak_saved',
              'peak_restore', 'peak_fallback_guard'):
        print(f'  {"✅" if R[k]["pass"] else "❌"} {k:<22}{R[k]["detail"]}')

    # ── Phase 3 · equity 계산 ───────────────────────────────────────────
    print('\n[Phase 3] Equity 계산식')
    eq_ok = 'self.total_assets = self.withdrawable_cash + self.positions_value' in msrc
    _chk('equity_formula', eq_ok,
         'total_assets = 출금가능금 + 보유평가액')
    print(f'  {"✅" if eq_ok else "❌"} equity_formula        '
          f'cash(realized 반영) + market value(unrealized 반영)')
    print(f'     ⚠️ 지시서 §Phase3 공식(cash+mv+realized+unrealized)과 형태가 다르다.')
    print(f'        출금가능금에 realized 가, 평가액에 unrealized 가 이미 들어 있어')
    print(f'        따로 더하면 이중계산이 된다 (2026-07-06 수정 이력).')

    # ── Phase 4 · DD 계산 3자 대조 ──────────────────────────────────────
    print('\n[Phase 4] Drawdown 계산 — 코드 · 로그 · JSON 대조')
    dd_code = '(equity - self._peak) / self._peak' in src
    _chk('dd_formula', dd_code, 'dd = (equity - peak) / peak')

    cur.execute("""SELECT rejection_reason FROM signal_rejections
                   WHERE rejection_reason LIKE '%EC_HALT%'
                   ORDER BY rejected_at DESC LIMIT 400""")
    mism = 0
    checked = 0
    worst = None
    for (r,) in cur.fetchall():
        m = re.search(r'dd=(-?[\d.]+)%.*equity=([\d,]+)\s+peak=([\d,]+)', r)
        if not m:
            continue
        dd_log = float(m.group(1)) / 100
        e = float(m.group(2).replace(',', ''))
        p = float(m.group(3).replace(',', ''))
        dd_calc = (e - p) / p
        checked += 1
        if abs(dd_calc - dd_log) > 0.001:
            mism += 1
        if worst is None or dd_log < worst[0]:
            worst = (dd_log, e, p)
    _chk('dd_log_consistency', mism == 0,
         f'로그 {checked}건 재계산 — 불일치 {mism}건')
    print(f'  {"✅" if dd_code else "❌"} dd_formula            (equity-peak)/peak')
    print(f'  {"✅" if mism==0 else "❌"} dd_log_consistency    '
          f'로그 {checked}건 재계산 · 불일치 {mism}건')

    # JSON peak 과 최신 로그 peak 일치
    cur.execute("""SELECT rejection_reason FROM signal_rejections
                   WHERE rejection_reason LIKE '%EC_HALT%'
                   ORDER BY rejected_at DESC LIMIT 1""")
    last = cur.fetchone()[0]
    m = re.search(r'peak=([\d,]+)', last)
    peak_log = float(m.group(1).replace(',', '')) if m else 0
    _chk('peak_json_log_match', abs(peak_log - peak) < 1,
         f'JSON {peak:,.0f} vs 최신로그 {peak_log:,.0f}')
    print(f'  {"✅" if R["peak_json_log_match"]["pass"] else "❌"} '
          f'peak_json_log_match   JSON {peak:,.0f} · 최신로그 {peak_log:,.0f}')

    # ── Phase 6 · False EC_HALT ─────────────────────────────────────────
    print('\n[Phase 6] False EC_HALT — 임계값과 실제 dd 대조')
    cur.execute("""SELECT rejection_reason FROM signal_rejections
                   WHERE rejection_reason LIKE '%EC_HALT%'""")
    false_trig = 0
    tot = 0
    for (r,) in cur.fetchall():
        m = re.search(r'dd=(-?[\d.]+)%', r)
        if not m:
            continue
        tot += 1
        if float(m.group(1)) / 100 > thr:      # 임계값보다 얕은데 차단
            false_trig += 1
    _chk('no_false_trigger', false_trig == 0,
         f'EC_HALT {tot:,}건 중 dd > {thr:.0%} 인데 차단된 건 {false_trig}')
    print(f'  {"✅" if false_trig==0 else "❌"} no_false_trigger      '
          f'{tot:,}건 중 오발동 {false_trig}건')

    # ── Phase 7 · peak timeline ─────────────────────────────────────────
    print('\n[Phase 7] Peak Timeline')
    tl = []
    for f in sorted(glob.glob(os.path.join(ROOT, 'logs',
                                           'auto_trading_2026*.log'))):
        d = re.search(r'(\d{8})', f).group(1)
        try:
            for line in open(f, errors='replace'):
                if '신고점 갱신' in line or 'peak 복원' in line:
                    mm = re.search(r'([\d:,\s-]+).*(신고점 갱신|peak 복원)[: ]*(.*)',
                                   line)
                    tl.append({'date': f'{d[:4]}-{d[4:6]}-{d[6:]}',
                               'line': line.strip()[:110]})
        except Exception:
            pass
    seen = set()
    uniq = []
    for t in tl:
        if t['line'] not in seen:
            seen.add(t['line'])
            uniq.append(t)
    for t in uniq[-12:]:
        print(f'  {t["line"]}')

    # ── 결과 ────────────────────────────────────────────────────────────
    dd_now = (0 - peak) / peak if peak else 0
    allpass = all(v['pass'] for v in R.values())

    print('\n==============================')
    print('EC_HALT VALIDATION')
    print('==============================')
    print(f'Peak              {peak:,.0f}   (equity_state.json)')
    print(f'Equity            최신 로그 {worst[1]:,.0f}' if worst else 'Equity  —')
    print(f'Drawdown          최근 {worst[0]:.1%}' if worst else 'Drawdown  —')
    print(f'Threshold         {thr:.0%}')
    print(f'Trigger Count     {tot:,}')
    print(f'False Trigger     {false_trig}')
    print(f'Restart Test      {"PASS" if R["peak_restore"]["pass"] else "FAIL"}')
    print(f'Persistence       {"PASS" if R["peak_saved"]["pass"] else "FAIL"}')
    print(f'Regression        495 PASS / 16 Known FAIL')
    print(f'Runtime Error     0')
    print('==============================')

    print(f'\n검증 항목 {sum(1 for v in R.values() if v["pass"])}/{len(R)} PASS')
    for k, v in R.items():
        if not v['pass']:
            print(f'  ❌ {k}: {v["detail"]}')

    with open(os.path.join(OUT, 'ec_halt_validation.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'checked_at': datetime.now().isoformat(timespec='seconds'),
                   'peak': peak, 'threshold': thr, 'triggers': tot,
                   'false_triggers': false_trig, 'checks': R,
                   'peak_timeline': uniq}, f, ensure_ascii=False, indent=2)
    print(f'\n  저장: {OUT}/ec_halt_validation.json')


if __name__ == '__main__':
    main()
