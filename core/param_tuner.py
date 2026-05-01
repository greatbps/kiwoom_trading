"""
ParamTuner — YAML 파라미터 자동 조정 + 이력 관리

실계좌 운용 중. 안전 장치:
  1. confidence == 'LOW' → 자동 적용 거부
  2. 적용 전 반드시 YAML 백업
  3. 변경 이력 data/param_changes.json 에 기록
  4. rollback_last() 로 언제든 복원 가능
  5. strategy_hybrid.yaml 의 auto_apply_enabled: false 기본값
  6. check_safety_gates() — 쿨다운 / 반복차단 / Kill Switch
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

BASE_DIR        = Path(__file__).parent.parent
YAML_PATH       = BASE_DIR / 'config' / 'strategy_hybrid.yaml'
BACKUP_DIR      = BASE_DIR / 'data' / 'yaml_backups'
CHANGE_LOG      = BASE_DIR / 'data' / 'param_changes.json'
LOGS_DIR        = BASE_DIR / 'logs'
RISK_LOG        = BASE_DIR / 'data' / 'risk_log.json'
HEALTH_HISTORY  = BASE_DIR / 'data' / 'health_history.json'

# ── Safety Gate 상수 ──────────────────────────────────────────────────────────
MIN_COOLDOWN_DAYS      = 3    # 마지막 변경 후 최소 대기 일수
MIN_TRADES_AFTER       = 10   # 변경 후 최소 거래 수 (충분한 데이터 확보)
MAX_REPEAT_PER_PARAM   = 3    # 동일 파라미터 최대 동일방향 변경 횟수
KILL_AVG_THRESHOLD     = 40   # 7일 평균 건강도 이 미만 → Kill Switch
KILL_CONSEC_LOSSES     = 5    # 연속 손실 이 이상 → Kill Switch
# Recovery Gate
RECOVERY_AVG_THRESHOLD = 55   # 7일 평균 이 이상 → Kill Switch 해제 가능
RECOVERY_WINRATE_MIN   = 40   # 최근 승률 이 이상
RECOVERY_MIN_SAMPLE    = 3    # 최소 이력 N일
# Force 남용 방지
MAX_FORCE_PER_DAY      = 1    # 당일 force 최대 횟수
MAX_FORCE_PER_7D       = 2    # 7일 내 force 최대 횟수
# 거래량 붕괴 감지
VOLUME_COLLAPSE_RATIO  = 0.30 # recent_avg < baseline_avg × 이 비율 → 붕괴 경고
VOLUME_COLLAPSE_DAYS   = 3    # 변경 후 비교 기간 (일)
VOLUME_BASELINE_DAYS   = 7    # 변경 전 기준 기간 (일)

# ── 적용 가능한 패치 명세 ──────────────────────────────────────────────────────
# op: 'set' → value 직접 설정
# op: 'add' → 현재값 + delta (max/min 클램프 지원)
PATCH_SPECS: dict[str, list[dict]] = {
    # 방향 맞았는데 손절 → EF 임계값 상향 (흔들림 허용 확대)
    'stoploss': [
        {
            'param': 'risk_control.early_failure_structure.score_threshold',
            'op': 'add', 'delta': 1, 'max': 5, 'min': 2,
            'desc': 'EF score_threshold +1 (흔들림 내성 확대)',
        },
    ],
    # ef_shakeout 과다 → EF 민감도 낮춤
    'ops': [
        {
            'param': 'risk_control.early_failure_structure.score_threshold',
            'op': 'add', 'delta': 1, 'max': 5, 'min': 2,
            'desc': 'EF score_threshold +1 (조기 청산 억제)',
        },
    ],
    # no_demand 과다 → 진입 조건 강화
    'strategy': [
        {
            'param': 'smc.choch_grade.min_grade',
            'op': 'set', 'value': 'A',
            'desc': 'min_grade B → A (A급만 허용)',
        },
        {
            'param': 'smc.max_fallback_per_day',
            'op': 'add', 'delta': -1, 'max': 5, 'min': 1,
            'desc': 'max_fallback_per_day -1 (진입 기회 축소)',
        },
    ],
    # <1h 청산 50%+ → EF 억제 + 시간창 축소
    'daytrading': [
        {
            'param': 'risk_control.early_failure_structure.score_threshold',
            'op': 'add', 'delta': 1, 'max': 5, 'min': 2,
            'desc': 'EF score_threshold +1 (스윙 보유 강제)',
        },
        {
            'param': 'smc.choch_grade.grade_b_cutoff',
            'op': 'set', 'value': '10:30',
            'desc': 'grade_b_cutoff → 10:30 (오후 B급 진입 추가 제한)',
        },
    ],
    'healthy':  [],  # 건강한 운영 → 변경 없음
    'mixed':    [],
    'unknown':  [],
}


def _get_nested(d: dict, dotpath: str) -> Any:
    cur = d
    for k in dotpath.split('.'):
        if not isinstance(cur, dict) or k not in cur:
            raise KeyError(f'경로 없음: {dotpath}')
        cur = cur[k]
    return cur


def _set_nested(d: dict, dotpath: str, value: Any) -> None:
    keys = dotpath.split('.')
    cur  = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


class ParamTuner:

    def __init__(self) -> None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # ── I/O ──────────────────────────────────────────────────────────────────

    def load_yaml(self) -> dict:
        with open(YAML_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _save_yaml(self, data: dict) -> None:
        with open(YAML_PATH, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def backup_yaml(self, tag: str = '') -> Path:
        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        name = f'strategy_hybrid_{ts}_{tag}.yaml' if tag else f'strategy_hybrid_{ts}.yaml'
        dest = BACKUP_DIR / name
        shutil.copy2(YAML_PATH, dest)
        return dest

    def load_change_log(self) -> list:
        if not CHANGE_LOG.exists():
            return []
        try:
            return json.loads(CHANGE_LOG.read_text())
        except Exception:
            return []

    def _save_change_log(self, log: list) -> None:
        CHANGE_LOG.write_text(json.dumps(log[-50:], ensure_ascii=False, indent=2))

    def is_auto_apply_enabled(self) -> bool:
        try:
            cfg = self.load_yaml()
            return bool(cfg.get('risk_control', {}).get('auto_apply_enabled', False))
        except Exception:
            return False

    # ── Core ─────────────────────────────────────────────────────────────────

    def apply_patch(
        self,
        ops_type:     str,
        health_score: int,
        ops_verdict:  str,
        confidence:   str,
        force:        bool = False,
    ) -> dict:
        """
        ops_type 에 해당하는 PATCH_SPECS 를 YAML 에 적용.
        반환: {success, applied, backup_path, error?}
        """
        if confidence == 'LOW':
            return {'success': False, 'error': 'LOW confidence — 자동 적용 거부', 'applied': []}

        patch_spec = PATCH_SPECS.get(ops_type, [])
        if not patch_spec:
            return {'success': False, 'error': f'ops_type={ops_type} — 적용할 패치 없음', 'applied': []}

        backup_path = self.backup_yaml(tag=ops_type)
        data        = self.load_yaml()
        applied:    list[dict] = []
        has_error   = False

        for spec in patch_spec:
            param = spec['param']
            try:
                op = spec.get('op', 'set')
                if op == 'set':
                    try:
                        old_val = _get_nested(data, param)
                    except KeyError:
                        old_val = None
                    _set_nested(data, param, spec['value'])
                    applied.append({'param': param, 'old': old_val, 'new': spec['value'], 'desc': spec.get('desc', '')})

                elif op == 'add':
                    old_val = float(_get_nested(data, param))
                    new_val = old_val + float(spec['delta'])
                    if spec.get('max') is not None:
                        new_val = min(new_val, float(spec['max']))
                    if spec.get('min') is not None:
                        new_val = max(new_val, float(spec['min']))
                    new_val_final = int(new_val) if isinstance(_get_nested(data, param), int) else round(new_val, 4)
                    _set_nested(data, param, new_val_final)
                    applied.append({'param': param, 'old': old_val, 'new': new_val_final, 'desc': spec.get('desc', '')})

            except Exception as e:
                applied.append({'param': param, 'error': str(e)})
                has_error = True

        if not has_error:
            self._save_yaml(data)

        log = self.load_change_log()
        log.append({
            'date':          datetime.now().strftime('%Y-%m-%d'),
            'timestamp':     datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'ops_type':      ops_type,
            'ops_verdict':   ops_verdict,
            'health_score':  health_score,
            'confidence':    confidence,
            'force':         force,
            'changes':       applied,
            'backup':        str(backup_path),
            'applied':       not has_error,
        })
        self._save_change_log(log)

        return {
            'success':     not has_error,
            'applied':     applied,
            'backup_path': str(backup_path),
        }

    # ── Safety Gates ─────────────────────────────────────────────────────────

    def _count_trades_since_last_change(self, last_date: str) -> int:
        """마지막 변경일(포함) 이후 TRADE_RESULT 총 건수"""
        _RE  = re.compile(r'\[TRADE_RESULT\]')
        count = 0
        start = datetime.strptime(last_date, '%Y-%m-%d')
        end   = datetime.now()
        cur   = start
        while cur <= end:
            p = LOGS_DIR / f'auto_trading_{cur.strftime("%Y%m%d")}.log'
            if p.exists():
                count += sum(1 for ln in p.read_text(errors='ignore').splitlines() if _RE.search(ln))
            cur += timedelta(days=1)
        return count

    def _check_kill_switch(self) -> tuple[bool, list[str]]:
        """Kill Switch 조건 확인 (force=True 로도 우회 불가)"""
        reasons: list[str] = []

        # ① 7일 평균 건강도 < KILL_AVG_THRESHOLD
        if HEALTH_HISTORY.exists():
            try:
                hist = json.loads(HEALTH_HISTORY.read_text())
                recent = sorted(hist, key=lambda x: x['date'])[-7:]
                if len(recent) >= 3:
                    avg = sum(x['score'] for x in recent) / len(recent)
                    if avg < KILL_AVG_THRESHOLD:
                        reasons.append(f'7일 평균 건강도 {avg:.0f}점 < {KILL_AVG_THRESHOLD}')
            except Exception:
                pass

        # ② 연속 손실 ≥ KILL_CONSEC_LOSSES
        if RISK_LOG.exists():
            try:
                risk = json.loads(RISK_LOG.read_text())
                consec = risk.get('consecutive_losses', 0)
                if consec >= KILL_CONSEC_LOSSES:
                    reasons.append(f'연속 손실 {consec}회 ≥ {KILL_CONSEC_LOSSES}')
            except Exception:
                pass

        return bool(reasons), reasons

    def _check_recovery_gate(self) -> tuple[bool, str | None]:
        """
        Kill Switch 해제 여부 확인.
        7일 평균 건강도 ≥ 55 AND 최근 승률 ≥ 40% → 회복 인정.
        반환: (eligible, reason_if_not)
        """
        if not HEALTH_HISTORY.exists():
            return False, '건강 이력 없음 — 회복 판단 불가'
        try:
            hist   = json.loads(HEALTH_HISTORY.read_text())
            recent = sorted(hist, key=lambda x: x['date'])[-7:]
            if len(recent) < RECOVERY_MIN_SAMPLE:
                return False, f'이력 {len(recent)}일 < 최소 {RECOVERY_MIN_SAMPLE}일'

            avg = sum(x['score'] for x in recent) / len(recent)
            if avg < RECOVERY_AVG_THRESHOLD:
                return False, f'7일 평균 {avg:.0f}점 < 회복 기준 {RECOVERY_AVG_THRESHOLD}점'

            wr_entries = [x for x in recent if x.get('wr') is not None]
            if wr_entries:
                recent_wr = sum(x['wr'] for x in wr_entries) / len(wr_entries)
                if recent_wr < RECOVERY_WINRATE_MIN:
                    return False, f'최근 승률 {recent_wr:.0f}% < {RECOVERY_WINRATE_MIN}%'

            return True, None
        except Exception as e:
            return False, f'회복 판단 오류: {e}'

    def _check_force_limit(self) -> tuple[bool, str | None]:
        """
        Force 남용 방지: 당일 {MAX_FORCE_PER_DAY}회, 7일 {MAX_FORCE_PER_7D}회 한도.
        반환: (ok, reason_if_blocked)
        """
        log           = self.load_change_log()
        today         = datetime.now().strftime('%Y-%m-%d')
        cutoff_7d     = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        force_entries = [e for e in log if e.get('force') and e.get('applied')]

        today_count = sum(1 for e in force_entries if e.get('date') == today)
        week_count  = sum(1 for e in force_entries if e.get('date', '') >= cutoff_7d)

        if today_count >= MAX_FORCE_PER_DAY:
            return False, f'당일 강제 적용 {today_count}회 — 한도 {MAX_FORCE_PER_DAY}회 초과'
        if week_count >= MAX_FORCE_PER_7D:
            return False, f'7일 강제 적용 {week_count}회 — 한도 {MAX_FORCE_PER_7D}회 초과'
        return True, None

    def _check_volume_collapse(self) -> tuple[bool, str | None]:
        """
        거래량 붕괴 감지.
        최근 {VOLUME_COLLAPSE_DAYS}일 평균 거래 수 < 기준 {VOLUME_BASELINE_DAYS}일 평균 × {VOLUME_COLLAPSE_RATIO}
        → 시장이 비활성 상태이므로 파라미터 조정 의미 없음.
        반환: (ok, reason_if_collapsed)
        """
        _RE  = re.compile(r'\[TRADE_RESULT\]')
        now  = datetime.now()
        recent_counts: list[int] = []
        baseline_counts: list[int] = []

        for i in range(VOLUME_BASELINE_DAYS + VOLUME_COLLAPSE_DAYS):
            day = now - timedelta(days=i)
            p   = LOGS_DIR / f'auto_trading_{day.strftime("%Y%m%d")}.log'
            cnt = 0
            if p.exists():
                cnt = sum(1 for ln in p.read_text(errors='ignore').splitlines() if _RE.search(ln))
            if i < VOLUME_COLLAPSE_DAYS:
                recent_counts.append(cnt)
            else:
                baseline_counts.append(cnt)

        baseline_avg = sum(baseline_counts) / max(len(baseline_counts), 1)
        if baseline_avg < 1:
            return True, None  # 기준 데이터 자체가 없으면 패스

        recent_avg = sum(recent_counts) / max(len(recent_counts), 1)
        if recent_avg < baseline_avg * VOLUME_COLLAPSE_RATIO:
            ratio = recent_avg / baseline_avg
            return False, (
                f'거래량 붕괴: 최근 {recent_avg:.1f}건/일 vs 기준 {baseline_avg:.1f}건/일 '
                f'({ratio:.0%}) — 시장 비활성 상태에서 파라미터 조정 보류'
            )
        return True, None

    def check_safety_gates(self, ops_type: str) -> dict:
        """
        자동 적용 전 안전 게이트 3단계 체크.
        Kill Switch 는 force=True 로도 우회 불가.

        반환:
          can_apply           bool   — 최종 통과 여부
          blocked_by          str|None — 차단 이유
          cooldown_remaining  int    — 쿨다운 남은 일
          trades_since        int    — 마지막 변경 후 거래 수
          trades_required     int    — MIN_TRADES_AFTER
          repeat_max          int    — 해당 ops_type 최다 반복 횟수
          kill_switch         bool
          kill_switch_reasons list[str]
        """
        log     = self.load_change_log()
        applied = [e for e in log if e.get('applied') and e.get('ops_type') not in ('ROLLBACK',)]

        # ─ Kill Switch (force 우회 불가) ──────────────────────────────────────
        ks_active, ks_reasons = self._check_kill_switch()

        # ─ Cooldown ───────────────────────────────────────────────────────────
        cooldown_remaining = 0
        cooldown_ok        = True
        trades_since       = 0
        trades_ok          = True

        if applied:
            last_entry   = applied[-1]
            last_date    = last_entry['date']
            days_elapsed = (datetime.now() - datetime.strptime(last_date, '%Y-%m-%d')).days

            if days_elapsed < MIN_COOLDOWN_DAYS:
                cooldown_ok        = False
                cooldown_remaining = MIN_COOLDOWN_DAYS - days_elapsed

            trades_since = self._count_trades_since_last_change(last_date)
            trades_ok    = trades_since >= MIN_TRADES_AFTER

        # ─ Repeat Direction Block ─────────────────────────────────────────────
        repeat_ok  = True
        repeat_max = 0

        spec = PATCH_SPECS.get(ops_type, [])
        for s in spec:
            param = s['param']
            count = sum(
                1 for e in applied
                if e.get('ops_type') == ops_type
                for ch in e.get('changes', [])
                if ch.get('param') == param and not ch.get('error')
            )
            repeat_max = max(repeat_max, count)
            if count >= MAX_REPEAT_PER_PARAM:
                repeat_ok = False

        # ─ Volume Collapse ────────────────────────────────────────────────────
        vol_ok, vol_reason = self._check_volume_collapse()

        # ─ Recovery Gate (Kill Switch 해제 가능 여부) ─────────────────────────
        recovery_eligible, recovery_reason = self._check_recovery_gate()

        # ─ Force Limit ────────────────────────────────────────────────────────
        force_ok, force_reason = self._check_force_limit()

        # ─ 최종 판정 ─────────────────────────────────────────────────────────
        blocked_by: str | None = None
        if ks_active and not recovery_eligible:
            blocked_by = f'Kill Switch: {" / ".join(ks_reasons)}'
        elif ks_active and recovery_eligible:
            # Kill Switch 조건이 있어도 Recovery Gate 통과 → 적용 허용
            pass
        elif not cooldown_ok:
            blocked_by = f'쿨다운 {cooldown_remaining}일 남음 (마지막 변경 후 {MIN_COOLDOWN_DAYS}일 대기)'
        elif not trades_ok:
            blocked_by = f'변경 후 거래 {trades_since}/{MIN_TRADES_AFTER}건 — 데이터 부족'
        elif not repeat_ok:
            blocked_by = f'동일 파라미터 {repeat_max}회 반복 변경 — 과적합 방지 ({MAX_REPEAT_PER_PARAM}회 한도)'
        elif not vol_ok:
            blocked_by = vol_reason

        # Kill Switch: recovery_eligible이면 해제 가능, 아니면 절대 차단
        ks_blocked = ks_active and not recovery_eligible
        can_apply  = (not ks_blocked) and cooldown_ok and trades_ok and repeat_ok and vol_ok

        return {
            'can_apply':           can_apply,
            'blocked_by':          blocked_by,
            'cooldown_remaining':  cooldown_remaining,
            'trades_since':        trades_since,
            'trades_required':     MIN_TRADES_AFTER,
            'repeat_max':          repeat_max,
            'repeat_limit':        MAX_REPEAT_PER_PARAM,
            'kill_switch':         ks_active,
            'kill_switch_reasons': ks_reasons,
            'recovery_eligible':   recovery_eligible,
            'recovery_reason':     recovery_reason,
            'force_ok':            force_ok,
            'force_reason':        force_reason,
            'volume_ok':           vol_ok,
            'volume_reason':       vol_reason,
        }

    def rollback_last(self) -> dict:
        """마지막 applied=True 변경사항 롤백"""
        log        = self.load_change_log()
        candidates = [e for e in reversed(log) if e.get('ops_type') != 'ROLLBACK' and e.get('applied')]
        if not candidates:
            return {'success': False, 'error': '롤백 가능한 변경 이력 없음'}

        last        = candidates[0]
        backup_path = Path(last.get('backup', ''))
        if not backup_path.exists():
            return {'success': False, 'error': f'백업 파일 없음: {backup_path.name}'}

        self.backup_yaml(tag='pre_rollback')
        shutil.copy2(backup_path, YAML_PATH)

        log.append({
            'date':         datetime.now().strftime('%Y-%m-%d'),
            'timestamp':    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'ops_type':     'ROLLBACK',
            'ops_verdict':  f'→ {last["timestamp"]} 복원',
            'health_score': None,
            'confidence':   None,
            'changes':      [{'action': f'복원: {backup_path.name}'}],
            'backup':       str(backup_path),
            'applied':      True,
        })
        self._save_change_log(log)

        return {'success': True, 'restored_from': str(backup_path), 'entry': last}
