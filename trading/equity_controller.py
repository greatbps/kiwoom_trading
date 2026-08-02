"""
trading/equity_controller.py — 멀티데이 에쿼티 커브 기반 리스크 조절 (v3)

DD → size multiplier (5-tier 연속, 회복 자동 반영):
  DD > -2%  → 1.00  (완전 회복)
  DD > -5%  → 0.85  (경계)
  DD > -10% → 0.65  (위험)
  DD > -12% → 0.50  (심각)
  else      → 0.30  (생존 모드)
  DD ≤ -18% → can_enter()=False  (신규 진입 전면 차단)

Confidence Boost (연속 함수, 경계값 점프 없음):
  conf < 0.70  → boost=0 (효과 없음)
  conf = 0.85  → boost=+7.5%
  conf = 1.00  → boost=+15%  (상한 1.0)
  → mult = min(1.0, ec_mult × (1 + (conf-0.70)×0.5))

Pattern Quality Filter (비율 기반, 완전 이진 컷 없음):
  expectancy ≥ 0        → 1.0
  expectancy = -0.2     → max(0.5, 1.0 + (-0.2)) = 0.80
  expectancy = -0.5     → 0.50 (최솟값)
  win_rate < 0.40       → 추가 × 0.80
  avg_loss > avg_win    → 추가 × 0.85

Peak 갱신:
  eod_only_peak=True    → handle_eod()의 update_peak_eod()에서만 확정 갱신
  장중 update_peak()    → 시간 미만이면 무시 (가짜 peak 방지)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

_STATE_PATH   = Path(__file__).parent.parent / 'data' / 'equity_state.json'
_BACKUP_PATH  = Path(__file__).parent.parent / 'data' / 'equity_state.json.bak'
_STATE_TMP    = Path(__file__).parent.parent / 'data' / 'equity_state.json.tmp'
_BACKUP_TMP   = Path(__file__).parent.parent / 'data' / 'equity_state.json.bak.tmp'
_WEIGHTS_PATH = Path(__file__).parent.parent / 'data' / 'pattern_weights.json'

_EOD_TIME = dtime(15, 20)   # 이 시각 이후에만 update_peak() 허용


class EquityController:
    """멀티데이 에쿼티 커브 기반 position size 조절기 (v3)."""

    def __init__(self, config: dict):
        self._cfg  = config.get('equity_control', {})
        self._peak: float = 0.0
        self.state_integrity = 'OK'  # OK | BACKUP_USED | RECOVERED_FROM_DB | RECOVERY_FAILED
        self._load()

    # ── 상태 I/O ──────────────────────────────────────────────────────

    @staticmethod
    def _atomic_write(path: Path, tmp_path: Path, payload: str):
        """임시파일 기록 → fsync → 원자적 rename (프로세스 강제종료 시 손상 파일 방지)."""
        path.parent.mkdir(exist_ok=True)
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # 동일 파일시스템 내 rename은 원자적

    def _recover_peak_from_db(self):
        """[T3] Primary/Backup 모두 손상 시 — account_snapshot 이력에서 peak 재구성 시도."""
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=os.getenv("POSTGRES_HOST", "localhost"),
                dbname=os.getenv("POSTGRES_DB", "trading_system"),
                user=os.getenv("POSTGRES_USER", "postgres"),
                password=os.getenv("POSTGRES_PASSWORD", ""),
            )
            cur = conn.cursor()
            cur.execute("SELECT MAX(total_assets) FROM account_snapshot")
            row = cur.fetchone()
            conn.close()
            if row and row[0] and float(row[0]) > 0:
                return float(row[0])
        except Exception as e:
            logger.debug(f"[EQUITY_CTRL] DB peak 복구 조회 실패: {e}")
        return None

    def _load(self):
        # 1) Primary
        if _STATE_PATH.exists():
            try:
                data = json.loads(_STATE_PATH.read_text(encoding='utf-8'))
                self._peak = float(data.get('peak', 0.0))
                self.state_integrity = 'OK'
                logger.info(f"[EQUITY_CTRL] peak 복원(primary): {self._peak:,.0f}원")
                return
            except Exception as e:
                logger.warning(f"[EQUITY_CTRL] Primary 상태 로드 실패: {e}")

        # 2) Backup
        if _BACKUP_PATH.exists():
            try:
                data = json.loads(_BACKUP_PATH.read_text(encoding='utf-8'))
                self._peak = float(data.get('peak', 0.0))
                self.state_integrity = 'BACKUP_USED'
                logger.warning(f"[EQUITY_CTRL] Primary 손상 → Backup에서 peak 복원: {self._peak:,.0f}원")
                return
            except Exception as e:
                logger.warning(f"[EQUITY_CTRL] Backup 상태 로드도 실패: {e}")

        # 3) Recovery — DB(account_snapshot 이력 최고 자산)로 재구성 시도
        recovered = self._recover_peak_from_db()
        if recovered is not None:
            self._peak = recovered
            self.state_integrity = 'RECOVERED_FROM_DB'
            logger.critical(
                f"[EQUITY_CTRL] Primary/Backup 모두 손상 → DB 이력으로 peak 복구: {self._peak:,.0f}원. "
                f"상태파일 손상 원인 점검 필요"
            )
            self._save()  # 복구된 값을 정상 상태로 즉시 재영속화
            return

        # 4) 완전 실패 — peak=0 유지, Health Check가 반드시 감지해야 함
        self.state_integrity = 'RECOVERY_FAILED'
        logger.critical(
            "[EQUITY_CTRL] Primary/Backup/DB 복구 모두 실패 — peak=0 유지. "
            "드로다운 서킷브레이커 무력화 가능 상태, System Health Check FAIL 확인 필요"
        )

    def _save(self):
        try:
            payload = json.dumps(
                {'peak': self._peak, 'updated_at': datetime.now().isoformat()},
                ensure_ascii=False, indent=2,
            )
            self._atomic_write(_STATE_PATH, _STATE_TMP, payload)
            self._atomic_write(_BACKUP_PATH, _BACKUP_TMP, payload)
        except Exception as e:
            logger.warning(f"[EQUITY_CTRL] 상태 저장 실패: {e}")

    def _do_update_peak(self, equity: float):
        if equity <= 0 or equity <= self._peak:
            return
        prev = self._peak
        self._peak = equity
        self._save()
        if prev > 0:
            logger.info(f"[EQUITY_CTRL] 신고점 갱신: {prev:,.0f} → {equity:,.0f}원")

    # ── Public API ────────────────────────────────────────────────────

    def update_peak(self, equity: float):
        """장중 peak 갱신 (eod_only_peak=True이면 15:20 이전 무시)."""
        if not self._cfg.get('enabled', True):
            return
        if self._cfg.get('eod_only_peak', True) and datetime.now().time() < _EOD_TIME:
            return
        self._do_update_peak(equity)

    def update_peak_eod(self, equity: float):
        """EOD 확정 peak 갱신 (시간 무관). handle_eod()에서 호출."""
        if not self._cfg.get('enabled', True):
            return
        self._do_update_peak(equity)

    def can_enter(self, equity: float) -> Tuple[bool, str]:
        """DD ≤ max_dd_halt(-18%) → 신규 진입 전면 차단."""
        if not self._cfg.get('enabled', True):
            return True, 'disabled'
        # [CBF-1 2026-07-20] Fail Closed: NaN은 예외 없이 비교식을 전부 False로 통과시켜
        # 무음으로 EC_OK(허용) 처리되던 버그 — equity 계산 자체가 무효하므로 명시적 차단.
        if equity != equity:  # NaN 검사 (math.isnan 대신 자기비교 — equity가 None일 수도 있어 안전)
            return False, 'EC_HALT_EXCEPTION: equity=NaN (계산값 무효, Fail Closed)'
        if self._peak <= 0 or equity <= 0:
            return True, 'no_peak'
        halt_pct = self._cfg.get('max_dd_halt', -0.18)
        dd = (equity - self._peak) / self._peak
        if dd <= halt_pct:
            return False, (
                f'EC_HALT: dd={dd:.1%} ≤ {halt_pct:.0%} '
                f'(equity={equity:,.0f} peak={self._peak:,.0f})'
            )
        return True, f'EC_OK: dd={dd:.1%}'

    def get_drawdown_mult(self, equity: float) -> Tuple[float, str]:
        """
        전고점 대비 낙폭 → position_size multiplier (5-tier 연속, 회복 자동 반영).

        반환: (mult, reason_str)
        """
        if not self._cfg.get('enabled', True):
            return 1.0, 'equity_ctrl_disabled'
        if self._peak <= 0 or equity <= 0:
            return 1.0, 'equity_ctrl_no_peak'

        dd = (equity - self._peak) / self._peak

        t = self._cfg.get('drawdown_tiers', {})
        t_buf      = t.get('recovery_buffer', -0.02)
        t5_mult    = t.get('dd_5_mult',       0.85)
        t10_mult   = t.get('dd_10_mult',      0.65)
        t12_mult   = t.get('dd_12_mult',      0.50)
        t_surv     = t.get('survival_mult',   0.30)

        if dd > t_buf:
            mult, tag = 1.0,      'EC_OK'
        elif dd > -0.05:
            mult, tag = t5_mult,  'EC_CAUTION'
        elif dd > -0.10:
            mult, tag = t10_mult, 'EC_DANGER'
        elif dd > -0.12:
            mult, tag = t12_mult, 'EC_DEEP'
        else:
            mult, tag = t_surv,   'EC_SURVIVAL'

        return mult, f'{tag}: dd={dd:.1%} ×{mult}'

    @staticmethod
    def confidence_boost(ec_mult: float, confidence: float) -> float:
        """
        EC mult를 confidence에 비례하여 1.0 방향으로 완화 (연속 함수).

        conf < 0.70  → 효과 없음 (boost=0)
        conf = 0.85  → ×1.075  (선형 보간)
        conf = 1.00  → ×1.150
        상한: 1.0

        Returns: 보정된 ec_mult
        """
        if confidence < 0.70:
            return ec_mult
        boost = 1.0 + (confidence - 0.70) * 0.5   # [1.0, 1.15] 구간
        return min(1.0, ec_mult * boost)

    def get_pattern_quality_mult(self, phase_key: str) -> Tuple[float, str]:
        """
        pattern_weights.json → 비율 기반 expectancy 필터 + 승률/손익비 이중 검사.

        expectancy ≥ 0           → 1.0
        expectancy < 0           → max(0.5, 1.0 + expectancy)  (비율 페널티)
        win_rate < 0.40          → 추가 × 0.80
        avg_loss > avg_win       → 추가 × 0.85

        반환: (mult, reason_str)
        """
        if not self._cfg.get('pattern_quality_filter', True):
            return 1.0, 'pq_disabled'
        if not phase_key or not _WEIGHTS_PATH.exists():
            return 1.0, 'pq_no_data'
        try:
            data  = json.loads(_WEIGHTS_PATH.read_text(encoding='utf-8'))
            entry = data.get('weights', {}).get(phase_key)
            if not isinstance(entry, dict):
                return 1.0, f'pq_no_entry:{phase_key}'

            wr  = float(entry.get('win_rate', 0.5))
            aw  = float(entry.get('avg_win',  0.0))
            al  = abs(float(entry.get('avg_loss', 0.0)))

            if aw == 0.0 and al == 0.0:
                return 1.0, f'pq_no_aw_al:{phase_key}'

            # ① expectancy 비율 페널티
            expectancy = wr * aw - (1.0 - wr) * al
            if expectancy >= 0:
                mult = 1.0
            else:
                mult = max(0.5, 1.0 + expectancy)   # -0.2→0.8, -0.5→0.5

            reasons = [f'E={expectancy:.3f}→×{mult:.2f}']

            # ② win_rate 필터
            if wr < 0.40:
                wf = self._cfg.get('low_winrate_mult', 0.80)
                mult *= wf
                reasons.append(f'WR={wr:.0%}<40%→×{wf}')

            # ③ avg_loss > avg_win 필터
            if al > 0 and aw > 0 and al > aw:
                lf = self._cfg.get('high_loss_mult', 0.85)
                mult *= lf
                reasons.append(f'AL({al:.2f})>AW({aw:.2f})→×{lf}')

            mult = max(0.40, round(mult, 3))  # 절대 하한
            tag  = 'PQ_NEG' if mult < 1.0 else 'PQ_OK'
            return mult, f'{tag}: {phase_key} {" | ".join(reasons)}'

        except Exception as e:
            logger.debug(f"[EQUITY_CTRL] pattern_quality_mult 오류: {e}")
            return 1.0, 'pq_error'

    @property
    def peak(self) -> float:
        return self._peak
