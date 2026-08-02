"""
System Health Check v1.0 — Fail Silent → Fail Visible

이번 주 시스템 감리(docs/SYSTEM_AUDIT_2026-07-20.md)에서 발견된 결함들
(EC_HALT 계산오류/Regime DB기록누락/SCORE_ENGINE 입력누락/daily_scan 미실행/
Exit DB선기록/equity_controller 상태파일손상 가능/로그 이중출력)의 공통점은
"기능은 고장났지만 시스템은 정상처럼 계속 동작했다"는 것 — Fail Silent였다.

이 스크립트는 그 반대: 매일 자동으로 같은 계열의 잠복 결함을 하루 이내에
발견하기 위한 읽기 전용(Read Only) 진단 도구다.

절대 원칙:
    - 전략/매매판단/점수계산/Entry/Exit 로직에 관여하지 않는다.
    - 읽기(Read) → 검사(Check) → 보고(Report)만 수행한다.
    - 이상 발견 시에도 스스로 아무것도 고치거나 차단하지 않는다 (보고만).

실행:
    python3 -m analysis.system_health_check                  # 시각 기준 자동 모드 판단
    python3 -m analysis.system_health_check --mode pre        # 장전(08:55) 관점
    python3 -m analysis.system_health_check --mode post       # 장후(16:05) 관점
    python3 -m analysis.system_health_check --date 2026-07-17 --mode post
    python3 -m analysis.system_health_check --json

크론(등록 예정):
    55 8  * * 1-5  python3 -m analysis.system_health_check --mode pre  >> logs/system_health_check.log 2>&1
    5  16 * * 1-5  python3 -m analysis.system_health_check --mode post >> logs/system_health_check.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

import psycopg2

PASS, WARN, FAIL = 'PASS', 'WARN', 'FAIL'
CRITICAL, MAJOR, MINOR = 'Critical', 'Major', 'Minor'


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _log_path(d: date) -> Path:
    return BASE / 'logs' / f'auto_trading_{d.strftime("%Y%m%d")}.log'


def _read_log(d: date) -> List[str]:
    p = _log_path(d)
    if not p.exists():
        return []
    try:
        with open(p, encoding='utf-8', errors='replace') as f:
            return f.readlines()
    except Exception:
        return []


def _distinct_symbols_for_tag(lines: List[str], tag: str, code_group: int = 1) -> set:
    """태그별 등장 종목코드를 distinct set으로 추출 (로그 이중출력 영향 제거)."""
    pat = re.compile(rf'\[{re.escape(tag)}\][^0-9]*?(\d{{6}})')
    out = set()
    for line in lines:
        if f'[{tag}]' not in line:
            continue
        m = pat.search(line)
        if m:
            out.add(m.group(1))
    return out


def _result(status: str, detail: str, severity: Optional[str] = None, lines: Optional[List[str]] = None) -> Dict[str, Any]:
    return {'status': status, 'severity': severity, 'detail': detail, 'lines': lines or []}


# ─── 1. daily_scan ─────────────────────────────────────────────────

def check_daily_scan(target_date: date) -> Dict[str, Any]:
    """
    [2026-07-20 수정] daily_watchlist.json.scan_date만 보고 판정하면 오탐(False
    Positive) 발생 — daily_scan.py는 BUY 신호가 있을 때만 이 파일을 갱신하는
    설계라("신호 없음 → 파일 저장 안 함", backtest/daily_scan.py:110-115),
    정상적으로 실행됐지만 신호가 0건인 날에도 "미실행"으로 오판정했었다.
    "파일이 갱신되지 않았다" ≠ "daily_scan이 실행되지 않았다".

    이제는 실행 여부 자체는 logs/daily_scan.log의 오늘자 실행 마커로 판정하고,
    BUY 신호 생성 여부/watchlist 갱신 여부는 별도 항목으로 분리해서 보고한다.
    daily_scan/SCORE_ENGINE/Watchlist 로직 자체는 건드리지 않음 — Health Check만 수정.
    """
    log_path = BASE / 'logs' / 'daily_scan.log'
    today_str = target_date.isoformat()

    if not log_path.exists():
        return _result(FAIL, 'daily_scan.log 자체가 없음 — daily_scan 미실행 의심', CRITICAL)
    try:
        text = log_path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return _result(FAIL, f'daily_scan.log 읽기 실패: {e}', CRITICAL)

    run_marker = re.compile(rf'일봉 SMC 스캔\s*\(기준:\s*{re.escape(today_str)}\)')
    matches = list(run_marker.finditer(text))
    if not matches:
        mtime = datetime.fromtimestamp(log_path.stat().st_mtime).isoformat()
        return _result(
            FAIL, f'오늘({today_str}) 실행 로그 없음 — daily_scan 미실행 의심', CRITICAL,
            [f'daily_scan.log 마지막 수정: {mtime}'],
        )

    # 오늘자 마지막 실행 블록만 추출 (다음 실행 시작 마커 전까지, 또는 파일 끝까지)
    start = matches[-1].start()
    next_run = run_marker.search(text, pos=matches[-1].end())
    block = text[start: next_run.start() if next_run else len(text)]

    buy_m = re.search(r'결과:\s*(\d+)개\s*종목\s*BUY\s*신호', block)
    buy_count = int(buy_m.group(1)) if buy_m else None
    watchlist_updated = bool(buy_count)  # 설계상 신호>0일 때만 저장됨
    pattern_done = '패턴 저장' in block
    completed_at = datetime.fromtimestamp(log_path.stat().st_mtime).strftime('%H:%M:%S')

    lines = [
        f'daily_scan execution : PASS (기준={today_str})',
        f'scan completed       : {completed_at}',
        f'BUY candidates       : {buy_count if buy_count is not None else "파싱 실패"}',
        f'watchlist updated    : {"YES" if watchlist_updated else "NO (정상 — 신호 0건이면 미저장 설계)"}',
        f'pattern scan         : {"완료" if pattern_done else "미완료/확인 불가"}',
    ]

    if buy_count is None:
        return _result(WARN, '오늘 실행 확인됐으나 BUY 신호 수 파싱 실패 (로그 포맷 변경 가능성)', MINOR, lines)
    return _result(PASS, f'daily_scan 오늘 실행 확인, BUY 신호 {buy_count}건', None, lines)


# ─── 2. SCORE_ENGINE ───────────────────────────────────────────────

_SCORE_INPUT_RE = re.compile(
    r'\[SCORE_INPUT\] watchlist=(\d+) ohlcv_loaded=(\d+) missing=(\d+)'
)
_SCORE_TOP5_RE = re.compile(r"\[SCORE_ENGINE\] top5: (\[.*\])")


def _score_input_for_date(d: date) -> Optional[Dict[str, int]]:
    lines = _read_log(d)
    for line in lines:
        m = _SCORE_INPUT_RE.search(line)
        if m:
            return {'watchlist': int(m.group(1)), 'ohlcv_loaded': int(m.group(2)), 'missing': int(m.group(3))}
    return None


def _top5_all_zero_for_date(d: date) -> Optional[bool]:
    lines = _read_log(d)
    for line in lines:
        m = _SCORE_TOP5_RE.search(line)
        if m:
            try:
                top5 = eval(m.group(1), {'__builtins__': {}})  # 로그가 만든 안전한 리터럴 리스트
                return all(float(score) == 0.0 for _, score in top5)
            except Exception:
                return None
    return None


def check_score_engine(target_date: date) -> Dict[str, Any]:
    si = _score_input_for_date(target_date)
    if si is None:
        return _result(WARN, '[SCORE_INPUT] 로그 없음 (아직 실행 전이거나 구버전 코드)', None, [])

    lines = [f"watchlist={si['watchlist']} ohlcv_loaded={si['ohlcv_loaded']} missing={si['missing']}"]

    if si['watchlist'] != si['ohlcv_loaded']:
        return _result(
            FAIL,
            f"watchlist({si['watchlist']}) != ohlcv_loaded({si['ohlcv_loaded']}) — 입력 누락",
            CRITICAL, lines,
        )

    # 5거래일 연속 top5 전부 0.0 인지 확인
    zero_days = 0
    checked_days = 0
    d = target_date
    while checked_days < 5:
        z = _top5_all_zero_for_date(d)
        if z is not None:
            checked_days += 1
            if z:
                zero_days += 1
        # 하루 전으로 (주말 스킵은 로그파일 존재여부로 자연히 처리됨)
        d = date.fromordinal(d.toordinal() - 1)
        if checked_days >= 5 or (target_date.toordinal() - d.toordinal()) > 10:
            break

    lines.append(f'최근 {checked_days}거래일 중 top5 전부 0.0인 날: {zero_days}일')
    if checked_days >= 5 and zero_days >= 5:
        return _result(WARN, '5거래일 연속 top5 점수 전부 0.0 (daily_scan 등 상류 확인 필요)', None, lines)
    return _result(PASS, 'watchlist=ohlcv_loaded 일치, 점수 정상 분포', None, lines)


# ─── 3. Candidate Pipeline ─────────────────────────────────────────

def check_candidate_pipeline(target_date: date, mode: str) -> Dict[str, Any]:
    lines_raw = _read_log(target_date)
    if not lines_raw:
        if mode == 'pre':
            return _result(PASS, '오늘 로그 없음 (장전 — 정상)', None, [])
        return _result(WARN, '오늘 로그 파일이 없음', None, [])

    wl = _score_input_for_date(target_date)
    n_watchlist = wl['watchlist'] if wl else None

    n_pre = len(_distinct_symbols_for_tag(lines_raw, 'PRE_CANDIDATE'))
    n_signal = len(_distinct_symbols_for_tag(lines_raw, 'SIGNAL_PIPELINE'))
    n_accept = len(_distinct_symbols_for_tag(lines_raw, 'CANDIDATE_ACCEPT'))

    funnel = f"watchlist={n_watchlist if n_watchlist is not None else '?'} → pre_candidate={n_pre} → signal={n_signal} → accept={n_accept}"
    detail_lines = [funnel, '(distinct 종목코드 기준 — 로그 이중출력 영향 제거)']

    stages = [n_pre, n_signal, n_accept]
    # "이미 0이던 게 계속 0"은 정상(시장이 원래 조용함) — "이전 단계는 있었는데 다음 단계가 갑자기 0"만 이상 신호
    for i in range(1, len(stages)):
        if stages[i - 1] > 0 and stages[i] == 0:
            return _result(WARN, f'{funnel} — 중간 단계가 갑자기 0으로 끊김', None, detail_lines)

    if mode == 'pre':
        return _result(PASS, funnel + ' (장전 — 참고용)', None, detail_lines)
    return _result(PASS, funnel, None, detail_lines)


# ─── 4. Global Gate ────────────────────────────────────────────────

def check_global_gate(target_date: date, mode: str) -> Dict[str, Any]:
    try:
        from analysis.gate_health_check import run_gate_health_check, _log_tag_counts
        report = run_gate_health_check(target_date)
    except Exception as e:
        return _result(WARN, f'gate_health_check 호출 실패: {e}', None, [])

    cand = report['candidates']
    bd = report['gate_breakdown']
    ec_halt = bd.get('EC_HALT', 0)
    lines = [
        f"Candidate={cand}",
        f"EC_HALT={ec_halt}",
        f"DAILY_LOSS={bd.get('DAILY_LOSS', 0)}",
        f"DD_HALT={bd.get('DD_HALT', 0)}",
    ]

    if mode == 'pre' or cand == 0:
        lines.append('(장전 또는 후보 0건 — 비율 판정 보류)')
        return _result(PASS, 'Candidate 0건 (장전/데이터없음)', None, lines)

    if ec_halt == cand:
        return _result(FAIL, f'EC_HALT이 오늘 Candidate {cand}건 전부 차단(100%)', CRITICAL, lines)

    try:
        regime_block_today = _log_tag_counts(target_date).get('regime_block', 0)
    except Exception:
        regime_block_today = 0
    regime_pct_today = (regime_block_today / max(1, len(_distinct_symbols_for_tag(_read_log(target_date), 'PRE_CANDIDATE')) or cand))
    lines.append(f'REGIME_BLOCK(로그, 중복포함 원시건수)={regime_block_today}')

    # 최근 5거래일 REGIME 100% 연속 여부 (decision_ledger 기준, 중복영향 없음)
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT decided_at::date FROM research.decision_ledger
            WHERE decided_at::date <= %s ORDER BY 1 DESC LIMIT 5
        """, (str(target_date),))
        recent_days = [r[0] for r in cur.fetchall()]
        all_100 = True
        for d in recent_days:
            cur.execute("SELECT COUNT(*) FROM research.candidates WHERE observed_at::date=%s", (str(d),))
            c = cur.fetchone()[0]
            cur.execute("""
                SELECT COUNT(*) FROM research.decision_ledger
                WHERE decided_at::date=%s AND decision_reason_code IN ('REGIME_BLOCKED','OTHER')
            """, (str(d),))
            r = cur.fetchone()[0]
            if c == 0 or r < c:
                all_100 = False
                break
        conn.close()
        if all_100 and len(recent_days) >= 5:
            lines.append(f'최근 5거래일({[str(d) for d in recent_days]}) REGIME 100% 차단 연속')
            return _result(WARN, 'REGIME이 5거래일 연속 100% 차단 (시장상황일 수 있음, 참고용)', None, lines)
    except Exception as e:
        lines.append(f'(5일 연속 REGIME 체크 실패: {e})')

    return _result(PASS, f'EC_HALT={ec_halt}/{cand}, 정상 범위', None, lines)


