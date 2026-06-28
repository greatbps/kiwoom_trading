"""
스윙 트레이딩 데일리 러너

실행: python3 swing_runner.py
시점: 장 마감 후 (15:35 이후)
역할:
  1. 유니버스 종목 일봉 데이터 수집
  2. 각 종목 패턴 탐지 + 점수화
  3. Top-3 진입 후보 선정 (score >= 5 + trigger = True)
  4. 기존 보유 포지션 홀딩 판단 (MA5 기준)
  5. 다음날 매수/매도 주문 큐 생성 → logs/swing_orders_YYYYMMDD.json
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

# 액션 우선순위 — 동일 종목 충돌 시 높은 순 선택
_ACTION_PRIORITY: dict[str, int] = {
    'SELL':           5,
    'REDUCE':         4,
    'ADD_MOMENTUM':   3,
    'ADD_VALUE':      2,
    'TRAIL':          1,
    'HOLD':           0,
}

from analyzers.swing.signal_engine import SignalEngine
from analyzers.swing.state_machine import (
    SwingStateManager, SwingPosition, SwingState,
    SwingExitRecord, SwingExitHistoryManager,
)
from analyzers.swing.holding_manager import HoldingManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config/strategy_swing.yaml")
DEFAULT_LOOKBACK_DAYS = 120


def _get_db():
    """TradingDatabase 인스턴스 반환 (지연 임포트)."""
    from database.trading_db import TradingDatabase
    return TradingDatabase()


def _update_mfe_mae(pos: 'SwingPosition', df: pd.DataFrame) -> None:
    """당일 일봉(df 마지막 행)으로 peak/trough 갱신 후 PostgreSQL BUY 레코드 업데이트."""
    if pos.entry_price <= 0 or pos.trade_id is None:
        return
    try:
        day_high = float(df['high'].iloc[-1])
        day_low  = float(df['low'].iloc[-1])
        ep = pos.entry_price

        if day_high > pos.peak_price:
            pos.peak_price = day_high
        if pos.trough_price <= 0 or day_low < pos.trough_price:
            pos.trough_price = day_low

        mfe = round((pos.peak_price   - ep) / ep * 100, 3)
        mae = round((ep - pos.trough_price) / ep * 100, 3) if pos.trough_price > 0 else 0.0

        _get_db().update_swing_mfe_mae(pos.trade_id, mfe, mae, pos.peak_price, pos.trough_price)
        logger.debug(f"[SWING_MAE/MFE] {pos.stock_code} MFE={mfe:.2f}% MAE={mae:.2f}% "
                     f"peak={pos.peak_price:,.0f} trough={pos.trough_price:,.0f}")
    except Exception as e:
        logger.warning(f"[SWING_MAE/MFE] {pos.stock_code} 갱신 실패: {e}")


def _save_signal_snapshot(code: str, name: str, signal: dict, trade_id: int | None,
                           market_regime: str) -> None:
    """SignalEngine 스냅샷을 swing_features에 저장."""
    from datetime import date as date_cls
    try:
        _get_db().insert_swing_features({
            'trade_id':     trade_id,
            'stock_code':   code,
            'entry_date':   date_cls.today().isoformat(),
            'pattern':      signal.get('pattern'),
            'raw_score':    signal.get('score'),
            'final_score':  signal.get('final_score'),
            'phase':        signal.get('phase'),
            'trigger':      signal.get('trigger'),
            'confidence':   signal.get('confidence'),
            'entry_price':  signal.get('entry'),
            'stop_price':   signal.get('stop'),
            'target_price': signal.get('target'),
            'size':         signal.get('size'),
            'market_regime': market_regime,
            'meta':         signal.get('meta') or {},
        })
        logger.debug(f"[SWING_FEAT] {code} {name} snapshot 저장 (trade_id={trade_id})")
    except Exception as e:
        logger.warning(f"[SWING_FEAT] {code} snapshot 실패: {e}")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.error(f"[SWING_RUN] 설정 파일 없음: {CONFIG_PATH}")
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))


def get_market_regime() -> str:
    """
    KOSPI 지수(^KS11) 2단 레짐 판단 (선행성 보완).

    1단 — 추세: EMA20 vs EMA60 갭
    2단 — 모멘텀: 현재가 vs EMA20 (추세 내 위치)

    BULL_STRONG  — EMA20 > EMA60 × 1.005  AND  close > EMA20   → ADD_MOMENTUM 적극
    BULL_WEAK    — EMA20 > EMA60 × 1.005  AND  close ≤ EMA20   → ADD_MOMENTUM 차단
    NEUTRAL      — EMA20 within ±0.5% of EMA60                  → ADD 제한
    BEAR_STRONG  — EMA20 < EMA60 × 0.995  AND  close < EMA20   → ADD 전면 차단

    실패 시 NEUTRAL 반환.
    """
    try:
        import yfinance as yf
        df = yf.Ticker("^KS11").history(period="90d", interval="1d", auto_adjust=True)
        if df is None or len(df) < 61:
            return 'NEUTRAL'
        close_series = df['Close'].dropna()
        cur_close = float(close_series.iloc[-1])
        ema20 = float(close_series.ewm(span=20, adjust=False).mean().iloc[-1])
        ema60 = float(close_series.ewm(span=60, adjust=False).mean().iloc[-1])

        trend_bull = ema20 > ema60 * 1.005
        trend_bear = ema20 < ema60 * 0.995
        mom_bull   = cur_close > ema20

        if trend_bull and mom_bull:
            regime = 'BULL_STRONG'
        elif trend_bull and not mom_bull:
            regime = 'BULL_WEAK'
        elif trend_bear and not mom_bull:
            regime = 'BEAR_STRONG'
        else:
            regime = 'NEUTRAL'

        logger.info(
            f"[SWING_REGIME] {regime} | close={cur_close:.0f} "
            f"EMA20={ema20:.0f} EMA60={ema60:.0f} "
            f"trend_gap={ema20/ema60-1:+.2%} mom={'↑' if mom_bull else '↓'}"
        )
        return regime
    except Exception as e:
        logger.warning(f"[SWING_REGIME] 레짐 판단 실패: {e} → NEUTRAL")
        return 'NEUTRAL'


def load_universe(universe_file: str) -> list[dict]:
    path = Path(universe_file)
    if not path.exists():
        logger.warning(f"[SWING_RUN] 유니버스 파일 없음: {path}")
        return []
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        logger.error(f"[SWING_RUN] 유니버스 로드 실패: {e}")
        return []


def fetch_daily(code: str, market: str, lookback: int = DEFAULT_LOOKBACK_DAYS) -> Optional[pd.DataFrame]:
    """yfinance로 일봉 OHLCV 수집. 실패 시 None 반환."""
    try:
        import yfinance as yf

        suffix = ".KS" if market == "KS" else ".KQ"
        ticker_sym = f"{code}{suffix}"
        ticker = yf.Ticker(ticker_sym)
        df = ticker.history(period=f"{lookback}d", interval="1d", auto_adjust=True)

        if df is None or len(df) < 20:
            logger.debug(f"[SWING_RUN] 데이터 부족: {ticker_sym} ({len(df) if df is not None else 0}봉)")
            return None

        df = df.rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Volume': 'volume',
        })
        df = df[['open', 'high', 'low', 'close', 'volume']].dropna()
        return df

    except Exception as e:
        logger.warning(f"[SWING_RUN] {code} 데이터 수집 실패: {e}")
        return None


def process_hold_positions(
    positions: dict[str, SwingPosition],
    config: dict,
    universe: list[dict],
    market_regime: str = 'NEUTRAL',
    exit_hist_mgr: Optional[SwingExitHistoryManager] = None,
) -> list[dict]:
    """
    보유 포지션 평가.

    충돌 제어:
      - 종목당 하루 1 액션만 (우선순위: EXIT>REDUCE>ADD_MOMENTUM>ADD_VALUE>TRAIL)
      - ADD_MOMENTUM은 BULL 레짐에서만 허용
      - REDUCE는 수익 구간별 티어 적용

    Returns:
        orders: [{'code', 'name', 'action', ...}]
    """
    holding_mgr = HoldingManager(config)
    universe_map = {u['code']: u for u in universe}

    # {code: order_dict} — 종목당 최종 1개만
    pending: dict[str, dict] = {}

    hold_states = {SwingState.HOLD, SwingState.ADD, SwingState.TRIGGER}

    for code, pos in list(positions.items()):
        if pos.state not in hold_states:
            continue

        stock_info = universe_map.get(code, {'market': 'KS', 'name': pos.stock_name})
        df = fetch_daily(code, stock_info.get('market', 'KS'))

        if df is None:
            logger.warning(f"[SWING_RUN] {code} 데이터 없음 - HOLD 유지")
            continue

        # ── MAE/MFE 일별 갱신 ──────────────────────────────────────────────
        _update_mfe_mae(pos, df)

        # score drift: 현재 신호 점수 재계산 → 진입 점수 대비 변화량
        score_drift = 0.0
        try:
            engine = SignalEngine(df, config)
            sig = engine.run()
            score_now = sig['final_score'] if sig else pos.score
            score_drift = score_now - pos.score
            if score_drift != 0:
                logger.debug(
                    f"[SWING_DRIFT] {code} score_at_entry={pos.score:.1f} "
                    f"score_now={score_now:.1f} drift={score_drift:+.1f}"
                )
        except Exception:
            pass

        action, exit_reason = holding_mgr.evaluate(pos, df, score_drift=score_drift)

        # ── 레짐 필터: ADD_MOMENTUM → BULL에서만 ───────────────────────────
        if action == 'ADD_MOMENTUM' and market_regime != 'BULL':
            logger.info(
                f"[SWING_RUN] {code} ADD_MOMENTUM → 레짐={market_regime} (BULL 아님) → HOLD로 격하"
            )
            action = 'HOLD'
            exit_reason = 'HOLD'

        if action == 'HOLD':
            continue

        # ── 충돌 제어: 같은 종목에 이미 더 높은 우선순위 액션이 있으면 스킵 ──
        if code in pending:
            existing = pending[code]['action']
            if _ACTION_PRIORITY.get(existing, 0) >= _ACTION_PRIORITY.get(action, 0):
                logger.info(
                    f"[SWING_CONFLICT] {code} {action} 스킵 "
                    f"(기존 {existing} 우선순위 더 높음)"
                )
                continue

        # ── 액션별 주문 생성 ────────────────────────────────────────────────
        if action == 'EXIT':
            pnl_pct = round(pos.max_profit_pct - pos.drawdown_pct, 2)
            order = {
                'code': code, 'name': pos.stock_name, 'action': 'SELL',
                'reason': exit_reason,
                'entry_price': pos.entry_price,
                'holding_days': pos.holding_days,
                'max_profit_pct': round(pos.max_profit_pct, 2),
                'drawdown_pct': round(pos.drawdown_pct, 2),
                'pnl_pct': pnl_pct,
            }
            if exit_hist_mgr is not None:
                exit_hist_mgr.record(code, SwingExitRecord(
                    exit_date=date.today().isoformat(),
                    exit_reason=exit_reason,
                    exit_pnl_pct=pnl_pct,
                    exit_price=0.0,
                    entry_price=pos.entry_price,
                ))
            pos.state = SwingState.EXIT

        elif action == 'REDUCE':
            reduce_size = holding_mgr.reduce_lot_size(pos.max_profit_pct)
            order = {
                'code': code, 'name': pos.stock_name, 'action': 'REDUCE',
                'size': reduce_size,
                'profit_pct': round(pos.max_profit_pct, 2),
                'drawdown_pct': round(pos.drawdown_pct, 2),
            }

        elif action in ('ADD_VALUE', 'ADD_MOMENTUM'):
            add_size = holding_mgr.add_lot_size(pos, action)
            order = {
                'code': code, 'name': pos.stock_name, 'action': action,
                'size': add_size,
                'add_count': pos.add_count,
                'ma5_distance_pct': round(pos.ma5_distance_pct, 2),
            }
            pos.add_count += 1
            pos.state = SwingState.ADD

        elif action == 'TRAIL':
            order = {
                'code': code, 'name': pos.stock_name, 'action': 'TRAIL',
                'max_profit_pct': round(pos.max_profit_pct, 2),
                'holding_days': pos.holding_days,
            }

        else:
            continue

        pending[code] = order
        logger.info(f"[SWING_RUN] {code} → {action} 큐 등록")

    orders = list(pending.values())
    if orders:
        action_summary = ', '.join(f"{o['action']}:{o['code']}" for o in orders)
        logger.info(f"[SWING_RUN] 보유 평가 완료 ({len(orders)}건): {action_summary}")

    return orders


def _sector_of(code: str, universe_map: dict) -> str:
    """종목 섹터 반환. 정보 없으면 'UNKNOWN' (섹터 무지 = 같은 버킷으로 관리)."""
    return universe_map.get(code, {}).get('sector', '') or 'UNKNOWN'


def scan_new_signals(
    universe: list[dict],
    existing_positions: dict,   # {code: SwingPosition}
    config: dict,
    top_n: int = 3,
    exit_hist_mgr: Optional[SwingExitHistoryManager] = None,
) -> list[dict]:
    """
    유니버스 종목 중 미보유 종목 패턴 탐지 → Top-N 후보 반환.

    Fix 1: 동일 섹터 최대 1개 (기존 보유 + 신규 후보 합산)
    Fix 5: max_total_exposure 초과 시 신규 추가 불가

    Returns:
        candidates: [{'code', 'name', 'action': 'BUY', signal_dict...}]
    """
    swing_cfg = config.get('swing', {})
    lookback = swing_cfg.get('data', {}).get('lookback_days', DEFAULT_LOOKBACK_DAYS)
    min_score = swing_cfg.get('min_score_to_enter', 5.0)
    max_positions = swing_cfg.get('max_positions', 5)
    max_exposure = swing_cfg.get('max_total_exposure', 0.80)
    max_same_sector = swing_cfg.get('sector', {}).get('max_same_sector', 1)

    existing_codes = set(existing_positions.keys())

    if len(existing_codes) >= max_positions:
        logger.info(f"[SWING_RUN] 최대 포지션 {max_positions}개 도달 - 신규 탐색 생략")
        return []

    # 현재 보유 노출 합계 — quantity > 0 (실매수) 포지션만 (TRIGGER/미매수 제외)
    current_exposure = sum(
        p.allocated_size for p in existing_positions.values() if p.quantity > 0
    )
    if current_exposure >= max_exposure:
        logger.info(
            f"[SWING_RUN] 최대 노출 도달 exposure={current_exposure:.2f} >= {max_exposure:.2f} - 신규 생략"
        )
        return []

    universe_map = {u['code']: u for u in universe}

    # 기존 보유 섹터 현황 — quantity > 0 (실매수) 포지션만 (TRIGGER/미매수 제외)
    held_sectors: dict[str, int] = {}
    for code in existing_codes:
        pos = existing_positions.get(code)
        if pos and pos.quantity == 0:
            continue
        sec = _sector_of(code, universe_map)
        held_sectors[sec] = held_sectors.get(sec, 0) + 1

    all_signals = []
    # 후보 상태 추적 (Candidate Ranking 로그용)
    # code → {name, score, status, detail}
    # status: SCORE_MISS / COOLDOWN / PENDING → SELECTED / EXPOSURE_LIMIT / SECTOR_LIMIT / TOP_N_FULL
    signal_status: dict[str, dict] = {}

    for stock in universe:
        code = stock['code']
        name = stock['name']
        market = stock.get('market', 'KS')

        if code in existing_codes:
            continue

        df = fetch_daily(code, market, lookback)
        if df is None:
            continue

        try:
            engine = SignalEngine(df, config)
            signal = engine.run()
        except Exception as e:
            logger.warning(f"[SWING_RUN] {code} 신호 탐지 실패: {e}")
            continue

        if signal is None:
            continue

        if signal['final_score'] < min_score or not signal['trigger']:
            signal_status[code] = {
                'name': name, 'score': signal['final_score'],
                'status': 'SCORE_MISS',
                'detail': f"trigger={signal['trigger']} score={signal['final_score']:.1f}<{min_score}",
            }
            continue

        # ── 재진입 쿨다운 체크 ──────────────────────────────────────────────
        if exit_hist_mgr is not None:
            days = exit_hist_mgr.days_since_exit(code)
            if days is not None:
                rec = exit_hist_mgr.get(code)
                cooldown_cfg = swing_cfg.get('cooldown', {})
                by_reason = cooldown_cfg.get('by_reason', {})
                cooldown = by_reason.get(rec.exit_reason, by_reason.get('default', 3))
                override_min_score = cooldown_cfg.get('override_min_score', 9.0)

                if cooldown > 0 and days < cooldown:
                    if signal['final_score'] >= override_min_score:
                        logger.info(
                            f"[SWING_COOLDOWN] {code} {name} 고득점 예외 허용 "
                            f"(days={days}<{cooldown}일, reason={rec.exit_reason}, "
                            f"score={signal['final_score']}≥{override_min_score})"
                        )
                    else:
                        signal_status[code] = {
                            'name': name, 'score': signal['final_score'],
                            'status': 'COOLDOWN',
                            'detail': (
                                f"days={days}<{cooldown}일 "
                                f"{rec.exit_reason} pnl={rec.exit_pnl_pct:+.2f}%"
                            ),
                        }
                        logger.info(
                            f"[SWING_COOLDOWN] {code} {name} 쿨다운 차단 "
                            f"(days={days}<{cooldown}일, reason={rec.exit_reason}, "
                            f"pnl={rec.exit_pnl_pct:+.2f}%, score={signal['final_score']})"
                        )
                        continue

        signal_status[code] = {'name': name, 'score': signal['final_score'], 'status': 'PENDING'}
        all_signals.append({
            'code': code,
            'name': name,
            'sector': stock.get('sector', '') or 'UNKNOWN',
            'action': 'BUY',
            **signal,
        })
        logger.info(
            f"[SWING_RUN] {code} {name} 신호: {signal['pattern']} "
            f"score={signal['final_score']} entry={signal['entry']}"
        )

    # 최종 점수 내림차순 정렬
    all_signals.sort(key=lambda x: x['final_score'], reverse=True)

    # Top-N 선정 — 섹터 다각화(Fix 1) + 노출 한도(Fix 5) 적용
    candidates: list[dict] = []
    candidate_sectors: dict[str, int] = dict(held_sectors)  # 기존 보유 섹터 포함
    used_exposure = current_exposure

    for sig in all_signals:
        if len(candidates) >= top_n:
            signal_status[sig['code']]['status'] = 'TOP_N_FULL'
            continue

        # 노출 한도 체크 (Fix 5)
        new_size = sig.get('size', swing_cfg.get('size', {}).get('initial', 0.5))
        if used_exposure + new_size > max_exposure:
            signal_status[sig['code']].update({
                'status': 'EXPOSURE_LIMIT',
                'detail': f"{used_exposure:.2f}+{new_size:.2f}>{max_exposure:.2f}",
            })
            logger.info(
                f"[SWING_RUN] {sig['code']} 노출 한도 초과 "
                f"({used_exposure:.2f}+{new_size:.2f} > {max_exposure:.2f}) → 건너뜀"
            )
            continue

        # 섹터 다각화 체크 (UNKNOWN 포함 — 모르는 섹터도 버킷 관리)
        sec = sig.get('sector', 'UNKNOWN') or 'UNKNOWN'
        if candidate_sectors.get(sec, 0) >= max_same_sector:
            signal_status[sig['code']].update({
                'status': 'SECTOR_LIMIT',
                'detail': f"{sec} {candidate_sectors[sec]}개",
            })
            logger.info(
                f"[SWING_RUN] {sig['code']} 섹터 중복 ({sec}, 이미 {candidate_sectors[sec]}개) → 건너뜀"
            )
            continue

        signal_status[sig['code']]['status'] = 'SELECTED'
        candidates.append(sig)
        used_exposure += new_size
        candidate_sectors[sec] = candidate_sectors.get(sec, 0) + 1

    logger.info(
        f"[SWING_RUN] 진입 후보 {len(candidates)}개 선정 "
        f"(전체 신호 {len(all_signals)}개 | exposure={used_exposure:.2f}/{max_exposure:.2f})"
    )

    # ── Candidate Ranking 로그 ──────────────────────────────────────────────
    if signal_status:
        ranked = sorted(signal_status.items(), key=lambda x: x[1]['score'], reverse=True)
        logger.info("[SWING_CANDIDATE_RANK] === Candidate Ranking (%d signals) ===", len(ranked))
        for rank, (code, info) in enumerate(ranked, 1):
            detail = f"  ({info['detail']})" if info.get('detail') else ''
            logger.info(
                "[SWING_CANDIDATE_RANK]  #%d %-6s %-15s  score=%-5.1f  %s%s",
                rank, code, info['name'], info['score'], info['status'], detail,
            )

    return candidates


def find_upgrade_candidate(
    positions: dict,
    universe: list[dict],
    config: dict,
) -> Optional[tuple]:
    """
    포트폴리오 업그레이드: 기존 최약체 포지션보다 점수 높은 신호 탐색.

    자리가 꽉 찼을 때 호출. 발견 시 (weakest_pos, best_signal_dict) 반환.
    섹터/노출 제약 없음 — 1대1 교체이므로 자원 변동 없음.
    """
    if not positions:
        return None

    weakest = min(positions.values(), key=lambda p: p.score)
    swing_cfg = config.get('swing', {})
    lookback = swing_cfg.get('data', {}).get('lookback_days', DEFAULT_LOOKBACK_DAYS)
    existing_codes = set(positions.keys())
    best_signal: Optional[dict] = None

    for stock in universe:
        code = stock['code']
        if code in existing_codes:
            continue

        df = fetch_daily(code, stock.get('market', 'KS'), lookback)
        if df is None:
            continue

        try:
            engine = SignalEngine(df, config)
            signal = engine.run()
        except Exception:
            continue

        if signal is None or not signal['trigger']:
            continue
        if signal['final_score'] <= weakest.score:
            continue

        if best_signal is None or signal['final_score'] > best_signal['final_score']:
            best_signal = {
                'code': code,
                'name': stock['name'],
                'sector': stock.get('sector', '') or 'UNKNOWN',
                'action': 'BUY',
                **signal,
            }

    if best_signal is None:
        return None

    logger.info(
        f"[SWING_RUN] 업그레이드 후보 발견: "
        f"{weakest.stock_code}(score={weakest.score:.1f}) → "
        f"{best_signal['code']}(score={best_signal['final_score']:.1f})"
    )
    return weakest, best_signal


def write_order_queue(
    orders: list[dict],
    order_dir: str,
    run_date: date,
    market_regime: str = '',
) -> Path:
    """주문 큐를 JSON 파일로 저장."""
    dir_path = Path(order_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    output_path = dir_path / f"swing_orders_{run_date.strftime('%Y-%m-%d')}.json"
    payload = {
        'generated_at': datetime.now().isoformat(),
        'run_date': run_date.isoformat(),
        'market_regime': market_regime,
        'order_count': len(orders),
        'orders': orders,
    }

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    logger.info(f"[SWING_RUN] 주문 큐 저장: {output_path} ({len(orders)}건)")
    return output_path


def main() -> None:
    logger.info("=" * 60)
    logger.info("[SWING_RUN] 스윙 데일리 러너 시작")
    logger.info("=" * 60)

    config = load_config()
    swing_cfg = config.get('swing', {})
    data_cfg = swing_cfg.get('data', {})

    universe_file = data_cfg.get('universe_file', 'data/swing_universe.json')
    state_file = data_cfg.get('state_file', 'data/swing_positions.json')
    order_dir = data_cfg.get('order_dir', 'logs/')
    top_n = swing_cfg.get('top_n_candidates', 3)

    universe = load_universe(universe_file)
    if not universe:
        logger.error("[SWING_RUN] 유니버스 없음 - 종료")
        return

    logger.info(f"[SWING_RUN] 유니버스: {len(universe)}종목")

    # 상태 로드
    state_mgr = SwingStateManager(path=state_file)
    positions = state_mgr.load()

    # 청산 이력 로드 (재진입 쿨다운용)
    exit_history_file = data_cfg.get('exit_history_file', 'data/swing_exit_history.json')
    exit_hist_mgr = SwingExitHistoryManager(path=exit_history_file)
    exit_hist_mgr.load()

    # ── 0단계: 시장 레짐 판단 ────────────────────────────────────────────
    logger.info("[SWING_RUN] 0단계: 시장 레짐 판단")
    market_regime = get_market_regime()

    # ── 1단계: 보유 포지션 평가 ──────────────────────────────────────────
    logger.info("[SWING_RUN] 1단계: 보유 포지션 평가")
    exit_orders = process_hold_positions(positions, config, universe, market_regime,
                                         exit_hist_mgr=exit_hist_mgr)

    # EXIT 처리된 종목 제거
    for order in exit_orders:
        if order['action'] == 'SELL':
            state_mgr.remove(order['code'])
            positions.pop(order['code'], None)

    # ── 2단계: 신규 신호 탐색 ────────────────────────────────────────────
    logger.info("[SWING_RUN] 2단계: 신규 신호 탐색")
    max_positions = swing_cfg.get('max_positions', 5)
    at_capacity = len(positions) >= max_positions

    if at_capacity:
        new_candidates = []
    else:
        new_candidates = scan_new_signals(universe, positions, config, top_n=top_n,
                                          exit_hist_mgr=exit_hist_mgr)

    # ── 2.5단계: 포트폴리오 업그레이드 (자리 꽉 찼을 때) ───────────────────
    if at_capacity:
        logger.info("[SWING_RUN] 2.5단계: 포트폴리오 업그레이드 탐색")
        upgrade = find_upgrade_candidate(positions, universe, config)
        if upgrade:
            weakest, best_sig = upgrade
            logger.info(
                f"[SWING_RUN] 포트폴리오 교체 결정: "
                f"{weakest.stock_code}({weakest.score:.1f}점) → "
                f"{best_sig['code']}({best_sig['final_score']:.1f}점)"
            )
            exit_orders.append({
                'code': weakest.stock_code,
                'name': weakest.stock_name,
                'action': 'SELL',
                'reason': 'PORTFOLIO_UPGRADE',
                'replaced_by': best_sig['code'],
                'score_old': round(weakest.score, 1),
                'score_new': round(best_sig['final_score'], 1),
            })
            state_mgr.remove(weakest.stock_code)
            positions.pop(weakest.stock_code, None)
            new_candidates = [best_sig]
        else:
            logger.info("[SWING_RUN] 업그레이드 후보 없음 - 현 포트폴리오 유지")

    # 신규 후보 → TRIGGER 상태로 등록
    for cand in new_candidates:
        code = cand['code']
        pos = SwingPosition(
            stock_code=code,
            stock_name=cand['name'],
            state=SwingState.TRIGGER,
            pattern=cand.get('pattern', ''),
            score=cand.get('final_score', 0.0),
            entry_price=cand.get('entry', 0.0),
            entry_date=date.today(),
            allocated_size=cand.get('size', 0.5),
        )
        state_mgr.set(pos)

    # ── 3단계: 주문 큐 저장 ──────────────────────────────────────────────
    logger.info("[SWING_RUN] 3단계: 주문 큐 저장")
    all_orders = exit_orders + new_candidates
    output_path = write_order_queue(all_orders, order_dir, date.today(),
                                    market_regime=market_regime)

    # ── 4단계: 상태 저장 ─────────────────────────────────────────────────
    state_mgr.save(state_mgr.all)
    exit_hist_mgr.save()

    logger.info("=" * 60)
    logger.info(
        f"[SWING_RUN] 완료 | EXIT/ADD/TRAIL: {len(exit_orders)}건 | "
        f"신규 BUY: {len(new_candidates)}건 | 주문큐: {output_path}"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
