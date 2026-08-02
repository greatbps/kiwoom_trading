"""
Phase 0.1 — 포트폴리오 백테스트 (일 단위 횡단면 선별)

기존 `backtest.engine.BacktestEngine` 은 **종목별로 독립** 실행이라
"오늘 후보 중 상위 N개만" 같은 선별을 표현할 수 없다. 여기서는 날짜 축을
바깥 루프로 두고, 하루마다

    ① 보유 포지션 청산 판정
    ② 오늘 BUY 후보 수집
    ③ 선별 규칙 적용 (Baseline / Top3 / Top5 / Random5)
    ④ 남은 슬롯만큼 진입

순서로 돈다.

━━━ 청산 로직은 새로 쓰지 않는다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  `BacktestEngine._check_swing_exit` 를 그대로 호출한다. 여기서 다시
  구현하면 케이스 간 비교가 아니라 **엔진 두 개의 비교**가 되어 버리고,
  Phase 0 의 금지사항(청산 로직 변경)에도 걸린다.

━━━ 진입가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  신호일 t 의 **다음 봉 시가**. 엔진과 동일하다. 신호 자체가 t-1 까지의
  확정봉만 보므로 미래를 당겨쓰지 않는다.

━━━ 자금 모형 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  슬롯당 고정 금액(초기자본 / 5). 케이스마다 **주당 투입금이 같아야**
  총손익을 원 단위로 직접 비교할 수 있다. 복리를 걸면 초반 한두 거래의
  운이 뒤 구간 전체를 밀어 버려서, 거래 수가 적은 이번 실험에서는
  선별 규칙의 효과가 묻힌다.
"""
from __future__ import annotations

import os
import random
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine
from trading.score_engine import ScoreEngine

# ── 청산 프로파일 (일봉 근사) ──────────────────────────────────────────────
#
# ⚠️ 운영 config(strategy_hybrid.yaml)의 청산은 5분봉 기준이라 일봉으로
#    그대로 옮길 수 없다. 아래 값은 **일봉 스윙 근사치**이며, 4개 케이스에
#    똑같이 적용된다. 절대 수익률의 크기는 이 선택에 따라 달라지지만,
#    케이스 간 비교는 영향받지 않는다.
EXIT_PROFILE = dict(
    swing_mode=True,
    tp_pct=0.15,
    sl_pct=-0.05,
    min_hold_bars=3,
    trailing_pct=0.08,
    be_trigger_pct=0.05,
    max_hold_bars=30,
    commission=0.0035,
)

INITIAL_CAPITAL = 100_000_000
SLOT_DIVISOR = 5          # 슬롯당 금액 = 초기자본 / 5 (전 케이스 공통)


@dataclass
class PTrade:
    symbol: str
    signal_date: str   # 신호일 (진입일 전 거래일) — 신규/기존 후보 구분용
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_won: float
    exit_reason: str
    hold_bars: int
    score: float
    rank: int          # 그날 후보 중 몇 등이었나 (1 = 최상위)
    n_candidates: int  # 그날 후보가 몇 개였나


@dataclass
class CaseResult:
    name: str
    trades: list[PTrade] = field(default_factory=list)
    equity: pd.Series = None
    selection_days: int = 0        # 후보가 하나라도 있던 날
    binding_days: int = 0          # 후보 > 빈 슬롯 → 실제로 '골라야' 했던 날
    dropped_candidates: int = 0    # 슬롯이 없어 버려진 후보 수


# ── 선별 규칙 ────────────────────────────────────────────────────────────
def sel_baseline(ranked, n_slots, rng):
    """상한 없음 — 신호가 나면 전부 잡는다. 현재 시스템의 기준점."""
    return [s for s, _ in ranked]


def sel_top(n):
    def f(ranked, n_slots, rng):
        return [s for s, _ in ranked[:n]]
    return f


def sel_random(n):
    def f(ranked, n_slots, rng):
        pool = [s for s, _ in ranked]
        rng.shuffle(pool)
        return pool[:n]
    return f


