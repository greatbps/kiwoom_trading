"""
Phase 0.2 부록 — 후보 확대 × 슬롯 상한

본 실험(A~E)은 '상한 없음' 으로 돌렸다. 케이스 간 비교를 오염시키지
않으려면 그게 맞다. 그런데 MDD 탈락을 그대로 결론으로 쓰면 곤란하다 —

  운영 시스템에는 이미 상한이 있다 (`ScoreEngine.MAX_SELECTED = 5`).

후보를 260건으로 늘리면 동시 보유가 함께 늘고, 상한 없는 구성에서는
그만큼 노출이 커져 MDD 가 벌어진다. 즉 MDD -16.31% 는 신호 품질이 아니라
**동시 노출의 함수**일 수 있다. 상한을 씌웠을 때도 기준을 못 넘는지
확인해야 "Case E 탈락" 이라고 말할 수 있다.

⚠️ 이건 새 정책 제안이 아니라 기존 상한값을 그대로 적용해 본 것이다.
   Portfolio 로직은 건드리지 않았다.

사용법:
    python -m phase0.slot_sweep
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase0 import data_cache as dc
from phase0.candidate_gen import CASES, ParamCandidateGen
from phase0.portfolio import (PortfolioBacktest, kpi, sel_baseline, sel_top)

MDD_FLOOR = -15.0
MIN_CANDIDATES = 200


def main():
    data = dc.load_ohlcv()
    days = dc.trading_days(data)
    days_set = set(days)

    print('=' * 78)
    print('  후보 확대 × 슬롯 상한 — MDD 탈락이 신호 품질 탓인가 노출 탓인가')
    print('=' * 78)

    for case in ('A. Baseline (현행)', 'B. MA50 완화 (>=-2%)', 'E. 조합 (B+C+D)'):
        sig = ParamCandidateGen(**CASES[case]).scan(data)
        n_cand = sum(1 for s in sig.values() for d in s if d in days_set)
        print(f'\n  {case}   후보 {n_cand}건')
        print(f'    {"상한":>6}{"거래":>6}{"승률%":>8}{"PF":>8}{"수익%":>9}'
              f'{"MDD%":>9}{"기대값%":>9}{"연속손실":>8}   판정')
        for slots in (3, 5, 8, 10, None):
            selr = sel_baseline if slots is None else sel_top(slots)
            k = kpi(PortfolioBacktest(data, sig, days, selr, slots, 0)
                    .run(f'{case}/{slots}'))
            if not k.get('trades'):
                continue
            mdd = k['mdd_pct']
            pf = k['profit_factor'] or 0
            okk = (n_cand >= MIN_CANDIDATES and mdd > MDD_FLOOR
                   and pf >= 1.541)
            print(f'    {(str(slots) if slots else "무제한"):>6}'
                  f'{k["trades"]:>6}{k["win_rate"]:>8.1f}{pf:>8.3f}'
                  f'{k["total_return_pct"]:>9.2f}{mdd:>9.2f}'
                  f'{k["expectancy_pct"]:>9.3f}'
                  f'{k["max_consecutive_loss"]:>8}   '
                  f'{"✅ 3기준 통과" if okk else ""}')

    print('\n  * 판정 = 후보>=200 AND PF>=1.541 AND MDD>-15%')
    print('  * 운영 현행 상한은 ScoreEngine.MAX_SELECTED = 5')


if __name__ == '__main__':
    main()
