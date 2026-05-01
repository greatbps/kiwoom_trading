"""
analysis/eq_promote.py — EQ 모델 Shadow → Live 자동 프로모션

흐름:
    1. entry_features 레이블 수 확인 (min_samples)
    2. Walk Forward AUC 계산
    3. AUC >= promote_auc_threshold → YAML shadow_mode: false 패치 + 텔레그램 알림
    4. AUC < threshold → 현재 상태 유지 + 진행 현황 알림

사용법:
    python -m analysis.eq_promote [--dry-run] [--force]

크론 (장 마감 후):
    50 15 * * 1-5 cd /home/greatbps/projects/kiwoom_trading && python -m analysis.eq_promote >> logs/eq_promote.log 2>&1
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT      = Path(__file__).parent.parent
_DB_PATH   = _ROOT / "data" / "trades.db"
_YAML_PATH = _ROOT / "config" / "strategy_hybrid.yaml"

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '8252382230:AAEPiPmgvoe73_Z1matB7GTNvqhyNKTPpGM')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '19196452')

# 프로모션 기준
PROMOTE_AUC    = 0.60   # 이 이상이면 live 전환
MIN_SAMPLES    = 50     # 최소 레이블 건수
MIN_WFO_WINDOWS = 3     # 유효 WFO 윈도우 최소 수


# ── 텔레그램 ──────────────────────────────────────────────────────

def _send_telegram(msg: str):
    try:
        import requests
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"[PROMOTE] 텔레그램 전송 실패: {e}")


# ── YAML shadow_mode 패치 ─────────────────────────────────────────

def _patch_yaml_shadow_mode(yaml_path: Path, value: bool, dry_run: bool) -> bool:
    """
    eq_ml_filter.shadow_mode 값을 변경.
    Returns True if changed.
    """
    text = yaml_path.read_text(encoding='utf-8')
    pattern = r'(eq_ml_filter:.*?shadow_mode:\s*)(true|false)'
    match   = re.search(pattern, text, re.DOTALL)
    if not match:
        logger.warning("[PROMOTE] shadow_mode 패턴 못 찾음")
        return False

    current = match.group(2).lower() == 'true'
    target  = 'true' if value else 'false'

    if current == value:
        logger.info(f"[PROMOTE] shadow_mode 이미 {target}, 변경 없음")
        return False

    if dry_run:
        logger.info(f"[PROMOTE] [DRY] shadow_mode: {match.group(2)} → {target}")
        return True

    new_text = text[:match.start(2)] + target + text[match.end(2):]
    yaml_path.write_text(new_text, encoding='utf-8')
    logger.info(f"[PROMOTE] shadow_mode: {match.group(2)} → {target}")
    return True


# ── WFO 실행 ─────────────────────────────────────────────────────

def _run_wfo(db_path: Path) -> dict:
    """Walk Forward 실행 → {mean_auc, n_valid_windows, labeled_count}"""
    import sqlite3
    import numpy as np

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM entry_features
        WHERE outcome_win IS NOT NULL
        ORDER BY timestamp
    """).fetchall()
    conn.close()
    data = [dict(r) for r in rows]

    labeled = len(data)
    if labeled < MIN_SAMPLES:
        return {'labeled': labeled, 'mean_auc': 0.0, 'n_valid': 0, 'error': f'샘플 부족 ({labeled}/{MIN_SAMPLES})'}

    from analysis.eq_walk_forward import run_walk_forward
    import numpy as np

    results = run_walk_forward(data, n_windows=5, test_ratio=0.3, min_train=20)
    valid   = [r for r in results if not r.get('skipped')]
    aucs    = [r['auc'] for r in valid]

    return {
        'labeled':   labeled,
        'mean_auc':  round(float(np.mean(aucs)), 3) if aucs else 0.0,
        'std_auc':   round(float(np.std(aucs)), 3)  if aucs else 0.0,
        'n_valid':   len(valid),
        'windows':   results,
    }


# ── 현재 shadow_mode 상태 읽기 ────────────────────────────────────

def _current_shadow_mode(yaml_path: Path) -> bool:
    text = yaml_path.read_text(encoding='utf-8')
    m = re.search(r'shadow_mode:\s*(true|false)', text)
    return m.group(1).lower() == 'true' if m else True


# ── 메인 ─────────────────────────────────────────────────────────

