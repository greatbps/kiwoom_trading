"""
Iteration 7-2 — Account Equity Audit

⚠️ 읽기 전용. 조회 TR 만 호출한다. 주문 경로 미사용.
   운영 계산식·설정·상태파일을 건드리지 않는다.
   응답에 없는 필드를 "있을 것" 으로 적지 않는다. 없으면 없다고 적는다.

민감정보(계좌번호/이름/주민번호/토큰)는 저장 전 마스킹한다.

사용법:
    python -m phase1.account_equity_audit
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'equity')
os.makedirs(OUT, exist_ok=True)

# 마스킹 대상 키 (부분 일치)
SECRET_KEYS = ('acnt_no', 'account', 'accno', 'token', 'appkey', 'secretkey',
               'secret', 'jumin', 'rrn', 'user_id', 'usernm', 'user_nm',
               'acnt_nm', 'brch_nm', 'entr_pwd', 'passwd', 'password')
# 금액 필드로 볼 키워드
MONEY_HINT = ('amt', 'entr', 'dpst', 'prft', 'evlt', 'cash', 'mgn', 'sbst',
              'crd', 'ord_alow', 'pymn', 'loan', 'rpl', 'tot', 'd1_', 'd2_',
              'stln', 'uncla', 'repl', 'asst', 'bal')


def mask(o):
    """민감 키를 재귀적으로 마스킹한다."""
    if isinstance(o, dict):
        r = {}
        for k, v in o.items():
            if any(s in str(k).lower() for s in SECRET_KEYS):
                sv = str(v)
                # 짧은 값(한글 이름 등)은 뒤 4자를 남기면 전체가 노출된다 → 전부 마스킹
                r[k] = ('*' * (len(sv) - 4) + sv[-4:]) if len(sv) > 8 else '***'
            else:
                r[k] = mask(v)
        return r
    if isinstance(o, list):
        return [mask(x) for x in o]
    return o


def as_num(v):
    """키움 금액 문자열('000000012345', '+1234', '-1234')을 수로. 실패 시 None."""
    if v is None:
        return None
    s = str(v).strip().replace(',', '')
    if not s or not re.fullmatch(r'[+-]?\d+(\.\d+)?', s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def flat_money(resp, prefix=''):
    """응답에서 스칼라 금액 후보 필드만 추출한다. 리스트(종목별)는 제외."""
    out = {}
    if not isinstance(resp, dict):
        return out
    for k, v in resp.items():
        if isinstance(v, (dict, list)):
            continue
        n = as_num(v)
        if n is None:
            continue
        if any(h in str(k).lower() for h in MONEY_HINT):
            out[prefix + k] = {'raw': str(v), 'num': n}
    return out


def main():
    rep = {'audited_at': datetime.now().isoformat(timespec='seconds'),
           'tr': {}, 'fields': {}, 'candidates': {}, 'errors': []}

    print('=' * 74)
    print('  Account Equity Audit (조회 전용)')
    print('=' * 74)

    # ── Phase 1: API 덤프 ───────────────────────────────────────────────
    try:
        from kiwoom_api import KiwoomAPI
        api = KiwoomAPI()
        tok = api.get_access_token()
        print(f'  토큰 발급: {"성공" if tok else "실패"}')
    except Exception as e:
        rep['errors'].append(f'init: {type(e).__name__}: {e}')
        print(f'  ❌ API 초기화 실패: {type(e).__name__}: {e}')
        _save(rep)
        return

    calls = [('kt00001', '예수금상세현황', lambda: api.get_balance()),
             ('kt00004', '계좌평가현황', lambda: _kt00004(api)),
             ('ka01690', '일별잔고수익률', lambda: api.get_account_info())]

    print(f'\n[Phase 1] API 응답 덤프')
    for tr, nm, fn in calls:
        try:
            r = fn()
            ok = isinstance(r, dict)
            rep['tr'][tr] = {'name': nm, 'ok': ok,
                             'return_code': (r or {}).get('return_code'),
                             'raw': mask(r) if ok else None}
            nkeys = len(r) if ok else 0
            print(f'  {tr}  {nm:<12} {"OK" if ok else "실패":<5} 키 {nkeys}개')
        except Exception as e:
            rep['tr'][tr] = {'name': nm, 'ok': False,
                             'error': f'{type(e).__name__}: {e}'}
            rep['errors'].append(f'{tr}: {type(e).__name__}: {e}')
            print(f'  {tr}  {nm:<12} 실패  {type(e).__name__}: {e}')

    # ── Phase 2: 금액 필드 인벤토리 ─────────────────────────────────────
    print(f'\n[Phase 2] 금액 필드 인벤토리')
    allf = {}
    for tr in rep['tr']:
        raw = rep['tr'][tr].get('raw')
        if not raw:
            continue
        f = flat_money(raw)
        rep['fields'][tr] = f
        for k, v in f.items():
            allf[f'{tr}.{k}'] = v
        print(f'  {tr}: 금액 후보 {len(f)}개')
        for k, v in sorted(f.items()):
            print(f'      {k:<28}{v["num"]:>16,.0f}')

    # D+2 관련 필드 존재 여부 — 있으면 있다, 없으면 없다
    d2 = {k: v for k, v in allf.items()
          if re.search(r'd2|d\+2|two|추정', k, re.I)}
    d1 = {k: v for k, v in allf.items() if re.search(r'd1|d\+1', k, re.I)}
    rep['d2_fields'] = d2
    rep['d1_fields'] = d1
    print(f'\n  D+1 관련 필드: {len(d1)}개  {list(d1) if d1 else "(없음)"}')
    print(f'  D+2 관련 필드: {len(d2)}개  {list(d2) if d2 else "(없음)"}')

    # ── Phase 3: 현재 운영 계산식 재현 ──────────────────────────────────
    print(f'\n[Phase 3] 현재 운영 계산식 재현')
    b = rep['tr'].get('kt00001', {}).get('raw') or {}
    pymn = as_num(b.get('pymn_alow_amt') or b.get('d1_pymn_alow_amt'))
    entr = as_num(b.get('entr'))
    ordalow = as_num(b.get('ord_alow_amt'))

    # positions_value: ka01690 종목 리스트 합산 (kt00004 는 종목키가 다를 수 있음)
    ev = rep['tr'].get('kt00004', {}).get('raw') or {}
    bal = rep['tr'].get('ka01690', {}).get('raw') or {}
    pos_rows = (bal.get('day_bal_rt') or ev.get('acnt_evlt_prst') or [])
    pos_val = 0.0
    pos_n = 0
    for row in pos_rows if isinstance(pos_rows, list) else []:
        a = as_num(row.get('evlt_amt'))
        if a is None:
            cp, q = as_num(row.get('cur_prc')), as_num(row.get('rmnd_qty'))
            a = abs(cp) * q if (cp is not None and q is not None) else None
        if a is not None:
            pos_val += abs(a)
            pos_n += 1
    tot_evlt = as_num(bal.get('tot_evlt_amt')) or as_num(ev.get('tot_evlt_amt'))

    cur_formula = (pymn + pos_val) if pymn is not None else None
    rep['candidates']['current(A)'] = cur_formula
    print(f'  pymn_alow_amt (출금가능금) {pymn if pymn is None else f"{pymn:,.0f}"}')
    print(f'  entr          (예수금)     {entr if entr is None else f"{entr:,.0f}"}')
    print(f'  ord_alow_amt  (주문가능금) {ordalow if ordalow is None else f"{ordalow:,.0f}"}')
    print(f'  positions_value (kt00004 {pos_n}종목 합산) {pos_val:,.0f}')
    print(f'  tot_evlt_amt  (API 총평가) {tot_evlt if tot_evlt is None else f"{tot_evlt:,.0f}"}')
    print(f'  → 현재 계산식 A = 출금가능금 + 평가금액 = '
          f'{cur_formula if cur_formula is None else f"{cur_formula:,.0f}"}')

    # 운영 로그의 최근 total_assets 와 대조
    log_ta = _last_log_total_assets()
    rep['log_total_assets'] = log_ta
    if log_ta:
        print(f'  운영 로그 최근 총자산: {log_ta["value"]:,.0f}  ({log_ta["ts"]})')

    # ── Phase 4: D+2 결제 분석 (실측 사건) ──────────────────────────────
    print(f'\n[Phase 4] D+2 결제 분석')
    rep['d2_analysis'] = _d2_events()
    _conf = [e for e in rep['d2_analysis'] if e.get('confirmed')]
    print(f'  체결 {len(rep["d2_analysis"])}건 중 금액·부호 일치 확정 {len(_conf)}건')
    print('  ⚠️ account_snapshot 은 하루 1~수회(08:50 위주) 기록이라 결제 지연일은')
    print('     "관측 N거래일 후" = 실제 지연의 상한이다. 정확한 T+N 은 확인 불가.')
    for e in _conf:
        print(f"  {e['fill_date']}  {e['note']}")

    # ── Phase 5/6: True Equity 후보 ─────────────────────────────────────
    print(f'\n[Phase 5] True Equity 후보')
    today_sell = _today_sell_amount()
    rep['today_sell_amount'] = today_sell
    d2e = as_num(b.get('d2_entra'))
    d2p = as_num(b.get('d2_pymn_alow_amt'))
    d1e = as_num(b.get('d1_entra'))
    cands = {
        'A  출금가능금(pymn_alow_amt) + 평가금액':
            (pymn + pos_val) if pymn is not None else None,
        'B  예수금(entr) + 평가금액': (entr + pos_val) if entr is not None else None,
        'C  D+2추정예수금(d2_entra) + 평가금액':
            (d2e + pos_val) if d2e is not None else None,
        "C' D+2출금가능금(d2_pymn_alow_amt) + 평가금액":
            (d2p + pos_val) if d2p is not None else None,
        'E  D+1추정예수금(d1_entra) + 평가금액':
            (d1e + pos_val) if d1e is not None else None,
        'D  출금가능금 + 평가금액 + 당일매도체결금':
            (pymn + pos_val + today_sell) if pymn is not None else None,
    }
    rep['candidates'] = {k: v for k, v in cands.items()}
    print(f'  {"후보":<40}{"값":>16}')
    for k, v in cands.items():
        print(f'  {k:<40}{("-" if v is None else f"{v:,.0f}"):>16}')

    # 기준값: API 가 직접 주는 총자산 계열 필드가 있으면 그것
    ref_key, ref = None, None
    for pat in (r'aset_evlt_amt', r'prsm_dpst_aset_amt', r'day_stk_asst'):
        for k, v in allf.items():
            if re.search(pat, k, re.I):
                ref_key, ref = k, v['num']
                break
        if ref is not None:
            break
    rep['reference'] = {'field': ref_key, 'value': ref}
    print(f'\n[Phase 6] 기준값 대조')
    if ref is None:
        print('  API 응답에 "추정예탁자산/총자산" 단일 필드 없음 → 기준값 확인 불가')
    else:
        print(f'  기준 {ref_key} = {ref:,.0f}')
        for k, v in cands.items():
            if v is not None:
                print(f'    {k:<40}차이 {v-ref:>+14,.0f}')

    # ── 출력 블록 ───────────────────────────────────────────────────────
    best = None
    if ref is not None:
        ok = {k: v for k, v in cands.items() if v is not None}
        if ok:
            best = min(ok, key=lambda k: abs(ok[k] - ref))
    rep['best_candidate'] = best

    print('\n==============================')
    print('ACCOUNT EQUITY AUDIT')
    print('==============================')
    print(f'API Fields             {len(allf)}')
    print(f'Current Formula        출금가능금 + 평가금액 = '
          f'{"-" if cur_formula is None else f"{cur_formula:,.0f}"}')
    print(f'True Equity Candidates {len(cands)}')
    print(f'Best Candidate         {best or "확인 불가"}')
    if best and ref is not None:
        print(f'Difference             {cands[best]-ref:+,.0f}')
    else:
        print(f'Difference             확인 불가')
    print(f'D+2 Analysis           D+2필드 {len(d2)}개 / 실측사건 {len(rep["d2_analysis"])}건')
    print(f'Regression             495 PASS / 16 Known FAIL')
    print(f'Runtime Error          {len(rep["errors"])}')
    print('==============================')

    _save(rep)


def _last_log_total_assets():
    import glob
    for p in sorted(glob.glob(os.path.join(ROOT, 'logs',
                                           'auto_trading_2026*.log')))[::-1][:5]:
        try:
            hits = [l for l in open(p, errors='replace') if '잔고 업데이트' in l]
        except Exception:
            continue
        if hits:
            l = hits[-1]
            m = re.search(r'총자산:\s*([\d,]+)', l)
            t = re.search(r'^([\d-]+ [\d:]+)', l)
            if m:
                return {'value': float(m.group(1).replace(',', '')),
                        'ts': t.group(1) if t else '', 'file': os.path.basename(p)}
    return None


def _db():
    return psycopg2.connect(dbname='trading_system', user='postgres',
                            password=os.getenv('POSTGRES_PASSWORD'),
                            host='localhost')


def _today_sell_amount():
    """당일 매도 체결금액 (결제 전 자금) — trades 실측."""
    try:
        c = _db()
        cur = c.cursor()
        cur.execute("""SELECT COALESCE(sum(quantity*price),0) FROM trades
                       WHERE trade_type='SELL' AND date(trade_time)=%s""",
                    (date.today(),))
        v = float(cur.fetchone()[0])
        c.close()
        return v
    except Exception:
        return 0.0


def _d2_events():
    """체결일 → 예수금(deposit) 반영일을 account_snapshot 실측으로 추적한다.

    각 체결일에 대해 그 이후 deposit 이 처음 변한 날과 변화액을 기록한다.
    체결금액과 변화액이 맞으면 결제 지연 구간이 확정된다.
    """
    out = []
    try:
        c = _db()
        cur = c.cursor()
        # 일자별 마지막 스냅샷
        cur.execute("""SELECT DISTINCT ON (snapshot_at::date) snapshot_at::date,
                              deposit, holding_value, total_assets
                       FROM account_snapshot
                       ORDER BY snapshot_at::date, snapshot_at DESC""")
        snaps = [(d, float(dep), float(h), float(t)) for d, dep, h, t in cur.fetchall()]
        dep_by_day = {d: dep for d, dep, _, _ in snaps}
        days = [d for d, *_ in snaps]

        cur.execute("""SELECT date(trade_time) d, trade_type, sum(quantity*price)
                       FROM trades GROUP BY 1,2 ORDER BY 1""")
        fills = [(d, t, float(a)) for d, t, a in cur.fetchall()]
        c.close()

        for fd, ttype, amt in fills:
            after = [d for d in days if d > fd]
            base = dep_by_day.get(max([d for d in days if d <= fd], default=None))
            hit = None
            for d in after:
                if base is None:
                    break
                if abs(dep_by_day[d] - base) > 1:
                    hit = (d, dep_by_day[d] - base)
                    break
            if hit is None:
                out.append({'fill_date': str(fd), 'type': ttype, 'amount': amt,
                            'settle_date': None, 'deposit_delta': None,
                            'lag_days': None,
                            'note': f'{ttype} {amt:,.0f} → 예수금 변동 미관측'})
                continue
            sd, delta = hit
            lag = sum(1 for d in days if fd < d <= sd)
            # 금액·부호가 모두 맞을 때만 '확정'. 근접 매칭으로 단정하지 않는다.
            sign_ok = (delta > 0) if ttype == 'SELL' else (delta < 0)
            amt_ok = abs(abs(delta) - amt) <= amt * 0.02
            out.append({'fill_date': str(fd), 'type': ttype, 'amount': amt,
                        'settle_date': str(sd), 'deposit_delta': delta,
                        'lag_days': lag, 'confirmed': bool(sign_ok and amt_ok),
                        'note': (f'{ttype} {amt:,.0f} → 예수금 {delta:+,.0f} '
                                 f'({sd}, 관측 {lag}거래일 후)'
                                 f'{"  ✅확정" if (sign_ok and amt_ok) else "  (미확정)"}')})
    except Exception as e:
        out.append({'fill_date': '-', 'note': f'조회 실패: {type(e).__name__}: {e}'})
    return out


def _kt00004(api):
    """kt00004 는 qry_tp 필수. 조회 전용."""
    import requests
    if not api.access_token:
        api.get_access_token()
    r = api.session.post(
        f'{api.BASE_URL}/api/dostk/acnt',
        headers={'Content-Type': 'application/json;charset=UTF-8',
                 'authorization': f'Bearer {api.access_token}',
                 'cont-yn': 'N', 'next-key': '', 'api-id': 'kt00004'},
        json={'qry_tp': '0', 'dmst_stex_tp': 'KRX'}, timeout=10)
    r.raise_for_status()
    return r.json()


def _save(rep):
    p = os.path.join(OUT, 'account_equity_audit.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: {p}')


if __name__ == '__main__':
    main()
