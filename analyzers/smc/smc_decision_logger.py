"""
SMC 결정 로거 — 의사결정 핵심만 기록

출력 파일: logs/smc_decision_YYYYMMDD.log

포맷:
  [CHOCH]  code | direction | level=N | wick=N | close=N | penetration=X%
  [SWEEP]  code | type=penetration|equal_level|filtered | dist=X%
  [ENTRY]  code name | price=N | ob=N~N | grade=A|B
  [REJECT] reason (count)  ← EOD 집계만
  [SUMMARY] CHoCH=N / SWEEP=N (P:N/E:N/F:N) / ENTRY=N
"""
import logging
from datetime import datetime
from collections import defaultdict


class SMCDecisionLogger:
    def __init__(self):
        self._log = logging.getLogger('smc_decision')
        self._log.setLevel(logging.INFO)
        self._log.propagate = False

        if not self._log.handlers:
            _today = datetime.today().strftime('%Y%m%d')
            _fh = logging.FileHandler(
                f'logs/smc_decision_{_today}.log', encoding='utf-8'
            )
            _fh.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%H:%M:%S'))
            self._log.addHandler(_fh)

        # 일일 집계
        self._cnt = defaultdict(int)   # CHoCH, SWEEP_P, SWEEP_E, SWEEP_F, ENTRY
        self._rejects = defaultdict(int)  # reason → count

    # ── 결정 로그 ─────────────────────────────────────────────────

    def log_choch(self, code: str, direction: str, level: float,
                  wick: float, close: float, penetration_pct: float = 0.0):
        self._cnt['CHoCH'] += 1
        self._log.info(
            f'[CHOCH] {code} | {direction} | level={level:.0f} | '
            f'wick={wick:.0f} | close={close:.0f} | penetration={penetration_pct:.2f}%'
        )

    def log_sweep(self, code: str, sweep_type: str, dist_pct: float, reason: str = ''):
        if sweep_type == 'penetration':
            self._cnt['SWEEP_P'] += 1
        elif sweep_type == 'equal_level':
            self._cnt['SWEEP_E'] += 1
        else:
            self._cnt['SWEEP_F'] += 1

        msg = f'[SWEEP] {code} | type={sweep_type} | dist={dist_pct:.2f}%'
        if reason:
            msg += f' | reason={reason}'
        self._log.info(msg)

    def log_entry(self, code: str, name: str, price: float,
                  ob_low: float = 0, ob_high: float = 0,
                  grade: str = '', rr: float = 0):
        self._cnt['ENTRY'] += 1
        msg = f'[ENTRY] {code} {name} | price={price:.0f}'
        if ob_low and ob_high:
            msg += f' | ob={ob_low:.0f}~{ob_high:.0f}'
        if grade:
            msg += f' | grade={grade}'
        if rr:
            msg += f' | RR={rr:.1f}'
        self._log.info(msg)

    def log_reject(self, reason: str):
        """진입 차단 사유 집계 (개별 출력 없음, EOD 요약만)"""
        self._rejects[reason] += 1

    # ── EOD 요약 ──────────────────────────────────────────────────

    def print_daily_summary(self):
        total_sweep = self._cnt['SWEEP_P'] + self._cnt['SWEEP_E'] + self._cnt['SWEEP_F']
        self._log.info('=' * 56)
        self._log.info(f'[SUMMARY] {datetime.today().strftime("%Y-%m-%d")}')
        self._log.info(f'  CHoCH = {self._cnt["CHoCH"]}')
        self._log.info(
            f'  SWEEP = {total_sweep}'
            f' (P:{self._cnt["SWEEP_P"]} / E:{self._cnt["SWEEP_E"]} / F:{self._cnt["SWEEP_F"]})'
        )
        self._log.info(f'  ENTRY = {self._cnt["ENTRY"]}')
        if self._rejects:
            self._log.info('[REJECT]')
            for reason, cnt in sorted(self._rejects.items(), key=lambda x: -x[1]):
                self._log.info(f'  {reason} = {cnt}')
        self._log.info('=' * 56)

    def get_counts(self) -> dict:
        return dict(self._cnt)


# 싱글턴 (프로세스 내 1개)
_instance: SMCDecisionLogger = None


def get_smc_logger() -> SMCDecisionLogger:
    global _instance
    if _instance is None:
        _instance = SMCDecisionLogger()
    return _instance
