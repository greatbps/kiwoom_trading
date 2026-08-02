"""
Candidate Generator 프로파일 — feature flag

━━━ 기본값은 언제나 운영 현행이다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  `ACTIVE = 'OPS'`. 아무것도 설정하지 않으면 지금 돌고 있는 조건 그대로다.
  검증 전 후보 생성 조건이 바뀌는 일은 없어야 한다.

━━━ 왜 여기에 두는가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  작업지시서가 `daily_scan.py` · `adapter.py` · `score_engine.py` 수정을
  금지했다. 그래서 세 파일을 건드리지 않고, 프로파일 정의와 선택만
  이 모듈에 모은다. Paper 검증을 통과해 CandidateGenerator v1.1 로
  승격될 때, 운영 코드는 이 모듈의 값을 읽어 가기만 하면 된다.

━━━ 프로파일 (Phase 0.3 작업지시서 정의) ━━━━━━━━━━━━━━━━━━━━━━━

      OPS  RVOL>=1.50  ATR 2~8%      MA50 상승      MAX_SELECTED 5
      E    RVOL>=1.50  ATR 1.5~10%   MA50 >= -2%    MAX_SELECTED 5
      G    RVOL>=1.35  ATR 1.5~10%   MA50 >= -2%    MAX_SELECTED 6

  ⚠️ 지시서의 'E' 는 Phase 0.2 의 Case E(B+C+D, RVOL 1.2)가 아니라
     Case F(B+D, RVOL 운영값 유지)다. Volume 완화는 두 안 모두에서
     빠졌다 — Phase 0.2 품질 분석에서 신규 후보 PF 가 1.108 로
     거의 이익을 못 냈기 때문이다.

  ⚠️ G 의 RVOL 1.35 는 Phase 0.2 데이터를 보고 정한 값이다.
     과최적화 위험이 있고, 그래서 Paper 검증을 거친다.
"""
from __future__ import annotations

import os

PROFILES = {
    'OPS': dict(rvol_min=1.50, atr_min=0.020, atr_max=0.08,
                ma50_slope_min=None, max_selected=5),
    'E': dict(rvol_min=1.50, atr_min=0.015, atr_max=0.10,
              ma50_slope_min=-0.02, max_selected=5),
    'G': dict(rvol_min=1.35, atr_min=0.015, atr_max=0.10,
              ma50_slope_min=-0.02, max_selected=6),
}

# feature flag — 기본은 운영 현행. 환경변수로만 바꾼다.
ACTIVE = os.environ.get('KIWOOM_CANDIDATE_PROFILE', 'OPS')

VERSION = {'OPS': 'v1.0 (운영 현행)',
           'E': 'v1.1-rc1 (Paper 검증 중)',
           'G': 'v1.1-rc2 (Paper 검증 중)'}


def get(name: str | None = None) -> dict:
    n = (name or ACTIVE).upper()
    if n not in PROFILES:
        # ⚠️ 모르는 프로파일을 만나면 운영 현행으로 떨어뜨리지 않는다.
        #    오타 하나로 조용히 다른 조건이 도는 것이 더 위험하다.
        raise ValueError(f'알 수 없는 프로파일: {n} (가능: {list(PROFILES)})')
    return dict(PROFILES[n])


def gen_kwargs(name: str | None = None) -> dict:
    """ParamCandidateGen 에 넘길 인자만 (max_selected 제외)."""
    p = get(name)
    p.pop('max_selected', None)
    return p


def max_selected(name: str | None = None) -> int:
    return get(name)['max_selected']
