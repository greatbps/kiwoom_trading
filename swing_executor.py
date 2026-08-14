"""
swing_executor.py — 스윙 주문 실행기

실행 시점: 매일 장 시작 전 (09:00~09:05) 또는 수동 실행
역할:
  swing_orders_YYYYMMDD.json 읽어 아래 5가지 액션 집행:
    ENTRY        — 신규 진입 (지정가 매수)
    SELL         — 전량 청산 (시장가 매도)
    REDUCE       — 부분 청산 30% (시장가 매도)
    ADD_VALUE    — 눌림 추가매수 (지정가, 평균단가 아래 조건)
    ADD_MOMENTUM — 모멘텀 피라미딩 (지정가, 신고가 돌파 조건)
    TRAIL        — 상태 업데이트만 (주문 없음)

Usage:
    python3 swing_executor.py            # 오늘 날짜 주문 파일 실행
    python3 swing_executor.py --date 2026-05-04
    python3 swing_executor.py --dry-run  # 주문 없이 시뮬레이션만
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from math import floor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from kiwoom_api import KiwoomAPI
from analyzers.swing.state_machine import (
    SwingStateManager, SwingPosition, SwingState,
    SwingExitHistoryManager, SwingExitRecord,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

ORDER_DIR         = Path("logs")
STATE_FILE        = Path("data/swing_positions.json")
STRATEGY_FILE     = Path("data/positions_strategy.json")
EXIT_HISTORY_FILE = Path("data/swing_exit_history.json")


def _record_position_strategy(code: str, strategy: str) -> None:
    """주문 시점에 종목별 entry_strategy를 영구 기록."""
    try:
        data = json.loads(STRATEGY_FILE.read_text(encoding='utf-8')) if STRATEGY_FILE.exists() else {}
        data[code] = strategy
        STRATEGY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        logger.warning(f"[STRATEGY_FILE] 기록 실패 {code}: {e}")


def _get_db():
    """TradingDatabase 인스턴스 반환 (지연 임포트)."""
    from database.trading_db import TradingDatabase
    return TradingDatabase()


def _record_buy_db(code: str, name: str, quantity: int, price: int,
                   pattern: str, score: float,
                   stop_price: float = 0.0, target_price: float = 0.0,
                   market_regime: str = '') -> int | None:
    """스윙 매수를 PostgreSQL trades 테이블에 기록. trade_id 반환."""
    from datetime import datetime
    try:
        trade_id = _get_db().insert_swing_buy({
            'stock_code': code,
            'stock_name': name,
            'trade_time': datetime.now().isoformat(),
            'price': float(price),
            'quantity': quantity,
            'amount': float(price * quantity),
            'entry_reason': f"SWING:{pattern} score={score:.1f}",
            'swing_pattern': pattern,
            'swing_score': score,
            'market_regime': market_regime or None,
            'stop_price': float(stop_price) if stop_price else None,
            'target_price': float(target_price) if target_price else None,
            'entry_context': {
                'pattern': pattern, 'score': score,
                'entry_price': float(price),
                'stop_price': float(stop_price) if stop_price else None,
                'target_price': float(target_price) if target_price else None,
                'market_regime': market_regime,
            },
        })
        logger.info(f"[SWING_BUY_DB] {code} {name} BUY {quantity}주 @ {price:,} → PostgreSQL trade_id={trade_id}")
        return trade_id
    except Exception as e:
        logger.warning(f"[SWING_BUY_DB] PostgreSQL 기록 실패 {code}: {e}")
        return None


def _record_sell_db(code: str, name: str, quantity: int, price: float,
                    reason: str, pos: 'SwingPosition') -> None:
    """스윙 매도/부분청산을 PostgreSQL에 기록."""
    from datetime import datetime, date as date_cls
    try:
        ep = pos.entry_price
        pnl_pct = round((price - ep) / ep * 100, 3) if ep > 0 else 0.0
        realized = round((price - ep) * quantity, 0)
        peak   = pos.peak_price   or price
        trough = pos.trough_price or price
        mfe = round((peak   - ep) / ep * 100, 3) if ep > 0 else 0.0
        mae = round((ep - trough) / ep * 100, 3) if ep > 0 and trough > 0 else 0.0
        entry_dt = pos.entry_date.isoformat() if isinstance(pos.entry_date, date_cls) else None
        _get_db().insert_swing_sell({
            'stock_code': code,
            'stock_name': name,
            'trade_time': datetime.now().isoformat(),
            'price': float(price),
            'quantity': quantity,
            'amount': float(price * quantity),
            'exit_reason': reason,
            'entry_time': entry_dt,
            'holding_minutes': (pos.holding_days or 0) * 390,
            'realized_profit': float(realized),
            'profit_rate': float(pnl_pct),
            'mfe_pct': float(mfe),
            'mae_pct': float(mae),
            'peak_price': float(peak),
            'trough_price': float(trough),
            'exit_context': {
                'entry_price': float(ep),
                'exit_reason': reason,
                'holding_days': pos.holding_days,
                'add_count': pos.add_count,
                'max_profit_pct': pos.max_profit_pct,
                'drawdown_pct': pos.drawdown_pct,
            },
        })
        logger.info(
            f"[SWING_SELL_DB] {code} {name} SELL {quantity}주 @ {price:,.0f} "
            f"pnl={pnl_pct:+.2f}% MFE={mfe:.2f}% MAE={mae:.2f}%"
        )
    except Exception as e:
        logger.warning(f"[SWING_SELL_DB] PostgreSQL 기록 실패 {code}: {e}")

def _log_decision_swing(
    code: str, name: str, decision_type: str,
    signals: dict, reason: str = '',
    confidence: int = None, trade_id: int = None,
    outcome_pnl_pct: float = None,
) -> None:
    """Trading OS decision_log 기록 — swing_executor 전용 헬퍼.
    실패해도 매매 흐름에 영향 없음 (try/except 완전 격리).
    """
    import json as _json
    from datetime import datetime as _dt
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname='trading_system', user='postgres',
            password=os.getenv('POSTGRES_PASSWORD'), host='localhost'
        )
        cur = conn.cursor()

        # 오늘 market_context_id / session_id (없으면 NULL — MIE 미실행일 허용)
        cur.execute(
            "SELECT id FROM market_context WHERE context_date = CURRENT_DATE"
        )
        row = cur.fetchone()
        ctx_id = row[0] if row else None

        cur.execute(
            "SELECT id FROM trading_sessions WHERE session_date = CURRENT_DATE"
        )
        row = cur.fetchone()
        session_id = row[0] if row else None

        cur.execute(
            "SELECT id FROM research_environment WHERE status='active' LIMIT 1"
        )
        row = cur.fetchone()
        re_id = row[0] if row else None

        cur.execute(
            """INSERT INTO decision_log
                 (decision_time, trade_id, stock_code, stock_name, decision_type,
                  strategy_version, signals, decision_reason, confidence,
                  outcome_pnl_pct, outcome_filled_at,
                  market_context_id, session_id, research_environment_id,
                  model_version, prompt_version)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                _dt.now(), trade_id, code, name, decision_type,
                'swing-v2.0',
                _json.dumps(signals, ensure_ascii=False, default=str),
                reason, confidence,
                outcome_pnl_pct,
                _dt.now() if outcome_pnl_pct is not None else None,
                ctx_id, session_id, re_id,
                'rule-engine-swing', 'swing-v2.0',
            )
        )
        conn.commit()
        conn.close()
        logger.debug(f"[DECISION_LOG] {decision_type} {code} 기록 완료")
    except Exception as _e:
        logger.debug(f"[DECISION_LOG] 기록 실패 {code}: {_e}")