# ─── 5. Exit 검증 ──────────────────────────────────────────────────

def check_exit_sync(target_date: date, mode: str) -> Dict[str, Any]:
    if mode == 'pre':
        return _result(PASS, '장전 — 검증 대상 없음', None, [])
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trades WHERE trade_type='SELL' AND trade_time::date=%s", (str(target_date),))
        trade_db_sells = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM research.decision_ledger
            WHERE decided_at::date=%s AND lifecycle_status IN ('OUTCOME_RECORDED','OUTCOME_PENDING')
        """, (str(target_date),))
        ledger_exits = cur.fetchone()[0]
        conn.close()
    except Exception as e:
        return _result(WARN, f'DB 조회 실패: {e}', None, [])

    lines = [f'Trade DB SELL={trade_db_sells}', f'Decision Ledger 청산기록={ledger_exits}']
    if trade_db_sells != ledger_exits:
        return _result(
            FAIL,
            f'Trade DB({trade_db_sells}) != Decision Ledger({ledger_exits}) 청산건수 불일치',
            CRITICAL, lines,
        )
    return _result(PASS, f'Trade DB=Ledger={trade_db_sells}건 일치', None, lines)


# ─── 6. Equity Controller ──────────────────────────────────────────

def check_equity_controller(target_date: date, mode: str) -> Dict[str, Any]:
    state_path = BASE / 'data' / 'equity_state.json'
    if not state_path.exists():
        return _result(FAIL, 'equity_state.json 없음', CRITICAL, [])
    try:
        data = json.loads(state_path.read_text(encoding='utf-8'))
    except Exception as e:
        return _result(FAIL, f'상태파일 로드 실패(손상 의심): {e}', CRITICAL, ['복구 필요: .bak_* 백업본 확인 또는 최근 검증된 peak으로 수동 복원'])

    peak = float(data.get('peak', 0) or 0)
    lines = [f"peak={peak:,.0f}", f"updated_at={data.get('updated_at', '?')}"]

    if peak <= 0:
        return _result(FAIL, f'peak={peak} — no_peak 상태, 드로다운 서킷브레이커 무력화 가능', CRITICAL, lines)

    # 현재 자산(DB 스냅샷) 대비 DD 계산
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT total_assets, snapshot_at FROM account_snapshot ORDER BY snapshot_at DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            current = float(row[0])
            dd = (current - peak) / peak
            lines.append(f'current={current:,.0f} (snapshot {row[1]})')
            lines.append(f'drawdown={dd:+.2%}')
            tier = ('EC_OK' if dd > -0.02 else 'EC_CAUTION' if dd > -0.05 else
                    'EC_DANGER' if dd > -0.10 else 'EC_DEEP' if dd > -0.12 else 'EC_SURVIVAL')
            lines.append(f'tier={tier}' + (' (복구모드/축소운영 중)' if tier != 'EC_OK' else ''))
    except Exception as e:
        lines.append(f'(현재자산 비교 실패: {e})')

    return _result(PASS, f'peak={peak:,.0f}원 정상', None, lines)


# ─── 7. Scheduler ──────────────────────────────────────────────────

_SCHEDULER_JOBS = {
    'daily_scan':    ('logs/daily_scan.log', None),
    'watchlist':     ('data/watchlist.json', None),
    'market_open':   ('logs/watchdog.log', '08:45'),
    'swing_runner':  ('logs/swing_runner.log', None),
    'health_check':  ('logs/system_health_check.log', None),
    'weekly_review': ('logs/weekly_review_log.csv', None),
}


def check_scheduler(target_date: date, mode: str) -> Dict[str, Any]:
    today_str = target_date.isoformat()
    lines = []
    missing = []
    for job, (relpath, _hint) in _SCHEDULER_JOBS.items():
        p = BASE / relpath
        ran_today = False
        if p.exists():
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime).date()
                ran_today = (mtime == target_date)
                if not ran_today:
                    # 파일 내용에 오늘 날짜가 있는지 한 번 더 확인 (append형 로그 대응)
                    txt = p.read_text(encoding='utf-8', errors='replace')[-20000:]
                    ran_today = today_str in txt or today_str.replace('-', '') in txt
            except Exception:
                pass
        status_str = 'OK' if ran_today else 'MISSING'
        lines.append(f'{job:<14}: {status_str}')
        # weekly_review는 주 1회라 매일 FAIL 취급하지 않음, swing_runner/daily_scan은
        # 장전(pre) 시점엔 아직 안 돌았을 수 있는 것도 있어 mode로 완화
        if not ran_today:
            if job == 'weekly_review':
                continue
            if mode == 'pre' and job in ('swing_runner',):
                continue  # swing_runner는 15:35 실행이라 장전엔 당연히 없음(전날 실행분 봐야함, 완화)
            missing.append(job)

    if missing:
        return _result(FAIL, f'오늘 미실행 작업: {missing}', MAJOR, lines)
    return _result(PASS, '오늘 예정 작업 전부 실행 확인', None, lines)


# ─── 8. Exception Audit ────────────────────────────────────────────

def check_exception_audit(target_date: date) -> Dict[str, Any]:
    lines_raw = _read_log(target_date)
    error_count = sum(1 for l in lines_raw if ' - ERROR - ' in l or ' - CRITICAL - ' in l)
    # 이번 감리에서 찾은 '무음 except' 경로들 — 그나마 로그가 남는 지점만 관측 가능
    silent_markers = [
        ('[EQUITY_CTRL] 상태 로드 실패', 'equity_controller 상태로드 실패'),
        ('[EQUITY_CTRL] Primary 손상', 'equity_controller Primary 손상, Backup으로 대체'),
        ('[EQUITY_CTRL] Primary/Backup 모두 손상', 'equity_controller Primary+Backup 손상 (DB 복구 또는 완전실패)'),
        ('[BUY_DB_ERROR]', '매수 DB 기록 실패'),
        ('[SELL_DB_ERROR]', '매도 DB 기록 실패'),
        ('[RISK_MGR_DB_FAIL]', 'risk_manager TradeDB 영구저장 실패'),
        ('상태 저장 실패', 'stop_loss_executor 저장 실패'),
    ]
    hit_markers = []
    for marker, desc in silent_markers:
        cnt = sum(1 for l in lines_raw if marker in l)
        if cnt > 0:
            hit_markers.append(f'{desc} {cnt}건')

    lines = [f'ERROR/CRITICAL 로그: {error_count}건']
    if hit_markers:
        lines.append('무음 except 경로 실행 흔적: ' + '; '.join(hit_markers))
        return _result(WARN, f'ERROR/CRITICAL {error_count}건, 무음 except 경로 실행 감지', None, lines)
    if error_count > 0:
        return _result(WARN, f'ERROR/CRITICAL {error_count}건 발생', None, lines)
    return _result(PASS, 'ERROR/CRITICAL 없음, 알려진 무음 except 경로 흔적 없음', None, lines)


# ─── 9. Log/DB 일치성 ──────────────────────────────────────────────

def check_log_db_sync(target_date: date, mode: str) -> Dict[str, Any]:
    if mode == 'pre':
        return _result(PASS, '장전 — 검증 대상 없음', None, [])
    lines_raw = _read_log(target_date)
    log_reject = len(_distinct_symbols_for_tag(lines_raw, 'REGIME_BLOCK'))
    log_accept = len(_distinct_symbols_for_tag(lines_raw, 'CANDIDATE_ACCEPT'))

    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(DISTINCT stock_code) FROM research.decision_ledger
            WHERE decided_at::date=%s AND decision_reason_code IN ('REGIME_BLOCKED','OTHER')
        """, (str(target_date),))
        db_reject = cur.fetchone()[0]
        conn.close()
    except Exception as e:
        return _result(WARN, f'DB 조회 실패: {e}', None, [])

    lines = [
        f'REGIME_BLOCK: log(distinct)={log_reject} vs DB(distinct)={db_reject}',
        f'CANDIDATE_ACCEPT: log(distinct)={log_accept}',
    ]
    if log_reject != db_reject:
        return _result(FAIL, f'REGIME_BLOCK log({log_reject}) != DB({db_reject})', MAJOR, lines)
    return _result(PASS, 'Log/DB 종목 기준 건수 일치', None, lines)


