"""
Re-entry Cooldown Trigger Report
- 쿨다운 차단 이벤트 수집 및 운영 통계 리포트 생성
- 2026-02-07
- 2026-02-07 v2: check_cooldown_override (squeeze/momentum bypass)
- 2026-02-10: MarketSensor (EF 기반 시장 상태 판별 → 진입 차단)
"""
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Tuple
from collections import Counter

import numpy as np
import pandas as pd


# exit_reason 원본 문자열 → 표준 카테고리 매핑
# 🔧 2026-02-08: EF subtype 우선 매칭 (no_follow, no_demand)
_EXIT_REASON_KEYWORDS = [
    ('ef_no_follow',  ['early failure[no_follow]', 'ef[no_follow]']),
    ('ef_no_demand',  ['early failure[no_demand]', 'ef[no_demand]']),
    ('early_failure', ['early failure', 'early_failure', 'ef ']),
    ('stop_loss',     ['hard stop', 'hard_stop', '손절', '구조 손절', 'structure_stop', 'stop_loss']),
    ('trailing_stop', ['트레일링', 'trailing', 'atr 트레일링']),
    ('time_exit',     ['시간 기반', 'time_exit', '시간기반', '강제청산', '생명주기']),
    ('take_profit',   ['부분익절', 'take_profit', 'squeeze']),
    ('partial_exit',  ['부분청산', 'partial']),
]


def categorize_exit_reason(raw_reason: str) -> str:
    """원본 exit_reason 문자열을 표준 카테고리로 변환"""
    if not raw_reason:
        return 'default'
    lower = raw_reason.lower()
    for category, keywords in _EXIT_REASON_KEYWORDS:
        for kw in keywords:
            if kw in lower:
                return category
    return 'default'


def check_cooldown_override(
    df: pd.DataFrame,
    exit_reason_category: str,
    config: Dict
) -> Tuple[bool, str]:
    """
    쿨다운 Override 체크 — 강한 신호가 쿨다운을 bypass할 수 있는지 판단.

    핵심 원칙: early_failure 후에는 절대 override 불가.

    Args:
        df: OHLCV + 기술적 지표 DataFrame (execute_buy의 df 파라미터)
        exit_reason_category: categorize_exit_reason() 결과 (표준 카테고리)
        config: override_rules config dict

    Returns:
        (can_override, reason_str)
    """
    if not config.get('enabled', False):
        return False, ""

    # 절대 차단 카테고리 체크 (early_failure, ef_no_demand 등)
    blocked_reasons = config.get('blocked_reasons', ['early_failure', 'ef_no_demand'])
    if exit_reason_category in blocked_reasons:
        return False, f"override 불가: {exit_reason_category}"

    if df is None or df.empty or len(df) < 20:
        return False, "데이터 부족"

    # Squeeze Override
    sqz_config = config.get('squeeze_override', {})
    if sqz_config.get('enabled', False):
        can_squeeze, sqz_reason = _check_squeeze_override(df, sqz_config)
        if can_squeeze:
            return True, sqz_reason

    # Momentum Override
    mom_config = config.get('momentum_override', {})
    if mom_config.get('enabled', False):
        can_momentum, mom_reason = _check_momentum_override(df, mom_config)
        if can_momentum:
            return True, mom_reason

    # 🔧 2026-02-08: Close Override (종가 전용)
    close_config = config.get('close_override', {})
    if close_config.get('enabled', False):
        can_close, close_reason = _check_close_override(df, close_config)
        if can_close:
            return True, close_reason

    return False, ""


def _check_squeeze_override(df: pd.DataFrame, config: Dict) -> Tuple[bool, str]:
    """Squeeze Override: BB width percentile ≤ 15, volume ≥ 2.5x, squeeze ON"""
    try:
        close = df['close']
        bb_width_pctile_max = config.get('bb_width_percentile_max', 15)
        vol_mult_min = config.get('volume_mult_min', 2.5)
        require_squeeze = config.get('require_squeeze_on', True)

        # BB width percentile (20-bar window)
        bb_length = 20
        bb_basis = close.rolling(window=bb_length).mean()
        bb_dev = close.rolling(window=bb_length).std()
        bb_width = (bb_dev * 2) / bb_basis  # normalized BB width

        if len(bb_width.dropna()) < bb_length:
            return False, ""

        recent_width = bb_width.iloc[-bb_length:]
        current_rank = (recent_width < bb_width.iloc[-1]).sum()
        percentile = (current_rank / len(recent_width)) * 100

        if percentile > bb_width_pctile_max:
            return False, ""

        # Volume check
        if 'volume' in df.columns and len(df) >= 20:
            current_vol = df['volume'].iloc[-1]
            avg_vol = df['volume'].tail(20).mean()
            if avg_vol <= 0 or current_vol / avg_vol < vol_mult_min:
                return False, ""
        else:
            return False, ""

        # Squeeze ON check
        if require_squeeze:
            from utils.squeeze_momentum_realtime import calculate_squeeze_momentum
            sqz_df = calculate_squeeze_momentum(df)
            if not sqz_df['sqz_on'].iloc[-1]:
                return False, ""

        return True, f"Squeeze Override (BB pctile={percentile:.0f}%, vol={current_vol/avg_vol:.1f}x)"

    except Exception:
        return False, ""


