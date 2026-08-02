"""
Phase 1 — Baseline 재현 및 동결

Phase 0 에서 쓴 것과 **같은 코드 경로**로 다시 돌려 숫자가 재현되는지
확인하고 `phase1/baseline.json` 에 박아 둔다. 이후 모든 실험은 이 파일의
값과 비교한다.

⚠️ 재현이 안 되면 그 자체가 사건이다. 같은 입력·같은 코드인데 숫자가
   달라졌다면 데이터 제공처가 과거 봉을 고쳤다는 뜻이고, 그러면 Phase 0
   의 결론도 다시 봐야 한다.

사용법:
    python -m phase1.baseline
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase0 import data_cache as dc
from phase0 import profiles as pf
from phase0.candidate_gen import ParamCandidateGen, verify_parity
from phase0.portfolio import (EXIT_PROFILE, INITIAL_CAPITAL, SLOT_DIVISOR,
                              PortfolioBacktest, kpi, sel_baseline)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'baseline.json')

# Phase 0 이 남긴 값 — 재현 대상
EXPECTED = {'trades': 81, 'win_rate': 43.2, 'profit_factor': 1.813,
            'total_return_pct': 36.38, 'mdd_pct': -11.27}


def _git(*a):
    try:
        return subprocess.run(['git', *a], capture_output=True, text=True,
                              cwd=os.path.dirname(os.path.dirname(
                                  os.path.abspath(__file__)))).stdout.strip()
    except Exception:
        return 'UNKNOWN'


def main():
    data = dc.load_ohlcv()
    cached = dc.build_signals(data)
    days = dc.trading_days(data)

    print('=' * 74)
    print('  Phase 1 — Baseline 재현')
    print('=' * 74)
    print(f'  기간 {dc.START} ~ {dc.END}   {len(days)}거래일   '
          f'{len(data)}종목 (DEFAULT_CANDIDATES)')

    ok, msg = verify_parity(data, cached)
    print(f'  후보 생성 패리티: {"✅ " if ok else "❌ "}{msg}')
    if not ok:
        sys.exit(1)

    sig = ParamCandidateGen(**pf.gen_kwargs('OPS')).scan(data)
    ncand = sum(1 for s in sig.values() for d in s if d in set(days))

    # ⚠️ Phase 0 Baseline 은 '상한 없음' 으로 낸 값이다. 슬롯을 씌우면
    #    다른 숫자가 나온다 — 비교 기준을 섞지 않는다.
    res = PortfolioBacktest(data, sig, days, sel_baseline, None, 0).run('OPS')
    k = kpi(res)

    print(f'\n  후보 {ncand}건   거래 {k["trades"]}건')
    print(f'  {"항목":<16}{"Phase0 기록":>12}{"재현":>12}   일치')
    allok = True
    for key, exp in EXPECTED.items():
        got = k[key]
        same = abs(got - exp) < 0.02
        allok &= same
        print(f'  {key:<16}{exp:>12}{got:>12}   {"✅" if same else "❌"}')

    print(f'\n  판정: {"✅ 재현됨" if allok else "❌ 재현 실패 — Phase 0 결론 재검토 필요"}')

    snap = {
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'git_commit': _git('rev-parse', 'HEAD'),
        'reproduced': allok,
        'period': {'start': dc.START, 'end': dc.END,
                   'trading_days': len(days)},
        'universe': {'source': 'backtest.scanner.DEFAULT_CANDIDATES',
                     'symbols': len(data), 'survivorship_bias': True},
        'profile': {'name': 'OPS', 'params': pf.get('OPS'),
                    'slot_cap': None,
                    'note': 'Phase 0 Baseline 은 상한 없음으로 낸 값이다'},
        'exit_profile': EXIT_PROFILE,
        'capital': {'initial': INITIAL_CAPITAL, 'slot_divisor': SLOT_DIVISOR,
                    'compounding': False},
        'candidates': ncand,
        'kpi': k,
        'expected_from_phase0': EXPECTED,
        'caveats': [
            '청산은 일봉 스윙 근사다. 운영 5분봉 청산과 다르다.',
            'yfinance 데이터 변동성이 실제보다 크다 (삼성전자 연율 56%).',
            '이 값은 백테스트다. 실거래 성과와 직접 비교하면 안 된다.',
        ],
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2, default=str)
    print(f'  저장: {OUT}')


if __name__ == '__main__':
    main()
