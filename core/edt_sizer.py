"""
core/edt_sizer.py — EDT 실시간 size_mult 계산기
=================================================
Kelly 기반 권장값 × DrawdownEngine 보정 × 세션 가드 → 최종 edt_size_mult 반환.

사용법 (main_auto_trading.py):

    from core.edt_sizer import EdtSizer
    edt_sizer = EdtSizer(config)                       # 초기화 (1회)
    edt_sizer.load_kelly('logs/edt_symbol_weights.json')  # 장 시작 시

    # 진입 직전
    base_mult = smc_details.get('prefilter', {}).get('edt_size_mult', 1.0)
    guard_adj, guard_reasons = edt_sizer.check_session_guards(
        positions       = self.positions,
        time_now        = datetime.now(),
        kospi_change_pct= kospi_pct,   # 당일 KOSPI 등락률
    )
    final_mult = edt_sizer.get_final_mult(
        base_mult = base_mult * guard_adj,
        symbol    = stock_code,
        tag       = 'EARLY_DOWNTREND_HEALTHY_PULLBACK',  # or None
    )
    position_size = int(position_size * final_mult)
"""

import json
import logging
import os
from datetime import date
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Kelly 권장 size_mult 기본값 (데이터 없을 때)
_KELLY_DEFAULTS = {
    'EARLY_DOWNTREND_HEALTHY_PULLBACK': 1.2,
    'EARLY_DOWNTREND_REVERSAL':         1.1,
    'EARLY_DOWNTREND_UPTREND_EXEMPT':   1.1,
}

# Drawdown 레벨별 EDT 부스트 상한
# CAUTION/DANGER 시 부스트 상한 축소 (손실 구간에서 베팅 확대 방지)
_DD_BOOST_CAP = {
    'NORMAL':  1.5,
    'CAUTION': 1.2,
    'DANGER':  1.0,   # 부스트 없음
    'HALT':    0.0,   # 진입 차단 (상위 레이어에서 처리)
}