def _check_momentum_override(df: pd.DataFrame, config: Dict) -> Tuple[bool, str]:
    """Momentum Override: ROC(3) ≥ 2.5%, RSI(14) ≥ 65, close > VWAP"""
    try:
        close = df['close']
        roc3_min = config.get('roc3_min_pct', 2.5)
        rsi14_min = config.get('rsi14_min', 65)
        require_vwap = config.get('require_above_vwap', True)

        # ROC(3)
        if len(close) < 4:
            return False, ""
        roc3 = ((close.iloc[-1] / close.iloc[-4]) - 1) * 100
        if roc3 < roc3_min:
            return False, ""

        # RSI(14)
        if 'rsi' in df.columns:
            rsi_val = df['rsi'].iloc[-1]
        else:
            # RSI 직접 계산
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi_val = 100 - (100 / (1 + rs.iloc[-1])) if not np.isnan(rs.iloc[-1]) else 0
        if rsi_val < rsi14_min:
            return False, ""

        # VWAP check
        if require_vwap:
            if 'vwap' in df.columns:
                vwap = df['vwap'].iloc[-1]
                if close.iloc[-1] <= vwap:
                    return False, ""
            else:
                return False, ""

        return True, f"Momentum Override (ROC3={roc3:.1f}%, RSI={rsi_val:.0f})"

    except Exception:
        return False, ""


def _check_close_override(df: pd.DataFrame, config: Dict) -> Tuple[bool, str]:
    """
    🔧 2026-02-08: 종가 전용 Override
    조건: C1(시간 14:30~15:20) + C2(close>VWAP AND close>EMA20) + C3(vol_ratio≥1.5)
    """
    try:
        import logging
        logger = logging.getLogger('close_override')

        now = datetime.now()
        close = df['close']
        current_close = close.iloc[-1]

        # C1: 종가 시간대 체크
        time_start = config.get('time_start', '14:30')
        time_end = config.get('time_end', '15:20')
        h_start, m_start = map(int, time_start.split(':'))
        h_end, m_end = map(int, time_end.split(':'))

        current_minutes = now.hour * 60 + now.minute
        start_minutes = h_start * 60 + m_start
        end_minutes = h_end * 60 + m_end

        if not (start_minutes <= current_minutes <= end_minutes):
            return False, ""

        # C2: 가격 구조 — close > VWAP AND close > EMA20
        above_vwap = True
        vwap_val = None
        if config.get('require_above_vwap', True):
            if 'vwap' in df.columns:
                vwap_val = df['vwap'].iloc[-1]
                if current_close <= vwap_val:
                    above_vwap = False
            else:
                above_vwap = False

        above_ema20 = True
        ema20_val = None
        if config.get('require_above_ema20', True):
            if 'ema20' in df.columns:
                ema20_val = df['ema20'].iloc[-1]
            else:
                ema20_val = close.ewm(span=20, adjust=False).mean().iloc[-1]
            if current_close <= ema20_val:
                above_ema20 = False

        if not above_vwap or not above_ema20:
            logger.debug(
                f"Close Override 미충족: above_vwap={above_vwap}, above_ema20={above_ema20}"
            )
            return False, ""

        # C3: 체결 강도 — 최근 N봉 평균 거래량 / 20봉 평균 >= vol_ratio_min
        vol_ratio_min = config.get('volume_ratio_min', 1.5)
        vol_lookback = config.get('volume_lookback', 5)

        if 'volume' not in df.columns or len(df) < 20:
            return False, ""

        recent_vol = df['volume'].iloc[-vol_lookback:].mean()
        avg_vol_20 = df['volume'].tail(20).mean()

        if avg_vol_20 <= 0:
            return False, ""

        vol_ratio = recent_vol / avg_vol_20
        if vol_ratio < vol_ratio_min:
            logger.debug(
                f"Close Override 거래량 미달: vol_ratio={vol_ratio:.2f} < {vol_ratio_min}"
            )
            return False, ""

        # 모든 조건 충족 — 상세 로그
        detail = (
            f"Close Override (time={now.strftime('%H:%M')}, "
            f"close>VWAP={'Y' if above_vwap else 'N'}, "
            f"close>EMA20={'Y' if above_ema20 else 'N'}, "
            f"vol_ratio={vol_ratio:.2f})"
        )
        logger.info(f"[OVERRIDE] {detail}")

        return True, detail

    except Exception:
        return False, ""


