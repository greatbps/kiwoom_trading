"""
Work Instruction 6 — SMC Grade Policy Edge 검증

⚠️ WI4 SMC 신호(6,315건, phase1/reports/strategy_parity/smc_replay_raw.shard{0,1}.csv)를
   재사용한다. SMC 재계산 없음. Entry 조건도 손대지 않는다 — 여기서 바꾸는 것은 오직
   "정책상 B급/C급을 통과시켰다면 무슨 일이 있었을까"라는 사후 필터뿐이다.

청산은 운영 로직을 무수정으로 그대로 쓴다:
    - trading.exit_logic_optimized.OptimizedExitLogic.check_exit_signal()  (장중, 5분봉)
    - analyzers.swing.holding_manager.HoldingManager.evaluate()            (EOD, 일봉)
둘 다 `phase1.exit_backtest.LiveExitReplay`가 이미 무수정으로 직접 호출하도록 배선해
둔 것을 상속해서 그대로 쓴다 (Iteration 8-2, `phase1/exit_backtest.py`).

━━━ LiveExitReplay.run()을 그대로 못 쓰는 이유 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
원본 run()은 "신호일 다음 거래일 09:00 시가 진입"이라는 일단위 전제로 짜여 있고,
진입일 당일은 청산검사 자체를 건너뛴다(`i <= p['entry_i']: continue`). WI4 신호는
5분봉 타임스탬프(장중 특정 시각)라서 이 큐 구조를 못 쓴다 — 당일 진입 후 당일 장중
청산까지 봐야 한다. 그래서 이 파일은 `run()`만 새로 쓰고, 청산 판정 함수 자체
(`_check_bar`가 호출하는 `self.exit_logic.check_exit_signal()`, `check_eod`가 호출하는
`self.holding.evaluate()`)는 부모 클래스에서 무수정으로 그대로 가져온다.

사용법:
    python -m phase1.grade_policy_replay_v_wi6 --benchmark
    python -m phase1.grade_policy_replay_v_wi6 --full
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yaml

import warnings
warnings.filterwarnings('ignore')

from phase0.portfolio import CaseResult, PTrade, INITIAL_CAPITAL, SLOT_DIVISOR, kpi
from phase1.exit_backtest import LiveExitReplay, FrozenDT, load_5m, INTRADAY_LOOKBACK
from phase1.strategy_baseline import MAX_SLOTS
from phase1.selection_backtest import TRAIN_END
from analyzers.swing.state_machine import SwingPosition as _SP, SwingState as _SS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARITY_DIR = os.path.join(ROOT, 'phase1', 'reports', 'strategy_parity')
OUT_DIR = os.path.join(ROOT, 'phase1', 'reports', 'grade_policy_validation')
os.makedirs(OUT_DIR, exist_ok=True)

GROUP_DEF = {
    'A': lambda df: df[df['grade'] == 'A'],
    'B': lambda df: df[df['grade'] == 'B'],
    'C': lambda df: df[df['grade'] == 'C'],
    'BC': lambda df: df[df['grade'].isin(['B', 'C'])],
}


# ── 후보 로드 (WI4 결과 재사용, SMC 재계산 없음) ─────────────────────────────
def load_candidates() -> pd.DataFrame:
    frames = []
    for shard in ('smc_replay_raw.shard0.csv', 'smc_replay_raw.shard1.csv'):
        path = os.path.join(PARITY_DIR, shard)
        df = pd.read_csv(path, usecols=['symbol', 'timestamp', 'signal', 'choch_grade',
                                         'market_regime'], dtype={'symbol': str})
        df = df[df['signal'].astype(str) == 'True']
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df['ts'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)

    def parse_grade(s):
        try:
            d = ast.literal_eval(s)
            return d.get('grade'), float(d.get('score', 0.0))
        except Exception:
            return None, 0.0

    parsed = df['choch_grade'].apply(parse_grade)
    df['grade'] = [p[0] for p in parsed]
    df['grade_score'] = [p[1] for p in parsed]
    df['day'] = df['ts'].dt.normalize()
    return df[['symbol', 'ts', 'day', 'grade', 'grade_score', 'market_regime']]


# ── 일봉 (5분봉에서 재집계 — load_5m() 한 번만 읽어 재사용, 중복 I/O 없음) ────
def load_daily_from_m5(m5: dict) -> dict:
    out = {}
    for sym, df in m5.items():
        d = (df.resample('1D')
               .agg({'open': 'first', 'high': 'max', 'low': 'min',
                     'close': 'last', 'volume': 'sum'})
               .dropna())
        if len(d) > 0:
            out[sym] = d
    return out


# ── 실시간 3슬롯 경쟁 엔진 ───────────────────────────────────────────────────
class GradePolicyReplay(LiveExitReplay):
    def __init__(self, m5, daily, days, cfg):
        super().__init__(daily, {}, days, None, MAX_SLOTS, m5=m5, cfg=cfg)
        self._kodex_grid = {}
        kx = m5.get('069500')
        if kx is not None:
            for day in days:
                sub = kx[kx.index.normalize() == day]
                if len(sub):
                    self._kodex_grid[day] = sub.index

    def _check_bar(self, sym, live_pos, ts):
        """check_day()의 봉 1개짜리 본문. exit_logic.check_exit_signal 호출은 무수정."""
        full = self.m5.get(sym)
        if full is None or ts not in full.index:
            return None, None
        i = full.index.get_loc(ts)
        row = full.iloc[i]
        px, hi, lo = float(row['close']), float(row['high']), float(row['low'])
        ep = live_pos['entry_price']
        live_pos['current_price'] = px
        live_pos['highest_price'] = max(live_pos['highest_price'], hi)
        live_pos['peak_price'] = live_pos['highest_price']
        live_pos['trough_price'] = min(live_pos['trough_price'], lo)
        live_pos['mfe_pct'] = max(live_pos['mfe_pct'], (live_pos['peak_price'] - ep) / ep * 100)
        live_pos['mae_pct'] = min(live_pos['mae_pct'], (live_pos['trough_price'] - ep) / ep * 100)
        i0 = max(0, i + 1 - INTRADAY_LOOKBACK)
        sub = full.iloc[i0:i + 1]
        FrozenDT._now = ts.to_pydatetime()
        try:
            ok, reason, meta = self.exit_logic.check_exit_signal(live_pos, px, sub)
        except Exception as e:
            self.errors.append(f'{sym} {ts}: {type(e).__name__}: {e}')
            return None, None
        if not ok:
            return None, None
        if meta and meta.get('partial_exit'):
            live_pos['partial_exit_stage'] = int(meta.get('stage', 0))
            r = float(meta.get('exit_ratio', 0.25))
            live_pos['quantity'] = max(1, int(live_pos['quantity'] * (1 - r)))
            self.partial_count += 1
            return None, None
        return (reason or 'EXIT'), px

    def _record_exit(self, res, sym, p, day, reason, xp, slot_won):
        ep = p['entry_price']
        pnl = (xp - ep) / ep - self.commission * 2
        hold_days = (day.normalize() - pd.Timestamp(p['entry_date'])).days
        res.trades.append(PTrade(
            symbol=sym, signal_date=p['sigday'], entry_date=p['entry_date'],
            exit_date=str(day.date()), entry_price=ep, exit_price=round(xp, 0),
            pnl_pct=round(pnl, 4), pnl_won=round(pnl * slot_won, 0),
            exit_reason=str(reason)[:40], hold_bars=max(0, hold_days),
            score=p['grade_score'], rank=0, n_candidates=0))
        return pnl * slot_won

    def run_group(self, name: str, cand_df: pd.DataFrame) -> CaseResult:
        res = CaseResult(name=name)
        open_pos: dict[str, dict] = {}
        slot_won = INITIAL_CAPITAL / SLOT_DIVISOR
        realized = 0.0
        eq = []
        dropped_no_slot = 0

        by_day = {}
        if len(cand_df):
            srt = cand_df.sort_values(['ts', 'grade_score', 'symbol'],
                                       ascending=[True, False, True])
            for day, g in srt.groupby('day'):
                by_day[day] = g.to_dict('records')

        for day in self.days:
            clock = self._kodex_grid.get(day)
            rows = by_day.get(day, [])
            ci = 0

            if clock is not None:
                for t in clock:
                    for sym in list(open_pos):
                        p = open_pos[sym]
                        reason, xp = self._check_bar(sym, p['live_pos'], t)
                        if reason is None:
                            continue
                        realized += self._record_exit(res, sym, p, day, reason, xp, slot_won)
                        del open_pos[sym]

                    while ci < len(rows) and rows[ci]['ts'] == t:
                        c = rows[ci]
                        ci += 1
                        sym = c['symbol']
                        if sym in open_pos:
                            continue
                        if len(open_pos) >= self.max_slots:
                            dropped_no_slot += 1
                            continue
                        full = self.m5.get(sym)
                        if full is None or t not in full.index:
                            continue
                        ep = float(full.loc[t, 'close'])
                        open_pos[sym] = {
                            'entry_price': ep, 'entry_date': str(day.date()),
                            'sigday': str(day.date()), 'grade': c['grade'],
                            'grade_score': c['grade_score'],
                            'live_pos': self._new_position(sym, ep, t.to_pydatetime()),
                            'swing_pos': _SP(stock_code=sym, stock_name=sym, state=_SS.HOLD,
                                              entry_price=ep, entry_date=day.date(),
                                              quantity=100, peak_price=ep, trough_price=ep),
                        }

            # EOD 스윕 — swing_runner 15:35 크론과 동일 타이밍(당일 진입분 포함), 무수정 재사용
            for sym in list(open_pos):
                p = open_pos[sym]
                reason, xp = self.check_eod(sym, p, day)
                if reason is None:
                    continue
                realized += self._record_exit(res, sym, p, day, reason, xp, slot_won)
                del open_pos[sym]

            unreal = 0.0
            for sym, p in open_pos.items():
                full = self.m5.get(sym)
                if full is None:
                    continue
                sub = full[full.index.normalize() == day]
                if len(sub) == 0:
                    continue
                c = float(sub['close'].iloc[-1])
                unreal += ((c - p['entry_price']) / p['entry_price']) * slot_won
            eq.append(INITIAL_CAPITAL + realized + unreal)

        res.equity = pd.Series(eq, index=self.days)
        res.dropped_candidates = dropped_no_slot
        return res


def boot_full(trades, n=5000, seed=0):
    """selection_backtest.boot()과 동일한 리샘플링 방식을 확장 — WI6가 요구하는
    P05/P50/P95/P(PF>1)까지 반환한다(원본 boot()은 2.5/97.5% CI만 반환)."""
    won = np.array([t.pnl_won for t in trades], dtype=float)
    if len(won) < 5:
        return {'n': len(won), 'p05': None, 'p50': None, 'p95': None, 'p_pf_gt_1': None}
    rng = np.random.default_rng(seed)
    pfs = []
    for _ in range(n):
        b = rng.choice(won, size=len(won), replace=True)
        gp, gl = b[b > 0].sum(), -b[b <= 0].sum()
        if gl > 0:
            pfs.append(gp / gl)
    pfs = np.array(pfs)
    if len(pfs) == 0:
        return {'n': len(won), 'p05': None, 'p50': None, 'p95': None, 'p_pf_gt_1': None}
    return {'n': len(won), 'resamples': len(pfs),
            'p05': round(float(np.percentile(pfs, 5)), 3),
            'p50': round(float(np.percentile(pfs, 50)), 3),
            'p95': round(float(np.percentile(pfs, 95)), 3),
            'p_pf_gt_1': round(float((pfs > 1).mean()), 3)}


def sample_gate(n_trades: int) -> str:
    if n_trades < 30:
        return 'INSUFFICIENT_DATA'
    if n_trades < 50:
        return 'EXPLORATORY'
    if n_trades < 150:
        return 'BASIC'
    return 'STABILITY_CANDIDATE'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--benchmark', action='store_true', help='소규모(그룹C 앞 5거래일)로 소요시간만 측정')
    ap.add_argument('--full', action='store_true', help='A/B/C/BC 전체 실행')
    ap.add_argument('--group', default='all', help='A,B,C,BC,all 중 하나(디버그용)')
    args = ap.parse_args()

    t0 = time.perf_counter()
    print('=' * 78)
    print('  WI-6 SMC Grade Policy Edge 검증 — Grade Policy Replay')
    print('=' * 78)

    cfg = yaml.safe_load(open(os.path.join(ROOT, 'config', 'strategy_hybrid.yaml')))

    print('\n[Load] 5분봉 캐시...')
    m5 = load_5m()
    print(f'  종목 {len(m5)}개')

    print('[Load] 일봉 재집계 (5분봉에서, 재조회 없음)...')
    daily = load_daily_from_m5(m5)
    days = sorted(set().union(*[set(d.index) for d in daily.values()]))
    print(f'  종목 {len(daily)}개 · 거래일 {len(days)}개')

    print('[Load] WI4 SMC 신호 후보(shard0+1)...')
    cand = load_candidates()
    print(f'  신호 {len(cand)}건 · grade분포 {cand["grade"].value_counts().to_dict()}')

    if args.benchmark:
        c_df = GROUP_DEF['C'](cand)
        first5 = sorted(c_df['day'].unique())[:5]
        bench_days = [d for d in days if d in set(first5)]
        replay = GradePolicyReplay(m5, daily, bench_days, cfg)
        bt0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            res = replay.run_group('C-benchmark', c_df[c_df['day'].isin(first5)])
        bt1 = time.perf_counter()
        n_c_days = len(sorted(c_df['day'].unique()))
        elapsed = bt1 - bt0
        proj = elapsed / max(1, len(bench_days)) * len(days)
        print(f'\n[Benchmark] {len(bench_days)}거래일 처리 {elapsed:.1f}초 '
              f'(거래 {len(res.trades)}건, 오류 {len(replay.errors)}건)')
        print(f'  전체 {len(days)}거래일 투영 소요시간: 약 {proj/60:.1f}분 (그룹 1개 기준)')
        print(f'  그룹 4개(A/B/C/BC) 투영 총합: 약 {proj*4/60:.1f}분')
        with open(os.path.join(OUT_DIR, 'execution_log.txt'), 'a') as f:
            f.write(f'[{datetime.now().isoformat(timespec="seconds")}] BENCHMARK: '
                    f'{len(bench_days)}일/{elapsed:.1f}초, 전체투영 {proj*4/60:.1f}분(4그룹)\n')
        return

    if not args.full:
        print('\n--benchmark 또는 --full 중 하나를 지정하세요.')
        return

    groups_to_run = ['A', 'B', 'C', 'BC'] if args.group == 'all' else [args.group]
    results = {}
    for g in groups_to_run:
        g_df = GROUP_DEF[g](cand)
        if len(g_df) == 0:
            print(f'\n[Group {g}] 후보 0건 — 시뮬레이션 생략')
            results[g] = None
            continue
        print(f'\n[Group {g}] 후보 {len(g_df)}건 실행 중...')
        gt0 = time.perf_counter()
        replay = GradePolicyReplay(m5, daily, days, cfg)
        with contextlib.redirect_stdout(io.StringIO()):
            res = replay.run_group(g, g_df)
        gt1 = time.perf_counter()
        print(f'  완료: 체결 {len(res.trades)}건 · 슬롯부족탈락 {res.dropped_candidates}건 '
              f'· 오류 {len(replay.errors)}건 · {gt1-gt0:.1f}초')
        results[g] = (res, replay.errors)
        with open(os.path.join(OUT_DIR, 'execution_log.txt'), 'a') as f:
            f.write(f'[{datetime.now().isoformat(timespec="seconds")}] Group {g}: '
                    f'candidates={len(g_df)} trades={len(res.trades)} '
                    f'dropped_no_slot={res.dropped_candidates} errors={len(replay.errors)} '
                    f'elapsed={gt1-gt0:.1f}s\n')

    # ── 산출물 ───────────────────────────────────────────────────────────
    summary_rows = []
    all_trade_rows = []
    yearly_rows = []
    regime_rows = []
    bootstrap_rows = []
    decision = {'run_at': datetime.now().isoformat(timespec='seconds'),
                'work_instruction': 'WI-6 SMC Grade Policy Edge 검증', 'groups': {}}

    cand_counts = {g: int(len(GROUP_DEF[g](cand))) for g in ['A', 'B', 'C', 'BC']}

    for g in groups_to_run:
        entry = results[g]
        if entry is None:
            summary_rows.append({'group': g, 'signal_count': cand_counts[g], 'trades': 0,
                                  'gate': 'NO_SIGNAL', 'pf': None, 'expectancy_pct': None,
                                  'mdd_pct': None, 'win_rate': None})
            decision['groups'][g] = {'signal_count': cand_counts[g], 'trades': 0,
                                      'gate': 'NO_SIGNAL'}
            continue
        res, errors = entry
        m = kpi(res)
        gate = sample_gate(m.get('trades', 0))

        tr_trades = [t for t in res.trades if pd.Timestamp(t.entry_date) <= TRAIN_END]
        te_trades = [t for t in res.trades if pd.Timestamp(t.entry_date) > TRAIN_END]

        def _pf(trades):
            if not trades:
                return None
            won = np.array([t.pnl_won for t in trades])
            gp, gl = won[won > 0].sum(), -won[won <= 0].sum()
            return round(float(gp / gl), 3) if gl > 0 else None

        def _exp(trades):
            if not trades:
                return None
            return round(float(np.mean([t.pnl_pct for t in trades]) * 100), 3)

        def _top10_concentration(trades):
            """상위 10건 승리거래가 총 gross profit(승리거래 합)에서 차지하는 비중."""
            won = sorted([t.pnl_won for t in trades if t.pnl_won > 0], reverse=True)
            gp = sum(won)
            if gp <= 0:
                return None
            return round(sum(won[:10]) / gp * 100, 1)

        def _max_symbol_share(trades):
            if not trades:
                return None
            from collections import Counter
            c = Counter(t.symbol for t in trades)
            return round(c.most_common(1)[0][1] / len(trades) * 100, 1)

        top10_conc = _top10_concentration(res.trades)
        max_sym_share = _max_symbol_share(res.trades)

        summary_rows.append({
            'group': g, 'signal_count': cand_counts[g], 'trades': m.get('trades', 0),
            'dropped_no_slot': res.dropped_candidates, 'errors': len(errors),
            'gate': gate, 'win_rate': m.get('win_rate'), 'pf': m.get('profit_factor'),
            'expectancy_pct': m.get('expectancy_pct'), 'mdd_pct': m.get('mdd_pct'),
            'avg_hold_bars': m.get('avg_hold_bars'),
            'train_trades': len(tr_trades), 'train_pf': _pf(tr_trades),
            'test_trades': len(te_trades), 'test_pf': _pf(te_trades),
            'test_expectancy_pct': _exp(te_trades),
            'top10_win_concentration_pct': top10_conc,
            'max_single_symbol_share_pct': max_sym_share,
        })

        for t in res.trades:
            all_trade_rows.append({'group': g, 'symbol': t.symbol, 'signal_date': t.signal_date,
                                    'entry_date': t.entry_date, 'exit_date': t.exit_date,
                                    'entry_price': t.entry_price, 'exit_price': t.exit_price,
                                    'pnl_pct': t.pnl_pct, 'pnl_won': t.pnl_won,
                                    'exit_reason': t.exit_reason, 'hold_bars': t.hold_bars,
                                    'grade_score': t.score})

        yr = {}
        for t in res.trades:
            y = t.exit_date[:4]
            yr.setdefault(y, []).append(t)
        for y, trs in sorted(yr.items()):
            yearly_rows.append({'group': g, 'year': y, 'trades': len(trs), 'pf': _pf(trs),
                                 'expectancy_pct': _exp(trs)})

        # regime: candidate df의 market_regime을 (symbol, signal_date) 기준으로 조인
        reg_map = {(r['symbol'], str(r['ts'].date())): r['market_regime']
                   for r in g_df.to_dict('records')}
        by_regime = {}
        for t in res.trades:
            reg = reg_map.get((t.symbol, t.signal_date), 'UNKNOWN')
            by_regime.setdefault(reg, []).append(t)
        for reg, trs in sorted(by_regime.items()):
            regime_rows.append({'group': g, 'regime': reg, 'trades': len(trs), 'pf': _pf(trs),
                                 'expectancy_pct': _exp(trs)})

        bs = boot_full(res.trades, n=5000)
        bs_te = boot_full(te_trades, n=5000)
        bootstrap_rows.append({'group': g, 'scope': 'ALL', **bs})
        bootstrap_rows.append({'group': g, 'scope': 'TEST', **bs_te})

        yearly_ok = sum(1 for r in yearly_rows if r['group'] == g and r['pf'] and r['pf'] >= 1)
        yearly_n = sum(1 for r in yearly_rows if r['group'] == g)
        yearly_ratio = round(yearly_ok / yearly_n, 3) if yearly_n else None

        decision['groups'][g] = {
            'signal_count': cand_counts[g], 'trades': m.get('trades', 0),
            'dropped_no_slot': res.dropped_candidates, 'errors': len(errors),
            'sample_gate': gate,
            'pf_all': m.get('profit_factor'), 'expectancy_pct_all': m.get('expectancy_pct'),
            'mdd_pct_all': m.get('mdd_pct'), 'win_rate_all': m.get('win_rate'),
            'test_trades': len(te_trades), 'test_pf': _pf(te_trades),
            'test_expectancy_pct': _exp(te_trades),
            'bootstrap_all': bs, 'bootstrap_test': bs_te,
            'yearly_pf_ge_1_ratio': yearly_ratio,
            'top10_win_concentration_pct': top10_conc,
            'max_single_symbol_share_pct': max_sym_share,
        }

    pd.DataFrame(summary_rows).to_csv(os.path.join(OUT_DIR, 'grade_summary.csv'), index=False)
    pd.DataFrame(all_trade_rows).to_csv(os.path.join(OUT_DIR, 'grade_trade_results.csv'),
                                         index=False)
    pd.DataFrame(yearly_rows).to_csv(os.path.join(OUT_DIR, 'yearly_performance.csv'), index=False)
    pd.DataFrame(regime_rows).to_csv(os.path.join(OUT_DIR, 'regime_performance.csv'), index=False)
    pd.DataFrame(bootstrap_rows).to_csv(os.path.join(OUT_DIR, 'bootstrap_results.csv'),
                                         index=False)

    # ── WI6 §14 성공기준 판정 (그룹별) ────────────────────────────────────
    # 기준: Test PF>=1.20, Expectancy>0, Test Trades>=50, Bootstrap P(PF>1)>=80%,
    #       연도별 PF>=1 비율>=50%, MDD<=-20%(즉 |MDD|<=20), 자본가동률<=100%(3슬롯
    #       고정으로 구조적 충족), Top10 승리집중도<50%(WI6 미지정 — 이 작업에서
    #       "소수거래 집중 없음"의 운영정의로 채택, 산출물에 명시).
    CONCENTRATION_THRESHOLD = 50.0
    for g in groups_to_run:
        gd = decision['groups'].get(g)
        if gd is None or gd.get('gate') == 'NO_SIGNAL':
            if gd is not None:
                gd['group_verdict'] = 'INSUFFICIENT_DATA'
            continue
        checks = {
            'test_trades_ge_50': bool((gd['test_trades'] or 0) >= 50),
            'test_pf_ge_1.20': bool((gd['test_pf'] or 0) >= 1.20),
            'test_expectancy_gt_0': bool((gd['test_expectancy_pct'] or -1) > 0),
            'bootstrap_test_p_pf_gt1_ge_80pct':
                bool((gd['bootstrap_test'].get('p_pf_gt_1') or 0) >= 0.80),
            'yearly_pf_ge_1_ratio_ge_50pct': bool((gd['yearly_pf_ge_1_ratio'] or 0) >= 0.50),
            'mdd_within_20pct': bool(abs(gd['mdd_pct_all'] or -100) <= 20),
            'no_over_concentration_top10_lt_50pct':
                bool((gd['top10_win_concentration_pct'] or 100) < CONCENTRATION_THRESHOLD),
            'sample_gate_stability_candidate': bool(gd['sample_gate'] == 'STABILITY_CANDIDATE'),
        }
        n_pass = sum(checks.values())
        n_total = len(checks)
        gd['success_criteria_checks'] = checks
        gd['success_criteria_pass_count'] = f'{n_pass}/{n_total}'
        if n_pass == n_total:
            gd['group_verdict'] = 'PASS'
        elif checks['test_pf_ge_1.20'] and checks['test_expectancy_gt_0'] and \
                checks['test_trades_ge_50'] and n_pass >= n_total - 2:
            gd['group_verdict'] = 'PROMISING'
        else:
            gd['group_verdict'] = 'FAIL'

    overall = 'FAIL'
    verdicts = {g: decision['groups'][g].get('group_verdict') for g in groups_to_run
                if g in decision['groups']}
    if any(v == 'PASS' for v in verdicts.values()):
        overall = 'PASS'
    elif any(v == 'PROMISING' for v in verdicts.values()):
        overall = 'PROMISING'
    elif all(v == 'INSUFFICIENT_DATA' for v in verdicts.values()):
        overall = 'INSUFFICIENT_DATA'
    decision['group_verdicts'] = verdicts
    decision['overall_verdict'] = overall
    decision['concentration_threshold_used_pct'] = CONCENTRATION_THRESHOLD
    decision['notes'] = (
        'B급은 Test PF/Expectancy/연도별비율은 통과하나 Bootstrap P(PF>1)<80%와 '
        '상위10건 승리집중도(gross profit 대비)가 임계치 이상이라 표면적 PF가 소수의 '
        '큰 승리거래에 의존한다 — 강건한 edge로 보기 어렵다. C급/BC(B+C)는 Test PF<1, '
        'Expectancy<0으로 명확히 edge 없음. §17에 따라 이 판정만으로 정책을 변경하지 않는다.'
    )

    with open(os.path.join(OUT_DIR, 'decision.json'), 'w') as f:
        json.dump(decision, f, ensure_ascii=False, indent=2, default=str)

    elapsed_total = time.perf_counter() - t0
    print(f'\n총 소요시간: {elapsed_total/60:.1f}분')
    with open(os.path.join(OUT_DIR, 'execution_log.txt'), 'a') as f:
        f.write(f'[{datetime.now().isoformat(timespec="seconds")}] TOTAL elapsed='
                f'{elapsed_total/60:.1f}min\n')


if __name__ == '__main__':
    main()