def _ds_record_swing_entry(
    code: str, name: str,
    pos: 'SwingPosition', order: dict,
    buy_price: int, trade_id: int,
    pattern: str, score: float, market_regime: str,
) -> None:
    """Research Layer: SWING 진입 → begin_evaluation → record_acceptance → record_order."""
    try:
        from database.trading_db import TradingDatabase
        from services.decision_service import DecisionService
        _ds = DecisionService(TradingDatabase())
        confidence = float(order.get('confidence') or 0)
        ctx = _ds.begin_evaluation(
            symbol=code,
            price=float(buy_price),
            features={
                'pattern': pattern, 'score': score,
                'market_regime': market_regime,
                'confidence': confidence,
                'stop_price': float(order.get('stop') or 0) or None,
                'target_price': float(order.get('target') or 0) or None,
                'phase': order.get('phase'),
                'trigger': order.get('trigger'),
            },
            stock_name=name,
            market='KOSPI',
            strategy_type='SWING',
        )
        decision_id = _ds.record_acceptance(ctx, confidence=confidence)
        if decision_id:
            _ds.record_order(
                decision_id=decision_id,
                trade_id=trade_id,
                executed_price=float(buy_price),
                trace_id=ctx.trace_id if ctx else None,
            )
            # decision_id / trace_id 포지션에 저장 → 청산 시 record_exit에 사용
            pos.decision_id = decision_id
            pos.trace_id    = ctx.trace_id if ctx else None
            logger.debug(f"[RESEARCH] SWING PASS recorded {code} trace={pos.trace_id}")
    except Exception as _e:
        logger.debug(f"[RESEARCH] swing entry record 실패 {code}: {_e}")


def _ds_record_swing_exit(
    pos: 'SwingPosition', sell_price: float, reason: str, pnl_pct: float | None,
) -> None:
    """Research Layer: SWING 청산 → record_exit."""
    try:
        decision_id = getattr(pos, 'decision_id', None)
        if not decision_id:
            return
        from database.trading_db import TradingDatabase
        from services.decision_service import DecisionService
        _ds = DecisionService(TradingDatabase())
        _ds.record_exit(
            decision_id=decision_id,
            exit_price=sell_price,
            exit_reason=reason,
            pnl_pct=float(pnl_pct or 0.0),
            trace_id=getattr(pos, 'trace_id', None),
        )
        logger.debug(f"[RESEARCH] SWING EXIT recorded {pos.stock_code} pnl={pnl_pct}")
    except Exception as _e:
        logger.debug(f"[RESEARCH] swing exit record 실패: {_e}")


