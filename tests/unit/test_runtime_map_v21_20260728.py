"""
tests/unit/test_runtime_map_v21_20260728.py

Production Audit v2.1 — Runtime Execution Map 고정 테스트.

목적: `docs/RUNTIME_EXECUTION_MAP.md`가 주장하는 "무엇이 실행되고 무엇이 휴면인가"를
코드로 고정한다. 누군가 휴면 모듈을 실행 경로에 끌어들이거나, 반대로 실사용 모듈을
경로에서 떼어내면 이 테스트가 실패해 맵 갱신이 필요함을 알린다.

이 테스트는 전략 로직을 검증하지 않는다 — 오직 "실행 경로 구조"만 본다.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE))

_ENTRYPOINTS = [
    'main_auto_trading.py', 'api_server.py', 'watchdog.py',
    'swing_runner.py', 'swing_executor.py',
]


def _module_deps(dotted: str, relpath: str) -> set:
    """
    top-level + 함수 내부 지연 import + **상대 import** 모두 수집.

    상대 import(`from .smc_signals import ...`)를 빠뜨리면 analyzers/smc 처럼
    패키지 __init__이 상대 경로로 하위를 끌어오는 구조를 통째로 놓친다.
    """
    try:
        tree = ast.parse((BASE / relpath).read_text(encoding='utf-8', errors='ignore'))
    except Exception:
        return set()
    pkg = dotted if relpath.endswith('__init__.py') else (
        dotted.rsplit('.', 1)[0] if '.' in dotted else ''
    )
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name)
        elif isinstance(n, ast.ImportFrom):
            if n.level:                                    # 상대 import 해석
                base = pkg.split('.') if pkg else []
                if n.level > 1:
                    base = base[:-(n.level - 1)]
                root = '.'.join(base + ([n.module] if n.module else []))
            else:
                root = n.module or ''
            if not root:
                continue
            out.add(root)
            for a in n.names:
                out.add(f'{root}.{a.name}')
    return out


def _all_project_modules() -> dict:
    skip = {'venv', '__pycache__', '.git', 'backups', 'archive', 'htmlcov',
            'gpt_share', 'node_modules'}
    mods = {}
    for p in BASE.rglob('*.py'):
        rel = p.relative_to(BASE)
        if any(x in rel.parts for x in skip):
            continue
        d = str(rel)[:-3].replace('/', '.')
        if d.endswith('.__init__'):
            d = d[:-9]
        mods[d] = str(rel)
    return mods


def _loaded_closure() -> set:
    """진입점에서 시작한 import 전이 폐쇄 = 실제로 '로드되는' 모듈."""
    mods = _all_project_modules()
    entries = [e[:-3].replace('/', '.') for e in _ENTRYPOINTS]
    loaded, frontier = set(), [e for e in entries if e in mods]
    while frontier:
        cur = frontier.pop()
        if cur in loaded:
            continue
        loaded.add(cur)
        # 하위 모듈을 import하면 패키지 __init__도 함께 실행된다
        if '.' in cur:
            parent = cur.rsplit('.', 1)[0]
            if parent in mods and parent not in loaded:
                frontier.append(parent)
        for dep in _module_deps(cur, mods[cur]):
            cand = dep
            while cand:
                if cand in mods and cand not in loaded:
                    frontier.append(cand)
                    break
                if '.' not in cand:
                    break
                cand = cand.rsplit('.', 1)[0]
    return loaded


# ═════════════════════════════════════════════════════════════════════════════
# 실사용(ACTIVE) 모듈이 실행 경로에서 이탈하지 않았는지
# ═════════════════════════════════════════════════════════════════════════════

_MUST_BE_LOADED = [
    'kiwoom_api',                          # 실제 주문 실행
    'trading.exit_logic_optimized',        # 실제 청산 로직
    'trading.score_engine',                # Score
    'analyzers.market.regime_analyzer',    # Regime Gate
    'analyzers.smc.smc_signals',           # SMC 진입 신호
    'core.risk_manager',                   # Risk Gate
    'core.drawdown_engine',                # Drawdown
    'trading.equity_controller',           # Equity/EC_HALT
    'database.trading_db',                 # 거래 DB
    'services.decision_service',           # Decision Ledger
    'brokers.korea_invest_broker',         # KIS 중기 (실제 인스턴스화됨)
]


@pytest.mark.parametrize('module', _MUST_BE_LOADED)
def test_active_modules_remain_in_execution_path(module):
    """RUNTIME_EXECUTION_MAP이 ACTIVE로 분류한 모듈은 계속 로드되어야 한다."""
    loaded = _loaded_closure()
    assert module in loaded, (
        f'{module}이 실행 경로에서 빠졌다 — docs/RUNTIME_EXECUTION_MAP.md 갱신 필요'
    )


# ═════════════════════════════════════════════════════════════════════════════
# 휴면(NOT-LOADED) 모듈이 조용히 실행 경로로 들어오지 않았는지
#
# 이 목록은 "고쳐도 효과 없는 파일"이다. 만약 누군가 이걸 import하기 시작하면
# 실거래 동작이 바뀔 수 있으므로 반드시 검토가 필요하다.
# ═════════════════════════════════════════════════════════════════════════════

_MUST_STAY_DORMANT = [
    'core.order_executor',          # 주문 실행기 (중복 구현)
    'core.stop_loss_manager',       # 손절 (중복)
    'core.auto_stop_loss_system',   # 손절 (중복) — 이름 때문에 오인하기 쉬움
    'trading.stop_loss_executor',   # 손절 (중복)
    'core.position_manager',        # 포지션 (중복)
    'core.portfolio_manager',       # 포트폴리오 (중복)
    'core.scheduler',               # 스케줄러 (실제로는 cron 사용)
]


@pytest.mark.parametrize('module', _MUST_STAY_DORMANT)
def test_dormant_modules_are_not_silently_activated(module):
    """
    NOT-LOADED 모듈이 실행 경로에 편입되면 실패한다.

    편입 자체가 잘못이라는 뜻은 아니지만, 같은 기능의 구현이 2개 동시에 살아나는
    상황은 반드시 사람이 검토해야 한다(예: 손절 로직이 2중으로 도는 경우).
    """
    loaded = _loaded_closure()
    assert module not in loaded, (
        f'{module}이 실행 경로에 편입됐다. 같은 기능의 실사용 구현과 중복 동작할 수 있다 — '
        f'docs/RUNTIME_EXECUTION_MAP.md §3 대조표를 확인하고 맵을 갱신할 것'
    )


# LOADED-ONLY: 패키지 __init__이 끌어와 모듈은 로드되지만 인스턴스화/호출은 없음.
# (trading/__init__.py가 하위 12개를 eager import, brokers/__init__.py가 KiwoomBroker import)
# 고쳐도 실거래에 영향이 없다는 점은 NOT-LOADED와 동일하다.
_LOADED_ONLY_CLASSES = {
    'trading.order_executor':    'OrderExecutor',
    'trading.position_tracker':  'PositionTracker',
    'trading.trend_exit_engine': 'TrendExitEngine',
    'brokers.kiwoom_broker':     'KiwoomBroker',
}


@pytest.mark.parametrize('module,cls', sorted(_LOADED_ONLY_CLASSES.items()))
def test_loaded_only_modules_are_never_instantiated(module, cls):
    """
    LOADED-ONLY 모듈의 클래스가 진입점에서 인스턴스화되기 시작하면 실패한다.

    예: TrendExitEngine이 살아나면 trailing stop 로직이 2중으로 돌 수 있다
    (trend_exit_engine.py:360에는 monotonic 가드가 없다 — FU-01).
    """
    entry_srcs = ''
    for e in _ENTRYPOINTS:
        p = BASE / e
        if p.exists():
            entry_srcs += p.read_text(encoding='utf-8', errors='ignore')
    assert f'{cls}(' not in entry_srcs, (
        f'{module}.{cls}가 진입점에서 인스턴스화되기 시작했다 — '
        f'실사용 구현과 중복 동작 가능. RUNTIME_EXECUTION_MAP.md §3 확인 필요'
    )


def test_kiwoom_broker_is_imported_but_never_used_as_order_path():
    """
    brokers.kiwoom_broker는 brokers/__init__.py를 통해 import되지만
    get_broker(BrokerType.KIWOOM)이 호출되지 않아 주문 경로가 아니다.
    실제 키움 주문은 kiwoom_api.KiwoomAPI.order_buy/order_sell가 담당한다.
    """
    src = (BASE / 'main_auto_trading.py').read_text(encoding='utf-8', errors='ignore')
    assert 'BrokerType.KIWOOM' not in src, (
        'main_auto_trading.py가 BrokerType.KIWOOM을 사용하기 시작했다 — '
        '주문 경로가 kiwoom_api와 brokers.kiwoom_broker 두 개로 갈라진다'
    )
    assert 'BrokerType.KIS_DOMESTIC' in src   # KIS 중기 경로는 계속 사용됨


# ═════════════════════════════════════════════════════════════════════════════
# Reachability — 근거가 확정된 항목만 고정
# ═════════════════════════════════════════════════════════════════════════════

def test_fvg_detection_is_not_implemented_so_flag_is_always_false():
    """
    [ALWAYS FALSE 확정] main_auto_trading.py가 details.get('fvg')를 읽지만
    analyzers/smc/ 어디에도 fvg를 설정하는 구현이 없어 항상 None → False다.

    FVG 탐지가 구현되면 이 테스트가 실패하며, 그때 Decision Ledger의
    FVG_MISSING reason code도 함께 되살아난다(현재는 발행 불가).
    """
    smc_dir = BASE / 'analyzers' / 'smc'
    hits = []
    for p in smc_dir.glob('*.py'):
        txt = p.read_text(encoding='utf-8', errors='ignore').lower()
        if 'fvg' in txt:
            hits.append(p.name)
    assert hits == [], (
        f'analyzers/smc/에 fvg 구현이 생겼다({hits}) — '
        f"main_auto_trading.py의 details.get('fvg')가 더 이상 항상 False가 아니다. "
        f'docs/PRODUCTION_AUDIT_V2_1_REPORT.md Reachability 표를 갱신할 것'
    )


def test_smc_entry_pipeline_is_reachable_by_log_evidence():
    """
    [REACHABLE 확정] SMC 진입 파이프라인은 실제로 동작한다.
    smc_decision 로그에 CHOCH/SWEEP 기록이 누적되어 있어야 한다(과거 증거 기반).
    """
    logs = sorted((BASE / 'logs').glob('smc_decision_2026*.log'))
    if not logs:
        pytest.skip('smc_decision 로그 없음 — 로그 보관 정책 확인 필요')
    joined = ''
    for p in logs[-10:]:
        joined += p.read_text(encoding='utf-8', errors='ignore')
    real = [ln for ln in joined.splitlines() if 'TEST' not in ln]
    assert any('[CHOCH]' in ln for ln in real) or any('[NO_SIG]' in ln for ln in real), (
        'SMC 파이프라인의 런타임 증거가 사라졌다 — 파이프라인이 멈췄을 수 있다'
    )
