"""
일봉 SMC 데일리 스캔 — 당일 진입 가능 종목 선별

역할:
  매일 08:30~08:50 실행 → 일봉 ATR+MA50+SMC 신호 종목 추출
  → daily_watchlist.json 저장 → main_auto_trading.py가 읽어서 5분봉 모니터링
  → daily_patterns.json 저장  → 패턴 정보를 score_engine에 전달 (패턴 점수)

연결 구조:
  daily_scan.py (일봉 사전 필터)
      ↓ daily_watchlist.json  — SMC BUY 종목 (5분봉 모니터링 대상)
      ↓ daily_patterns.json   — 종목별 패턴 결과 (score_engine 패턴 점수)
  main_auto_trading.py (5분봉 실시간 SMC)
      → 두 레이어 모두 통과 시 진입
      → score_engine에 pattern_result 전달 → 진입 우선순위 결정

사용법:
    python -m backtest.daily_scan               # 오늘 기준 스캔
    python -m backtest.daily_scan --dry-run     # 결과 출력만, 파일 저장 안 함
    python -m backtest.daily_scan --date 2024-12-20  # 특정 날짜 기준
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.loader  import load_multi
from backtest.adapter import SMCAdapter
from backtest.scanner import DEFAULT_CANDIDATES

logging.basicConfig(level=logging.WARNING)
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

logger = logging.getLogger('daily_scan')
logger.setLevel(logging.INFO)
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter('%(message)s'))
logger.addHandler(_h)

# 저장 경로 (main_auto_trading.py 루트 기준)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PATH  = os.path.join(_ROOT, 'data', 'daily_watchlist.json')
PATTERN_PATH = os.path.join(_ROOT, 'data', 'daily_patterns.json')

# 백테스트 검증 완료된 최적 파라미터
BEST_CONFIG = {
    'swing_lookback': 3,
    'sweep_lookback': 15,
}
BEST_ADAPTER_KWARGS = {
    'require_sweep':     False,
    'require_volume':    True,
    'atr_pct_min':       0.02,
    'atr_pct_max':       0.08,
    'require_ma50_trend': True,
}


def scan_today(
    symbols:  list = None,
    ref_date: str  = None,   # None = 오늘 기준
    lookback: int  = 120,    # 신호 판단에 필요한 과거 봉 수
    dry_run:  bool = False,
    skip_patterns: bool = False,   # True면 패턴 스캔 생략 (빠른 테스트용)
) -> list[str]:
    """
    오늘 진입 가능한 종목 추출.

    동작:
      1. 후보 종목 일봉 데이터 로드 (최근 lookback일)
      2. 마지막 확정봉 기준 SMC 신호 체크
      3. 신호 있는 종목 → daily_watchlist.json 저장

    Returns:
        신호 발생 종목 코드 리스트
    """
    symbols  = symbols or DEFAULT_CANDIDATES
    end_date = ref_date or datetime.today().strftime('%Y-%m-%d')
    start_dt = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=lookback * 2)).strftime('%Y-%m-%d')

    logger.info(f'\n{"="*55}')
    logger.info(f'  일봉 SMC 스캔  (기준: {end_date})')
    logger.info(f'  후보 {len(symbols)}개  lookback={lookback}봉')
    logger.info(f'{"="*55}')
    logger.info('[데이터 로드 중...]')

    data = load_multi(symbols, start_dt, end_date)
    logger.info(f'  → {len(data)}종목 로드\n')

    adapter = SMCAdapter(BEST_CONFIG, **BEST_ADAPTER_KWARGS)
    signals = []

    for symbol, df in data.items():
        if len(df) < 30:
            continue
        # 마지막 봉 기준 신호 체크 (i = len(df) - 1)
        i = len(df) - 1
        sig = adapter.get_signal(df, i)
        if sig == 'BUY':
            signals.append(symbol)
            logger.info(f'  ✅ {symbol}  BUY 신호')

    logger.info(f'\n  결과: {len(signals)}개 종목 BUY 신호')
    logger.info(f'  → {signals}')

    if not dry_run:
        if signals:
            _save(signals, end_date)
        else:
            # 신호 없음 → 파일 저장 안 함 (main이 기존 조건검색 결과 그대로 사용)
            logger.info('  ℹ️  신호 없음 — watchlist.json 미저장 (기존 조건검색 우선)')

    # ── 패턴 스캔 (동일 data 재사용 — 추가 API 콜 없음) ──────────────────
    if not skip_patterns:
        _run_pattern_scan(data, end_date, dry_run)

    return signals


def _save(signals: list[str], scan_date: str) -> None:
    """daily_watchlist.json 저장."""
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    payload = {
        'scan_date':   scan_date,
        'updated_at':  datetime.now().isoformat(),
        'symbols':     signals,
        'source':      'daily_smc_scan',
        'strategy':    'ATR+MA50+SMC_B+VOL',
    }
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f'\n  💾 저장: {OUTPUT_PATH}')


def _run_pattern_scan(data: dict, scan_date: str, dry_run: bool) -> dict:
    """
    PatternManager로 종목별 패턴 감지 실행.

    동일 데이터(data) 재사용 — load_multi() 추가 호출 없음.
    결과를 daily_patterns.json에 저장.

    Returns:
        {symbol: PatternResult} 감지된 종목만
    """
    try:
        from analyzers.patterns import PatternManager, DoubleBottomDetector, BullFlagDetector
    except ImportError as e:
        logger.warning(f'  [PATTERN] import 실패 — 패턴 스캔 생략: {e}')
        return {}

    logger.info(f'\n{"="*55}')
    logger.info('  패턴 스캔 (이중바닥 · 상승플래그)')
    logger.info(f'{"="*55}')

    manager = PatternManager(window=5, min_swing_pct=0.02)
    manager.register(DoubleBottomDetector(), BullFlagDetector())

    found: dict = {}

    for symbol, df in data.items():
        if len(df) < 30:
            continue
        try:
            pivots = manager.zigzag.get_pivots(df, n=10)
            result = manager.best(df, pivots)
            if result is not None:
                found[symbol] = result
                phase_mark = {'forming': '🔍', 'breakout': '🔥', 'confirmed': '✅'}.get(result.phase, '?')
                logger.info(
                    f'  {phase_mark} {symbol}  {result.pattern}({result.phase})'
                    f'  conf={result.confidence:.2f}  RR={result.rr}'
                    f'  entry={result.entry:.0f} stop={result.stop:.0f} target={result.target:.0f}'
                )
        except Exception as e:
            logger.debug(f'  [PATTERN] {symbol} 오류: {e}')

    breakout_cnt = sum(1 for r in found.values() if r.phase == 'breakout')
    forming_cnt  = sum(1 for r in found.values() if r.phase == 'forming')
    logger.info(
        f'\n  패턴 감지: {len(found)}개  '
        f'(🔥돌파={breakout_cnt}  🔍형성중={forming_cnt})'
    )

    if not dry_run and found:
        _save_patterns(found, scan_date)

    return found


def _save_patterns(found: dict, scan_date: str) -> None:
    """daily_patterns.json 저장."""
    os.makedirs(os.path.dirname(PATTERN_PATH), exist_ok=True)
    payload = {
        'scan_date':  scan_date,
        'updated_at': datetime.now().isoformat(),
        'patterns':   {sym: _pattern_to_dict(r) for sym, r in found.items()},
    }
    with open(PATTERN_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f'  💾 패턴 저장: {PATTERN_PATH}')


def _pattern_to_dict(result) -> dict:
    """PatternResult → JSON 직렬화 가능한 dict."""
    return {
        'pattern':    result.pattern,
        'confidence': round(float(result.confidence), 4),
        'entry':      float(result.entry),
        'stop':       float(result.stop),
        'target':     float(result.target),
        'phase':      result.phase,
        'timeframe':  result.timeframe,
        'rr':         result.rr,
        'meta':       {k: (float(v) if hasattr(v, '__float__') else v)
                       for k, v in result.meta.items()},
    }


def load_daily_patterns() -> dict:
    """
    main_auto_trading.py에서 호출 — 오늘 패턴 스캔 결과 읽기.

    Returns:
        {symbol: pattern_dict}  오늘 스캔 결과만, 날짜 불일치 시 빈 dict.
        pattern_dict 키: pattern, confidence, entry, stop, target, phase, rr, meta

    사용 예시 (main_auto_trading.py):
        from backtest.daily_scan import load_daily_patterns
        _daily_patterns = load_daily_patterns()   # 1회 로드

        # ScoreEngine 호출 시
        from analyzers.patterns.base import PatternResult
        pat_dict = _daily_patterns.get(code)
        pat_obj  = _dict_to_pattern(pat_dict) if pat_dict else None
        score    = score_engine.score(code, ohlcv, pattern_result=pat_obj)
    """
    if not os.path.exists(PATTERN_PATH):
        return {}
    try:
        with open(PATTERN_PATH, encoding='utf-8') as f:
            data = json.load(f)
        today = datetime.today().strftime('%Y-%m-%d')
        if data.get('scan_date') != today:
            return {}
        return data.get('patterns', {})
    except Exception:
        return {}


def _dict_to_pattern(d: dict):
    """
    daily_patterns.json의 dict → PatternResult 복원.

    main_auto_trading.py에서 score_engine에 전달할 때 사용.
    """
    try:
        from analyzers.patterns.base import PatternResult
        return PatternResult(
            pattern    = d['pattern'],
            confidence = d['confidence'],
            entry      = d['entry'],
            stop       = d['stop'],
            target     = d['target'],
            phase      = d['phase'],
            timeframe  = d.get('timeframe', 'daily'),
            meta       = d.get('meta', {}),
        )
    except Exception:
        return None


def load_daily_watchlist() -> list[str]:
    """
    main_auto_trading.py에서 호출 — 오늘 스캔 결과 읽기.

    Returns:
        종목 코드 리스트 (오늘 날짜 아니면 빈 리스트)
    """
    if not os.path.exists(OUTPUT_PATH):
        return []
    try:
        with open(OUTPUT_PATH, encoding='utf-8') as f:
            data = json.load(f)
        today = datetime.today().strftime('%Y-%m-%d')
        if data.get('scan_date') != today:
            return []   # 오래된 스캔 결과 → 무시
        return data.get('symbols', [])
    except Exception:
        return []


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='일봉 SMC 데일리 스캔')
    parser.add_argument('--date',    type=str,  default=None,  help='기준 날짜 (YYYY-MM-DD)')
    parser.add_argument('--dry-run', action='store_true',      help='파일 저장 없이 출력만')
    parser.add_argument('--lookback',type=int,  default=120,   help='과거 봉 수')
    args = parser.parse_args()

    result = scan_today(
        ref_date = args.date,
        lookback = args.lookback,
        dry_run  = args.dry_run,
    )

    if result:
        print(f'\n[최종 오늘 BUY 후보]')
        for sym in result:
            print(f'  {sym}')
    else:
        print('\n[오늘 BUY 신호 없음]')