# ─── 10. Logging Audit (이중출력) ──────────────────────────────────

def check_logging_audit(target_date: date) -> Dict[str, Any]:
    lines_raw = _read_log(target_date)
    if not lines_raw:
        return _result(PASS, '오늘 로그 없음', None, [])

    # 타임스탬프+메시지본문(태그 이후)이 완전히 동일한 라인이 연속/근접해서 몇 번 나오는지 카운트
    def _strip_ts(line: str) -> str:
        return re.sub(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ - \w+ - ', '', line).strip()

    counter = Counter(_strip_ts(l) for l in lines_raw if '[REGIME_BLOCK]' in l or 'CANDIDATE_ACCEPT' in l or '[RESEARCH]' in l)
    dup_examples = [(msg, n) for msg, n in counter.items() if n >= 2]

    lines = [f'중복 감지 대상 라인 유형: {len(counter)}개, 그중 2회 이상: {len(dup_examples)}개']
    if dup_examples:
        sample = dup_examples[0]
        lines.append(f'예: "{sample[0][:80]}..." {sample[1]}회')
        return _result(WARN, f'동일 이벤트 중복 로깅 {len(dup_examples)}건 감지', None, lines)
    return _result(PASS, '중복 로깅 없음', None, lines)


# ─── 종합 ───────────────────────────────────────────────────────────

_CHECK_ORDER = [
    'daily_scan', 'score_engine', 'candidate_pipeline', 'global_gate',
    'exit_sync', 'equity_controller', 'scheduler', 'exception',
    'log_db_sync', 'logging',
]


def run_all(target_date: date, mode: str) -> Dict[str, Any]:
    results = {
        'daily_scan':         check_daily_scan(target_date),
        'score_engine':       check_score_engine(target_date),
        'candidate_pipeline': check_candidate_pipeline(target_date, mode),
        'global_gate':        check_global_gate(target_date, mode),
        'exit_sync':          check_exit_sync(target_date, mode),
        'equity_controller':  check_equity_controller(target_date, mode),
        'scheduler':          check_scheduler(target_date, mode),
        'exception':          check_exception_audit(target_date),
        'log_db_sync':        check_log_db_sync(target_date, mode),
        'logging':            check_logging_audit(target_date),
    }

    if any(r['status'] == FAIL for r in results.values()):
        overall = FAIL
    elif any(r['status'] == WARN for r in results.values()):
        overall = WARN
    else:
        overall = PASS

    return {
        'date': str(target_date),
        'mode': mode,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'checks': results,
        'overall': overall,
    }


def print_report(report: Dict[str, Any]) -> None:
    print('=' * 41)
    print('SYSTEM HEALTH CHECK')
    print(f"{report['date']} ({report['mode']})")
    print('=' * 41)
    for name in _CHECK_ORDER:
        r = report['checks'][name]
        print(f'{name:<20} {r["status"]}')
    print('-' * 41)
    print('OVERALL')
    print(report['overall'])
    print('-' * 41)

    warnings = [(n, r) for n, r in report['checks'].items() if r['status'] == WARN]
    print('Warnings')
    if warnings:
        for i, (n, r) in enumerate(warnings, 1):
            print(f'{i}. [{n}] {r["detail"]}')
    else:
        print('None')
    print('-' * 41)

    failures = [(n, r) for n, r in report['checks'].items() if r['status'] == FAIL]
    print('Failures')
    if failures:
        for i, (n, r) in enumerate(failures, 1):
            print(f'{i}. [{n}] ({r["severity"]}) {r["detail"]}')
    else:
        print('None')
    print('=' * 41)

    # 상세 라인 (디버깅용)
    print('\n--- 상세 ---')
    for name in _CHECK_ORDER:
        r = report['checks'][name]
        if r['lines']:
            print(f'[{name}]')
            for l in r['lines']:
                print(f'  {l}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='System Health Check v1.0 (Read-Only)')
    parser.add_argument('--date', default=None, help='날짜 (YYYY-MM-DD). 기본: 오늘')
    parser.add_argument('--mode', choices=['pre', 'post'], default=None,
                        help='pre=장전(08:55) 관점, post=장후(16:05) 관점. 기본: 현재 시각으로 자동판단')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()
    if args.mode:
        mode = args.mode
    else:
        mode = 'pre' if datetime.now().time() < dtime(12, 0) else 'post'

    report = run_all(target, mode)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report)

    sys.exit(1 if report['overall'] == FAIL else 0)
