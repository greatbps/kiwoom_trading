"""
일일 ML 자동 재훈련 파이프라인.

비교 로직:
  기존 모델 AUC 로드 → 새 모델 훈련 → 비교
  개선(≥0.01) 또는 기존 없음 → 저장
  그 외 → 기존 모델 유지

결과는 텔레그램으로 전송, logs/retrain_history.json에 기록.

사용법:
    python -m analysis.auto_retrain [--days 90] [--force]

크론 등록 (평일 장 마감 후):
    30 16 * * 1-5 cd /home/greatbps/projects/kiwoom_trading && python -m analysis.auto_retrain >> logs/auto_retrain.log 2>&1
"""

import os
import json
import pickle
import logging
import argparse
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_MODELS_DIR  = Path(__file__).parent.parent / 'models'
_RETRAIN_LOG = Path(__file__).parent.parent / 'logs' / 'retrain_history.json'

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '8252382230:AAEPiPmgvoe73_Z1matB7GTNvqhyNKTPpGM')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID',   '19196452')

IMPROVE_THRESHOLD = 0.01   # 기존 대비 AUC 개선 최소 기준
MIN_SAMPLES       = 30


# ────────────────────────────────────────────
# 내부 유틸
# ────────────────────────────────────────────

def _send_telegram(msg: str) -> None:
    try:
        import requests
        url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
        requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg}, timeout=10)
    except Exception as e:
        logger.warning(f"[RETRAIN] 텔레그램 전송 실패: {e}")


def _prev_auc() -> float:
    """저장된 최신 모델 AUC. 없으면 0.0."""
    latest = _MODELS_DIR / 'lgbm_entry_latest.pkl'
    if not latest.exists():
        return 0.0
    try:
        with open(latest, 'rb') as f:
            payload = pickle.load(f)
        return float(payload.get('metrics', {}).get('auc', 0.0))
    except Exception:
        return 0.0


def _append_log(entry: dict) -> None:
    history = []
    if _RETRAIN_LOG.exists():
        try:
            history = json.loads(_RETRAIN_LOG.read_text(encoding='utf-8'))
        except Exception:
            pass
    history.append(entry)
    _RETRAIN_LOG.parent.mkdir(parents=True, exist_ok=True)
    _RETRAIN_LOG.write_text(
        json.dumps(history[-90:], indent=2, ensure_ascii=False),
        encoding='utf-8',
    )


# ────────────────────────────────────────────
# 메인 재훈련 루프
# ────────────────────────────────────────────

def run(days: int = 90, force: bool = False) -> dict:
    """
    재훈련 실행.
    force=True  → AUC 비교 없이 무조건 저장
    force=False → 기존 대비 IMPROVE_THRESHOLD 이상 개선 시에만 저장
    Returns: result dict
    """
    from analysis.ml_pipeline import train as ml_train

    prev = _prev_auc()
    tag  = datetime.now().strftime('%Y%m%d_%H%M')

    # 1차 훈련 (저장 없이 결과 확인)
    metrics = ml_train(days=days, min_samples=MIN_SAMPLES, save=False, model_tag=tag)

    result = {
        'timestamp': datetime.now().isoformat(),
        'days':      days,
        'prev_auc':  prev,
        'new_auc':   metrics.get('auc', 0.0),
        'n_samples': metrics.get('n_samples', 0),
        'saved':     False,
        'error':     metrics.get('error'),
    }

    # 오류 시 조기 종료
    if metrics.get('error'):
        msg = f"⚠️ [ML 재훈련] 실패\n{metrics['error']}"
        logger.warning(f"[RETRAIN] {metrics['error']}")
        _send_telegram(msg)
        _append_log(result)
        return result

    new_auc = metrics['auc']
    should_save = force or (prev == 0.0) or ((new_auc - prev) >= IMPROVE_THRESHOLD)

    if should_save:
        # 저장 포함 재훈련 (동일 tag 사용하므로 파일명 일관성 유지)
        metrics = ml_train(days=days, min_samples=MIN_SAMPLES, save=True, model_tag=tag)
        result['saved'] = True
        result['model_path'] = metrics.get('model_path', '')

    # 텔레그램 메시지 구성
    fi_items = list(metrics.get('feature_importance', {}).items())[:3]
    fi_str   = ' / '.join(f"{k}:{v:.0f}" for k, v in fi_items)
    delta    = new_auc - prev
    delta_str = f"+{delta:.4f}" if delta >= 0 else f"{delta:.4f}"

    if should_save:
        status_icon = '✅ 저장됨'
    else:
        status_icon = f'⏭ 유지 (개선 {delta_str} < {IMPROVE_THRESHOLD})'

    msg = (
        f"📊 [ML 재훈련] {status_icon}\n"
        f"샘플: {metrics['n_samples']}건 (최근 {days}일)\n"
        f"AUC : {new_auc:.4f}  이전 {prev:.4f}  ({delta_str})\n"
        f"F1  : {metrics.get('f1', 0):.4f}  "
        f"(prec={metrics.get('precision', 0):.3f} rec={metrics.get('recall', 0):.3f})\n"
        f"승률: {metrics.get('win_rate', 0):.1%}\n"
        f"피처 top3: {fi_str}"
    )
    logger.info(
        f"[RETRAIN] {status_icon} AUC={new_auc:.4f} prev={prev:.4f} "
        f"n={metrics['n_samples']} saved={result['saved']}"
    )
    _send_telegram(msg)

    result.update({
        'new_auc':  new_auc,
        'f1':       metrics.get('f1'),
        'win_rate': metrics.get('win_rate'),
    })
    _append_log(result)
    return result


# ────────────────────────────────────────────
# CLI + 크론 가이드
# ────────────────────────────────────────────

def _print_cron_guide():
    cron_line = (
        "30 16 * * 1-5  "
        "cd /home/greatbps/projects/kiwoom_trading && "
        "python -m analysis.auto_retrain >> logs/auto_retrain.log 2>&1"
    )
    print("\n크론 등록 명령:")
    print("  crontab -e")
    print(f"  {cron_line}")


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    parser = argparse.ArgumentParser(description='일일 ML 자동 재훈련')
    parser.add_argument('--days',       type=int,         default=90,  help='학습 기간 (일, 기본 90)')
    parser.add_argument('--force',      action='store_true',           help='AUC 비교 무시하고 강제 저장')
    parser.add_argument('--cron-guide', action='store_true',           help='크론 등록 가이드 출력')
    args = parser.parse_args()

    if args.cron_guide:
        _print_cron_guide()
        raise SystemExit(0)

    result = run(days=args.days, force=args.force)

    if result.get('error'):
        print(f"[ERROR] {result['error']}")
        raise SystemExit(1)

    delta = result['new_auc'] - result['prev_auc']
    status = '저장됨' if result['saved'] else '유지'
    print(
        f"[{status}] "
        f"AUC: {result['prev_auc']:.4f} → {result['new_auc']:.4f}  "
        f"({'%+.4f' % delta})  "
        f"n={result['n_samples']}"
    )
    if result.get('model_path'):
        print(f"모델: {result['model_path']}")