class EdtSizer:
    """EDT 필터 연동 실시간 size_mult 결정기."""

    def __init__(self, config: dict):
        self._config     = config
        self._kelly_mult: dict = {}       # {tag: recommended_mult}
        self._sym_weight: dict = {}       # {symbol: weight (0~1)}
        self._dd_engine            = None
        self._cfg = (config or {}).get('edt_sizer', {})
        self._enabled = self._cfg.get('enabled', True)

        # 세션 가드 상태
        self._halt_day: Optional[date] = None   # Guard 1: HALT 발동 날짜
        self._daily_realized_pnl: float = 0.0  # Guard 4: 당일 실현 손익 합계(%)
        self._sector_map: dict = {}             # Guard 2: {code: sector_name}

        self._init_dd_engine(config)
        self._load_sector_map()

    def _init_dd_engine(self, config: dict):
        try:
            from core.drawdown_engine import DrawdownEngine
            self._dd_engine = DrawdownEngine(config)
        except Exception as e:
            logger.debug(f"[EDT_SIZER] DrawdownEngine 연동 실패 (무시): {e}")

    def _load_sector_map(self):
        """data/sector_map.json 로드 → {code: sector_name} 역인덱스."""
        path = self._cfg.get('sector_map_path', 'data/sector_map.json')
        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            for sector, codes in raw.get('sectors', {}).items():
                for code in codes:
                    self._sector_map[str(code)] = sector
            logger.debug(f"[EDT_SIZER] 섹터맵 로드: {len(self._sector_map)}종목")
        except Exception as e:
            logger.debug(f"[EDT_SIZER] 섹터맵 로드 실패 (무시): {e}")

    # ── 데이터 로드 ──────────────────────────────────────────────────────

    def load_kelly(self, kelly_path: str = 'logs/edt_symbol_weights.json'):
        """장 시작 시 종목별 weight + Kelly 권장값 로드."""
        if not os.path.exists(kelly_path):
            return
        try:
            with open(kelly_path, encoding='utf-8') as f:
                data = json.load(f)
            self._sym_weight = {
                sym: info.get('weight', 0.5)
                for sym, info in data.items()
            }
            logger.info(f"[EDT_SIZER] 종목 weight {len(self._sym_weight)}건 로드")
        except Exception as e:
            logger.debug(f"[EDT_SIZER] weight 로드 실패: {e}")

    def update_kelly_mults(self, kelly_dict: dict):
        """offline tracker에서 계산한 kelly 결과를 직접 주입."""
        for tag, info in kelly_dict.items():
            self._kelly_mult[tag] = info.get('recommended_mult', 1.0)

    # ── 핵심 API ─────────────────────────────────────────────────────────

    def get_final_mult(
        self,
        base_mult: float,
        symbol: str = '',
        tag: Optional[str] = None,
    ) -> float:
        """
        최종 size_mult 계산.

        흐름:
            base_mult (EDT 필터 출력)
            × kelly_adj   (Kelly 권장값 / default 1.0 기반)
            × sym_adj     (종목별 승률 weight)
            capped by     DD_BOOST_CAP[dd_level]
            floored by    0.1 (최소 보호)
        """
        if not self._enabled:
            return base_mult

        # 1. Kelly 보정 (부스트 구간만 적용 — 차단/부분차단은 건드리지 않음)
        kelly_adj = 1.0
        if base_mult > 1.0 and tag:
            rec  = self._kelly_mult.get(tag) or _KELLY_DEFAULTS.get(tag, 1.0)
            kelly_adj = rec  # Kelly 권장 자체가 최종 배수
            base_mult = 1.0  # base_mult를 Kelly 권장으로 대체

        # 2. 종목별 승률 가중 (데이터 있을 때만, ±20% 범위 제한)
        sym_adj = 1.0
        if symbol and symbol in self._sym_weight:
            w       = self._sym_weight[symbol]
            sym_adj = max(0.8, min(1.2, 0.6 + w * 0.8))  # weight 0→0.6, 1.0→1.4 선형

        raw_mult = base_mult * kelly_adj * sym_adj

        # 3. DrawdownEngine 레벨별 부스트 상한
        dd_cap   = 1.5
        dd_level = 'NORMAL'
        if self._dd_engine is not None:
            try:
                _dd_mult, dd_level = self._dd_engine.get_size_mult(strategy='smc')
                dd_cap = _DD_BOOST_CAP.get(dd_level, 1.0)
                # DD가 이미 축소를 적용했다면 그것도 반영
                if _dd_mult < 1.0 and raw_mult < 1.0:
                    raw_mult = min(raw_mult, _dd_mult)
            except Exception:
                pass

        final = max(0.1, min(dd_cap, raw_mult))

        if abs(final - base_mult) > 0.01:
            logger.info(
                f"[EDT_SIZER] {symbol} base={base_mult:.2f} "
                f"kelly_adj={kelly_adj:.2f} sym_adj={sym_adj:.2f} "
                f"dd_cap={dd_cap}({dd_level}) → final={final:.3f}"
            )

        return round(final, 3)

    # ── 세션 가드 ─────────────────────────────────────────────────────────

    def check_session_guards(
        self,
        positions: dict,
        time_now=None,
        kospi_change_pct: float = 0.0,
    ) -> Tuple[float, List[str]]:
        """
        4개 세션 가드를 순차 체크하여 (size_adj, reasons) 반환.

        Guard 1 — Halt Recovery  : DD_HALT 다음날 방어 모드 (첫30분 차단 / 이후 ×0.7)
        Guard 2 — Sector Conc.   : 동일 섹터 2개+ 포지션 → ×0.7
        Guard 3 — Heavy Market   : KOSPI ≤ -1.5% → ×0.7
        Guard 4 — Win Overheat   : 당일 누적 +3%+ → ×0.5

        여러 가드 동시 발동 시 size_adj는 곱 적용 (min floor 적용 없음 — 호출자 책임).
        """
        if not self._enabled:
            return 1.0, []

        cfg_g = self._cfg.get('session_guards', {})
        if not cfg_g.get('enabled', True):
            return 1.0, []

        adj = 1.0
        reasons: List[str] = []

        # Guard 1: Halt Recovery
        cfg1 = cfg_g.get('halt_recovery', {})
        if cfg1.get('enabled', True):
            _a, _r = self._check_halt_recovery(time_now, cfg1)
            if _a < 1.0:
                adj *= _a
                reasons.append(_r)

        # Guard 2: Sector Concentration
        cfg2 = cfg_g.get('sector_concentration', {})
        if cfg2.get('enabled', True) and positions:
            _a, _r = self._check_sector_concentration(positions, cfg2)
            if _a < 1.0:
                adj *= _a
                reasons.append(_r)

        # Guard 3: Heavy Market Day
        cfg3 = cfg_g.get('heavy_market', {})
        if cfg3.get('enabled', True):
            _a, _r = self._check_heavy_market(kospi_change_pct, cfg3)
            if _a < 1.0:
                adj *= _a
                reasons.append(_r)

        # Guard 4: Win Overheat
        cfg4 = cfg_g.get('win_overheat', {})
        if cfg4.get('enabled', True):
            _a, _r = self._check_win_overheat(cfg4)
            if _a < 1.0:
                adj *= _a
                reasons.append(_r)

        if reasons:
            logger.info(f"[EDT_GUARD] adj={adj:.2f} | {' | '.join(reasons)}")

        return round(adj, 3), reasons

    def _check_halt_recovery(self, time_now, cfg: dict) -> Tuple[float, str]:
        """Guard 1: DD_HALT 발생 다음 거래일 방어."""
        if self._halt_day is None:
            return 1.0, ''
        today = date.today()
        days_since = (today - self._halt_day).days
        if days_since <= 0:
            # HALT 당일 — DD 상한 0.0이 이미 처리, 여기선 개입 안 함
            return 1.0, ''
        recovery_days = cfg.get('recovery_days', 1)
        if days_since > recovery_days:
            self._halt_day = None   # 회복 완료
            return 1.0, ''
        # 복귀 첫날: 장 시작 후 N분은 완전 차단
        if time_now is not None:
            end_min = cfg.get('defensive_end_min', 30)
            defensive_end = time_now.replace(hour=9, minute=end_min, second=0, microsecond=0)
            if time_now < defensive_end:
                return 0.0, f'HALT_RECOVERY_BLOCK(09:00-09:{end_min:02d} 차단)'
        rec_mult = cfg.get('recovery_size_mult', 0.7)
        return rec_mult, f'HALT_RECOVERY({days_since}일후 ×{rec_mult})'

    def _check_sector_concentration(self, positions: dict, cfg: dict) -> Tuple[float, str]:
        """Guard 2: 동일 섹터 N개+ 포지션 → size 축소."""
        if not self._sector_map:
            return 1.0, ''
        sector_counts: dict = {}
        for code in positions:
            sector = self._sector_map.get(str(code))
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if not sector_counts:
            return 1.0, ''
        max_sector = max(sector_counts, key=sector_counts.get)
        max_count  = sector_counts[max_sector]
        threshold  = cfg.get('max_same_sector', 2)
        if max_count >= threshold:
            adj = cfg.get('size_mult', 0.7)
            return adj, f'SECTOR_CONC({max_sector} {max_count}포지션 ×{adj})'
        return 1.0, ''

    def _check_heavy_market(self, kospi_change_pct: float, cfg: dict) -> Tuple[float, str]:
        """Guard 3: KOSPI 낙폭 과대 → size 축소."""
        threshold = cfg.get('drop_threshold_pct', -1.5)
        if kospi_change_pct <= threshold:
            adj = cfg.get('size_mult', 0.7)
            return adj, f'HEAVY_MKT(KOSPI {kospi_change_pct:+.1f}% ×{adj})'
        return 1.0, ''

    def _check_win_overheat(self, cfg: dict) -> Tuple[float, str]:
        """Guard 4: 당일 누적 수익 과열 → size 축소."""
        threshold = cfg.get('daily_gain_threshold_pct', 3.0)
        if self._daily_realized_pnl >= threshold:
            adj = cfg.get('size_mult', 0.5)
            return adj, f'WIN_OVERHEAT(당일+{self._daily_realized_pnl:.1f}% ×{adj})'
        return 1.0, ''

    # ── PnL 트래킹 + 리셋 ─────────────────────────────────────────────────

    def record_pnl(self, pnl_pct: float):
        """청산 후 DrawdownEngine PnL 갱신 + 세션 가드 추적."""
        # Guard 4: 당일 실현 손익 누적
        self._daily_realized_pnl += pnl_pct

        if self._dd_engine is not None:
            try:
                self._dd_engine.record_pnl(pnl_pct, strategy='smc')
                # Guard 1: HALT 레벨 진입 감지 → 다음날 복귀 방어 예약
                _, dd_level = self._dd_engine.get_size_mult(strategy='smc')
                if dd_level == 'HALT' and self._halt_day != date.today():
                    self._halt_day = date.today()
                    logger.warning('[EDT_GUARD] DD_HALT 감지 → 내일 복귀 방어 모드 예약')
            except Exception:
                pass

    def reset_daily(self):
        """daily_routine 시작 시 호출 — 당일 누적값 초기화 (halt_day는 유지)."""
        self._daily_realized_pnl = 0.0
        # _halt_day는 날짜 비교로 자동 만료 — 리셋하지 않음
        if self._dd_engine is not None:
            try:
                self._dd_engine.reset_daily()
            except Exception:
                pass