class PortfolioBacktest:
    """
    Args:
        data:      {symbol: ohlcv df}
        signals:   {symbol: set(Timestamp)}  BUY 신호일
        days:      실험 구간 거래일 (오름차순)
        selector:  (ranked, n_slots, rng) -> [symbol, ...]
        max_slots: 동시 보유 상한 (None = 무제한)
    """

    def __init__(self, data, signals, days, selector, max_slots,
                 seed=0, score_pattern=False):
        self.data = data
        self.signals = signals
        self.days = days
        self.selector = selector
        self.max_slots = max_slots
        self.rng = random.Random(seed)
        # ⚠️ pattern_weights OFF — 작업지시서 Step 3 고정.
        #    켜면 과거 실거래에서 학습한 가중치가 들어가 look-ahead 가 된다.
        self.score_engine = ScoreEngine(score_pattern=score_pattern)
        self.engine = BacktestEngine(**EXIT_PROFILE)
        # ⚠️ 손익 계산은 이 값을 쓴다. EXIT_PROFILE 을 직접 참조하면
        #    슬리피지 실험에서 인스턴스별로 다르게 줄 수가 없다.
        self.commission = EXIT_PROFILE['commission']
        # 종목별 날짜→행번호 (매 루프 검색하면 느리다)
        self._pos_of = {s: {d: i for i, d in enumerate(df.index)}
                        for s, df in data.items()}

    # ── 스코어 ───────────────────────────────────────────────────────────
    def _score(self, sym, day) -> float:
        """
        ⚠️ ScoreEngine 은 `.iloc[-1]` 로 '지금'을 본다. 반드시 당일까지만
           잘라서 넘긴다 — 전체 df 를 주면 미래를 보고 점수를 매긴다.
        """
        i = self._pos_of[sym].get(day)
        if i is None:
            return 0.0
        sub = self.data[sym].iloc[:i + 1]
        s = self.score_engine.score(sym, ohlcv=sub)
        # smc 항목은 daily_scan 결과 집합 기준인데, 여기서는 후보 자체가
        # 이미 BUY 신호이므로 전원 동일 가산 → 순위에 영향이 없다.
        # volume / ma50 만 실제 변별력을 갖는다.
        return s['total'] + 2.0   # smc=+2 를 수동 반영 (후보 전원 공통)

    # ── 실행 ─────────────────────────────────────────────────────────────
    def run(self, name: str) -> CaseResult:
        res = CaseResult(name=name)
        open_pos: dict[str, dict] = {}
        pending: list[tuple] = []          # 다음 거래일 시가 진입 예약
        slot_won = INITIAL_CAPITAL / SLOT_DIVISOR
        realized = 0.0
        eq = []

        for day in self.days:
            # ① 예약분 진입 (전일 신호 → 오늘 시가)
            still = []
            for sym, score, rank, ncand, sigday in pending:
                i = self._pos_of[sym].get(day)
                if i is None:
                    still.append((sym, score, rank, ncand, sigday))  # 오늘 못 샀으면 다음날
                    continue
                df = self.data[sym]
                ep = float(df.iloc[i]['open'])
                open_pos[sym] = {
                    'entry_price': ep, 'entry_i': i,
                    'entry_date': str(day.date()),
                    'peak_price': ep, 'be_raised': False, 'trail_active': False,
                    'atr': self.engine._calc_atr(df, max(0, i - 1)),
                    'score': score, 'rank': rank, 'ncand': ncand,
                    'sigday': sigday,
                }
            pending = still

            # ② 청산 판정
            for sym in list(open_pos):
                p = open_pos[sym]
                df = self.data[sym]
                i = self._pos_of[sym].get(day)
                if i is None or i <= p['entry_i']:
                    continue
                row = df.iloc[i]
                close, high, low = (float(row['close']), float(row['high']),
                                    float(row['low']))
                ep = p['entry_price']
                bars = i - p['entry_i']
                chg = (close - ep) / ep
                reason, xp = self.engine._check_swing_exit(
                    p, close, high, low, chg, bars, ep)
                if not reason:
                    continue
                pnl = (xp - ep) / ep - self.commission * 2
                res.trades.append(PTrade(
                    symbol=sym, signal_date=p['sigday'],
                    entry_date=p['entry_date'],
                    exit_date=str(day.date()), entry_price=ep,
                    exit_price=round(xp, 0), pnl_pct=round(pnl, 4),
                    pnl_won=round(pnl * slot_won, 0), exit_reason=reason,
                    hold_bars=bars, score=p['score'], rank=p['rank'],
                    n_candidates=p['ncand']))
                realized += pnl * slot_won
                del open_pos[sym]

            # ③ 후보 수집 + 선별
            cands = [s for s, dd in self.signals.items()
                     if day in dd and s not in open_pos
                     and s not in {x[0] for x in pending}]
            if cands:
                ranked = sorted(((s, self._score(s, day)) for s in cands),
                                key=lambda x: x[1], reverse=True)
                free = (len(ranked) if self.max_slots is None
                        else max(0, self.max_slots - len(open_pos) - len(pending)))
                res.selection_days += 1
                # ⚠️ '고르는 행위'가 결과를 바꾸는 날은 **후보 > 빈 슬롯** 인
                #    날뿐이다. 후보 > 전체 슬롯 으로 세면 안 된다 — 슬롯은
                #    전날까지의 보유분이 이미 먹고 있을 수 있다.
                if len(ranked) > free:
                    res.binding_days += 1
                    res.dropped_candidates += len(ranked) - free
                if free > 0:
                    picked = self.selector(ranked, free, self.rng)[:free]
                    rank_of = {s: n for n, (s, _) in enumerate(ranked, 1)}
                    sc_of = dict(ranked)
                    for s in picked:
                        pending.append((s, sc_of[s], rank_of[s], len(ranked),
                                        str(day.date())))

            # ④ 평가금 (미실현 포함)
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


