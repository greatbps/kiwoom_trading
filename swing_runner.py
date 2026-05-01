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

from analyzers.swing.signal_engine import SignalEngine
from analyzers.swing.state_machine import SwingStateManager, SwingPosition, SwingState
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


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.error(f"[SWING_RUN] 설정 파일 없음: {CONFIG_PATH}")
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))


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
) -> list[dict]:
    """
    보유 포지션 평가.

    Returns:
        exit_orders: [{'code', 'name', 'action': 'SELL', 'reason'}]
    """
    holding_mgr = HoldingManager(config)
    universe_map = {u['code']: u for u in universe}
    exit_orders = []

    hold_states = {SwingState.HOLD, SwingState.ADD, SwingState.TRIGGER}

    for code, pos in list(positions.items()):
        if pos.state not in hold_states:
            continue

        stock_info = universe_map.get(code, {'market': 'KS', 'name': pos.stock_name})
        df = fetch_daily(code, stock_info.get('market', 'KS'))

        if df is None:
            logger.warning(f"[SWING_RUN] {code} 데이터 없음 - HOLD 유지")
            continue

        action = holding_mgr.evaluate(pos, df)

        if action == 'EXIT':
            exit_orders.append({
                'code': code,
                'name': pos.stock_name,
                'action': 'SELL',
                'reason': 'MA5_EXIT',
                'entry_price': pos.entry_price,
                'holding_days': pos.holding_days,
                'max_profit_pct': round(pos.max_profit_pct, 2),
                'drawdown_pct': round(pos.drawdown_pct, 2),
            })
            pos.state = SwingState.EXIT
            logger.info(f"[SWING_RUN] {code} → EXIT 큐 등록")

        elif action == 'ADD':
            add_size = holding_mgr.add_lot_size(pos)
            exit_orders.append({
                'code': code,
                'name': pos.stock_name,
                'action': 'ADD',
                'size': add_size,
                'add_count': pos.add_count,
                'ma5_distance_pct': round(pos.ma5_distance_pct, 2),
            })
            pos.add_count += 1
            pos.state = SwingState.ADD
            logger.info(f"[SWING_RUN] {code} → ADD 큐 등록 (size={add_size})")

        elif action == 'TRAIL':
            exit_orders.append({
                'code': code,
                'name': pos.stock_name,
                'action': 'TRAIL',
                'max_profit_pct': round(pos.max_profit_pct, 2),
                'holding_days': pos.holding_days,
            })
            logger.info(f"[SWING_RUN] {code} → TRAIL 모드")

    return exit_orders


def _sector_of(code: str, universe_map: dict) -> str:
    """종목 섹터 반환. 정보 없으면 'UNKNOWN' (섹터 무지 = 같은 버킷으로 관리)."""
    return universe_map.get(code, {}).get('sector', '') or 'UNKNOWN'


def scan_new_signals(
    universe: list[dict],
    existing_positions: dict,   # {code: SwingPosition}
    config: dict,
    top_n: int = 3,
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

    # 현재 보유 노출 합계 (Fix 5)
    current_exposure = sum(p.allocated_size for p in existing_positions.values())
    if current_exposure >= max_exposure:
        logger.info(
            f"[SWING_RUN] 최대 노출 도달 exposure={current_exposure:.2f} >= {max_exposure:.2f} - 신규 생략"
        )
        return []

    universe_map = {u['code']: u for u in universe}

    # 기존 보유 섹터 현황 (UNKNOWN 포함)
    held_sectors: dict[str, int] = {}
    for code in existing_codes:
        sec = _sector_of(code, universe_map)   # 항상 non-empty (UNKNOWN fallback)
        held_sectors[sec] = held_sectors.get(sec, 0) + 1

    all_signals = []

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
            continue

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
            break

        # 노출 한도 체크 (Fix 5)
        # sig['size'] = score_from_size() 결과 (0.5/0.7/1.0) → SwingPosition.allocated_size와 동일 단위
        new_size = sig.get('size', swing_cfg.get('size', {}).get('initial', 0.5))
        if used_exposure + new_size > max_exposure:
            logger.info(
                f"[SWING_RUN] {sig['code']} 노출 한도 초과 "
                f"({used_exposure:.2f}+{new_size:.2f} > {max_exposure:.2f}) → 건너뜀"
            )
            continue

        # 섹터 다각화 체크 (UNKNOWN 포함 — 모르는 섹터도 버킷 관리)
        sec = sig.get('sector', 'UNKNOWN') or 'UNKNOWN'
        if candidate_sectors.get(sec, 0) >= max_same_sector:
            logger.info(
                f"[SWING_RUN] {sig['code']} 섹터 중복 ({sec}, 이미 {candidate_sectors[sec]}개) → 건너뜀"
            )
            continue

        candidates.append(sig)
        used_exposure += new_size
        candidate_sectors[sec] = candidate_sectors.get(sec, 0) + 1

    logger.info(
        f"[SWING_RUN] 진입 후보 {len(candidates)}개 선정 "
        f"(전체 신호 {len(all_signals)}개 | exposure={used_exposure:.2f}/{max_exposure:.2f})"
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
) -> Path:
    """주문 큐를 JSON 파일로 저장."""
    dir_path = Path(order_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    output_path = dir_path / f"swing_orders_{run_date.strftime('%Y-%m-%d')}.json"
    payload = {
        'generated_at': datetime.now().isoformat(),
        'run_date': run_date.isoformat(),
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

    # ── 1단계: 보유 포지션 평가 ──────────────────────────────────────────
    logger.info("[SWING_RUN] 1단계: 보유 포지션 평가")
    exit_orders = process_hold_positions(positions, config, universe)

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
        new_candidates = scan_new_signals(universe, positions, config, top_n=top_n)

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
    output_path = write_order_queue(all_orders, order_dir, date.today())

    # ── 4단계: 상태 저장 ─────────────────────────────────────────────────
    state_mgr.save(state_mgr.all)

    logger.info("=" * 60)
    logger.info(
        f"[SWING_RUN] 완료 | EXIT/ADD/TRAIL: {len(exit_orders)}건 | "
        f"신규 BUY: {len(new_candidates)}건 | 주문큐: {output_path}"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
