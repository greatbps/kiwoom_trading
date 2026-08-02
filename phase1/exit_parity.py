"""
Iteration 7-2 — Swing Exit Parity Audit & Strategy Alignment

목적은 수익률 최적화가 아니다. **검증한 전략과 운영 전략을 같게 만드는 것**이다.

━━━ 기존 분류가 57%를 OTHER 로 버리고 있었다 ━━━━━━━━━━━━━━━━━━━

  Iteration 5 의 매핑 21.9% 는 분류기 결함이 섞인 값이었다.
  정규식이 `장마감` 만 보고 `장 마감`(공백 포함)을 놓쳐, 시간 청산 83건이
  통째로 OTHER 로 갔다. 원문을 다시 보고 분류를 고친다.

사용법:
    python -m phase1.exit_parity
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))

OUT = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(OUT, n)          # noqa: E731

# ── Live exit_reason → 분류 태그 ─────────────────────────────────────────
#
# ⚠️ 순서가 중요하다. 위에서부터 먼저 맞는 것을 쓴다.
#    `장 마감` 처럼 공백이 들어간 표기를 놓치면 대량 오분류가 난다.
RULES = [
    (r'초기\s*실패\s*컷|EARLY\s*FAILURE|EARLY_FAIL', 'EARLY_FAILURE'),
    (r'오버나이트|OVERNIGHT', 'OVERNIGHT_BLOCK'),
    (r'MA5[_ ]?EXIT|MA5', 'MA5_EXIT'),
    (r'DRAWDOWN', 'DRAWDOWN_STOP'),
    (r'SWING_HARD_STOP', 'SWING_HARD_STOP'),
    (r'STRUCTURE_STOP|구조\s*손절', 'STRUCTURE_STOP'),
    (r'HARD[_ ]?STOP', 'HARD_STOP'),
    (r'트레일링|TRAIL', 'TRAILING'),
    (r'BREAK.?EVEN|BE[_ ]STOP|본전', 'BREAK_EVEN'),
    (r'익절|TAKE[_ ]?PROFIT|\bTP\d?\b', 'TAKE_PROFIT'),
    # 시간 청산 — `장 마감`(공백) · `최종 강제 청산` 을 반드시 잡는다
    # ⚠️ `시간 기반 청산` · `시간 초과` · `장 마감`(공백) 표기가 뒤섞여 있다.
    #    하나라도 놓치면 대량 오분류가 난다 — 실제로 83건이 그렇게 샜다.
    (r'강제\s*청산|장\s*마감|EOD|시간\s*(초과|기반)|TIME[_ ]?EXIT', 'TIME_EXIT'),
    (r'약화\s*신호|VWAP\s*하향|EMA\d*↓|SQUEEZE|모멘텀\s*반전', 'STRUCTURE_WEAK'),
    (r'MFE\s*부족|NO[_ ]?PROGRESS', 'NO_PROGRESS'),
    (r'손절|STOP[_ ]?LOSS', 'STOP_LOSS'),
]

# ── 분류 태그 → Backtest 규칙 ────────────────────────────────────────────
#
# 판단 기준은 "나쁜가" 가 아니라 "같은 의미인가" 다.
MAP = {
    'HARD_STOP':       ('SL', '손실 제한 — 백테스트 SL 과 동일 의미'),
    'SWING_HARD_STOP': ('SL', '손실 제한 (SWING fallback)'),
    'STRUCTURE_STOP':  ('SL', '구조 기반 손절 — SL 의 가격 산출 방식 차이'),
    'STOP_LOSS':       ('SL', '손실 제한'),
    'EARLY_FAILURE':   ('SL', '시간 제한이 붙은 손절 — 손실 제한이라는 점에서 SL'),
    'TAKE_PROFIT':     ('TP', '이익 실현'),
    'TRAILING':        ('TRAIL', '고점 대비 되돌림 청산'),
    'BREAK_EVEN':      ('BE_STOP', '본전 보호'),
    'TIME_EXIT':       ('MAX_HOLD', '보유 기간 상한 — 일봉 MAX_HOLD 와 동일 의미'),
    'OVERNIGHT_BLOCK': ('MAX_HOLD', '당일 보유 상한 — MAX_HOLD 를 1일로 둔 것'),
    'MA5_EXIT':        (None, '이동평균 이탈 — 백테스트에 대응 규칙 없음'),
    'DRAWDOWN_STOP':   (None, '계좌 단위 리스크 — 거래 단위 청산 규칙이 아님'),
    'STRUCTURE_WEAK':  (None, '구조/모멘텀 약화 — 백테스트에 대응 규칙 없음'),
    'NO_PROGRESS':     (None, '일정 시간 내 진전 없음 — 대응 규칙 없음'),
    'OTHER':           (None, '분류 실패'),
}


def classify(reason: str) -> str:
    r = reason or ''
    for pat, tag in RULES:
        if re.search(pat, r, re.IGNORECASE):
            return tag
    return 'OTHER'


def conn():
    return psycopg2.connect(dbname='trading_system', user='postgres',
                            password=os.getenv('POSTGRES_PASSWORD'),
                            host='localhost')


def main():
    cur = conn().cursor()
    rep = {'created_at': datetime.now().isoformat(timespec='seconds')}

    print('=' * 84)
    print('  Iteration 7-2 — Exit Parity Audit')
    print('=' * 84)

    # ═══ Task 1 — Live Exit 전체 재분류 ═════════════════════════════════
    cur.execute("""
        SELECT s.trade_id, s.stock_code, s.stock_name, s.trade_time,
               s.price, s.quantity, s.exit_reason, s.realized_profit,
               s.profit_rate, s.holding_minutes, s.strategy_name,
               b.trade_time AS buy_time, b.price AS buy_price
        FROM trades s
        LEFT JOIN LATERAL (
            SELECT trade_time, price FROM trades b
            WHERE b.trade_type='BUY' AND b.stock_code=s.stock_code
              AND b.trade_time <= s.trade_time
            ORDER BY b.trade_time DESC LIMIT 1
        ) b ON TRUE
        WHERE s.trade_type='SELL' AND s.realized_profit IS NOT NULL
        ORDER BY s.trade_time""")

    rows = []
    for (tid, cd, nm, ts, px, qty, xr, rp, pr, hm, sn, bt, bp) in cur.fetchall():
        tag = classify(xr)
        bt_rule, basis = MAP[tag]
        hold_d = (ts.date() - bt.date()).days if bt else None
        rows.append({
            'trade_id': tid, 'symbol': cd, 'name': nm,
            'entry_date': str(bt)[:19] if bt else '',
            'entry_price': float(bp) if bp else None,
            'exit_date': str(ts)[:19], 'exit_price': float(px),
            'holding_period_days': hold_d,
            'holding_minutes': hm,
            'profit_loss': float(rp), 'profit_rate': float(pr) if pr else None,
            'live_exit_reason': (xr or '')[:80],
            'exit_class': tag,
            'backtest_rule': bt_rule or '(대응 없음)',
            'mapping_basis': basis,
            'strategy_horizon': 'SWING' if (sn or '').lower() == 'swing'
                                else 'INTRADAY',
        })

    with open(P('live_exit_analysis.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    cnt = Counter(r['exit_class'] for r in rows)
    print(f'\n  Task 1 — Live 청산 {n}건 재분류')
    print(f'  {"분류":<18}{"건":>5}{"비중%":>8}{"승률%":>8}{"순손익":>13}'
          f'{"평균보유일":>10}')
    for tag, c in cnt.most_common():
        g = [r for r in rows if r['exit_class'] == tag]
        wins = sum(1 for r in g if r['profit_loss'] > 0)
        net = sum(r['profit_loss'] for r in g)
        hd = [r['holding_period_days'] for r in g
              if r['holding_period_days'] is not None]
        print(f'  {tag:<18}{c:>5}{c/n*100:>8.1f}{wins/c*100:>8.1f}'
              f'{net:>13,.0f}'
              f'{(sum(hd)/len(hd) if hd else 0):>10.1f}')
    unclassified = cnt.get('OTHER', 0)
    print(f'\n  분류 실패 {unclassified}건 '
          f'({unclassified/n*100:.1f}%)')

    # ═══ Task 2 — Backtest Mapping ══════════════════════════════════════
    mapped = sum(1 for r in rows if r['backtest_rule'] != '(대응 없음)')
    cov = mapped / n * 100
    print('\n' + '-' * 84)
    print('  Task 2 — Backtest Exit Mapping')
    print('-' * 84)
    print(f'  {"Live 분류":<18}{"→ Backtest":<12}{"건":>5}{"비중%":>8}   근거')
    map_rows = []
    for tag, c in cnt.most_common():
        bt_rule, basis = MAP[tag]
        map_rows.append({'live_class': tag,
                         'backtest_rule': bt_rule or '(대응 없음)',
                         'trades': c, 'pct': round(c / n * 100, 1),
                         'basis': basis})
        print(f'  {tag:<18}{(bt_rule or "(없음)"):<12}{c:>5}{c/n*100:>8.1f}'
              f'   {basis[:38]}')
    with open(P('exit_mapping.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(map_rows[0]))
        w.writeheader()
        w.writerows(map_rows)

    print(f'\n  Mapping 커버리지 {mapped}/{n} = {cov:.1f}%   '
          f'목표 90% → {"✅ PASS" if cov >= 90 else "❌ FAIL"}')
    print(f'  (Iteration 5 측정 21.9% — 그 값은 분류기 결함이 섞여 있었다.')
    print(f'   `장마감` 정규식이 `장 마감`(공백)을 놓쳐 시간청산 83건이 OTHER 였다)')
    rep['task1_2'] = {'total': n, 'classes': dict(cnt),
                      'unclassified': unclassified,
                      'mapping_coverage_pct': round(cov, 1),
                      'pass': cov >= 90}

    # ═══ 5-1 OVERNIGHT_BLOCK ════════════════════════════════════════════
    print('\n' + '-' * 84)
    print('  5-1 OVERNIGHT_BLOCK 분석')
    print('-' * 84)
    ob = [r for r in rows if r['exit_class'] == 'OVERNIGHT_BLOCK']
    _ob_stat(ob, rows)
    with open(P('overnight_analysis.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        if ob:
            w = csv.DictWriter(f, fieldnames=list(ob[0]))
            w.writeheader()
            w.writerows(ob)
    rep['overnight'] = {'trades': len(ob),
                        'net': sum(r['profit_loss'] for r in ob),
                        'wins': sum(1 for r in ob if r['profit_loss'] > 0)}

    # ═══ 5-2 EARLY_FAILURE ══════════════════════════════════════════════
    print('\n' + '-' * 84)
    print('  5-2 EARLY_FAILURE 분석')
    print('-' * 84)
    ef = [r for r in rows if r['exit_class'] == 'EARLY_FAILURE']
    mins = []
    for r in ef:
        m = re.search(r'\(([\d.]+)분', r['live_exit_reason'])
        if m:
            mins.append(float(m.group(1)))
        r['minutes_to_exit'] = float(m.group(1)) if m else None
    net = sum(r['profit_loss'] for r in ef)
    prs = [r['profit_rate'] for r in ef if r['profit_rate'] is not None]
    print(f'  {len(ef)}건   승률 {sum(1 for r in ef if r["profit_loss"]>0)/max(1,len(ef))*100:.1f}%   '
          f'순손익 {net:,.0f}원   1건당 {net/max(1,len(ef)):,.0f}원')
    if mins:
        print(f'  진입 후 청산까지: 평균 {sum(mins)/len(mins):.1f}분  '
              f'최소 {min(mins):.0f}  최대 {max(mins):.0f}')
    if prs:
        print(f'  평균 손실률 {sum(prs)/len(prs):.2f}%  '
              f'최악 {min(prs):.2f}%')
    print('  ⚠️ 이후 반등 여부는 분봉이 없어 측정 불가 — 추정하지 않는다.')
    with open(P('early_failure_analysis.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        if ef:
            w = csv.DictWriter(f, fieldnames=list(ef[0]))
            w.writeheader()
            w.writerows(ef)
    rep['early_failure'] = {'trades': len(ef), 'net': net,
                            'avg_minutes': round(sum(mins)/len(mins), 1)
                            if mins else None,
                            'avg_loss_pct': round(sum(prs)/len(prs), 2)
                            if prs else None}

    # ═══ 5-3 MA5_EXIT ═══════════════════════════════════════════════════
    print('\n' + '-' * 84)
    print('  5-3 MA5_EXIT 분석 — 손실 제한인가 수익 제한인가')
    print('-' * 84)
    ma5 = [r for r in rows if r['exit_class'] == 'MA5_EXIT']
    if ma5:
        w_ = [r for r in ma5 if r['profit_loss'] > 0]
        l_ = [r for r in ma5 if r['profit_loss'] <= 0]
        print(f'  {len(ma5)}건   이익 {len(w_)}건 / 손실 {len(l_)}건')
        print(f'  순손익 {sum(r["profit_loss"] for r in ma5):,.0f}원')
        print(f'  {"종목":<10}{"손익":>12}{"수익률%":>9}  보유일')
        for r in sorted(ma5, key=lambda x: x['profit_loss'])[:8]:
            print(f'  {(r["name"] or "")[:9]:<10}{r["profit_loss"]:>12,.0f}'
                  f'{(r["profit_rate"] or 0):>9.2f}'
                  f'{(r["holding_period_days"] if r["holding_period_days"] is not None else 0):>7}')
        verdict = ('손실 제한' if len(l_) > len(w_) else '수익 제한')
        print(f'\n  → 손실 {len(l_)} vs 이익 {len(w_)} → **{verdict}** 쪽으로 작동')
        rep['ma5'] = {'trades': len(ma5), 'wins': len(w_), 'losses': len(l_),
                      'net': sum(r['profit_loss'] for r in ma5),
                      'verdict': verdict}

    # ═══ §8 원장 무결성 ═════════════════════════════════════════════════
    print('\n' + '-' * 84)
    print('  §8 거래 원장 무결성')
    print('-' * 84)
    cur.execute("SELECT count(*) FROM trades WHERE stock_code LIKE 'TEST%'")
    n_test = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM trades s WHERE s.trade_type='SELL'
      AND s.realized_profit IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM trades b WHERE b.trade_type='BUY'
        AND b.stock_code=s.stock_code AND b.trade_time <= s.trade_time)""")
    orphan = cur.fetchone()[0]
    matched = sum(1 for r in rows if r['entry_date'])
    print(f'  TEST 종목코드 행                {n_test}건')
    print(f'  선행 BUY 가 없는 SELL           {orphan}/{n} '
          f'({orphan/n*100:.1f}%)')
    print(f'  BUY→SELL lifecycle 연결됨       {matched}/{n} '
          f'({matched/n*100:.1f}%)')
    print('  ⚠️ 시각 기준 LATERAL 조인으로 이어 붙였다. 같은 종목을 여러 번')
    print('     매매하면 최근 BUY 에 붙으므로 부분청산 케이스는 부정확할 수 있다.')
    rep['ledger'] = {'test_rows': n_test, 'orphan_sells': orphan,
                     'lifecycle_matched': matched, 'total': n}

    with open(P('exit_parity_report.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: live_exit_analysis.csv · exit_mapping.csv · '
          f'overnight_analysis.csv · early_failure_analysis.csv')


def _ob_stat(ob, rows):
    if not ob:
        print('  해당 없음')
        return
    net = sum(r['profit_loss'] for r in ob)
    wins = sum(1 for r in ob if r['profit_loss'] > 0)
    print(f'  {len(ob)}건   승률 {wins/len(ob)*100:.1f}%   '
          f'순손익 {net:,.0f}원   1건당 {net/len(ob):,.0f}원')
    print(f'  {"종목":<10}{"손익":>12}{"수익률%":>9}  보유일  전략')
    for r in sorted(ob, key=lambda x: x['profit_loss']):
        print(f'  {(r["name"] or "")[:9]:<10}{r["profit_loss"]:>12,.0f}'
              f'{(r["profit_rate"] or 0):>9.2f}'
              f'{(r["holding_period_days"] if r["holding_period_days"] is not None else 0):>7}'
              f'  {r["strategy_horizon"]}')
    sw = [r for r in ob if r['strategy_horizon'] == 'SWING']
    print(f'\n  이 중 SWING {len(sw)}건 — '
          f'스윙은 정의상 오버나이트를 전제로 한다.')
    print('  스윙 포지션을 14:50 에 강제 청산하면 전략이 성립하지 않는다.')


if __name__ == '__main__':
    main()