def _update_exit_history(code: str, sell_price: float, reason: str, entry_price: float) -> None:
    """실제 체결가로 exit_history 갱신 — runner 추정값(exit_price=0.0)을 정확한 값으로 교체."""
    try:
        pnl_pct = round((sell_price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0.0
        mgr = SwingExitHistoryManager(path=str(EXIT_HISTORY_FILE))
        mgr.load()
        mgr.record(code, SwingExitRecord(
            exit_date=date.today().isoformat(),
            exit_reason=reason,
            exit_pnl_pct=pnl_pct,
            exit_price=sell_price,
            entry_price=entry_price,
        ))
        mgr.save()
        logger.info(f"[EXEC_EXIT] exit_history 갱신: {code} reason={reason} pnl={pnl_pct:+.2f}% price={sell_price:,.0f}")
    except Exception as e:
        logger.warning(f"[EXEC_EXIT] exit_history 갱신 실패 {code}: {e}")


# 종목당 총 허용 비중 상한 (초기 + 추가매수 합산)
MAX_SINGLE_EXPOSURE     = 0.80   # 전체 자본의 80% (소액 계좌)
# ADD_VALUE: 평균단가 대비 이 비율 이하여야 집행 (단가 개선 보장)
VALUE_ADD_MAX_ABOVE_AVG = -0.005  # 평균단가 -0.5% 이하
# ADD_MOMENTUM: 평균단가 대비 이 비율 이하까지만 집행 (추격 상한)
MOMENTUM_ADD_MAX_ABOVE_AVG = 0.08  # 평균단가 +8% 이하


# ── 유틸 ───────────────────────────────────────────────────────────────────────

def _tick(price: float) -> int:
    """KRX 호가 단위 적용."""
    if price < 1_000:    unit = 1
    elif price < 5_000:  unit = 5
    elif price < 10_000: unit = 10
    elif price < 50_000: unit = 50
    elif price < 100_000: unit = 100
    elif price < 500_000: unit = 500
    else:                unit = 1_000
    return int(price // unit * unit)


def _init_api() -> KiwoomAPI:
    api = KiwoomAPI()
    api.get_access_token()
    return api


def _get_price(api: KiwoomAPI, code: str) -> float | None:
    try:
        result = api.get_stock_info(code)
        if not result:
            return None
        val = result.get('cur_prc') or result.get('stck_prpr') or result.get('price')
        if val:
            return abs(float(str(val).replace(',', '').lstrip('+')))
    except Exception as e:
        logger.warning(f"[EXEC] 현재가 조회 실패 {code}: {e}")
    return None


def _get_cash(api: KiwoomAPI) -> float:
    try:
        info = api.get_balance()
        if info:
            cash_str = info.get('ord_alow_amt') or info.get('entr') or '0'
            return float(str(cash_str).replace(',', ''))
    except Exception as e:
        logger.warning(f"[EXEC] 잔고 조회 실패: {e}")
    return 0.0


def _get_holding_qty(api: KiwoomAPI, code: str) -> int:
    try:
        info = api.get_account_info()
        for pos in (info.get('day_bal_rt', []) if info else []):
            if pos.get('stk_cd', '').strip() == code.strip():
                return int(str(pos.get('rmnd_qty', 0)).replace(',', '') or '0')
    except Exception as e:
        logger.warning(f"[EXEC] 보유수량 조회 실패 {code}: {e}")
    return 0


def _load_orders(target_date: date) -> list[dict]:
    for d in [target_date, target_date - timedelta(days=1), target_date - timedelta(days=3)]:
        path = ORDER_DIR / f"swing_orders_{d.isoformat()}.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding='utf-8'))
            orders = payload.get('orders', [])
            logger.info(f"[EXEC] 주문 파일 로드: {path} ({len(orders)}건)")
            return orders
    logger.warning(f"[EXEC] 주문 파일 없음 (오늘={target_date})")
    return []


def _place_buy(api: KiwoomAPI, code: str, quantity: int, price: int, dry_run: bool) -> bool:
    """공통 매수 주문 실행."""
    if dry_run:
        logger.info(f"[EXEC][DRY] BUY {code} {quantity}주 @ {price:,}")
        return True
    try:
        result = api.order_buy(stock_code=code, quantity=quantity, price=price, trade_type="0")
        if result and result.get('return_code') == 0:
            logger.info(f"[EXEC] ✅ 매수 성공 {code} | 주문번호={result.get('ord_no','?')}")
            return True
        msg = result.get('return_msg', '?') if result else 'API 응답 없음'
        logger.error(f"[EXEC] ❌ 매수 실패 {code}: {msg}")
    except Exception as e:
        logger.error(f"[EXEC] ❌ 매수 예외 {code}: {e}")
    return False


# ── ENTRY ─────────────────────────────────────────────────────────────────────

def _execute_entry(
    api: KiwoomAPI,
    order: dict,
    available_cash: float,
    total_capital: float,
    state_mgr: SwingStateManager,
    dry_run: bool,
    market_regime: str = '',
) -> tuple[bool, float]:
    """신규 진입. (성공여부, 사용한 현금) 반환."""
    code      = order.get('code', '')
    name      = order.get('name', code)
    size      = float(order.get('size', 0.5))
    pattern   = order.get('pattern', '')
    score     = float(order.get('final_score', 0))
    ai_score  = order.get('ai_score')   # AnalysisEngine 0-100 (None if gate disabled)
    ai_rec    = order.get('ai_rec')

    # Iteration 25: Volatility Size Reduction (기본 off) — core/risk_layer.py 공식 재사용
    _vsr_enabled = False
    try:
        import yaml as _yaml_rl
        _rl_cfg_path = Path(__file__).parent / 'config' / 'strategy_hybrid.yaml'
        _vsr_enabled = _yaml_rl.safe_load(
            _rl_cfg_path.read_text(encoding='utf-8')
        ).get('risk_layer', {}).get('volatility_size_reduction', {}).get('enabled', False)
    except Exception:
        pass
    from core.risk_layer import volatility_size_mult
    _vsr_mult = volatility_size_mult(order.get('volatility20'), _vsr_enabled)
    if _vsr_mult != 1.0:
        logger.info(f"[RISK_LAYER] {code} Volatility Size Reduction 적용: size {size} → {size * _vsr_mult}")
    size = size * _vsr_mult

    current_price = _get_price(api, code)
    if not current_price:
        logger.warning(f"[EXEC_ENTRY] {code} 현재가 실패 → 스킵")
        return False, 0.0

    # 종목당 최대 비중 한도
    max_invest = total_capital * MAX_SINGLE_EXPOSURE
    invest_amount = min(available_cash * size, max_invest)
    quantity = floor(invest_amount / current_price)
    if quantity <= 0:
        logger.warning(f"[EXEC_ENTRY] {code} 수량=0 (현재가={current_price:,} 투자금={invest_amount:,.0f}) → 스킵")
        # TRIGGER 상태 포지션 제거 — 미매수 상태로 positions.json에 남으면
        # 다음 runner가 holding_manager 평가 후 가짜 SELL 주문을 생성할 수 있음
        positions = state_mgr.load()
        if code in positions:
            state_mgr.remove(code)
            state_mgr.save(state_mgr.all)
            logger.info(f"[EXEC_ENTRY] {code} TRIGGER 포지션 제거 (미매수 정리)")
        return False, 0.0

    buy_price = _tick(current_price)
    used_cash = quantity * buy_price

    try:
        import yaml as _yaml
        _cfg_path = Path(__file__).parent / 'config' / 'strategy_hybrid.yaml'
        _ruleset_v = _yaml.safe_load(_cfg_path.read_text(encoding='utf-8')).get(
            'ruleset_version', 'unknown'
        )
    except Exception:
        _ruleset_v = 'unknown'
    _ai_str = f" ai={ai_score:.0f}({ai_rec})" if ai_score is not None else ""
    logger.info(
        f"[ENTRY_SNAPSHOT] {code} {name} | "
        f"ruleset={_ruleset_v} horizon=SWING pattern={pattern} score={score}{_ai_str} | "
        f"regime_long={market_regime} | "
        f"현재가={current_price:,} 수량={quantity} 투자금={used_cash:,.0f}원 (size={size*100:.0f}%)"
    )

    ok = _place_buy(api, code, quantity, buy_price, dry_run)
    if ok:
        if not dry_run:
            pos = SwingPosition(
                stock_code=code, stock_name=name,
                state=SwingState.HOLD,
                pattern=pattern, score=score,
                entry_price=buy_price,
                entry_date=date.today(),
                allocated_size=size,
                quantity=quantity,
                entry_market_regime=market_regime,
                atr_pct_at_entry=float(order.get('atr_pct') or 0),
            )
            state_mgr.set(pos)
            state_mgr.save(state_mgr.all)
            _record_position_strategy(code, 'swing')
            trade_id = _record_buy_db(
                code, name, quantity, buy_price, pattern, score,
                stop_price=float(order.get('stop') or 0),
                target_price=float(order.get('target') or 0),
                market_regime=market_regime,
            )
            if trade_id:
                pos.trade_id = trade_id
                state_mgr.set(pos)
                state_mgr.save(state_mgr.all)
                # Trading OS: decision_log 기록
                _log_decision_swing(
                    code=code, name=name, decision_type='BUY',
                    signals={
                        'pattern':       pattern,
                        'score':         score,
                        'market_regime': market_regime,
                        'size':          size,
                        'buy_price':     buy_price,
                        'phase':         order.get('phase'),
                        'trigger':       order.get('trigger'),
                        'confidence':    order.get('confidence'),
                    },
                    reason=f"SWING:{pattern}",
                    confidence=int(float(order.get('confidence') or 0) * 100) or None,
                    trade_id=trade_id,
                )
                # Research Layer: Decision Lifecycle PASS → ORDER
                _ds_record_swing_entry(
                    code=code, name=name,
                    pos=pos, order=order,
                    buy_price=buy_price, trade_id=trade_id,
                    pattern=pattern, score=score, market_regime=market_regime,
                )
                # SignalEngine 스냅샷 저장 (분석 파이프라인용)
                try:
                    from database.trading_db import TradingDatabase
                    TradingDatabase().insert_swing_features({
                        'trade_id':     trade_id,
                        'stock_code':   code,
                        'entry_date':   date.today().isoformat(),
                        'pattern':      pattern,
                        'raw_score':    order.get('score'),
                        'final_score':  score,
                        'phase':        order.get('phase'),
                        'trigger':      order.get('trigger'),
                        'confidence':   order.get('confidence'),
                        'entry_price':  float(buy_price),
                        'stop_price':   float(order.get('stop') or 0) or None,
                        'target_price': float(order.get('target') or 0) or None,
                        'size':         size,
                        'market_regime': market_regime,
                        'meta':         {**(order.get('meta') or {}),
                                     'ai_score': ai_score, 'ai_rec': ai_rec},
                    })
                except Exception as _fe:
                    logger.debug(f"[SWING_FEAT] feature snapshot 실패 {code}: {_fe}")
        return True, used_cash
    return False, 0.0


# ── ADD (VALUE / MOMENTUM 공통 안전장치) ──────────────────────────────────────

def _execute_add(
    api: KiwoomAPI,
    order: dict,
    available_cash: float,
    total_capital: float,
    state_mgr: SwingStateManager,
    dry_run: bool,
) -> tuple[bool, float]:
    """
    ADD_VALUE / ADD_MOMENTUM 공통 실행.

    안전장치:
      1. add_count < 2 (최대 2회)
      2. 가격 조건 — VALUE: 평균단가 -0.5% 이하 / MOMENTUM: 평균단가 +8% 이하
      3. 종목 총 노출 ≤ total_capital × 25%
    """
    code    = order.get('code', '')
    name    = order.get('name', code)
    action  = order.get('action', 'ADD_VALUE')   # 'ADD_VALUE' or 'ADD_MOMENTUM'
    add_size = float(order.get('size', 0.25))

    positions = state_mgr.load()
    pos = positions.get(code)
    if pos is None:
        logger.warning(f"[EXEC_ADD] {code} 포지션 정보 없음 → 스킵")
        return False, 0.0

    # 안전장치 1: ADD 횟수 한도
    if pos.add_count >= 2:
        logger.warning(f"[EXEC_ADD] {code} 횟수 한도 (add_count={pos.add_count}) → 차단")
        return False, 0.0

    current_price = _get_price(api, code)
    if not current_price:
        logger.warning(f"[EXEC_ADD] {code} 현재가 실패 → 스킵")
        return False, 0.0

    # 안전장치 2: 가격 조건 (전략 유형별 분기)
    if pos.entry_price > 0:
        pct_vs_avg = (current_price - pos.entry_price) / pos.entry_price
        if action == 'ADD_VALUE':
            if pct_vs_avg > VALUE_ADD_MAX_ABOVE_AVG:
                logger.warning(
                    f"[EXEC_ADD_VALUE] {code} 현재가가 평균단가 대비 {pct_vs_avg*100:+.1f}% "
                    f"(한도 {VALUE_ADD_MAX_ABOVE_AVG*100:.1f}%) → 차단 (단가 개선 불가)"
                )
                return False, 0.0
        else:  # ADD_MOMENTUM
            if pct_vs_avg > MOMENTUM_ADD_MAX_ABOVE_AVG:
                logger.warning(
                    f"[EXEC_ADD_MOMENTUM] {code} 현재가가 평균단가 대비 {pct_vs_avg*100:+.1f}% "
                    f"(한도 {MOMENTUM_ADD_MAX_ABOVE_AVG*100:.0f}%) → 차단 (추격 상한 초과)"
                )
                return False, 0.0

    # 안전장치 3: 종목당 총 비중 한도
    current_qty = _get_holding_qty(api, code) or pos.quantity
    current_exposure = current_qty * current_price
    max_exposure = total_capital * MAX_SINGLE_EXPOSURE
    if current_exposure >= max_exposure:
        logger.warning(
            f"[EXEC_ADD] {code} 비중 한도 초과 ({current_exposure:,.0f} >= {max_exposure:,.0f}) → 차단"
        )
        return False, 0.0

    add_invest = min(available_cash * add_size, max_exposure - current_exposure)
    quantity   = floor(add_invest / current_price)
    if quantity <= 0:
        logger.warning(f"[EXEC_ADD] {code} 수량=0 → 스킵")
        return False, 0.0

    buy_price = _tick(current_price)
    used_cash = quantity * buy_price
    tag = 'VALUE' if action == 'ADD_VALUE' else 'MOMENTUM'

    logger.info(
        f"[EXEC_ADD_{tag}] {code} {name} | add_count={pos.add_count+1}/2 | "
        f"MA5이격={order.get('ma5_distance_pct',0):+.1f}% "
        f"단가대비={(current_price/pos.entry_price-1)*100:+.1f}% "
        f"현재가={current_price:,} 수량={quantity} 투자금={used_cash:,.0f}원"
    )

    ok = _place_buy(api, code, quantity, buy_price, dry_run)
    if ok:
        if not dry_run:
            new_qty = pos.quantity + quantity
            new_avg = (pos.entry_price * pos.quantity + buy_price * quantity) / max(new_qty, 1)
            pos.entry_price = new_avg
            pos.quantity    = new_qty
            pos.add_count  += 1
            pos.state       = SwingState.ADD
            state_mgr.set(pos)
            state_mgr.save(state_mgr.all)
        return True, used_cash
    return False, 0.0


# ── REDUCE (부분 청산) ─────────────────────────────────────────────────────────

def _execute_reduce(
    api: KiwoomAPI,
    order: dict,
    state_mgr: SwingStateManager,
    dry_run: bool,
) -> bool:
    """보유 수량의 30% 부분 청산. 수익 환원 또는 위험 축소."""
    code      = order.get('code', '')
    name      = order.get('name', code)
    reduce_pct = float(order.get('size', 0.30))

    total_qty = _get_holding_qty(api, code)
    if total_qty <= 0:
        positions = state_mgr.load()
        pos = positions.get(code)
        total_qty = getattr(pos, 'quantity', 0) if pos else 0

    if total_qty <= 0:
        logger.warning(f"[EXEC_REDUCE] {code} 보유수량=0 → 스킵")
        return False

    reduce_qty = max(1, floor(total_qty * reduce_pct))
    profit_pct = order.get('profit_pct', 0)
    dd_pct     = order.get('drawdown_pct', 0)

    logger.info(
        f"[EXEC_REDUCE] {code} {name} | {reduce_qty}/{total_qty}주 ({reduce_pct*100:.0f}%) 부분청산 | "
        f"profit={profit_pct:.1f}% drawdown={dd_pct:.1f}%"
    )

    if dry_run:
        logger.info(f"[EXEC_REDUCE][DRY] 주문 생략")
        return True

    try:
        result = api.order_sell(stock_code=code, quantity=reduce_qty, price=0, trade_type="3")
        if result and result.get('return_code') == 0:
            logger.info(f"[EXEC_REDUCE] ✅ 부분청산 성공 {code} | 주문번호={result.get('ord_no','?')}")
            positions = state_mgr.load()
            pos = positions.get(code)
            if pos:
                sell_price = _get_price(api, code) or pos.entry_price
                _record_sell_db(code, name, reduce_qty, sell_price,
                                f"REDUCE:{reason or 'partial'}", pos)
                pos.quantity = max(0, total_qty - reduce_qty)
                state_mgr.set(pos)
                state_mgr.save(state_mgr.all)
            return True
        msg = result.get('return_msg', '?') if result else 'API 응답 없음'
        logger.error(f"[EXEC_REDUCE] ❌ 부분청산 실패 {code}: {msg}")
    except Exception as e:
        logger.error(f"[EXEC_REDUCE] ❌ 예외 {code}: {e}")
    return False


# ── EXIT ──────────────────────────────────────────────────────────────────────

def _execute_exit(
    api: KiwoomAPI,
    order: dict,
    state_mgr: SwingStateManager,
    dry_run: bool,
) -> bool:
    code   = order.get('code', '')
    name   = order.get('name', code)
    reason = order.get('reason', '')

    # 계좌 기준 보유 수량 우선, 없으면 상태 파일
    quantity = _get_holding_qty(api, code)
    if quantity <= 0:
        positions = state_mgr.load()
        pos = positions.get(code)
        quantity = getattr(pos, 'quantity', 0) if pos else 0

    if quantity <= 0:
        logger.warning(f"[EXEC_EXIT] {code} {name}: 보유수량=0 → 스킵")
        state_mgr.remove(code)
        state_mgr.save(state_mgr.all)
        return False

    logger.info(f"[EXEC_EXIT] {code} {name} | 사유={reason} | 수량={quantity} | 시장가 매도")

    # 매도 API 호출 전에 pos를 미리 확보 — 호출 후 state가 비어있을 수 있음
    positions_pre = state_mgr.load()
    pos_pre = positions_pre.get(code)

    if dry_run:
        logger.info(f"[EXEC_EXIT][DRY] 주문 생략")
        return True

    try:
        result = api.order_sell(stock_code=code, quantity=quantity, price=0, trade_type="3")
        if result and result.get('return_code') == 0:
            logger.info(f"[EXEC_EXIT] ✅ 매도 성공 {code} | 주문번호={result.get('ord_no','?')}")
            sell_price = _get_price(api, code)

            if pos_pre:
                sell_price = sell_price or pos_pre.entry_price
                _record_sell_db(code, name, quantity, sell_price, reason, pos_pre)
                _update_exit_history(code, sell_price, reason, pos_pre.entry_price)
                # Trading OS: decision_log SELL 기록
                _ep = pos_pre.entry_price
                _pnl = round((sell_price - _ep) / _ep * 100, 3) if _ep > 0 else None
                _log_decision_swing(
                    code=code, name=name, decision_type='SELL',
                    signals={
                        'exit_reason':   reason,
                        'sell_price':    sell_price,
                        'entry_price':   _ep,
                        'quantity':      quantity,
                        'holding_days':  getattr(pos_pre, 'holding_days', None),
                        'pattern':       getattr(pos_pre, 'pattern', None),
                    },
                    reason=reason,
                    trade_id=getattr(pos_pre, 'trade_id', None),
                    outcome_pnl_pct=_pnl,
                )
                # Research Layer: Decision Lifecycle EXIT 기록
                _ds_record_swing_exit(pos=pos_pre, sell_price=sell_price, reason=reason, pnl_pct=_pnl)
            else:
                # state에 포지션 없음 — order dict의 entry_price로 최소 기록
                entry_price = float(order.get('entry_price', 0))
                sell_price  = sell_price or entry_price
                if entry_price > 0 and sell_price > 0:
                    realized   = round((sell_price - entry_price) * quantity, 0)
                    pnl_pct    = round((sell_price - entry_price) / entry_price * 100, 3)
                    _get_db().insert_swing_sell({
                        'stock_code':       code,
                        'stock_name':       name,
                        'trade_time':       __import__('datetime').datetime.now().isoformat(),
                        'price':            float(sell_price),
                        'quantity':         quantity,
                        'amount':           float(sell_price * quantity),
                        'exit_reason':      reason,
                        'entry_time':       None,
                        'holding_minutes':  int(order.get('holding_days', 0)) * 390,
                        'realized_profit':  float(realized),
                        'profit_rate':      float(pnl_pct),
                        'mfe_pct':          float(order.get('max_profit_pct', 0)),
                        'mae_pct':          abs(float(order.get('drawdown_pct', 0))),
                        'peak_price':       float(sell_price),
                        'trough_price':     float(sell_price),
                        'exit_context': {
                            'entry_price':  float(entry_price),
                            'exit_reason':  reason,
                            'holding_days': order.get('holding_days', 0),
                            'source':       'order_fallback',
                        },
                    })
                    logger.info(
                        f"[SWING_SELL_DB] {code} {name} SELL {quantity}주 @ {sell_price:,.0f} "
                        f"pnl={pnl_pct:+.2f}% [order_fallback]"
                    )
                    _update_exit_history(code, sell_price, reason, entry_price)
                    # Trading OS: decision_log SELL 기록 (fallback)
                    _log_decision_swing(
                        code=code, name=name, decision_type='SELL',
                        signals={
                            'exit_reason':  reason,
                            'sell_price':   sell_price,
                            'entry_price':  entry_price,
                            'quantity':     quantity,
                            'source':       'order_fallback',
                        },
                        reason=reason,
                        outcome_pnl_pct=pnl_pct,
                    )
                else:
                    logger.warning(f"[SWING_SELL_DB] {code} pos 없음 + entry_price 불명 → DB 기록 스킵")

            state_mgr.remove(code)
            state_mgr.save(state_mgr.all)
            return True
        msg = result.get('return_msg', '?') if result else 'API 응답 없음'
        logger.error(f"[EXEC_EXIT] ❌ 매도 실패 {code}: {msg}")
    except Exception as e:
        logger.error(f"[EXEC_EXIT] ❌ 매도 예외 {code}: {e}")
    return False


# ── TRAIL (상태 업데이트만) ───────────────────────────────────────────────────

def _handle_trail(order: dict, state_mgr: SwingStateManager) -> None:
    code = order.get('code', '')
    positions = state_mgr.load()
    pos = positions.get(code)
    if pos:
        pos.state = SwingState.HOLD   # TRAIL은 별도 주문 없이 HOLD 유지
        state_mgr.set(pos)
        state_mgr.save(state_mgr.all)
    logger.info(
        f"[EXEC_TRAIL] {code} | max_profit={order.get('max_profit_pct',0):.1f}% "
        f"days={order.get('holding_days',0)} → 트레일링 모드 (주문 없음, 보유 유지)"
    )


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='스윙 주문 실행기')
    parser.add_argument('--date', type=str, default=None)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    dry_run = args.dry_run

    logger.info("=" * 60)
    logger.info(f"[EXEC] 스윙 실행기 시작 | {target_date} | dry_run={dry_run}")
    logger.info("=" * 60)

    api = _init_api()

    orders = _load_orders(target_date)
    if not orders:
        logger.info("[EXEC] 실행할 주문 없음 — 종료")
        return

    # 주문 파일의 market_regime (swing_runner가 기록한 값)
    order_file_regime = ''
    for d in [target_date, target_date - timedelta(days=1), target_date - timedelta(days=3)]:
        p = ORDER_DIR / f"swing_orders_{d.isoformat()}.json"
        if p.exists():
            try:
                payload = json.loads(p.read_text(encoding='utf-8'))
                order_file_regime = payload.get('market_regime', '')
            except Exception:
                pass
            break

    state_mgr    = SwingStateManager(path=str(STATE_FILE))
    avail_cash   = _get_cash(api)
    total_capital = avail_cash  # 첫 조회값을 총 자본 기준으로 사용
    logger.info(f"[EXEC] 주문가능금액: {avail_cash:,.0f}원")

    # 주문 분류 (처리 순서: EXIT → REDUCE → ADD → ENTRY → TRAIL)
    exit_orders   = [o for o in orders if o.get('action') == 'SELL']
    reduce_orders = [o for o in orders if o.get('action') == 'REDUCE']
    add_orders    = [o for o in orders if o.get('action') in ('ADD_VALUE', 'ADD_MOMENTUM')]
    entry_orders  = [o for o in orders if o.get('action') == 'BUY']
    trail_orders  = [o for o in orders if o.get('action') == 'TRAIL']

    logger.info(
        f"[EXEC] EXIT {len(exit_orders)} / REDUCE {len(reduce_orders)} / "
        f"ADD {len(add_orders)} / ENTRY {len(entry_orders)} / TRAIL {len(trail_orders)}"
    )

    counters = {'exit': 0, 'reduce': 0, 'add': 0, 'entry': 0}

    # ── 1. EXIT (리스크 제거, 현금 확보) ────────────────────────────────────
    for o in exit_orders:
        if _execute_exit(api, o, state_mgr, dry_run):
            counters['exit'] += 1
        time.sleep(0.5)

    # ── 2. REDUCE (과열 포지션 정리) ─────────────────────────────────────────
    for o in reduce_orders:
        if _execute_reduce(api, o, state_mgr, dry_run):
            counters['reduce'] += 1
        time.sleep(0.5)

    # EXIT/REDUCE 후 현금 재조회
    if (exit_orders or reduce_orders) and not dry_run:
        time.sleep(2)
        avail_cash = _get_cash(api)
        logger.info(f"[EXEC] EXIT/REDUCE 후 주문가능금액: {avail_cash:,.0f}원")

    # ── 3. ADD (기존 포지션 강화) ─────────────────────────────────────────────
    for o in add_orders:
        ok, used = _execute_add(api, o, avail_cash, total_capital, state_mgr, dry_run)
        if ok:
            counters['add'] += 1
            avail_cash = max(0.0, avail_cash - used)
        time.sleep(0.5)

    # ── 4. ENTRY (신규 진입) ──────────────────────────────────────────────────
    _sw_entry_enabled = True
    try:
        import yaml as _yaml_sw
        _sw_cfg_path = Path(__file__).parent / 'config' / 'strategy_hybrid.yaml'
        _sw_entry_enabled = _yaml_sw.safe_load(
            _sw_cfg_path.read_text(encoding='utf-8')
        ).get('swing', {}).get('enabled', True)
    except Exception:
        pass

    if not _sw_entry_enabled:
        logger.info(
            f"[STRATEGY_DISABLED] strategy=SWING enabled=false "
            f"— ENTRY {len(entry_orders)}건 건너뜀"
        )
        for o in entry_orders:
            logger.info(f"[STRATEGY_DISABLED] {o.get('code', '?')} strategy=SWING → skip")
    else:
        for o in entry_orders:
            positions = state_mgr.load()
            existing = positions.get(o.get('code'))
            if existing and existing.quantity > 0:
                logger.info(f"[EXEC_ENTRY] {o['code']} 이미 보유(qty={existing.quantity}) → 스킵")
                continue
            ok, used = _execute_entry(api, o, avail_cash, total_capital, state_mgr, dry_run,
                                       market_regime=order_file_regime)
            if ok:
                counters['entry'] += 1
                avail_cash = max(0.0, avail_cash - used)
            time.sleep(0.5)

    # ── 5. TRAIL (상태 업데이트만, 주문 없음) ────────────────────────────────
    for o in trail_orders:
        _handle_trail(o, state_mgr)

    logger.info("=" * 60)
    logger.info(
        f"[EXEC] 완료 | EXIT {counters['exit']}/{len(exit_orders)} | "
        f"REDUCE {counters['reduce']}/{len(reduce_orders)} | "
        f"ADD {counters['add']}/{len(add_orders)} | "
        f"ENTRY {counters['entry']}/{len(entry_orders)}"
        + (" [DRY-RUN]" if dry_run else "")
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
