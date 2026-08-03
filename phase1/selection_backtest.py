"""
Iteration 8-1 — Swing Entry Selection Optimization

⚠️ 진입 규칙을 바꾸지 않는다. 후보군은 전 케이스 완전히 동일하다.
   Entry Engine / Exit / 비용 / 슬롯 / 사이즈 전부 동일.
   **바뀌는 것은 "동점 후보 중 무엇을 고르는가" 뿐이다.**

후보 생성: swing_runner.SignalEngine 원본 (Iteration 8-0 Case A 와 동일)
데이터:    phase1/minute_cache/5m (Iteration 6) — 신규 수집 0

사용법:
    python -m phase1.selection_backtest
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'phase1', 'selection')
os.makedirs(OUT, exist_ok=True)

for _n in ('analyzers.patterns.manager', 'analyzers.swing.signal_engine',
           'trading.score_engine', 'swing_runner'):
    logging.getLogger(_n).setLevel(logging.ERROR)
logging.getLogger().setLevel(logging.ERROR)

from phase0.portfolio import PortfolioBacktest, sel_baseline, INITIAL_CAPITAL
from phase1.strategy_baseline import (load_daily, PullbackStrategy, MAX_SLOTS,
                                      metrics, WARMUP)

# Train / Test 분리 — Case E 가중치는 Train 에서만 고른다
TRAIN_END = pd.Timestamp('2026-01-31')


# ── 랭킹 지표 (모두 당일까지만 본다) ─────────────────────────────────────
def _rvol(df, i, win=20):
    if i < win:
        return 0.0
    v = df['volume'].values
    avg = v[i - win:i].mean()
    return float(v[i] / avg) if avg > 0 else 0.0


def _momentum(df, i, n=20):
    if i < n:
        return 0.0
    c = df['close'].values
    return float(c[i] / c[i - n] - 1.0) if c[i - n] > 0 else 0.0


def _trend_quality(df, i):
    """EMA 정배열 + MA50 기울기. 값이 클수록 추세가 깨끗하다."""
    if i < 60:
        return 0.0
    c = df['close'].iloc[:i + 1]
    e5 = c.ewm(span=5).mean().iloc[-1]
    e20 = c.ewm(span=20).mean().iloc[-1]
    e60 = c.ewm(span=60).mean().iloc[-1]
    px = float(c.iloc[-1])
    if e60 <= 0 or px <= 0:
        return 0.0
    align = (1.0 if px > e5 else 0.0) + (1.0 if e5 > e20 else 0.0) \
            + (1.0 if e20 > e60 else 0.0)
    ma50 = c.rolling(50).mean()
    slope = float((ma50.iloc[-1] / ma50.iloc[-11] - 1.0)) if len(ma50) > 11 \
        and not np.isnan(ma50.iloc[-11]) and ma50.iloc[-11] > 0 else 0.0
    spread = float((e5 - e60) / e60)
    return align + slope * 10.0 + spread * 2.0


def _atr_pct(df, i, win=14):
    if i < win + 1:
        return 0.0
    h = df['high'].values[i - win:i + 1]
    l = df['low'].values[i - win:i + 1]
    c = df['close'].values[i - win - 1:i]
    tr = np.maximum(h - l, np.maximum(abs(h - c), abs(l - c)))
    px = df['close'].values[i]
    return float(tr.mean() / px) if px > 0 else 0.0


def _zs(x):
    x = np.asarray(x, dtype=float)
    s = x.std()
    return (x - x.mean()) / s if s > 1e-12 else np.zeros_like(x)


# ── 선별 백테스트 ────────────────────────────────────────────────────────
class SelectionBacktest(PortfolioBacktest):
    """PortfolioBacktest 를 그대로 쓰되 점수 함수만 갈아 끼운다.

    phase0/portfolio.py 는 수정하지 않는다 (다른 이터레이션이 쓰는 공용 코드).
    """

    def __init__(self, *a, scorer=None, **kw):
        super().__init__(*a, **kw)
        self._scorer = scorer          # None = 현행 ScoreEngine

    def _score(self, sym, day):
        if self._scorer is None:
            return super()._score(sym, day)
        i = self._pos_of[sym].get(day)
        if i is None:
            return 0.0
        return self._scorer(self.data[sym], i)


def _composite(weights):
    """RVOL / Momentum / Trend / 저변동성 가중합. 종목 간 비교라 원단위 그대로."""
    wr, wm, wt, wv = weights

    def f(df, i):
        return (wr * _rvol(df, i)
                + wm * _momentum(df, i) * 10.0
                + wt * _trend_quality(df, i)
                - wv * _atr_pct(df, i) * 10.0)
    return f


CASES = [
    ('A', 'Current ScoreEngine (Top-3)', None),
    ('B', 'RVOL Ranking', lambda df, i: _rvol(df, i)),
    ('C', 'Momentum Ranking (20d)', lambda df, i: _momentum(df, i)),
    ('D', 'Trend Quality Ranking', lambda df, i: _trend_quality(df, i)),
]

# Case E 후보 가중치 — 최대 5개. Train 에서 고르고 Test 로만 평가한다.
E_GRID = [
    ('E1 RVOL+Trend',      (1.0, 0.0, 1.0, 0.0)),
    ('E2 Mom+Trend',       (0.0, 1.0, 1.0, 0.0)),
    ('E3 균등',            (1.0, 1.0, 1.0, 0.0)),
    ('E4 Trend-저변동',    (0.0, 0.0, 1.0, 1.0)),
    ('E5 RVOL+Mom',        (1.0, 1.0, 0.0, 0.0)),
]


def boot(trades, n=2000, seed=0):
    won = np.array([t.pnl_won for t in trades], dtype=float)
    if len(won) < 5:
        return {'n': len(won), 'pf_ci': [None, None], 'p_pf_below_1': None}
    rng = np.random.default_rng(seed)
    pfs = []
    for _ in range(n):
        b = rng.choice(won, size=len(won), replace=True)
        gp, gl = b[b > 0].sum(), -b[b <= 0].sum()
        if gl > 0:
            pfs.append(gp / gl)
    pfs = np.array(pfs)
    return {'n': len(won),
            'pf_ci': [round(float(np.percentile(pfs, 2.5)), 3),
                      round(float(np.percentile(pfs, 97.5)), 3)],
            'p_pf_below_1': round(float((pfs < 1).mean()), 3)}


def run_case(data, sigs, days, scorer, seed=0):
    bt = SelectionBacktest(data, sigs, days, sel_baseline, MAX_SLOTS,
                           seed=seed, scorer=scorer)
    return bt.run('x')


def main():
    t0 = time.perf_counter()
    cfg = yaml.safe_load(open(os.path.join(ROOT, 'config',
                                           'strategy_hybrid.yaml')))
    print('=' * 78)
    print('  Entry Selection Backtest — 진입 규칙 무변경, 선별만 비교')
    print('=' * 78)

    data = load_daily()
    days = sorted({d for df in data.values() for d in df.index})

    # ── Phase 1: 후보 생성 (전 케이스 공통, 1회만) ──────────────────────
    st = PullbackStrategy('A', 'Live Swing (Pullback)', 'swing_runner.SignalEngine')
    sigs = {}
    for sym, d in data.items():
        s = st.signals(sym, d, cfg)
        if s:
            sigs[sym] = s
    nsig = sum(len(v) for v in sigs.values())
    print(f'\n[Phase 1] 후보 생성 (swing_runner.SignalEngine 원본)')
    print(f'  종목 {len(data)} · 거래일 {len(days)} '
          f'({days[0].date()} ~ {days[-1].date()})')
    print(f'  bar {st.funnel["bars"]:,} → signal {st.funnel["signal"]:,} '
          f'→ 신호일 {nsig:,} (종목 {len(sigs)})')

    # 현행 ScoreEngine 점수 분포 — 동점이 얼마나 되는가
    probe = SelectionBacktest(data, sigs, days, sel_baseline, MAX_SLOTS,
                              scorer=None)
    dist = {}
    ties = binding = 0
    for day in days:
        c = [s for s in sigs if day in sigs[s]]
        if len(c) < 2:
            continue
        sc = [probe._score(s, day) for s in c]
        for v in sc:
            dist[round(v, 1)] = dist.get(round(v, 1), 0) + 1
        binding += 1
        top = max(sc)
        if sc.count(top) > 1:
            ties += 1
    print(f'\n[Phase 1-2] 현행 ScoreEngine 점수 분포 (후보 2개 이상인 날 {binding}일)')
    for v in sorted(dist, reverse=True):
        print(f'    {v:>5} 점 : {dist[v]:>5,}건')
    print(f'  최고점 동점일 {ties}/{binding}일 ({ties/max(1,binding)*100:.1f}%)')
    print(f'  → 동점은 종목코드 순서로 갈린다 (sorted 안정정렬). 선별 아님.')

    rep = {'run_at': datetime.now().isoformat(timespec='seconds'),
           'universe': len(data), 'days': len(days),
           'signal_days': nsig, 'symbols_with_signal': len(sigs),
           'score_distribution': {str(k): v for k, v in dist.items()},
           'tie_days': ties, 'candidate_days': binding,
           'common': {'entry_engine': 'swing_runner.SignalEngine (무변경)',
                      'exit': 'phase0.portfolio.EXIT_PROFILE',
                      'commission': 0.0035, 'max_slots': MAX_SLOTS},
           'cases': {}}

    # ── Phase 2: Case A~D 전체 구간 ─────────────────────────────────────
    print(f'\n[Phase 2] Selection 비교 (전체 구간)')
    hdr = (f'  {"Case":<30}{"Trades":>7}{"Win%":>7}{"PF":>7}{"Exp%":>8}'
           f'{"MDD%":>8}{"Hold":>6}')
    print(hdr)
    results = {}
    for key, name, sc in CASES:
        r = run_case(data, sigs, days, sc)
        m = metrics(r, days)
        results[key] = (name, r, m)
        rep['cases'][key] = {'name': name, 'metrics': m,
                             'bootstrap': boot(r.trades)}
        print(f'  {key+" "+name[:27]:<30}{m["trades"]:>7}{m["win_rate"]:>7}'
              f'{m["pf"]:>7}{m["expectancy"]:>8}{m["mdd"]:>8}{m["avg_hold"]:>6}')

    # 통제군 — 무작위 선택. 선별에 실력이 있는지 판정하는 기준선.
    rnd_pf, rnd_m = [], []
    for s in range(20):
        rng = np.random.default_rng(s)
        r = run_case(data, sigs, days,
                     (lambda df, i, _r=rng: float(_r.random())), seed=s)
        mm = metrics(r, days)
        if mm['trades']:
            rnd_pf.append(mm['pf'] if mm['pf'] != float('inf') else 5.0)
            rnd_m.append(mm)
    rep['random_control'] = {
        'runs': len(rnd_pf),
        'pf_mean': round(float(np.mean(rnd_pf)), 3),
        'pf_p5': round(float(np.percentile(rnd_pf, 5)), 3),
        'pf_p95': round(float(np.percentile(rnd_pf, 95)), 3),
        'trades_mean': round(float(np.mean([m['trades'] for m in rnd_m])), 1),
    }
    rc = rep['random_control']
    print(f'  {"— 무작위 선택 (통제군, 20회)":<30}{rc["trades_mean"]:>7}'
          f'{"":>7}{rc["pf_mean"]:>7}   PF 5~95% [{rc["pf_p5"]}, {rc["pf_p95"]}]')

    # ── Phase 2-2: Case E — Train 에서 고르고 Test 로 평가 ───────────────
    tr_days = [d for d in days if d <= TRAIN_END]
    te_days = [d for d in days if d > TRAIN_END]
    print(f'\n[Phase 2-2] Case E — Train {len(tr_days)}일 / Test {len(te_days)}일')
    print(f'  {"조합":<22}{"Train PF":>10}{"Test PF":>10}{"Test Trades":>13}')
    e_res = []
    for nm, w in E_GRID:
        f = _composite(w)
        rtr = run_case(data, sigs, tr_days, f)
        rte = run_case(data, sigs, te_days, f)
        mtr, mte = metrics(rtr, tr_days), metrics(rte, te_days)
        e_res.append((nm, w, mtr, mte, rte))
        print(f'  {nm:<22}{mtr["pf"]:>10}{mte["pf"]:>10}{mte["trades"]:>13}')
    best_e = max(e_res, key=lambda x: x[2]['pf'])       # Train 기준으로 선택
    print(f'  → Train 최고: {best_e[0]}  (Test PF {best_e[3]["pf"]})')

    # 선택된 E 를 전체 구간으로도 한 번 (참고)
    r_e = run_case(data, sigs, days, _composite(best_e[1]))
    m_e = metrics(r_e, days)
    rep['cases']['E'] = {
        'name': f'Composite — {best_e[0]} (Train 선택)',
        'weights': list(best_e[1]),
        'train_pf': best_e[2]['pf'], 'test_pf': best_e[3]['pf'],
        'test_trades': best_e[3]['trades'],
        'metrics': m_e, 'bootstrap': boot(r_e.trades),
        'grid': [{'name': n, 'weights': list(w),
                  'train_pf': a['pf'], 'test_pf': b['pf'],
                  'test_trades': b['trades']} for n, w, a, b, _ in e_res],
    }
    results['E'] = (rep['cases']['E']['name'], r_e, m_e)
    print(f'  {"E "+best_e[0]+" (전체구간)":<30}{m_e["trades"]:>7}'
          f'{m_e["win_rate"]:>7}{m_e["pf"]:>7}{m_e["expectancy"]:>8}'
          f'{m_e["mdd"]:>8}{m_e["avg_hold"]:>6}')

    # ── Phase 3/4: 출력 ─────────────────────────────────────────────────
    base = rep['cases']['A']
    print('\n' + '=' * 30)
    print('ENTRY SELECTION BACKTEST')
    print('=' * 30)
    print(f'{"Method":<30}{"Trades":>7}{"Win%":>7}{"PF":>7}{"Exp%":>8}'
          f'{"MDD%":>8}{"Hold":>6}{"PF 95%CI":>18}{"P(PF<1)":>9}')
    for k in ('A', 'B', 'C', 'D', 'E'):
        s = rep['cases'][k]
        m, bs = s['metrics'], s['bootstrap']
        ci = f'[{bs["pf_ci"][0]}, {bs["pf_ci"][1]}]' if bs['pf_ci'][0] is not None else '-'
        print(f'{k+" "+s["name"][:27]:<30}{m["trades"]:>7}{m["win_rate"]:>7}'
              f'{m["pf"]:>7}{m["expectancy"]:>8}{m["mdd"]:>8}{m["avg_hold"]:>6}'
              f'{ci:>18}{str(bs["p_pf_below_1"]):>9}')
    print('=' * 30)

    # 채택 판정 — 세 조건 모두
    bA, mA = base['bootstrap'], base['metrics']
    adopt = []
    for k in ('B', 'C', 'D', 'E'):
        s = rep['cases'][k]
        m, bs = s['metrics'], s['bootstrap']
        if bs['pf_ci'][0] is None:
            continue
        ok = (m['pf'] > mA['pf']
              and bs['pf_ci'][0] > bA['pf_ci'][0]
              and bs['p_pf_below_1'] < bA['p_pf_below_1'])
        s['adoptable'] = bool(ok)
        s['mdd_worse'] = bool(abs(m['mdd']) > abs(mA['mdd']))
        if ok:
            adopt.append(k)
    rep['adoptable'] = adopt
    rep['verdict'] = 'PASS' if adopt else 'FAIL'
    rep['elapsed_sec'] = round(time.perf_counter() - t0, 1)

    print(f'\n채택 조건(PF↑ AND CI하한↑ AND P(PF<1)↓) 충족: '
          f'{adopt if adopt else "없음"}')
    print(f'Runtime Error 0 · Elapsed {rep["elapsed_sec"]}s')

    with open(os.path.join(OUT, 'selection_results.json'), 'w',
              encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(OUT, 'bootstrap_selection.json'), 'w',
              encoding='utf-8') as f:
        json.dump({k: v['bootstrap'] for k, v in rep['cases'].items()},
                  f, ensure_ascii=False, indent=2)
    print(f'\n  저장: {OUT}/selection_results.json · bootstrap_selection.json')
    return rep


if __name__ == '__main__':
    main()
