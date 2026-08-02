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
from datetime import date, datetime
from collections import defaultdict


class _DailyFileHandler(logging.Handler):
    """자정 넘겨도 프로세스가 계속 살아있으면 기록 시점 날짜 기준으로 파일을 갈아끼움
    (싱글턴 로거가 프로세스 시작일 파일명으로 고정되던 문제 수정, 2026-07-07)."""

    def __init__(self, path_fmt: str, encoding: str = 'utf-8'):
        super().__init__()
        self._path_fmt = path_fmt
        self._encoding = encoding
        self._current_date = None
        self._fh = None
        self._ensure_today()

    def _ensure_today(self):
        today = date.today()
        if today == self._current_date:
            return
        if self._fh is not None:
            self._fh.close()
        self._current_date = today
        self._fh = logging.FileHandler(
            self._path_fmt.format(date=today.strftime('%Y%m%d')), encoding=self._encoding
        )
        if self.formatter:
            self._fh.setFormatter(self.formatter)

    def setFormatter(self, fmt):
        super().setFormatter(fmt)
        if self._fh:
            self._fh.setFormatter(fmt)

    def emit(self, record):
        self._ensure_today()
        self._fh.emit(record)


class SMCDecisionLogger:
    def __init__(self):
        self._log = logging.getLogger('smc_decision')
        self._log.setLevel(logging.INFO)
        self._log.propagate = False

        if not self._log.handlers:
            _fh = _DailyFileHandler('logs/smc_decision_{date}.log')
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

    def log_no_sig(self, code: str, details: dict, reason: str):
        """SMC 신호 없음 — 종목별 차단 근거를 파일에 기록.

        필드: choch=T/F, bos=T/F, sweep=T/F, prefilter=pass/fail, reason_key
        """
        choch   = 'T' if details.get('choch') else 'F'
        bos     = 'T' if details.get('bos')   else 'F'
        sweep   = 'T' if details.get('liquidity_sweep') else 'F'
        pf      = details.get('prefilter', {})
        pf_met  = pf.get('conditions_met', '?')
        pf_req  = pf.get('min_required', '?')
        htf     = 'T' if pf.get('htf_trend_alive') else 'F'
        reclaim = 'T' if pf.get('reclaim_detected') else 'F'
        rvol    = pf.get('rvol_at_prefilter', '?')
        # reason을 30자로 압축
        reason_short = reason[:80].replace('\n', ' ')
        self._log.info(
            f'[NO_SIG] {code} | choch={choch} bos={bos} sweep={sweep} '
            f'| pf={pf_met}/{pf_req} htf={htf} reclaim={reclaim} rvol={rvol} '
            f'| {reason_short}'
        )
        # 집계에도 추가
        _key = reason_short[:40].strip()
        self._rejects[_key] += 1

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
