"""
백테스트 실행기

사용법:
    python -m backtest.runner
    python -m backtest.runner --start 2023-01-01 --end 2024-12-31
    python -m backtest.runner --tp 0.05 --sl 0.03

    # TP/SL 그리드 (단타)
    python -m backtest.runner --grid-search

    # 스윙 그리드 (min_hold × trailing × BE trigger)
    python -m backtest.runner --swing-grid
    python -m backtest.runner --swing-grid --min-hold 4 8 16 --trailing 0.03 0.05
"""
import argparse
import itertools
import logging
import math
import sys
import os

# 루트 패키지 경로
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.loader  import load_multi
from backtest.adapter import SMCAdapter
from backtest.engine  import BacktestEngine
from backtest.metrics import calculate, aggregate
from backtest.fitness import RollingFitnessTracker

# 백테스트 중 SMC 내부 logger 억제
logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(message)s')
logging.getLogger('smc_decision').setLevel(logging.CRITICAL)
logging.getLogger('sweep_attempt').setLevel(logging.CRITICAL)
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

logger = logging.getLogger('backtest')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(message)s'))
logger.addHandler(handler)

# API 호출 시 True로 설정 → 모든 출력 억제
_QUIET = False


def _quiet_log(msg: str) -> None:
    if not _QUIET:
        logger.info(msg)


# ── 기본 종목 (20종목) ──────────────────────────────────────────────────────
DEFAULT_SYMBOLS = [
    # 대형주 KOSPI
    '005930',  # 삼성전자
    '000660',  # SK하이닉스
    '035420',  # NAVER
    '035720',  # 카카오
    '373220',  # LG에너지솔루션
    '006400',  # 삼성SDI
    '005380',  # 현대차
    '000270',  # 기아
    '005490',  # POSCO홀딩스
    '051910',  # LG화학
    # 코스닥
    '086520',  # 에코프로
    '247540',  # 에코프로비엠
    '403870',  # HPSP
    '042700',  # 한미반도체
    '058470',  # 리노공업
    # 기타 KOSPI
    '034020',  # 두산에너빌리티
    '003490',  # 대한항공
    '068270',  # 셀트리온
    '207940',  # 삼성바이오로직스
    '329180',  # HD현대중공업
]

# 전략 → adapter kwargs 매핑
_STRATEGY_MAP = {
    'B':           dict(require_sweep=False),
    'B+HTF':       dict(require_sweep=False, require_htf=True),
    'B+VOL':       dict(require_sweep=False, require_volume=True),
    'B+HTF+VOL':   dict(require_sweep=False, require_htf=True, require_volume=True),
    'B+VOL+EMA60': dict(require_sweep=False, require_volume=True, require_ema60=True),
    # ── 진입 타이밍 개선 전략 (MAE 개선용) ──────────────────────────────────
    # A: EMA20 이격 2% 제한
    'B+VOL+EXT':   dict(require_sweep=False, require_volume=True,
                        ema_extension_limit=0.02),
    # A+B: EMA20 이격 + CHoCH 돌파 추격 1.5% 제한
    'B+VOL+EXT+BD': dict(require_sweep=False, require_volume=True,
                         ema_extension_limit=0.02, breakout_dist_limit=0.015),
    # A+B+D: 위 + 눌림 확인
    'B+VOL+TIMING': dict(require_sweep=False, require_volume=True,
                         ema_extension_limit=0.02, breakout_dist_limit=0.015,
                         require_pullback=True),
    # A+B+D+E: 전체 진입 타이밍 필터 (최강화)
    'B+VOL+TIMING+ATR': dict(require_sweep=False, require_volume=True,
                              ema_extension_limit=0.02, breakout_dist_limit=0.015,
                              require_pullback=True, atr_pct_limit=0.06),
    # ── 종목 적합성 필터 ─────────────────────────────────────────────────────
    'B+VOL+ATR_RANGE':  dict(require_sweep=False, require_volume=True,
                              atr_pct_min=0.02, atr_pct_max=0.08),
    'B+VOL+ATR+MA50':   dict(require_sweep=False, require_volume=True,
                              atr_pct_min=0.02, atr_pct_max=0.08,
                              require_ma50_trend=True),
    # ── Fitness Score 필터 (ATR+MA50 기반 + rolling fitness) ────────────────
    'B+VOL+ATR+MA50+FIT': dict(require_sweep=False, require_volume=True,
                                atr_pct_min=0.02, atr_pct_max=0.08,
                                require_ma50_trend=True),
}