# ── KPI ──────────────────────────────────────────────────────────────────
def kpi(res: CaseResult) -> dict:
    t = res.trades
    n = len(t)
    if n == 0:
        return {'name': res.name, 'trades': 0}

    r = np.array([x.pnl_pct for x in t])
    won = np.array([x.pnl_won for x in t])
    wins, losses = r[r > 0], r[r <= 0]
    gp, gl = won[won > 0].sum(), -won[won <= 0].sum()

    # 최대 연속 손실 — 청산 시각 순으로 센다. 진입 순이 아니다.
    chrono = sorted(t, key=lambda x: (x.exit_date, x.symbol))
    run = mcl = 0
    for x in chrono:
        run = run + 1 if x.pnl_pct <= 0 else 0
        mcl = max(mcl, run)

    e = res.equity
    dd = (e - e.cummax()) / e.cummax()
    # 일수익률 — 거래가 드물어 0 이 많다. Sharpe 는 참고치로만 본다.
    dr = e.pct_change().dropna()

    return {
        'name': res.name,
        'trades': n,
        'win_rate': round(len(wins) / n * 100, 1),
        'avg_return_pct': round(r.mean() * 100, 2),
        'avg_win_pct': round(wins.mean() * 100, 2) if len(wins) else 0.0,
        'avg_loss_pct': round(losses.mean() * 100, 2) if len(losses) else 0.0,
        'profit_factor': round(gp / gl, 3) if gl > 0 else None,
        # 기대값 — 1거래당 평균 손익. 승률만으로는 알 수 없는 값이다.
        'expectancy_pct': round(r.mean() * 100, 3),
        'expectancy_won': int(won.mean()),
        'max_consecutive_loss': mcl,
        'total_pnl_won': int(won.sum()),
        'total_return_pct': round(won.sum() / INITIAL_CAPITAL * 100, 2),
        'mdd_pct': round(dd.min() * 100, 2),
        'sharpe': round(dr.mean() / dr.std() * np.sqrt(252), 2)
        if dr.std() > 0 else None,
        'avg_hold_bars': round(np.mean([x.hold_bars for x in t]), 1),
        'exit_reasons': dict(pd.Series([x.exit_reason for x in t])
                             .value_counts().items()),
        'selection_days': res.selection_days,
        'binding_days': res.binding_days,
        'dropped_candidates': res.dropped_candidates,
    }
