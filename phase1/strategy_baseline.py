"""
Iteration 8-0 — Strategy Baseline Backtest

⚠️ 전략을 수정하지 않는다. 파라미터·threshold·risk·exit 전부 무변경.
   운영 엔진(SignalEngine / ChochSignalEngine)을 그대로 호출한다.
   재구현하지 않는다 — 재구현하면 비교 대상이 운영 전략이 아니게 된다.

데이터: Iteration 6 데이터셋 그대로 (phase1/minute_cache/5m, 112종목,
        2025-08-01 ~ 2026-07-31). 새 수집 없음.
        일봉 전략이므로 같은 5분봉을 일봉으로 리샘플해서 쓴다.

동일 조건 (전 전략 공통):
    청산  phase0.portfolio.EXIT_PROFILE  (BacktestEngine)
    비용  commission 0.0035 왕복
    슬롯  max_slots=3   (config swing.max_positions)
    사이즈 INITIAL_CAPITAL / SLOT_DIVISOR
    기간  동일
    종목  동일 112

사용법:
    python -m phase1.strategy_baseline
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'phase1', 'minute_cache', '5m')
OUT = os.path.join(ROOT, 'phase1', 'baseline')
os.makedirs(OUT, exist_ok=True)

# 엔진이 INFO 로 수천 줄 뱉는다 — 백테스트 중에는 막는다 (엔진 동작은 무변경)
for _n in ('analyzers.patterns.manager', 'analyzers.swing.signal_engine',
           'analyzers.smc', 'backtest.adapter', 'swing_runner'):
    logging.getLogger(_n).setLevel(logging.ERROR)
logging.getLogger().setLevel(logging.ERROR)

WARMUP = 60          # 엔진이 지표를 만들 최소 봉수. 전 전략 공통.
MAX_SLOTS = 3        # config swing.max_positions


# ── 데이터 ───────────────────────────────────────────────────────────────
def load_daily():
    """5분봉 → 일봉. 데이터를 고치지 않는다 (집계만)."""
    out = {}
    for f in sorted(os.listdir(CACHE)):
        if not f.endswith('.pkl'):
            continue
        sym = f[:-4]
        df = pickle.load(open(os.path.join(CACHE, f), 'rb'))
        d = (df.resample('1D')
               .agg({'open': 'first', 'high': 'max', 'low': 'min',
                     'close': 'last', 'volume': 'sum'})
               .dropna())
        d.index = d.index.tz_localize(None)
        if len(d) > WARMUP + 10:
            out[sym] = d
    return out


# ── 전략별 신호 생성 ─────────────────────────────────────────────────────
class Strategy:
    def __init__(self, key, name, note):
        self.key, self.name, self.note = key, name, note
        self.funnel = {'bars': 0, 'signal': 0, 'filtered': 0, 'entry': 0}

    def signals(self, sym, d, cfg):
        raise NotImplementedError


class PullbackStrategy(Strategy):
    """A — 현재 Live Swing. swing_runner.SignalEngine 원본 호출."""

    def signals(self, sym, d, cfg):
        from swing_runner import SignalEngine
        min_score = (cfg.get('swing') or {}).get('min_score_to_enter', 5.0)
        hits = set()
        for i in range(WARMUP, len(d)):
            self.funnel['bars'] += 1
            sig = SignalEngine(d.iloc[:i + 1], cfg).run()   # look-ahead 차단
            if not sig:
                continue
            self.funnel['signal'] += 1
            if sig.get('final_score', 0) < min_score:
                self.funnel['filtered'] += 1
                continue
            self.funnel['entry'] += 1
            hits.add(d.index[i])
        return hits


class ChochStrategy(Strategy):
    """B — CHoCH. analyzers.swing.choch_engine.ChochSignalEngine 원본 호출."""

    def signals(self, sym, d, cfg):
        from analyzers.swing.choch_engine import ChochSignalEngine
        min_score = (cfg.get('swing') or {}).get('min_score_to_enter', 5.0)
        hits = set()
        for i in range(WARMUP, len(d)):
            self.funnel['bars'] += 1
            sig = ChochSignalEngine(d.iloc[:i + 1], cfg).run()
            if not sig:
                continue
            self.funnel['signal'] += 1
            if sig.get('final_score', 0) < min_score:
                self.funnel['filtered'] += 1
                continue
            self.funnel['entry'] += 1
            hits.add(d.index[i])
        return hits


class ExplorationCoreStrategy(Strategy):
    """C — Exploration 의 **기계적으로 재현 가능한 부분만**.

    ⚠️ 운영 exploration 은 `enabled: false` (비활성) 이고,
       진입 조건에 오프라인에서 재현 불가능한 게이트가 섞여 있다.
         · SMC_NO_SIG           (그날 SMC 가 신호를 안 냈어야 함)
         · Orchestrator ACCEPT  (L0~L6 파이프라인 결과)
         · NOT NO_TRADE_DAY     (MarketContext 실시간 판정)
         · confirm_entry 1봉 확인 대기 (실시간 체결 흐름)
       여기서는 YAML 에 적힌 수치 규칙만 적용한다.
         RVOL >= min_rvol · N봉 고점 돌파 · 거래량 >= volume_mult × 평균
       따라서 결과는 **거래수의 상한**이며 운영 exploration 의 성과가 아니다.
       Reference 로만 읽어야 한다.
    """

    def signals(self, sym, d, cfg):
        e = cfg.get('exploration') or {}
        min_rvol = e.get('min_rvol', 1.2)
        win = e.get('breakout_window', 10)
        vmult = e.get('volume_mult', 1.5)
        max3 = e.get('max_3bar_rise', 0.04)
        hits = set()
        c, h, v = d['close'].values, d['high'].values, d['volume'].values
        vma = pd.Series(v).rolling(20).mean().values
        for i in range(WARMUP, len(d)):
            self.funnel['bars'] += 1
            if not vma[i - 1] or np.isnan(vma[i - 1]):
                continue
            rvol = v[i] / vma[i - 1]
            broke = h[i] > np.max(h[i - win:i])
            if not (broke and rvol >= min_rvol and v[i] >= vmult * vma[i - 1]):
                continue
            self.funnel['signal'] += 1
            # max_3bar_rise — 과열 추격 차단 (YAML 수치 그대로)
            if i >= 3 and c[i - 3] > 0 and (c[i] / c[i - 3] - 1) > max3:
                self.funnel['filtered'] += 1
                continue
            self.funnel['entry'] += 1
            hits.add(d.index[i])
        return hits


# ── 지표 ─────────────────────────────────────────────────────────────────
def metrics(res, days):
    from phase0.portfolio import INITIAL_CAPITAL
    t = res.trades
    n = len(t)
    m = {'trades': n}
    if n == 0:
        return {**m, 'win_rate': 0.0, 'pf': 0.0, 'expectancy': 0.0,
                'avg_win': 0.0, 'avg_loss': 0.0, 'mdd': 0.0, 'avg_hold': 0.0,
                'profit': 0.0, 'loss': 0.0, 'avg_r': 0.0, 'recovery': 0.0,
                'monthly': 0.0, 'weekly': 0.0, 'daily': 0.0, 'net_pnl': 0.0}
    p = np.array([x.pnl_pct for x in t], dtype=float)
    won = np.array([x.pnl_won for x in t], dtype=float)
    wins, losses = p[p > 0], p[p <= 0]
    gp, gl = won[won > 0].sum(), -won[won <= 0].sum()
    eq = res.equity
    if eq is not None and len(eq):
        run = eq.cummax()
        mdd = float(((eq - run) / run).min())
    else:
        mdd = 0.0
    nmon = max(1e-9, len(days) / 21.0)
    nwk = max(1e-9, len(days) / 5.0)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    wr = len(wins) / n
    net = float(won.sum())
    return {
        'trades': n,
        'win_rate': round(wr * 100, 2),
        'pf': round(float(gp / gl), 3) if gl > 0 else float('inf'),
        'expectancy': round(float(p.mean()) * 100, 4),
        'avg_win': round(avg_win * 100, 3),
        'avg_loss': round(avg_loss * 100, 3),
        'mdd': round(mdd * 100, 2),
        'avg_hold': round(float(np.mean([x.hold_bars for x in t])), 2),
        'profit': round(float(gp), 0),
        'loss': round(float(gl), 0),
        'net_pnl': round(net, 0),
        # 평균 R — 손절폭(EXIT_PROFILE sl_pct=-5%)을 1R 로 본다
        'avg_r': round(float(p.mean() / 0.05), 3),
        'recovery': (round(float(net / INITIAL_CAPITAL / abs(mdd)), 3)
                     if mdd < 0 else float('inf')),
        'monthly': round(n / nmon, 2),
        'weekly': round(n / nwk, 2),
        'daily': round(n / max(1, len(days)), 3),
    }


def score_of(m):
    """PF → MDD → Expectancy → 거래빈도 순 가중. 값이 클수록 좋다."""
    pf = min(m['pf'], 5.0) if m['pf'] != float('inf') else 5.0
    mdd = abs(m['mdd'])
    freq = min(m['monthly'], 20.0)
    return round(pf * 40 - mdd * 1.5 + m['expectancy'] * 5 + freq * 0.5, 2)


def main():
    t0 = time.perf_counter()
    cfg = yaml.safe_load(open(os.path.join(ROOT, 'config',
                                           'strategy_hybrid.yaml')))
    print('=' * 78)
    print('  Strategy Baseline Backtest (전략 무수정)')
    print('=' * 78)

    data = load_daily()
    days = sorted({d for df in data.values() for d in df.index})
    print(f'\n[데이터] 종목 {len(data)} · 거래일 {len(days)} '
          f'({days[0].date()} ~ {days[-1].date()})')
    print(f'  출처 phase1/minute_cache/5m (Iteration 6) — 5분봉 → 일봉 리샘플')
    print(f'  워밍업 {WARMUP}봉 · 슬롯 {MAX_SLOTS} · 청산/비용 전 전략 동일')

    strats = [
        PullbackStrategy('A', 'Live Swing (Pullback)', 'swing_runner.SignalEngine'),
        ChochStrategy('B', 'CHoCH', 'analyzers.swing.choch_engine'),
        ExplorationCoreStrategy('C', 'Exploration (core rules only)',
                                'YAML 수치 규칙만 — Reference'),
    ]

    from phase0.portfolio import PortfolioBacktest, sel_baseline

    rep = {'run_at': datetime.now().isoformat(timespec='seconds'),
           'dataset': {'source': 'phase1/minute_cache/5m', 'symbols': len(data),
                       'days': len(days), 'start': str(days[0].date()),
                       'end': str(days[-1].date()), 'resample': '5m→1d'},
           'common': {'exit_profile': 'phase0.portfolio.EXIT_PROFILE',
                      'commission': 0.0035, 'max_slots': MAX_SLOTS,
                      'warmup_bars': WARMUP},
           'strategies': {}}

    for st in strats:
        t1 = time.perf_counter()
        sigs = {}
        for sym, d in data.items():
            try:
                s = st.signals(sym, d, cfg)
            except Exception as e:
                rep.setdefault('errors', []).append(
                    f'{st.key}/{sym}: {type(e).__name__}: {e}')
                s = set()
            if s:
                sigs[sym] = s
        nsig = sum(len(v) for v in sigs.values())
        bt = PortfolioBacktest(data, sigs, days, sel_baseline, MAX_SLOTS)
        res = bt.run(st.name)
        m = metrics(res, days)
        f = st.funnel
        # Entry Parity — 진입 예약이 실제 체결로 이어진 비율
        parity = round(m['trades'] / nsig * 100, 2) if nsig else 0.0
        rep['strategies'][st.key] = {
            'name': st.name, 'engine': st.note,
            'funnel': {**f, 'signal_days': nsig,
                       'symbols_with_signal': len(sigs),
                       'trades': m['trades'],
                       'dropped_no_slot': res.dropped_candidates,
                       'binding_days': res.binding_days},
            'entry_parity_pct': parity,
            'metrics': m, 'score': score_of(m),
            'elapsed_sec': round(time.perf_counter() - t1, 1),
        }
        print(f'\n[{st.key}] {st.name}')
        print(f'  엔진 {st.note}')
        print(f'  Funnel  bar {f["bars"]:,} → signal {f["signal"]:,} '
              f'→ filter통과 {f["entry"]:,} → 신호일 {nsig:,} '
              f'→ trade {m["trades"]:,}  (슬롯부족 폐기 {res.dropped_candidates:,})')
        print(f'  거래 {m["trades"]:,}  승률 {m["win_rate"]}%  PF {m["pf"]}  '
              f'Exp {m["expectancy"]}%  MDD {m["mdd"]}%  보유 {m["avg_hold"]}일')
        print(f'  월 {m["monthly"]}건 · 주 {m["weekly"]}건 · 일 {m["daily"]}건  '
              f'· Parity {parity}%  · Score {score_of(m)}')

    # ── 순위 ────────────────────────────────────────────────────────────
    rank = sorted(rep['strategies'].items(),
                  key=lambda kv: -kv[1]['score'])
    rep['ranking'] = [k for k, _ in rank]

    print('\n' + '=' * 30)
    print('STRATEGY BASELINE BACKTEST')
    print('=' * 30)
    hdr = (f'{"Strategy":<26}{"Trades":>7}{"Win%":>7}{"PF":>7}{"Exp%":>8}'
           f'{"MDD%":>8}{"Hold":>6}{"Mon":>6}{"Parity":>8}{"Score":>8}')
    print(hdr)
    for k, s in rank:
        m = s['metrics']
        print(f'{k+" "+s["name"][:23]:<26}{m["trades"]:>7,}{m["win_rate"]:>7}'
              f'{m["pf"]:>7}{m["expectancy"]:>8}{m["mdd"]:>8}{m["avg_hold"]:>6}'
              f'{m["monthly"]:>6}{s["entry_parity_pct"]:>8}{s["score"]:>8}')
    print('=' * 30)
    print(f'Runtime Error   {len(rep.get("errors", []))}')
    print(f'Elapsed         {time.perf_counter()-t0:.1f}s')

    with open(os.path.join(OUT, 'strategy_baseline.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: {OUT}/strategy_baseline.json')
    return rep


if __name__ == '__main__':
    main()