def _run_single_strategy(
    data: dict,
    config: dict,
    engine_kwargs: dict,
    label: str,
    mode: str = 'tp_sl',
    use_fitness: bool = False,
    fitness_kwargs: dict = None,
    **adapter_kwargs,
) -> tuple[list, list]:
    """
    use_fitness=True 시 RollingFitnessTracker를 생성해 종목별로 공유.
    종목 순서대로 누적 → 진행할수록 fitness 필터가 실시간 작동.
    """
    # 공유 fitness tracker (use_fitness=True 시)
    tracker = RollingFitnessTracker(**(fitness_kwargs or {})) if use_fitness else None

    all_metrics, all_trades = [], []

    for symbol, df in data.items():
        # 종목별 adapter (fitness tracker + symbol 주입)
        sym_adapter = SMCAdapter(
            config,
            fitness_tracker=tracker,
            symbol=symbol,
            **adapter_kwargs,
        )

        def _make_signal_func(adp):
            def _sig(df_, i):
                return adp.get_signal(df_, i)
            return _sig

        def _make_on_complete(sym, trk):
            def _cb(_, trade):
                if trk is not None:
                    trk.update(sym, trade.mfe_pct, trade.mae_pct)
            return _cb

        engine = BacktestEngine(
            signal_func=_make_signal_func(sym_adapter),
            on_trade_complete=_make_on_complete(symbol, tracker),
            **engine_kwargs,
        )
        result = engine.run(df, symbol)
        m = calculate(symbol, result.trades)
        all_metrics.append(m)
        all_trades.extend(result.trades)
        if m.trades > 0:
            _quiet_log(f'  [{label}] ' + m.summary(mode))

    return all_metrics, all_trades


def run(
    symbols:    list[str] = None,
    start:      str       = '2022-01-01',
    end:        str       = '2024-12-31',
    tp_pct:     float     = 0.05,
    sl_pct:     float     = 0.03,
    swing_lb:   int       = 2,
    sweep_lb:   int       = 15,
):
    symbols = symbols or DEFAULT_SYMBOLS

    logger.info(f'\n{"="*60}')
    logger.info(f'  SMC 백테스트  ({start} ~ {end})')
    logger.info(f'  TP:{tp_pct*100:.1f}%  SL:{sl_pct*100:.1f}%  종목:{len(symbols)}개')
    logger.info(f'{"="*60}')

    # 1. 데이터 로드 (1회만)
    logger.info('\n[1/3] 데이터 다운로드 중...')
    data = load_multi(symbols, start, end)
    if not data:
        logger.error('데이터 없음. 종료.')
        return

    logger.info(f'  → {len(data)}종목 로드 완료')
    config = {'swing_lookback': swing_lb, 'sweep_lookback': sweep_lb}

    # 전략 목록: (label, adapter kwargs)
    strategies = [
        ('B       (base)  ', dict(require_sweep=False)),
        ('B+HTF           ', dict(require_sweep=False, require_htf=True)),
        ('B+VOL           ', dict(require_sweep=False, require_volume=True)),
        ('B+HTF+VOL       ', dict(require_sweep=False, require_htf=True, require_volume=True)),
        ('B+VOL+EMA60     ', dict(require_sweep=False, require_volume=True, require_ema60=True)),
    ]

    engine_kwargs = dict(tp_pct=tp_pct, sl_pct=-sl_pct)
    results = []
    for idx, (label, kwargs) in enumerate(strategies, 2):
        logger.info(f'\n[{idx+1}/{len(strategies)+1}] 전략 {label.strip()}\n')
        m, t = _run_single_strategy(data, config, engine_kwargs, label.strip(), **kwargs)
        results.append((label, m, t))

    # ── 비교 요약 ──────────────────────────────────────────────────────────
    logger.info(f'\n{"="*65}')
    logger.info(f'  {"전략":<20} {"trades":>6}  {"win%":>5}  {"RR":>4}  {"MDD":>6}  {"ret%":>6}  pass')
    logger.info(f'{"="*65}')

    best = None
    for label, metrics, trades in results:
        agg = aggregate(metrics)
        win  = agg['overall_win_rate'] * 100
        ret  = agg['total_return'] * 100
        mdd  = agg['avg_mdd'] * 100
        rr   = agg['avg_rr']
        logger.info(
            f'  {label:<20} {agg["total_trades"]:>6}  {win:>4.1f}%  {rr:>4.2f}  {mdd:>5.1f}%  {ret:>+5.1f}%  {agg["passed_symbols"]}'
        )
        if best is None or ret > best[0]:
            best = (ret, label.strip(), agg)

    logger.info(f'\n  [평가 기준: trades≥30, win≥55%, RR≥1.5, MDD≥-15%]')

    if best:
        b = best[2]
        if b['overall_win_rate'] >= 0.55 and b['avg_rr'] >= 1.5:
            logger.info(f'\n  ✅ {best[1]} → 2차 (5분봉) 검증으로 진행 가능')
        else:
            logger.info(f'\n  🔧 최고 수익: {best[1]}  → TP/SL 튜닝 단계')

    return results


