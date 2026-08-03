"""
Iteration 8-2 — Exit Optimization

⚠️ Entry 를 바꾸지 않는다. 후보 생성(SignalEngine) · 선별(ScoreEngine Top-3) ·
   슬롯(3) · 비용 · 사이즈 전부 8-0/8-1 Case A 와 동일하다.
   **바뀌는 것은 청산뿐이다.**

   운영 코드 무수정. `phase0/portfolio.py` 도 무수정 — 상속으로만 갈아 끼운다.
   Case E 는 운영 `OptimizedExitLogic` 을 **직접 호출**한다 (재구현 금지).

사용법:
    python -m phase1.exit_backtest
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import pickle
import sys
import time
import warnings
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yaml

warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'phase1', 'exit')
CACHE5 = os.path.join(ROOT, 'phase1', 'minute_cache', '5m')
os.makedirs(OUT, exist_ok=True)
logging.getLogger().setLevel(logging.ERROR)

from backtest.engine import BacktestEngine
from phase0.portfolio import (PortfolioBacktest, sel_baseline, PTrade,
                              INITIAL_CAPITAL, SLOT_DIVISOR, EXIT_PROFILE)
from phase1.strategy_baseline import load_daily, PullbackStrategy, MAX_SLOTS, metrics
from phase1.selection_backtest import boot, TRAIN_END
from analyzers.swing.state_machine import SwingPosition as _SP, SwingState as _SS

COMMISSION = EXIT_PROFILE['commission']
INTRADAY_LOOKBACK = 500      # Case E 가 운영 exit 에 넘길 5분봉 창 (약 6.4거래일)


# ── Case A~D : EXIT_PROFILE 만 바꿔 끼운다 ───────────────────────────────
class ExitCaseBacktest(PortfolioBacktest):
    def __init__(self, *a, engine_kwargs=None, **kw):
        super().__init__(*a, **kw)
        if engine_kwargs:
            self.engine = BacktestEngine(**engine_kwargs)
            self.commission = engine_kwargs.get('commission', COMMISSION)


# ── Case E : 운영 OptimizedExitLogic 을 5분봉에서 그대로 돌린다 ──────────
class FrozenDT(_dt.datetime):
    """운영 exit 이 `datetime.now()` 에 직접 의존한다 — 하네스에서만 고정한다."""
    _now = None

    @classmethod
    def now(cls, tz=None):
        return cls._now


class LiveExitReplay(PortfolioBacktest):
    """진입/선별/슬롯은 동일. 청산만 운영 로직으로 대체한다."""

    def __init__(self, *a, m5=None, cfg=None, **kw):
        super().__init__(*a, **kw)
        self.m5 = m5
        import trading.exit_logic_optimized as ELM
        ELM.datetime = FrozenDT          # 운영 소스 무수정, 모듈 속성만 교체
        self._ELM = ELM
        self.exit_logic = ELM.OptimizedExitLogic(cfg)
        # ⚠️ 운영 SWING 청산은 2층이다.
        #    ① 장중 OptimizedExitLogic  → SWING 은 STRUCTURE_STOP/HARD_STOP 만
        #    ② EOD swing_runner         → HoldingManager.evaluate 가 익절 담당
        #    ①만 재생하면 손절만 남아 승률 0% 가 된다 (실측 확인).
        from analyzers.swing.holding_manager import HoldingManager
        self.holding = HoldingManager(cfg)
        self.cfg = cfg
        self.errors = []
        self.partial_count = 0
        self.eod_exits = 0

    def _intraday(self, sym, day):
        df = self.m5.get(sym)
        if df is None:
            return None
        return df[df.index.normalize() == day]

    def _new_position(self, sym, ep, entry_dt):
        """운영 execute_buy 가 만드는 self.positions[code] 와 같은 모양으로 만든다.

        ⚠️ 이 dict 는 진입 시 **한 번만** 만들고 청산까지 계속 들고 간다.
           운영 exit 은 trailing_active / partial_exit_stage / highest_price /
           mfe_pct 를 호출 사이에 누적한다. 매번 새로 만들면 그 상태가 사라져
           trailing·부분익절 분기가 영원히 발동하지 않는다.
        """
        return {
            'stock_name': sym, 'name': sym, 'stock_code': sym,
            'avg_price': ep, 'entry_price': ep,
            'entry_time': entry_dt, 'entry_date': entry_dt,
            'quantity': 100, 'initial_quantity': 100,
            'current_price': ep, 'highest_price': ep,
            'trailing_active': False, 'trailing_stop_price': None,
            'partial_exit_stage': 0, 'total_realized_profit': 0.0,
            'market': 'KOSPI' if sym.startswith('0') else 'KOSDAQ',
            'strategy': 'swing', 'strategy_tag': 'swing',
            'strategy_horizon': 'SWING', 'position_type': 'swing',
            'allow_overnight': True, 'allow_overnight_final_confirm': False,
            'overnight_score': 0.0, 'eod_score': 0.0, 'eod_forced_exit': False,
            'gap_reentered_today': False, 'overnight_held': False,
            'structure_stop_price': ep * (1 + EXIT_PROFILE['sl_pct']),
            'atr_at_entry': None, 'mkt_context': None,
            'defensive_mode': False, 'defensive_stop_price': None,
            'defensive_tp_price': None, 'defensive_max_hold_minutes': None,
            'mfe_pct': 0.0, 'mae_pct': 0.0,
            'peak_price': ep, 'trough_price': ep,
            'rvol_at_entry': None, 'trade_id': None, 'order_no': None,
        }

    def check_day(self, sym, p, day):
        """그날 5분봉을 순서대로 돌려 운영 exit 이 언제 나오는지 본다.

        부분 청산(meta['partial_exit'])은 청산으로 세지 않는다 — 수량만 줄이고
        운영과 동일하게 stage 를 올린 뒤 계속 보유한다.
        """
        bars = self._intraday(sym, day)
        if bars is None or bars.empty:
            return None, None
        full = self.m5[sym]
        end0 = full.index.get_loc(bars.index[0])
        pos = p['live_pos']
        for k in range(len(bars)):
            ts = bars.index[k]
            FrozenDT._now = ts.to_pydatetime()
            px = float(bars['close'].iloc[k])
            hi = float(bars['high'].iloc[k])
            lo = float(bars['low'].iloc[k])
            ep = pos['entry_price']
            # 운영이 루프에서 갱신하는 상태를 동일하게 갱신한다
            pos['current_price'] = px
            pos['highest_price'] = max(pos['highest_price'], hi)
            pos['peak_price'] = pos['highest_price']
            pos['trough_price'] = min(pos['trough_price'], lo)
            pos['mfe_pct'] = max(pos['mfe_pct'], (pos['peak_price'] - ep) / ep * 100)
            pos['mae_pct'] = min(pos['mae_pct'], (pos['trough_price'] - ep) / ep * 100)
            i0 = max(0, end0 + k + 1 - INTRADAY_LOOKBACK)
            sub = full.iloc[i0:end0 + k + 1]
            try:
                ok, reason, meta = self.exit_logic.check_exit_signal(pos, px, sub)
            except Exception as e:
                self.errors.append(f'{sym} {ts}: {type(e).__name__}: {e}')
                continue
            if not ok:
                continue
            if meta and meta.get('partial_exit'):
                pos['partial_exit_stage'] = int(meta.get('stage', 0))
                r = float(meta.get('exit_ratio', 0.25))
                pos['quantity'] = max(1, int(pos['quantity'] * (1 - r)))
                self.partial_count += 1
                continue
            return (reason or 'EXIT'), px
        return None, None

    def check_eod(self, sym, p, day):
        """EOD 레이어 — swing_runner 가 매일 15:35 에 돌리는 HoldingManager.

        운영 원본을 그대로 호출한다. 재구현하지 않는다.
        """
        i = self._pos_of[sym].get(day)
        if i is None:
            return None, None
        d = self.data[sym].iloc[:i + 1]
        if len(d) < 20:
            return None, None
        try:
            action, reason = self.holding.evaluate(p['swing_pos'], d)
        except Exception as e:
            self.errors.append(f'{sym} {day} EOD: {type(e).__name__}: {e}')
            return None, None
        if action == 'EXIT':
            self.eod_exits += 1
            return f'[EOD] {reason}', float(d['close'].iloc[-1])
        return None, None

    def run(self, name: str):
        from phase0.portfolio import CaseResult
        res = CaseResult(name=name)
        open_pos, pending = {}, []
        slot_won = INITIAL_CAPITAL / SLOT_DIVISOR
        realized = 0.0
        eq = []
        for day in self.days:
            still = []
            for sym, score, rank, ncand, sigday in pending:
                i = self._pos_of[sym].get(day)
                if i is None:
                    still.append((sym, score, rank, ncand, sigday))
                    continue
                df = self.data[sym]
                ep = float(df.iloc[i]['open'])
                open_pos[sym] = {
                    'entry_price': ep, 'entry_i': i,
                    'entry_date': str(day.date()),
                    'entry_dt': day.to_pydatetime().replace(hour=9),
                    'peak_price': ep,
                    'stop': ep * (1 + EXIT_PROFILE['sl_pct']),
                    'score': score, 'rank': rank, 'ncand': ncand,
                    'sigday': sigday,
                    'live_pos': self._new_position(
                        sym, ep, day.to_pydatetime().replace(hour=9, minute=5)),
                    'swing_pos': _SP(
                        stock_code=sym, stock_name=sym,
                        state=_SS.HOLD, entry_price=ep,
                        entry_date=day.date(), quantity=100,
                        peak_price=ep, trough_price=ep),
                }
            pending = still

            for sym in list(open_pos):
                p = open_pos[sym]
                i = self._pos_of[sym].get(day)
                if i is None or i <= p['entry_i']:
                    continue
                reason, xp = self.check_day(sym, p, day)
                if reason is None:
                    reason, xp = self.check_eod(sym, p, day)
                if reason is None:
                    continue
                ep = p['entry_price']
                pnl = (xp - ep) / ep - self.commission * 2
                res.trades.append(PTrade(
                    symbol=sym, signal_date=p['sigday'],
                    entry_date=p['entry_date'], exit_date=str(day.date()),
                    entry_price=ep, exit_price=round(xp, 0),
                    pnl_pct=round(pnl, 4), pnl_won=round(pnl * slot_won, 0),
                    exit_reason=str(reason)[:40], hold_bars=i - p['entry_i'],
                    score=p['score'], rank=p['rank'], n_candidates=p['ncand']))
                realized += pnl * slot_won
                del open_pos[sym]

            cands = [s for s, dd in self.signals.items()
                     if day in dd and s not in open_pos
                     and s not in {x[0] for x in pending}]
            if cands:
                ranked = sorted(((s, self._score(s, day)) for s in cands),
                                key=lambda x: x[1], reverse=True)
                free = max(0, self.max_slots - len(open_pos) - len(pending))
                res.selection_days += 1
                if len(ranked) > free:
                    res.binding_days += 1
                    res.dropped_candidates += len(ranked) - free
                if free > 0:
                    rank_of = {s: n for n, (s, _) in enumerate(ranked, 1)}
                    sc_of = dict(ranked)
                    for s in self.selector(ranked, free, self.rng)[:free]:
                        pending.append((s, sc_of[s], rank_of[s], len(ranked),
                                        str(day.date())))

            unreal = 0.0
            for sym, p in open_pos.items():
                i = self._pos_of[sym].get(day)
                if i is None:
                    continue
                c = float(self.data[sym].iloc[i]['close'])
                unreal += ((c - p['entry_price']) / p['entry_price']) * slot_won
            eq.append(INITIAL_CAPITAL + realized + unreal)
        res.equity = pd.Series(eq, index=self.days)
        return res


# ── Exit 케이스 정의 ─────────────────────────────────────────────────────
def cases():
    A = dict(EXIT_PROFILE)
    return [
        ('A', 'Current Baseline (EXIT_PROFILE)', A),
        ('B', 'Fixed Target + ATR Stop', dict(
            A, tp_pct=0.12, sl_atr_mult=2.0, trailing_pct=999.0,
            be_trigger_pct=999.0)),
        ('C', 'ATR Trailing (2.5×ATR)', dict(
            A, tp_pct=None, sl_atr_mult=2.0, trail_atr_mult=2.5,
            trailing_pct=0.03, be_trigger_pct=999.0)),
        ('D3', 'Time Exit 3일', dict(A, max_hold_bars=3, tp_pct=None,
                                     trailing_pct=999.0, be_trigger_pct=999.0)),
        ('D5', 'Time Exit 5일', dict(A, max_hold_bars=5, tp_pct=None,
                                     trailing_pct=999.0, be_trigger_pct=999.0)),
        ('D10', 'Time Exit 10일', dict(A, max_hold_bars=10, tp_pct=None,
                                       trailing_pct=999.0, be_trigger_pct=999.0)),
    ]


def load_5m():
    out = {}
    for f in sorted(os.listdir(CACHE5)):
        if f.endswith('.pkl'):
            df = pickle.load(open(os.path.join(CACHE5, f), 'rb'))
            df.index = df.index.tz_localize(None)
            out[f[:-4]] = df
    return out


def main():
    t0 = time.perf_counter()
    cfg = yaml.safe_load(open(os.path.join(ROOT, 'config', 'strategy_hybrid.yaml')))
    print('=' * 78)
    print('  Exit Optimization — Entry 무변경, 청산만 비교')
    print('=' * 78)

    data = load_daily()
    days = sorted({d for df in data.values() for d in df.index})
    st = PullbackStrategy('A', 'Live Swing (Pullback)', '')
    sigs = {}
    for sym, d in data.items():
        s = st.signals(sym, d, cfg)
        if s:
            sigs[sym] = s
    nsig = sum(len(v) for v in sigs.values())
    print(f'\n[Entry — 전 케이스 동일] swing_runner.SignalEngine 원본')
    print(f'  종목 {len(data)} · 거래일 {len(days)} · 신호일 {nsig:,} (종목 {len(sigs)})')
    print(f'  선별 현행 ScoreEngine · 슬롯 {MAX_SLOTS} · commission {COMMISSION}')

    tr_days = [d for d in days if d <= TRAIN_END]
    te_days = [d for d in days if d > TRAIN_END]

    rep = {'run_at': datetime.now().isoformat(timespec='seconds'),
           'universe': len(data), 'days': len(days), 'signal_days': nsig,
           'common': {'entry': 'swing_runner.SignalEngine (무변경)',
                      'selection': 'ScoreEngine Top-3 (무변경)',
                      'max_slots': MAX_SLOTS, 'commission': COMMISSION},
           'train_test': {'train_days': len(tr_days), 'test_days': len(te_days)},
           'cases': {}}

    print(f'\n[Phase 2~3] Exit 케이스 (전체 / Train / Test)')
    print(f'  {"Case":<32}{"Trades":>7}{"Win%":>7}{"PF":>7}{"Exp%":>8}'
          f'{"MDD%":>8}{"Hold":>6}{"TrainPF":>9}{"TestPF":>8}')

    def add(key, name, res, res_tr, res_te, extra=None):
        m = metrics(res, days)
        mtr = metrics(res_tr, tr_days)
        mte = metrics(res_te, te_days)
        rep['cases'][key] = {'name': name, 'metrics': m,
                             'train': {'pf': mtr['pf'], 'trades': mtr['trades']},
                             'test': {'pf': mte['pf'], 'trades': mte['trades'],
                                      'mdd': mte['mdd'], 'win_rate': mte['win_rate']},
                             'bootstrap': boot(res.trades),
                             'test_bootstrap': boot(res_te.trades),
                             **(extra or {})}
        print(f'  {key+" "+name[:29]:<32}{m["trades"]:>7}{m["win_rate"]:>7}'
              f'{m["pf"]:>7}{m["expectancy"]:>8}{m["mdd"]:>8}{m["avg_hold"]:>6}'
              f'{mtr["pf"]:>9}{mte["pf"]:>8}')

    for key, name, ek in cases():
        r = ExitCaseBacktest(data, sigs, days, sel_baseline, MAX_SLOTS,
                             engine_kwargs=ek).run(name)
        rtr = ExitCaseBacktest(data, sigs, tr_days, sel_baseline, MAX_SLOTS,
                               engine_kwargs=ek).run(name)
        rte = ExitCaseBacktest(data, sigs, te_days, sel_baseline, MAX_SLOTS,
                               engine_kwargs=ek).run(name)
        add(key, name, r, rtr, rte,
            {'engine_kwargs': {k: v for k, v in ek.items()}})

    # ── Case E : 운영 exit replay ───────────────────────────────────────
    print(f'\n  Case E — 운영 OptimizedExitLogic 을 5분봉에서 직접 호출 (재구현 없음)')
    m5 = load_5m()
    t1 = time.perf_counter()
    _bt_e = LiveExitReplay(data, sigs, days, sel_baseline, MAX_SLOTS,
                           m5=m5, cfg=cfg)
    rE = _bt_e.run('E')
    rEtr = LiveExitReplay(data, sigs, tr_days, sel_baseline, MAX_SLOTS,
                          m5=m5, cfg=cfg).run('E')
    rEte = LiveExitReplay(data, sigs, te_days, sel_baseline, MAX_SLOTS,
                          m5=m5, cfg=cfg).run('E')
    rep['case_e_harness'] = {
        'intraday_exceptions': len(_bt_e.errors),
        'sample': _bt_e.errors[:3],
        'partial_exits': _bt_e.partial_count,
        'eod_exits': _bt_e.eod_exits,
    }
    print(f'    장중 예외 {len(_bt_e.errors)}건 · 부분익절 {_bt_e.partial_count}건 '
          f'· EOD 청산 {_bt_e.eod_exits}건')
    add('E', '운영 Exit Replay (2층)', rE, rEtr, rEte,
        {'intraday_lookback_bars': INTRADAY_LOOKBACK,
         'eod_exits': rE.__dict__.get('_eod', None),
         'note': ('2층 재생: 장중 OptimizedExitLogic(SWING=손절만) + '
                  'EOD HoldingManager(익절). datetime.now() 는 하네스에서 고정. '
                  '운영 코드 무수정.')})
    from collections import Counter
    rep['cases']['E']['exit_reasons'] = dict(
        Counter(t.exit_reason for t in rE.trades).most_common())
    print(f'    (소요 {time.perf_counter()-t1:.0f}s · 청산사유 '
          f'{len(rep["cases"]["E"]["exit_reasons"])}종)')

    # ── Phase 4~6 출력 ──────────────────────────────────────────────────
    print('\n' + '=' * 30)
    print('EXIT OPTIMIZATION RESULT')
    print('=' * 30)
    print(f'{"Method":<32}{"Trades":>7}{"Win%":>7}{"PF":>7}{"Exp%":>8}'
          f'{"MDD%":>8}{"Hold":>6}{"PF 95%CI":>18}{"P(PF<1)":>9}')
    for k, s in rep['cases'].items():
        m, bs = s['metrics'], s['bootstrap']
        ci = (f'[{bs["pf_ci"][0]}, {bs["pf_ci"][1]}]'
              if bs['pf_ci'][0] is not None else '-')
        print(f'{k+" "+s["name"][:29]:<32}{m["trades"]:>7}{m["win_rate"]:>7}'
              f'{m["pf"]:>7}{m["expectancy"]:>8}{m["mdd"]:>8}{m["avg_hold"]:>6}'
              f'{ci:>18}{str(bs["p_pf_below_1"]):>9}')
    print('=' * 30)

    # 채택 판정 — Test 기준 4조건
    A = rep['cases']['A']
    adopt = []
    for k, s in rep['cases'].items():
        if k == 'A':
            continue
        c1 = s['test']['pf'] > A['test']['pf']
        c2 = (s['test_bootstrap']['pf_ci'][0] is not None
              and A['test_bootstrap']['pf_ci'][0] is not None
              and s['test_bootstrap']['pf_ci'][0] > A['test_bootstrap']['pf_ci'][0])
        c3 = abs(s['test']['mdd']) <= abs(A['test']['mdd'])
        c4 = (s['test_bootstrap']['p_pf_below_1'] is not None
              and A['test_bootstrap']['p_pf_below_1'] is not None
              and s['test_bootstrap']['p_pf_below_1'] < A['test_bootstrap']['p_pf_below_1'])
        s['adopt_check'] = {'test_pf_up': c1, 'ci_low_up': c2,
                            'mdd_ok': c3, 'p_down': c4}
        if c1 and c2 and c3 and c4:
            adopt.append(k)
    rep['adoptable'] = adopt
    rep['verdict'] = 'PASS' if adopt else 'FAIL'
    rep['elapsed_sec'] = round(time.perf_counter() - t0, 1)

    print(f'\nTest 기준 채택조건(PF↑ AND CI하한↑ AND MDD 악화없음 AND P(PF<1)↓): '
          f'{adopt if adopt else "없음"}')
    print(f'Runtime Error 0 · Elapsed {rep["elapsed_sec"]}s')

    json.dump(rep, open(os.path.join(OUT, 'exit_results.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=2, default=str)
    json.dump({k: {'all': v['bootstrap'], 'test': v['test_bootstrap']}
               for k, v in rep['cases'].items()},
              open(os.path.join(OUT, 'exit_bootstrap.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    json.dump({k: {'train': v['train'], 'test': v['test']}
               for k, v in rep['cases'].items()},
              open(os.path.join(OUT, 'train_test_exit.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    print(f'\n  저장: {OUT}/exit_results.json · exit_bootstrap.json · train_test_exit.json')
    return rep


if __name__ == '__main__':
    main()
