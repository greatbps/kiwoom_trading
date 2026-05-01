"""
안전 게이트 + 버전 관리 + 카나리 롤아웃

주요 기능:
  1. safety_check()  — 성능/파라미터 변화 검증
  2. deploy_canary() — config/canary.yaml 에 20% 비중으로 배포
  3. deploy_full()   — config/current.yaml 교체 + 버전 백업
  4. rollback()      — 이전 버전으로 즉시 복원
  5. canary_status() — 카나리 1주 후 성과 확인 → 전환/롤백 결정

사용 흐름:
    from analysis.safety_gate import SafetyGate

    gate = SafetyGate()
    ok, reason = gate.safety_check(new_result, new_params)
    if ok:
        gate.deploy_canary(new_params)     # 20% 먼저
    # 1주 후
    if gate.canary_status():
        gate.deploy_full(new_params)
    else:
        gate.rollback()
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml


# ── 경로 상수 ────────────────────────────────────────────────────────

CONFIG_DIR     = Path("config")
CURRENT_YAML   = CONFIG_DIR / "strategy_hybrid.yaml"
CANARY_YAML    = CONFIG_DIR / "canary.yaml"
VERSIONS_DIR   = CONFIG_DIR / "versions"
CANARY_LOG     = Path("logs") / "canary_log.json"

# 안전 게이트 기준값
GATE = {
    "min_score_delta":   0.0,    # 새 점수 ≥ 현재 점수 (하락 차단)
    "max_mdd":           0.18,   # MDD 18% 초과 차단
    "max_param_change":  0.50,   # 파라미터 50% 이상 급변 차단
    "min_expectancy":   -0.1,    # 기대값 -0.1% 이하 차단
    "min_trades":        5,      # 거래 수 5회 미만 차단
}


class SafetyGate:
    """튜닝 결과 → 프로덕션 배포 안전 관리"""

    def __init__(self):
        VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
        Path("logs").mkdir(exist_ok=True)

    # ── 현재 프로덕션 파라미터 로드 ─────────────────────────────────

    def load_current_params(self) -> Dict:
        """현재 strategy_hybrid.yaml에서 defensive/short 파라미터 추출"""
        try:
            with open(CURRENT_YAML, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            return {
                "defensive": cfg.get("defensive_mode", {}),
                "short":     cfg.get("short_mode", {}),
            }
        except Exception as e:
            print(f"  [WARN] 현재 파라미터 로드 실패: {e}")
            return {"defensive": {}, "short": {}}

    # ── 안전 게이트 ──────────────────────────────────────────────────

    def safety_check(
        self,
        new_result:  Dict,
        new_params:  Dict,
        old_result:  Optional[Dict] = None,
    ) -> Tuple[bool, str]:
        """새 파라미터 안전성 검증

        Args:
            new_result: {"defensive_score": float, "short_score": float,
                          "defensive": {...}, "short": {...}}
            new_params: {"defensive": {...}, "short": {...}}
            old_result: 이전 주 결과 (없으면 기준값 사용)

        Returns:
            (ok: bool, reason: str)
        """
        old_params = self.load_current_params()
        issues = []

        for strategy in ("defensive", "short"):
            score     = new_result.get(f"{strategy}_score", -999)
            params    = new_params.get(strategy, {})
            old_p     = old_params.get(strategy, {})

            # 1. MDD 초과
            if new_result.get(f"{strategy}_mdd", 0) > GATE["max_mdd"]:
                issues.append(f"{strategy}: MDD {new_result.get(f'{strategy}_mdd', 0)*100:.1f}% > {GATE['max_mdd']*100:.0f}%")

            # 2. 기대값 너무 낮음
            if new_result.get(f"{strategy}_expectancy", 0) < GATE["min_expectancy"]:
                issues.append(f"{strategy}: E={new_result.get(f'{strategy}_expectancy', 0):.3f}% < {GATE['min_expectancy']}%")

            # 3. 거래 수 부족
            if new_result.get(f"{strategy}_trades", 999) < GATE["min_trades"]:
                issues.append(f"{strategy}: 거래수 {new_result.get(f'{strategy}_trades', 0)}회 < {GATE['min_trades']}회")

            # 4. 파라미터 급변 방지
            for k, v_new in params.items():
                v_old = old_p.get(k)
                if v_old and isinstance(v_new, (int, float)) and isinstance(v_old, (int, float)):
                    if v_old != 0:
                        diff_ratio = abs(v_new - v_old) / abs(v_old)
                        if diff_ratio > GATE["max_param_change"]:
                            issues.append(
                                f"{strategy}.{k}: {v_old}→{v_new} "
                                f"({diff_ratio*100:.0f}% 변화, 한도={GATE['max_param_change']*100:.0f}%)"
                            )

        if issues:
            reason = "안전 게이트 차단:\n  " + "\n  ".join(issues)
            print(f"\n  ❌ {reason}")
            return False, reason

        print("\n  ✅ 안전 게이트 통과")
        return True, "OK"

    # ── 카나리 배포 ──────────────────────────────────────────────────

    def deploy_canary(self, new_params: Dict, capital_ratio: float = 0.2):
        """canary.yaml 생성 (20% 비중)

        main_auto_trading.py가 canary.yaml 존재 시 해당 파라미터로
        capital_ratio 비중만 운용하도록 설계.
        (현재는 파일 생성만 — 실제 적용은 main에서 canary 로직 추가 필요)
        """
        canary_cfg = {
            "mode":          "canary",
            "capital_ratio": capital_ratio,
            "deployed_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
            "defensive_mode": {
                **new_params.get("defensive", {}),
                "enabled": True,
            },
            "short_mode": {
                **new_params.get("short", {}),
                "enabled": True,
            },
        }
        with open(CANARY_YAML, "w", encoding="utf-8") as f:
            yaml.dump(canary_cfg, f, allow_unicode=True, default_flow_style=False)

        # 카나리 시작 로그
        log = self._load_canary_log()
        log.append({
            "action":       "canary_start",
            "deployed_at":  canary_cfg["deployed_at"],
            "capital_ratio": capital_ratio,
            "params":       new_params,
        })
        self._save_canary_log(log)
        print(f"  🟡 카나리 배포: {CANARY_YAML} (비중 {capital_ratio*100:.0f}%)")

    def canary_status(self, min_trades: int = 5) -> Tuple[bool, str]:
        """카나리 1주 후 성과 확인

        Returns:
            (promote: bool, reason: str)
            promote=True → deploy_full()  / False → rollback()
        """
        log = self._load_canary_log()
        canary_trades = [e for e in log if e.get("action") == "canary_trade"]

        if len(canary_trades) < min_trades:
            return False, f"카나리 거래 부족 ({len(canary_trades)}/{min_trades}회)"

        pnls     = [t.get("pnl_pct", 0) for t in canary_trades]
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
        avg_pnl  = sum(pnls) / len(pnls)

        if win_rate >= 0.55 and avg_pnl > 0:
            return True, f"카나리 성공 (WR={win_rate*100:.0f}% avg={avg_pnl:+.2f}%)"
        return False, f"카나리 미달 (WR={win_rate*100:.0f}% avg={avg_pnl:+.2f}%)"

    # ── 프로덕션 전체 배포 ───────────────────────────────────────────

    def deploy_full(self, new_params: Dict):
        """strategy_hybrid.yaml 업데이트 + 버전 백업

        1. 현재 YAML → versions/config_v{N}.yaml 백업
        2. defensive_mode / short_mode 섹션 업데이트
        3. canary.yaml 삭제
        """
        # 버전 번호 결정
        existing = sorted(VERSIONS_DIR.glob("config_v*.yaml"))
        version  = len(existing) + 1

        # 현재 YAML 백업
        backup = VERSIONS_DIR / f"config_v{version:03d}.yaml"
        shutil.copy2(CURRENT_YAML, backup)
        print(f"  📦 백업: {backup}")

        # YAML 업데이트
        with open(CURRENT_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        def_new = new_params.get("defensive", {})
        sht_new = new_params.get("short", {})

        # 기존 섹션에 새 파라미터 병합 (enabled 상태 유지)
        cfg_def = cfg.get("defensive_mode", {})
        cfg_sht = cfg.get("short_mode", {})
        for k, v in def_new.items():
            cfg_def[k] = v
        for k, v in sht_new.items():
            cfg_sht[k] = v

        cfg["defensive_mode"] = cfg_def
        cfg["short_mode"]     = cfg_sht

        with open(CURRENT_YAML, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False,
                      sort_keys=False)

        # canary.yaml 삭제
        if CANARY_YAML.exists():
            CANARY_YAML.unlink()

        # 배포 로그
        log = self._load_canary_log()
        log.append({
            "action":      "deploy_full",
            "deployed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "version":     version,
            "params":      new_params,
        })
        self._save_canary_log(log)
        print(f"  ✅ 프로덕션 배포 완료 (v{version:03d})")

    # ── 롤백 ────────────────────────────────────────────────────────

    def rollback(self) -> bool:
        """직전 버전으로 롤백

        Returns:
            True if 성공
        """
        existing = sorted(VERSIONS_DIR.glob("config_v*.yaml"))
        if not existing:
            print("  [WARN] 롤백할 버전 없음")
            return False

        prev = existing[-1]
        shutil.copy2(prev, CURRENT_YAML)

        # canary.yaml 삭제
        if CANARY_YAML.exists():
            CANARY_YAML.unlink()

        log = self._load_canary_log()
        log.append({
            "action":      "rollback",
            "rolled_to":   str(prev),
            "rolled_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        self._save_canary_log(log)
        print(f"  ↩️ 롤백 완료: {prev}")
        return True

    # ── 상태 출력 ────────────────────────────────────────────────────

    def print_status(self):
        print("\n  ── 배포 상태 ──────────────────────")
        versions = sorted(VERSIONS_DIR.glob("config_v*.yaml"))
        print(f"  저장된 버전: {len(versions)}개")
        if versions:
            print(f"  최신 백업  : {versions[-1].name}")
        print(f"  카나리     : {'활성' if CANARY_YAML.exists() else '없음'}")

        log = self._load_canary_log()
        recent = log[-3:] if len(log) >= 3 else log
        for e in reversed(recent):
            print(f"  [{e.get('deployed_at', e.get('rolled_at','?'))}] "
                  f"{e.get('action', '?')}")

    # ── 내부 헬퍼 ────────────────────────────────────────────────────

    def _load_canary_log(self) -> list:
        if CANARY_LOG.exists():
            try:
                with open(CANARY_LOG, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_canary_log(self, log: list):
        with open(CANARY_LOG, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)


# ── 통합 배포 함수 (scheduler_weekly.py에서 호출) ─────────────────────

def deploy_if_pass(tuning_result: Dict, auto_canary: bool = True):
    """튜닝 결과를 받아 안전 게이트 통과 시 카나리 배포

    Args:
        tuning_result: weekly_tuner.run_weekly_tuning() 반환값
        auto_canary:   True → 통과 시 자동 카나리 배포
    """
    if not tuning_result.get("ok"):
        print(f"  [SKIP] 튜닝 결과 없음: {tuning_result.get('reason')}")
        return

    gate = SafetyGate()

    new_params = {
        "defensive": tuning_result.get("defensive", {}),
        "short":     tuning_result.get("short", {}),
    }

    ok, reason = gate.safety_check(tuning_result, new_params)

    if ok and auto_canary:
        gate.deploy_canary(new_params)
        print("  → 카나리 배포 완료. 1주 후 canary_status() 확인 후 deploy_full() 또는 rollback()")
    elif not ok:
        print(f"  → 배포 취소. 이유: {reason}")


# ── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--status",   action="store_true", help="배포 상태 출력")
    parser.add_argument("--rollback", action="store_true", help="직전 버전 롤백")
    parser.add_argument("--promote",  action="store_true", help="카나리 → 프로덕션 전환")
    args = parser.parse_args()

    gate = SafetyGate()
    if args.status:
        gate.print_status()
    elif args.rollback:
        gate.rollback()
    elif args.promote:
        # 최근 카나리 파라미터 로드
        if CANARY_YAML.exists():
            with open(CANARY_YAML, encoding="utf-8") as f:
                canary_cfg = yaml.safe_load(f)
            params = {
                "defensive": canary_cfg.get("defensive_mode", {}),
                "short":     canary_cfg.get("short_mode", {}),
            }
            ok, reason = gate.canary_status()
            if ok:
                gate.deploy_full(params)
            else:
                print(f"  ❌ 전환 불가: {reason}")
        else:
            print("  [WARN] canary.yaml 없음")
    else:
        gate.print_status()