@dataclass
class ReentryBlockedEvent:
    timestamp: datetime
    symbol: str
    symbol_name: str
    direction: str            # 'long' / 'short'
    elapsed_min: float        # 마지막 청산 후 경과 시간
    cooldown_min: int         # 적용된 쿨다운 시간
    is_loss_cooldown: bool    # 손절 쿨다운 여부
    exit_reason: str          # 직전 청산 사유 (EF, Hard Stop 등)


class ReentryMetrics:
    """Re-entry Cooldown 운영 통계 수집기"""

    def __init__(self):
        self.events: List[ReentryBlockedEvent] = []
        self.total_entry_signals: int = 0
        self.override_count: int = 0
        # 🔧 2026-02-08 R2: ef_no_follow 차단 카운터 (override 비율 분모)
        self.ef_no_follow_blocked_count: int = 0
        # 🔧 2026-02-08 R2: 당일 override 비활성화 플래그
        self.override_disabled_today: bool = False
        self.session_date: str = datetime.now().strftime('%Y-%m-%d')

        # 🔧 2026-02-10: Market Sensor (EF 기반 시장 상태 판별)
        self._ms_ef_total: int = 0          # 당일 EF 총 발동
        self._ms_ef_morning: int = 0        # 오전(~12:00) EF 발동
        self._ms_ef_no_follow: int = 0      # no_follow 누적
        self._ms_ef_no_demand: int = 0      # no_demand 누적
        self._ms_afternoon_blocked: bool = False   # 오후 진입 차단
        self._ms_afternoon_blocked_at: str = ''    # 차단 시점
        self._ms_risk_off: bool = False            # Risk OFF Day
        self._ms_risk_off_at: str = ''             # Risk OFF 선언 시점

    def record_entry_signal(self):
        """진입 시도 카운트 (쿨다운 체크 직전)"""
        self.total_entry_signals += 1

    def record_blocked(self, event: ReentryBlockedEvent):
        """쿨다운 차단 이벤트 기록"""
        self.events.append(event)
        # 🔧 R2: ef_no_follow 차단 시 카운트 (override 전에 호출되어야 함)
        reason_cat = categorize_exit_reason(event.exit_reason)
        if reason_cat == 'ef_no_follow':
            self.ef_no_follow_blocked_count += 1

    def record_override(self):
        """쿨다운 Override 성공 카운트"""
        self.override_count += 1

    # ─── 🔧 2026-02-10: Market Sensor ───

    def record_ef_event(self, ef_subtype: str, config: Dict) -> Dict:
        """
        EF 발동 이벤트 기록 + 시장 상태 업데이트.

        Args:
            ef_subtype: 'no_follow' | 'no_demand'
            config: market_sensor config dict

        Returns:
            dict with keys: afternoon_blocked, risk_off, message
        """
        import logging
        logger = logging.getLogger('market_sensor')

        if not config.get('enabled', False):
            return {'afternoon_blocked': False, 'risk_off': False, 'message': ''}

        now = datetime.now()
        now_str = now.strftime('%H:%M')
        self._ms_ef_total += 1

        if ef_subtype == 'no_follow':
            self._ms_ef_no_follow += 1
        elif ef_subtype == 'no_demand':
            self._ms_ef_no_demand += 1

        # 오전 EF 카운트 (morning_cutoff 기준)
        morning_cutoff = config.get('morning_cutoff', '12:00')
        h_cut, m_cut = map(int, morning_cutoff.split(':'))
        cutoff_minutes = h_cut * 60 + m_cut
        current_minutes = now.hour * 60 + now.minute

        messages = []

        if current_minutes < cutoff_minutes:
            self._ms_ef_morning += 1

            # 규칙 ①: 오전 EF N회 → 오후 진입 차단
            morning_limit = config.get('morning_ef_limit', 2)
            if not self._ms_afternoon_blocked and self._ms_ef_morning >= morning_limit:
                self._ms_afternoon_blocked = True
                self._ms_afternoon_blocked_at = now_str
                msg = (
                    f"[MARKET_SENSOR] 오전 EF {self._ms_ef_morning}회 → "
                    f"오후 진입 차단 ({now_str})"
                )
                logger.warning(msg)
                messages.append(msg)

        # 규칙 ②: no_follow N회 → Risk OFF Day
        risk_off_limit = config.get('risk_off_no_follow_limit', 3)
        if not self._ms_risk_off and self._ms_ef_no_follow >= risk_off_limit:
            self._ms_risk_off = True
            self._ms_risk_off_at = now_str
            msg = (
                f"[MARKET_SENSOR] no_follow {self._ms_ef_no_follow}회 누적 → "
                f"RISK OFF DAY 선언 ({now_str})"
            )
            logger.warning(msg)
            messages.append(msg)

        return {
            'afternoon_blocked': self._ms_afternoon_blocked,
            'risk_off': self._ms_risk_off,
            'message': ' | '.join(messages) if messages else '',
        }

    def can_enter_trade(self, config: Dict) -> Tuple[bool, str]:
        """
        Market Sensor 기반 진입 가능 여부 판단.

        Returns:
            (can_enter, reason)
        """
        if not config.get('enabled', False):
            return True, ''

        now = datetime.now()

        # Risk OFF Day → 모든 신규 진입 차단
        if self._ms_risk_off:
            return False, (
                f"RISK_OFF_DAY (no_follow {self._ms_ef_no_follow}회, "
                f"{self._ms_risk_off_at} 선언)"
            )

        # 오후 차단 체크
        if self._ms_afternoon_blocked:
            morning_cutoff = config.get('morning_cutoff', '12:00')
            h_cut, m_cut = map(int, morning_cutoff.split(':'))
            cutoff_minutes = h_cut * 60 + m_cut
            current_minutes = now.hour * 60 + now.minute

            if current_minutes >= cutoff_minutes:
                return False, (
                    f"AFTERNOON_BLOCKED (오전 EF {self._ms_ef_morning}회, "
                    f"{self._ms_afternoon_blocked_at} 차단)"
                )

        return True, ''

    def get_market_sensor_status(self) -> Dict:
        """Market Sensor 상태 요약 (리포트용)"""
        return {
            'ef_total': self._ms_ef_total,
            'ef_morning': self._ms_ef_morning,
            'ef_no_follow': self._ms_ef_no_follow,
            'ef_no_demand': self._ms_ef_no_demand,
            'afternoon_blocked': self._ms_afternoon_blocked,
            'afternoon_blocked_at': self._ms_afternoon_blocked_at,
            'risk_off': self._ms_risk_off,
            'risk_off_at': self._ms_risk_off_at,
        }

    def check_override_abuse(self, config: Dict) -> Tuple[bool, str]:
        """
        🔧 2026-02-08 R2: Override 남용 방지 체크.
        override_count / ef_no_follow_blocked_count > 상한 → 당일 비활성화.

        Returns:
            (is_abused, message)
        """
        if not config.get('enabled', False):
            return False, ""

        if self.override_disabled_today:
            return True, "당일 override 이미 비활성화됨"

        max_ratio_pct = config.get('max_override_ratio_pct', 30)
        min_samples = config.get('min_samples', 3)

        if self.ef_no_follow_blocked_count < min_samples:
            return False, ""

        current_ratio = (self.override_count / self.ef_no_follow_blocked_count) * 100

        if current_ratio > max_ratio_pct:
            action = config.get('action', 'disable_today')
            if action == 'disable_today':
                self.override_disabled_today = True
                return True, (
                    f"override 남용 감지: {self.override_count}/{self.ef_no_follow_blocked_count} "
                    f"= {current_ratio:.0f}% > {max_ratio_pct}% → 당일 override 비활성화"
                )
            else:  # warn_only
                return False, (
                    f"override 비율 경고: {self.override_count}/{self.ef_no_follow_blocked_count} "
                    f"= {current_ratio:.0f}% > {max_ratio_pct}%"
                )

        return False, ""

    def generate_report(self) -> Dict:
        """6개 핵심 지표 리포트 생성"""
        blocked = len(self.events)
        total = self.total_entry_signals

        # ① Re-entry Block Count
        block_count = blocked

        # ② Re-entry Block Ratio
        block_ratio = (blocked / total * 100) if total > 0 else 0.0

        # ③ Cooldown Avg Elapsed (차단 시점 평균 경과시간)
        avg_elapsed = 0.0
        if blocked > 0:
            avg_elapsed = sum(e.elapsed_min for e in self.events) / blocked

        # ④ Loss Cooldown Ratio (손절 쿨다운 비율)
        loss_cooldown_count = sum(1 for e in self.events if e.is_loss_cooldown)
        loss_cooldown_ratio = (loss_cooldown_count / blocked * 100) if blocked > 0 else 0.0

        # ⑤ Top Blocked Symbols (종목별 차단 빈도)
        symbol_counter = Counter(
            (e.symbol, e.symbol_name) for e in self.events
        )
        top_blocked = [
            (name, count) for (_, name), count in symbol_counter.most_common(5)
        ]

        # ⑥ EF-triggered Cooldown Count
        ef_count = sum(
            1 for e in self.events
            if 'EARLY_FAILURE' in e.exit_reason.upper()
        )

        # ⑦ Exit Reason별 차단 분포
        reason_counter = Counter(
            categorize_exit_reason(e.exit_reason) for e in self.events
        )
        blocked_by_reason = dict(reason_counter.most_common())

        # 상태 판단
        ratio = blocked / total if total > 0 else 0
        if ratio <= 0.25:
            status = "건강"
            status_icon = "V"
        elif ratio <= 0.35:
            status = "주의 (진입 과다 가능성)"
            status_icon = "!"
        else:
            status = "경고 (SMC 신호 과잉 or EF 민감도 과도)"
            status_icon = "X"

        # 🔧 2026-02-08 R1: EF Subtype Ratio Drift 감시
        ef_no_follow_count = blocked_by_reason.get('ef_no_follow', 0)
        ef_no_demand_count = blocked_by_reason.get('ef_no_demand', 0)
        ef_unclassified_count = blocked_by_reason.get('early_failure', 0)
        ef_total = ef_no_follow_count + ef_no_demand_count + ef_unclassified_count

        if ef_total >= 5:
            no_demand_ratio = (ef_no_demand_count / ef_total) * 100
            no_follow_ratio = (ef_no_follow_count / ef_total) * 100
            unclassified_ratio = (ef_unclassified_count / ef_total) * 100

            if no_demand_ratio > 60:
                ef_drift_level = 'CRITICAL'
            elif no_demand_ratio > 40:
                ef_drift_level = 'WARN'
            else:
                ef_drift_level = 'OK'
        else:
            no_demand_ratio = 0.0
            no_follow_ratio = 0.0
            unclassified_ratio = 0.0
            ef_drift_level = 'N/A'  # 샘플 부족

        ef_subtype_ratio = {
            'ef_total': ef_total,
            'ef_no_follow': ef_no_follow_count,
            'ef_no_demand': ef_no_demand_count,
            'ef_unclassified': ef_unclassified_count,
            'no_demand_ratio_pct': round(no_demand_ratio, 1),
            'no_follow_ratio_pct': round(no_follow_ratio, 1),
            'unclassified_ratio_pct': round(unclassified_ratio, 1),
            'drift_level': ef_drift_level,
        }

        # 🔧 2026-02-08 R2: Override 남용 비율
        override_ratio_pct = 0.0
        if self.ef_no_follow_blocked_count >= 3:
            override_ratio_pct = (self.override_count / self.ef_no_follow_blocked_count) * 100

        return {
            'date': self.session_date,
            'total_entry_signals': total,
            'reentry_blocked_count': block_count,
            'blocked_ratio_pct': round(block_ratio, 1),
            'avg_elapsed_min': round(avg_elapsed, 1),
            'loss_cooldown_count': loss_cooldown_count,
            'loss_cooldown_ratio_pct': round(loss_cooldown_ratio, 1),
            'ef_triggered_count': ef_count,
            'override_count': self.override_count,
            'override_ratio_pct': round(override_ratio_pct, 1),
            'override_disabled_today': self.override_disabled_today,
            'top_blocked_symbols': top_blocked,
            'blocked_by_reason': blocked_by_reason,
            'ef_subtype_ratio': ef_subtype_ratio,
            # 🔧 2026-02-10: Market Sensor 상태
            'market_sensor': self.get_market_sensor_status(),
            'status': status,
            'status_icon': status_icon,
        }

    def print_report(self):
        """콘솔 출력"""
        report = self.generate_report()

        print()
        print(f"[REENTRY COOLDOWN REPORT - {report['date']}]")
        print("=" * 50)
        print(f"  Total Entry Signals     : {report['total_entry_signals']}")
        print(f"  Re-entry Blocked        : {report['reentry_blocked_count']}")
        print(f"  Blocked Ratio           : {report['blocked_ratio_pct']}%  [{report['status_icon']}] {report['status']}")
        print(f"  Avg Elapsed (Blocked)   : {report['avg_elapsed_min']} min")
        print(f"  Loss Cooldown Ratio     : {report['loss_cooldown_ratio_pct']}% ({report['loss_cooldown_count']}/{report['reentry_blocked_count']})")
        print(f"  EF-triggered Cooldowns  : {report['ef_triggered_count']}")
        print(f"  Cooldown Overrides      : {report['override_count']}")
        if report['override_ratio_pct'] > 0:
            print(f"  Override Ratio          : {report['override_ratio_pct']}% "
                  f"({self.override_count}/{self.ef_no_follow_blocked_count})"
                  f"{'  [DISABLED]' if report['override_disabled_today'] else ''}")
        print()

        # 🔧 R1: EF Subtype Ratio Drift 경고
        esr = report.get('ef_subtype_ratio', {})
        if esr.get('ef_total', 0) >= 5:
            drift = esr['drift_level']
            nd_ratio = esr['no_demand_ratio_pct']
            nf_ratio = esr['no_follow_ratio_pct']

            if drift == 'CRITICAL':
                print(f"  [CRITICAL] EF Subtype Drift: no_demand={nd_ratio}% > 60%")
                print(f"             진입 신호 품질 심각 저하 — ef_sensitivity_analyzer --days 7 실행 권장")
            elif drift == 'WARN':
                print(f"  [WARN] EF Subtype Drift: no_demand={nd_ratio}% > 40%")
                print(f"         가짜 신호 비율 증가 중 — 3일 연속 시 파라미터 점검 필요")
            else:
                print(f"  [OK] EF Subtype: no_follow={nf_ratio}% / no_demand={nd_ratio}%")

            if esr.get('unclassified_ratio_pct', 0) > 20:
                print(f"  [INFO] EF 미분류 비율 {esr['unclassified_ratio_pct']}% — 분류 커버리지 점검 필요")
            print()

        if report['blocked_by_reason']:
            print("  Blocked by Exit Reason:")
            for reason, count in report['blocked_by_reason'].items():
                pct = (count / report['reentry_blocked_count'] * 100) if report['reentry_blocked_count'] > 0 else 0
                print(f"   - {reason}: {count} ({pct:.0f}%)")
            print()

        if report['top_blocked_symbols']:
            print("  Top Blocked Symbols:")
            for i, (name, count) in enumerate(report['top_blocked_symbols'], 1):
                print(f"   {i}. {name} ({count})")
            print()

        # 🔧 2026-02-10: Market Sensor Status
        ms = report.get('market_sensor', {})
        if ms.get('ef_total', 0) > 0:
            print("  [MARKET SENSOR]")
            print(f"    EF Total       : {ms['ef_total']} (morning={ms['ef_morning']})")
            print(f"    no_follow      : {ms['ef_no_follow']} / no_demand: {ms['ef_no_demand']}")
            if ms.get('afternoon_blocked'):
                print(f"    Afternoon Block: ON ({ms['afternoon_blocked_at']})")
            else:
                print(f"    Afternoon Block: OFF")
            if ms.get('risk_off'):
                print(f"    Risk OFF Day   : ON ({ms['risk_off_at']})")
            else:
                print(f"    Risk OFF Day   : OFF")
            print()

        print("=" * 50)
        print()

    def save_daily(self, log_dir: str = "logs"):
        """일일 데이터 JSON 저장"""
        os.makedirs(log_dir, exist_ok=True)

        report = self.generate_report()

        # 이벤트 상세 데이터 추가
        events_data = []
        for e in self.events:
            events_data.append({
                'timestamp': e.timestamp.isoformat(),
                'symbol': e.symbol,
                'symbol_name': e.symbol_name,
                'direction': e.direction,
                'elapsed_min': round(e.elapsed_min, 1),
                'cooldown_min': e.cooldown_min,
                'is_loss_cooldown': e.is_loss_cooldown,
                'exit_reason': e.exit_reason,
                'exit_reason_category': categorize_exit_reason(e.exit_reason),
            })

        report['events'] = events_data

        filepath = os.path.join(log_dir, f"reentry_report_{self.session_date}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"  [Reentry Report] saved: {filepath}")