def run_grid_search(
    symbols:    list[str]   = None,
    start:      str         = '2022-01-01',
    end:        str         = '2024-12-31',
    swing_lb:   int         = 2,
    sweep_lb:   int         = 15,
    tp_range:   list        = None,
    sl_range:   list        = None,
    strategy:   str         = 'B+VOL',
    mode:       str         = 'tp_sl',   # 'tp_sl' | 'swing'
    # 스윙 전용 파라미터
    min_hold_range:   list  = None,
    trailing_range:   list  = None,
    be_trigger_range: list  = None,
    on_progress = None,    # callback(done, total, param1, param2, row) → 실시간 진행 보고
    use_fitness: bool  = False,   # Fitness Score 필터 활성화
    fitness_kwargs: dict = None,  # RollingFitnessTracker 파라미터
):
    """B+VOL 전략 그리드 서치.

    mode='tp_sl': TP × SL 조합
    mode='swing': min_hold × trailing × be_trigger 조합

    on_progress(done, total, p1, p2, row_dict | None):
        row_dict = None → 데이터 로드 완료 알림 (done=0)
        row_dict = {...} → 조합 1개 완료
    """
    symbols  = symbols  or DEFAULT_SYMBOLS

    # 기본값 (mode별)
    if mode == 'swing':
        # 스윙 핵심: TP 8~25%(추세 먹기), trailing 5~12%(너무 타이트하면 수익 못 키움)
        # min_hold 8/24/48 → 당일/하루/진짜스윙
        tp_range         = tp_range         or [0.08, 0.12, 0.18, 0.25]
        sl_range         = sl_range         or [0.02, 0.03, 0.04]
        min_hold_range   = min_hold_range   or [8, 24, 48]
        trailing_range   = trailing_range   or [0.05, 0.08, 0.12]
        be_trigger_range = be_trigger_range or [0.03, 0.05]
    else:
        tp_range = tp_range or [0.02, 0.03, 0.04, 0.05]
        sl_range = sl_range or [0.01, 0.015, 0.02, 0.025, 0.03]

    adapter_kwargs = _STRATEGY_MAP.get(strategy, _STRATEGY_MAP['B+VOL'])

    global _QUIET
    _QUIET = on_progress is not None

    def _log(msg):
        if not _QUIET:
            logger.info(msg)

    if mode == 'swing':
        # 스윙 그리드 → 4축: TP × trailing × min_hold × be_trigger
        # TP도 변수로 포함 → TP×trailing 히트맵 지원
        _log(f'\n{"="*70}')
        _log(f'  {strategy} 스윙 그리드 서치  ({start} ~ {end})')
        _log(f'  TP: {tp_range}  SL: {sl_range}')
        _log(f'  min_hold: {min_hold_range}  trailing: {trailing_range}  be_trigger: {be_trigger_range}')
        # SL은 단일 대표값 (중간값)
        sl_fixed = sl_range[len(sl_range) // 2]
        combos_swing = list(itertools.product(tp_range, min_hold_range, trailing_range, be_trigger_range))
        _log(f'  대표 SL={sl_fixed*100:.0f}%')
        _log(f'  조합 수: {len(combos_swing)}  종목: {len(symbols)}개')
        _log(f'{"="*70}')
    else:
        _log(f'\n{"="*65}')
        _log(f'  {strategy} TP/SL 그리드 서치  ({start} ~ {end})')
        _log(f'  TP: {tp_range}')
        _log(f'  SL: {sl_range}')
        combos = list(itertools.product(tp_range, sl_range))
        _log(f'  조합 수: {len(combos)}  종목: {len(symbols)}개')
        _log(f'{"="*65}')

    _log('\n[데이터 다운로드 중...]')
    data = load_multi(symbols, start, end)
    if not data:
        logger.error('데이터 없음. 종료.')
        return

    _log(f'  → {len(data)}종목 로드 완료\n')
    if on_progress:
        on_progress(0, 0, None, None, None)

    config = {'swing_lookback': swing_lb, 'sweep_lookback': sweep_lb}

    # ══════════════════════════════════════════════════════════════════════
    if mode == 'swing':
        return _swing_grid(
            data, config, combos_swing, sl_fixed,
            adapter_kwargs, on_progress, _log, mode,
            use_fitness=use_fitness,
            fitness_kwargs=fitness_kwargs,
        )
    # ══════════════════════════════════════════════════════════════════════
    # TP/SL 그리드
    combos = list(itertools.product(tp_range, sl_range))
    total  = len(combos)
    grid_results = []

    _log(f'  {"TP":>5}  {"SL":>5}  {"trades":>6}  {"win%":>5}  {"RR":>4}  {"MDD":>6}  {"ret%":>6}  {"RR×ret":>7}  pass')
    _log(f'  {"-"*62}')

    for done, (tp, sl) in enumerate(combos, 1):
        engine_kwargs = dict(tp_pct=tp, sl_pct=-sl)
        m_list, _ = _run_single_strategy(
            data, config, engine_kwargs,
            label=f'TP{tp*100:.0f}/SL{sl*100:.0f}',
            mode=mode,
            **adapter_kwargs,
        )
        agg   = aggregate(m_list, mode)
        win   = agg['overall_win_rate'] * 100
        ret   = agg['total_return'] * 100
        mdd   = agg['avg_mdd'] * 100
        rr    = agg['avg_rr']
        score = rr * ret if math.isfinite(rr) and math.isfinite(ret) else 0.0

        row = {
            'tp': tp, 'sl': sl,
            'trades': agg['total_trades'],
            'win_pct': round(win, 1), 'rr': rr,
            'mdd': round(mdd, 1), 'ret': round(ret, 1),
            'score': round(score, 2),
            'passed': agg['passed_symbols'],
        }
        grid_results.append(row)

        if on_progress:
            on_progress(done, total, tp, sl, row)

        _log(
            f'  {tp*100:>4.1f}%  {sl*100:>4.1f}%  '
            f'{agg["total_trades"]:>6}  {win:>4.1f}%  {rr:>4.2f}  '
            f'{mdd:>5.1f}%  {ret:>+5.1f}%  {score:>+7.2f}  {agg["passed_symbols"]}'
        )

    best = max(grid_results, key=lambda x: x['score'])
    _log(f'\n{"="*65}')
    _log(f'  [최적 조합 — score(RR×ret%) 기준]')
    _log(
        f'  TP={best["tp"]*100:.1f}%  SL={best["sl"]*100:.1f}%  '
        f'trades={best["trades"]}  win={best["win_pct"]:.1f}%  '
        f'RR={best["rr"]:.2f}  MDD={best["mdd"]:.1f}%  ret={best["ret"]:+.1f}%'
    )
    top3 = sorted(grid_results, key=lambda x: x['ret'], reverse=True)[:3]
    _log(f'\n  [수익률 Top3]')
    for r in top3:
        _log(
            f'    TP={r["tp"]*100:.1f}%/SL={r["sl"]*100:.1f}%  '
            f'ret={r["ret"]:+.1f}%  RR={r["rr"]:.2f}  win={r["win_pct"]:.1f}%'
        )

    return grid_results


def _swing_grid(
    data, config, combos, sl_fixed,
    adapter_kwargs, on_progress, _log, mode,
    use_fitness: bool = False,
    fitness_kwargs: dict = None,
):
    """스윙 그리드 내부 실행 — TP × min_hold × trailing × be_trigger."""
    total        = len(combos)
    grid_results = []

    _log(
        f'  {"TP":>5}  {"hold":>4}  {"trail":>5}  {"BE":>5}  '
        f'{"trades":>6}  {"win%":>5}  {"cap%":>5}  '
        f'{"MFE%":>5}  {"MAE%":>5}  {"SL%":>4}  '
        f'{"BW%":>5}  {"M10%":>5}  {"MDD":>6}  {"ret%":>6}  {"score":>7}  pass'
    )
    _log(f'  {"-"*114}')

    for done, (tp, min_hold, trail, be_trig) in enumerate(combos, 1):
        # be_trigger < trailing 이어야 의미 있음 (방어)
        if be_trig >= trail:
            continue

        engine_kwargs = dict(
            tp_pct         = tp,
            sl_pct         = -sl_fixed,
            min_hold_bars  = min_hold,
            trailing_pct   = trail,
            be_trigger_pct = be_trig,
            swing_mode     = True,
        )
        label = f'tp{tp*100:.0f}/h{min_hold}/tr{trail*100:.0f}/be{be_trig*100:.0f}'
        m_list, _ = _run_single_strategy(
            data, config, engine_kwargs,
            label=label, mode=mode,
            use_fitness=use_fitness,
            fitness_kwargs=fitness_kwargs,
            **adapter_kwargs,
        )
        agg = aggregate(m_list, mode)
        win  = agg['overall_win_rate'] * 100
        ret  = agg['total_return'] * 100
        mdd  = agg['avg_mdd'] * 100
        rr   = agg['avg_rr']
        cap  = agg['avg_capture_rate'] * 100
        mfe  = agg['avg_mfe_pct']
        mae  = agg['avg_mae_pct']
        sl   = agg['sl_hit_ratio'] * 100
        bwr  = agg['big_winner_ratio'] * 100
        m10  = agg['mfe_10plus_ratio'] * 100

        # 스윙 score: capture_rate × BW boost × MAE 패널티 × MDD 패널티
        mdd_penalty = max(0.0, 1.0 - abs(agg['avg_mdd']) / 0.10)
        mae_penalty = max(0.3, 1.0 - agg['mae_3plus_ratio'])   # MAE>3% 많으면 감점
        bw_boost    = 1.0 + agg['big_winner_ratio'] * 2.0
        score = (agg['avg_capture_rate'] * bw_boost * mdd_penalty * mae_penalty) if math.isfinite(cap) else 0.0

        row = {
            'tp':               tp,
            'min_hold':         min_hold,
            'trailing':         trail,
            'be_trigger':       be_trig,
            'trades':           agg['total_trades'],
            'win_pct':          round(win, 1),
            'rr':               rr,
            'capture_rate':     round(cap, 1),
            'avg_mfe_pct':      round(mfe, 2),
            'avg_mae_pct':      round(mae, 2),
            'mae_3plus_ratio':  round(agg['mae_3plus_ratio'] * 100, 1),
            'sl_hit_ratio':     round(sl, 1),
            'big_winner_ratio': round(bwr, 1),
            'mfe_10plus_ratio': round(m10, 1),
            'mdd':              round(mdd, 1),
            'ret':              round(ret, 1),
            'score':            round(score, 4),
            'passed':           agg['passed_symbols'],
            # 시간대별 수익
            'ret_d1':     agg['ret_d1'],
            'ret_d2_5':   agg['ret_d2_5'],
            'ret_d6_14':  agg['ret_d6_14'],
            'ret_d15plus': agg['ret_d15plus'],
            'cnt_d1':     agg['cnt_d1'],
            'cnt_d2_5':   agg['cnt_d2_5'],
            'cnt_d6_14':  agg['cnt_d6_14'],
            'cnt_d15plus': agg['cnt_d15plus'],
        }
        grid_results.append(row)

        if on_progress:
            on_progress(done, total, min_hold, trail, row)

        _log(
            f'  {tp*100:>4.0f}%  {min_hold:>4}  {trail*100:>4.0f}%  {be_trig*100:>4.0f}%  '
            f'{agg["total_trades"]:>6}  {win:>4.1f}%  {cap:>4.1f}%  '
            f'{mfe:>4.1f}%  {mae:>4.1f}%  {sl:>3.0f}%  '
            f'{bwr:>4.1f}%  {m10:>4.1f}%  '
            f'{mdd:>5.1f}%  {ret:>+5.1f}%  {score:>+7.4f}  {agg["passed_symbols"]}'
        )

    if not grid_results:
        _log('  [유효 조합 없음 — be_trigger < trailing 조건 불충족]')
        return []

    best = max(grid_results, key=lambda x: x['score'])
    _log(f'\n{"="*80}')
    _log(f'  [최적 조합 — score(capture_rate × BW boost × MDD 패널티) 기준]')
    _log(
        f'  TP={best["tp"]*100:.0f}%  hold={best["min_hold"]}봉  trail={best["trailing"]*100:.0f}%  '
        f'BE={best["be_trigger"]*100:.0f}%  '
        f'trades={best["trades"]}  win={best["win_pct"]:.1f}%  '
        f'cap={best["capture_rate"]:.1f}%  MFE={best["avg_mfe_pct"]:.1f}%  '
        f'BW={best["big_winner_ratio"]:.1f}%  M10={best["mfe_10plus_ratio"]:.1f}%  '
        f'MDD={best["mdd"]:.1f}%  ret={best["ret"]:+.1f}%'
    )

    # Big Winner 기준 Top3 (진짜 스윙 핵심)
    top3_bw = sorted(grid_results, key=lambda x: x['big_winner_ratio'], reverse=True)[:3]
    _log(f'\n  [Big Winner(10%+) Top3 — 계좌 성장 핵심]')
    for r in top3_bw:
        _log(
            f'    TP={r["tp"]*100:.0f}%  hold={r["min_hold"]}봉/trail={r["trailing"]*100:.0f}%  '
            f'BW={r["big_winner_ratio"]:.1f}%  M10(잠재)={r["mfe_10plus_ratio"]:.1f}%  '
            f'cap={r["capture_rate"]:.1f}%  ret={r["ret"]:+.1f}%'
        )

    top3 = sorted(grid_results, key=lambda x: x['ret'], reverse=True)[:3]
    _log(f'\n  [수익률 Top3]')
    for r in top3:
        _log(
            f'    TP={r["tp"]*100:.0f}%  hold={r["min_hold"]}봉/trail={r["trailing"]*100:.0f}%/BE={r["be_trigger"]*100:.0f}%  '
            f'ret={r["ret"]:+.1f}%  cap={r["capture_rate"]:.1f}%  '
            f'BW={r["big_winner_ratio"]:.1f}%  MFE={r["avg_mfe_pct"]:.1f}%'
        )

    return grid_results


def main():
    parser = argparse.ArgumentParser(description='SMC 백테스트')
    parser.add_argument('--start',       default='2024-01-01')
    parser.add_argument('--end',         default='2025-12-31')
    parser.add_argument('--tp',          type=float, default=0.05,  help='익절 비율 (기본 0.05)')
    parser.add_argument('--sl',          type=float, default=0.03,  help='손절 비율 (기본 0.03)')
    parser.add_argument('--swing-lb',    type=int,   default=5,     help='스윙 lookback (기본 5)')
    parser.add_argument('--sweep-lb',    type=int,   default=20,    help='스윕 lookback (기본 20)')
    parser.add_argument('--symbols',     nargs='+',  default=None,  help='종목 코드 목록')
    parser.add_argument('--grid-search', action='store_true',        help='TP/SL 그리드 서치 (단타)')
    parser.add_argument('--swing-grid',  action='store_true',        help='스윙 그리드 서치 (min_hold × trailing × BE)')
    parser.add_argument('--tp-range',    nargs='+',  type=float,    help='TP 목록 (예: 0.05 0.08 0.10 0.15)')
    parser.add_argument('--sl-range',    nargs='+',  type=float,    help='SL 목록 (예: 0.02 0.03 0.05 0.08)')
    parser.add_argument('--min-hold',    nargs='+',  type=int,      help='최소 보유봉 목록 (예: 4 8 16 24)')
    parser.add_argument('--trailing',    nargs='+',  type=float,    help='트레일링 % 목록 (예: 0.02 0.03 0.05)')
    parser.add_argument('--be-trigger',  nargs='+',  type=float,    help='BE 전환 % 목록 (예: 0.02 0.03 0.05)')
    parser.add_argument('--strategy',    default='B+VOL',           help='전략 이름')
    args = parser.parse_args()

    if args.swing_grid:
        run_grid_search(
            symbols          = args.symbols,
            start            = args.start,
            end              = args.end,
            swing_lb         = args.swing_lb,
            sweep_lb         = args.sweep_lb,
            tp_range         = args.tp_range,
            sl_range         = args.sl_range,
            min_hold_range   = args.min_hold,
            trailing_range   = args.trailing,
            be_trigger_range = args.be_trigger,
            strategy         = args.strategy,
            mode             = 'swing',
        )
    elif args.grid_search:
        run_grid_search(
            symbols  = args.symbols,
            start    = args.start,
            end      = args.end,
            swing_lb = args.swing_lb,
            sweep_lb = args.sweep_lb,
            tp_range = args.tp_range,
            sl_range = args.sl_range,
            strategy = args.strategy,
            mode     = 'tp_sl',
        )
    else:
        run(
            symbols  = args.symbols,
            start    = args.start,
            end      = args.end,
            tp_pct   = args.tp,
            sl_pct   = args.sl,
            swing_lb = args.swing_lb,
            sweep_lb = args.sweep_lb,
        )


if __name__ == '__main__':
    main()