def run(dry_run: bool = False, force: bool = False) -> dict:
    """
    dry_run=True  → YAML 수정 없이 판정만
    force=True    → AUC 조건 무시하고 무조건 live 전환
    """
    wfo = _run_wfo(_DB_PATH)
    labeled    = wfo.get('labeled', 0)
    mean_auc   = wfo.get('mean_auc', 0.0)
    n_valid    = wfo.get('n_valid', 0)
    error      = wfo.get('error')
    is_shadow  = _current_shadow_mode(_YAML_PATH)

    result = {
        'timestamp': datetime.now().isoformat(),
        'labeled':   labeled,
        'mean_auc':  mean_auc,
        'n_valid':   n_valid,
        'shadow_before': is_shadow,
        'shadow_after':  is_shadow,
        'promoted':  False,
        'error':     error,
    }

    if error and not force:
        msg = f"⏳ [EQ 모델] 프로모션 대기\n{error}\n현재 shadow_mode={is_shadow}"
        logger.info(f"[PROMOTE] {error}")
        _send_telegram(msg)
        return result

    # 프로모션 조건 판정
    qualifies = force or (
        mean_auc >= PROMOTE_AUC
        and n_valid >= MIN_WFO_WINDOWS
        and labeled >= MIN_SAMPLES
    )

    if qualifies and is_shadow:
        changed = _patch_yaml_shadow_mode(_YAML_PATH, False, dry_run)
        if changed:
            result['shadow_after'] = False
            result['promoted']     = True
            status = '🎉 [EQ 모델] LIVE 전환 완료!'
            detail = (
                f"레이블: {labeled}건  WFO AUC: {mean_auc:.3f} (±{wfo.get('std_auc',0):.3f})\n"
                f"유효 윈도우: {n_valid}/5\n"
                f"shadow_mode: true → false\n"
                f"{'[DRY RUN]' if dry_run else '전략 재시작 후 적용됩니다.'}"
            )
            logger.info(f"[PROMOTE] PROMOTED! AUC={mean_auc:.3f}")
            _send_telegram(f"{status}\n{detail}")
        else:
            logger.info("[PROMOTE] 이미 live 모드, 변경 없음")

    elif qualifies and not is_shadow:
        # 이미 live, 성능 리포트만
        msg = (
            f"✅ [EQ 모델] 이미 LIVE 운영 중\n"
            f"AUC: {mean_auc:.3f}  레이블: {labeled}건"
        )
        logger.info(f"[PROMOTE] 이미 live AUC={mean_auc:.3f}")
        _send_telegram(msg)

    else:
        # 아직 조건 미달
        gap_auc     = PROMOTE_AUC - mean_auc
        gap_samples = max(0, MIN_SAMPLES - labeled)
        needs = []
        if gap_samples > 0:
            needs.append(f"거래 {gap_samples}건 더 필요")
        if mean_auc < PROMOTE_AUC:
            needs.append(f"AUC {mean_auc:.3f} → {PROMOTE_AUC:.2f} 필요 (+{gap_auc:.3f})")
        if n_valid < MIN_WFO_WINDOWS:
            needs.append(f"유효 윈도우 {n_valid}/{MIN_WFO_WINDOWS}")

        msg = (
            f"⏳ [EQ 모델] Shadow 유지 중\n"
            f"레이블: {labeled}건  AUC: {mean_auc:.3f}\n"
            f"필요: {' | '.join(needs)}"
        )
        logger.info(f"[PROMOTE] 조건 미달 AUC={mean_auc:.3f} n={labeled}")
        _send_telegram(msg)

    return result


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

    parser = argparse.ArgumentParser(description='EQ 모델 Shadow → Live 프로모션')
    parser.add_argument('--dry-run', action='store_true', help='YAML 수정 없이 판정만')
    parser.add_argument('--force',   action='store_true', help='AUC 조건 무시하고 강제 전환')
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, force=args.force)

    print(f"\n{'='*50}")
    print(f"  레이블:  {result['labeled']}건")
    print(f"  AUC:     {result['mean_auc']:.3f}")
    print(f"  WFO 윈도우: {result['n_valid']}")
    if result.get('error'):
        print(f"  상태:    {result['error']}")
    elif result['promoted']:
        print(f"  결과:    ✅ PROMOTED → shadow_mode: false")
        if args.dry_run:
            print(f"           [DRY RUN — 실제 변경 없음]")
    else:
        print(f"  결과:    ⏳ Shadow 유지")
    print(f"{'='*50}\n")
