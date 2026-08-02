#!/usr/bin/env python3
"""
LCL v2.1 단위 테스트 + 통합 테스트

실행:
  python3 tests/test_lcl_v2.py

테스트 구성:
  [T01] YAML 설정 검증
  [T02] EARLY_CUT — 발동 (PASS)
  [T03] EARLY_CUT — RSI 기준 이상 → 미발동
  [T04] EARLY_CUT — VWAP 1봉만 이탈 (3봉 미충족) → 미발동
  [T05] EARLY_CUT — 거래량 decay 없음 → 미발동
  [T06] EARLY_CUT — pnl > 0 → 미발동
  [T07] EARLY_CUT — slope (RSI 상승) → 미발동
  [T08] TIME_STOP — 발동 (비스윙, 20분+)
  [T09] TIME_STOP — 스윙 포지션 → 미발동
  [T10] TIME_STOP — 고변동성 bdh≥5% → 15분 임계
  [T11] TIME_STOP — 고변동성 bdh≥10% → 12분 임계
  [T12] TIME_STOP_MFE — 발동
  [T13] TIME_STOP_MFE — mfe_pct None → 미발동
  [T14] VOL_TIGHT_STOP — 발동 (bdh≥5%, pnl≤-2.5%)
  [T15] VOL_TIGHT_STOP — bdh 미달 → 미발동
  [T16] VOL_TIGHT_STOP — 스윙 → 미발동
  [T17] MAE_WORSENING — 발동
  [T18] MAE_WORSENING — mfe_pct None → 미발동
  [T19] MAE_WORSENING — MFE 기준 충족 → 미발동
  [T20] BIG_WINNER — pnl > 0 → LCL 전체 미발동
  [T21] stock_code NameError 버그 — 발생 여부 확인
  [T22] df < 6행 → LCL 전체 미발동
  [T23] LCL enabled:false → 전체 미발동
  [T24] DB 연결 + 최근 trades 조회
  [T25] live_lcl_validation.py 실행 가능
  [T26] backtest_156_full_audit.py 실행 가능
  [T27] lcl_trigger_log_parser.py 실행 가능
"""

import sys
import os
import subprocess
import traceback
import yaml
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from trading.exit_logic_optimized import OptimizedExitLogic
from dotenv import load_dotenv

load_dotenv()

# ─── 결과 추적 ───────────────────────────────────────────────────────────────
RESULTS = []

def ok(tid, desc):
    RESULTS.append((tid, "✅ PASS", desc))
    print(f"  ✅ [{tid}] {desc}")

def fail(tid, desc, detail=""):
    RESULTS.append((tid, "❌ FAIL", desc))
    print(f"  ❌ [{tid}] {desc}")
    if detail:
        print(f"       → {detail}")

def warn(tid, desc, detail=""):
    RESULTS.append((tid, "⚠  WARN", desc))
    print(f"  ⚠  [{tid}] {desc}")
    if detail:
        print(f"       → {detail}")


# ─── 공통 픽스처 ─────────────────────────────────────────────────────────────

def load_yaml_config():
    with open("config/strategy_hybrid.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)

def make_exit_logic(config=None):
    if config is None:
        config = load_yaml_config()
    return OptimizedExitLogic(config)

def make_df(n=10, rsi_vals=None, vwap_above=False,
            vol_vals=None, high_pct=3.0, low_pct=0.0) -> pd.DataFrame:
    """
    n봉 mock DataFrame 생성
    rsi_vals: list[float], 없으면 기본값 35
    vwap_above: True이면 vwap > close (VWAP 이탈)
    vol_vals: list, 없으면 최근 봉이 감소하는 패턴
    high_pct: 일중 고가 % (bdh 계산용)
    """
    base_price = 10000.0
    high = base_price * (1 + high_pct / 100)
    low  = base_price * (1 - low_pct / 100)

    close_vals = [base_price] * n
    rsi = rsi_vals if rsi_vals else [35.0] * n
    # RSI 하락 추세: 기본은 rsi[-1] < rsi[-2]
    if rsi_vals is None:
        rsi = [40.0] * (n - 1) + [35.0]  # 마지막만 낮춤

    if vol_vals is None:
        # 마지막 2봉 감소 패턴 (decay + 평균 대비 수축)
        base_vol = 1000
        vol_vals = [base_vol] * (n - 2) + [600, 400]  # 평균보다 작고 decay

    # vwap 설정
    vwap_vals = ([base_price * 1.01] * n  # close < vwap = 이탈
                 if vwap_above else [base_price * 0.99] * n)

    df = pd.DataFrame({
        "close":  [float(v) for v in close_vals],
        "high":   [high]   * n,
        "low":    [low]    * n,
        "rsi":    [float(v) for v in rsi[:n]],
        "vwap":   [float(v) for v in vwap_vals],
        "volume": [float(v) for v in vol_vals[:n]],
    })
    return df

def make_position(entry_price=10000.0, strategy_horizon="",
                  elapsed_min=25.0, mfe_pct=None, mae_pct=None) -> dict:
    """기본 포지션 dict"""
    entry_time = datetime.now() - timedelta(minutes=elapsed_min)
    pos = {
        "entry_price":       entry_price,
        "entry_time":        entry_time,
        "highest_price":     entry_price,
        "partial_exit_stage": 0,
        "strategy_horizon":  strategy_horizon,
        "strategy":          "smc",
        "stock_code":        "999999",
        "grade":             "A",
    }
    if mfe_pct is not None: pos["mfe_pct"] = mfe_pct
    if mae_pct is not None: pos["mae_pct"] = mae_pct
    return pos

def current_price_for_pnl(entry_price, pnl_pct):
    """목표 pnl이 나오는 current_price 계산"""
    return entry_price * (1 + pnl_pct / 100)


SEP = "=" * 68

# ─── 테스트 함수들 ────────────────────────────────────────────────────────────

def t01_yaml_config():
    """YAML LCL 설정 검증"""
    try:
        cfg = load_yaml_config()
        rc = cfg.get("risk_control", {})
        lcl = rc.get("loss_control_layer", {})

        assert lcl.get("enabled") == True,             "enabled != True"
        assert "early_cut"    in lcl,                  "early_cut 섹션 없음"
        assert "time_stop"    in lcl,                  "time_stop 섹션 없음"
        assert "vol_tight_stop" in lcl,                "vol_tight_stop 섹션 없음"
        assert "mfe_mae_exit" in lcl,                  "mfe_mae_exit 섹션 없음"

        ec = lcl["early_cut"]
        assert ec.get("enabled"),                      "early_cut.enabled != True"
        assert float(ec.get("rsi_threshold")) == 38.0, "rsi_threshold != 38"
        assert int(ec.get("vwap_periods"))    == 3,    "vwap_periods != 3"
        assert ec.get("rsi_slope_check"),              "rsi_slope_check != True"
        assert ec.get("volume_decay_check"),           "volume_decay_check != True"

        ts = lcl["time_stop"]
        assert ts.get("enabled"),                      "time_stop.enabled != True"
        assert float(ts.get("max_loss_minutes")) == 20, "max_loss_minutes != 20"
        assert float(ts.get("high_vol_bdh_pct")) == 5.0, "high_vol_bdh_pct != 5.0"
        assert float(ts.get("high_vol_minutes")) == 15,  "high_vol_minutes != 15"

        mfm = lcl["mfe_mae_exit"]
        assert mfm.get("enabled"),                     "mfe_mae_exit.enabled != True"
        assert float(mfm.get("mae_worsening_ratio")) == 0.95, "mae_worsening_ratio != 0.95"

        ok("T01", "YAML LCL 설정 — 모든 필드 정상")
    except AssertionError as e:
        fail("T01", "YAML LCL 설정 검증 실패", str(e))
    except Exception as e:
        fail("T01", "YAML 로드 오류", str(e))


def t02_early_cut_fires():
    """EARLY_CUT: 5개 조건 모두 충족 → 발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10)
        df  = make_df(n=10, rsi_vals=[42,41,40,39,38,37,36,37,36,34],
                      vwap_above=True,
                      vol_vals=[1000,1000,1000,1000,1000,800,600,500,400,300])
        cur = current_price_for_pnl(10000, -0.8)
        fired, reason, meta = el.check_exit_signal(pos, cur, df)
        assert fired,                          f"미발동 reason={reason}"
        assert "[EARLY_CUT]" in reason,        f"잘못된 reason={reason}"
        ok("T02", f"EARLY_CUT 발동 확인 ({reason[:50]})")
    except AssertionError as e:
        fail("T02", "EARLY_CUT 발동 실패", str(e))
    except Exception as e:
        fail("T02", "EARLY_CUT 예외", traceback.format_exc()[-200:])


def t03_early_cut_no_fire_rsi_above():
    """EARLY_CUT: RSI >= 38 → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10)
        # RSI 마지막 2봉 40, 39 (< 38 아님)
        df = make_df(n=10, rsi_vals=[42,41,40,39,38,40,39,40,39,40],
                     vwap_above=True,
                     vol_vals=[1000,1000,1000,1000,1000,800,600,500,400,300])
        cur = current_price_for_pnl(10000, -0.8)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[EARLY_CUT]" not in reason, \
            f"RSI 기준 초과인데 발동: {reason}"
        ok("T03", "EARLY_CUT RSI 기준 미달 → 미발동")
    except AssertionError as e:
        fail("T03", "EARLY_CUT RSI 오작동", str(e))
    except Exception as e:
        fail("T03", "예외", traceback.format_exc()[-200:])


def t04_early_cut_no_fire_vwap_only_1bar():
    """EARLY_CUT: VWAP 이탈 1봉만 → 3봉 미충족 → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10)
        df = make_df(n=10, rsi_vals=[42,41,40,39,38,37,36,37,36,34],
                     vwap_above=False,   # close > vwap (이탈 아님)
                     vol_vals=[1000,1000,1000,1000,1000,800,600,500,400,300])
        # 마지막 1봉만 vwap 이탈로 변경
        df.loc[df.index[-1], "vwap"] = df["close"].iloc[-1] * 1.01
        cur = current_price_for_pnl(10000, -0.8)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[EARLY_CUT]" not in reason, \
            f"VWAP 1봉인데 발동: {reason}"
        ok("T04", "EARLY_CUT VWAP 1봉 이탈만 → 미발동")
    except AssertionError as e:
        fail("T04", "EARLY_CUT VWAP 오작동", str(e))
    except Exception as e:
        fail("T04", "예외", traceback.format_exc()[-200:])


def t05_early_cut_no_fire_vol_not_decay():
    """EARLY_CUT: 거래량 decay 없음 (최근봉 > 직전봉) → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10)
        # 거래량: 마지막 봉이 직전봉보다 큼 (decay 없음)
        df = make_df(n=10, rsi_vals=[42,41,40,39,38,37,36,37,36,34],
                     vwap_above=True,
                     vol_vals=[1000,1000,1000,1000,800,600,500,400,300,500])
        # vol[-1]=500 > vol[-2]=300 → decay=False
        cur = current_price_for_pnl(10000, -0.8)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[EARLY_CUT]" not in reason, \
            f"거래량 decay 없는데 발동: {reason}"
        ok("T05", "EARLY_CUT 거래량 decay 없음 → 미발동")
    except AssertionError as e:
        fail("T05", "EARLY_CUT 거래량 decay 오작동", str(e))
    except Exception as e:
        fail("T05", "예외", traceback.format_exc()[-200:])


def t06_early_cut_no_fire_positive_pnl():
    """EARLY_CUT: pnl > 0 → LCL 자체 진입 안 함 (profit_pct < 0 조건)"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10)
        df  = make_df(n=10, rsi_vals=[42,41,40,39,38,37,36,37,36,34],
                      vwap_above=True,
                      vol_vals=[1000,1000,1000,1000,1000,800,600,500,400,300])
        cur = current_price_for_pnl(10000, +1.0)  # pnl 양수
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[EARLY_CUT]" not in reason, \
            f"pnl>0인데 EARLY_CUT 발동: {reason}"
        ok("T06", "EARLY_CUT pnl>0 → 미발동 (LCL 진입 조건 미충족)")
    except AssertionError as e:
        fail("T06", "EARLY_CUT pnl>0 오작동", str(e))
    except Exception as e:
        fail("T06", "예외", traceback.format_exc()[-200:])


def t07_early_cut_no_fire_rsi_slope_rising():
    """EARLY_CUT: RSI 하락이 아닌 상승 → slope 조건 미충족 → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10)
        # RSI: 마지막 봉이 직전보다 높음 (30 → 35, slope 상승)
        df = make_df(n=10, rsi_vals=[40,39,38,37,36,35,34,33,30,35],
                     vwap_above=True,
                     vol_vals=[1000,1000,1000,1000,1000,800,600,500,400,300])
        cur = current_price_for_pnl(10000, -0.8)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[EARLY_CUT]" not in reason, \
            f"RSI 상승인데 발동: {reason}"
        ok("T07", "EARLY_CUT RSI slope 상승 → 미발동")
    except AssertionError as e:
        fail("T07", "EARLY_CUT slope 오작동", str(e))
    except Exception as e:
        fail("T07", "예외", traceback.format_exc()[-200:])


def t08_time_stop_fires():
    """TIME_STOP: 비스윙, 21분 경과, pnl=-1.0% → 발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=21, strategy_horizon="")  # 비스윙
        df  = make_df(n=10, high_pct=2.0)  # bdh < 5% (기본 20분 임계)
        cur = current_price_for_pnl(10000, -1.0)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert fired,                          f"미발동 reason={reason}"
        assert "[TIME_STOP]" in reason,        f"잘못된 reason={reason}"
        ok("T08", f"TIME_STOP 발동 확인 ({reason[:50]})")
    except AssertionError as e:
        fail("T08", "TIME_STOP 발동 실패", str(e))
    except Exception as e:
        fail("T08", "예외", traceback.format_exc()[-200:])


def t09_time_stop_no_fire_swing():
    """TIME_STOP: 스윙 포지션 → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=25, strategy_horizon="SWING")
        df  = make_df(n=10, high_pct=2.0)
        cur = current_price_for_pnl(10000, -1.0)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[TIME_STOP]" not in reason, \
            f"스윙인데 TIME_STOP 발동: {reason}"
        ok("T09", "TIME_STOP 스윙 → 미발동")
    except AssertionError as e:
        fail("T09", "TIME_STOP 스윙 오작동", str(e))
    except Exception as e:
        fail("T09", "예외", traceback.format_exc()[-200:])


def t10_time_stop_high_vol_15min():
    """TIME_STOP: bdh≥5% 고변동성 → 15분 임계, 16분 경과 → 발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=16, strategy_horizon="")
        df  = make_df(n=10, high_pct=5.5)  # bdh = 5.5% ≥ 5%
        cur = current_price_for_pnl(10000, -1.0)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert fired,                    f"미발동 reason={reason}"
        assert "[TIME_STOP]" in reason,  f"잘못된 reason={reason}"
        assert "15분" in reason or "15" in reason, f"임계 15분 미반영: {reason}"
        ok("T10", f"TIME_STOP 고변동성(bdh≥5%) 15분 임계 발동 ({reason[:45]})")
    except AssertionError as e:
        fail("T10", "TIME_STOP 고변동성 임계 실패", str(e))
    except Exception as e:
        fail("T10", "예외", traceback.format_exc()[-200:])


def t11_time_stop_very_high_vol_12min():
    """TIME_STOP: bdh≥10% 초고변동성 → 12분 임계, 13분 경과 → 발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=13, strategy_horizon="")
        df  = make_df(n=10, high_pct=10.5)  # bdh = 10.5% ≥ 10%
        cur = current_price_for_pnl(10000, -1.0)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert fired,                    f"미발동 reason={reason}"
        assert "[TIME_STOP]" in reason,  f"잘못된 reason={reason}"
        ok("T11", f"TIME_STOP 초고변동성(bdh≥10%) 12분 임계 발동 ({reason[:45]})")
    except AssertionError as e:
        fail("T11", "TIME_STOP 초고변동성 임계 실패", str(e))
    except Exception as e:
        fail("T11", "예외", traceback.format_exc()[-200:])


def t12_time_stop_mfe_fires():
    """TIME_STOP_MFE: 비스윙, 21분, MFE=0.1%<0.3%, pnl=-0.5% → 발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=21, strategy_horizon="", mfe_pct=0.1)
        df  = make_df(n=10, high_pct=2.0)
        cur = current_price_for_pnl(10000, -0.5)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert fired,                         f"미발동 reason={reason}"
        assert "[TIME_STOP_MFE]" in reason,   f"잘못된 reason={reason}"
        ok("T12", f"TIME_STOP_MFE 발동 확인 ({reason[:50]})")
    except AssertionError as e:
        fail("T12", "TIME_STOP_MFE 발동 실패", str(e))
    except Exception as e:
        fail("T12", "예외", traceback.format_exc()[-200:])


def t13_time_stop_mfe_no_fire_none():
    """TIME_STOP_MFE: mfe_pct=None → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=21, strategy_horizon="", mfe_pct=None)
        df  = make_df(n=10, high_pct=2.0)
        cur = current_price_for_pnl(10000, -0.5)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[TIME_STOP_MFE]" not in reason, \
            f"mfe=None인데 TIME_STOP_MFE 발동: {reason}"
        ok("T13", "TIME_STOP_MFE mfe_pct=None → 미발동")
    except AssertionError as e:
        fail("T13", "TIME_STOP_MFE None 오작동", str(e))
    except Exception as e:
        fail("T13", "예외", traceback.format_exc()[-200:])


def t14_vol_tight_stop_fires():
    """VOL_TIGHT_STOP: 비스윙, bdh=6%, pnl=-2.6% → 발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10, strategy_horizon="")
        df  = make_df(n=10, high_pct=6.0)  # bdh ≥ 5%
        cur = current_price_for_pnl(10000, -2.6)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert fired,                           f"미발동 reason={reason}"
        assert "[VOL_TIGHT_STOP]" in reason,    f"잘못된 reason={reason}"
        ok("T14", f"VOL_TIGHT_STOP 발동 확인 ({reason[:50]})")
    except AssertionError as e:
        fail("T14", "VOL_TIGHT_STOP 발동 실패", str(e))
    except Exception as e:
        fail("T14", "예외", traceback.format_exc()[-200:])


def t15_vol_tight_stop_no_fire_low_bdh():
    """VOL_TIGHT_STOP: bdh=3% < 5% → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10, strategy_horizon="")
        df  = make_df(n=10, high_pct=3.0)  # bdh < 5%
        cur = current_price_for_pnl(10000, -2.6)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[VOL_TIGHT_STOP]" not in reason, \
            f"bdh<5% 인데 발동: {reason}"
        ok("T15", "VOL_TIGHT_STOP bdh 미달 → 미발동")
    except AssertionError as e:
        fail("T15", "VOL_TIGHT_STOP bdh 오작동", str(e))
    except Exception as e:
        fail("T15", "예외", traceback.format_exc()[-200:])


def t16_vol_tight_stop_no_fire_swing():
    """VOL_TIGHT_STOP: 스윙 포지션 → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10, strategy_horizon="SWING")
        df  = make_df(n=10, high_pct=6.0)
        cur = current_price_for_pnl(10000, -2.6)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[VOL_TIGHT_STOP]" not in reason, \
            f"스윙인데 VOL_TIGHT_STOP 발동: {reason}"
        ok("T16", "VOL_TIGHT_STOP 스윙 → 미발동")
    except AssertionError as e:
        fail("T16", "VOL_TIGHT_STOP 스윙 오작동", str(e))
    except Exception as e:
        fail("T16", "예외", traceback.format_exc()[-200:])


def t17_mae_worsening_fires():
    """MAE_WORSENING: 비스윙, 11분, MFE=0.1%<0.3%, MAE=0.5%, pnl=-0.48% → 발동"""
    try:
        el = make_exit_logic()
        # mae_pct=0.5%, pnl=-0.48% → abs(-0.48) = 0.48 ≥ 0.5 * 0.95 = 0.475 → 발동
        pos = make_position(elapsed_min=11, strategy_horizon="",
                            mfe_pct=0.1, mae_pct=0.5)
        df  = make_df(n=10, high_pct=2.0)
        cur = current_price_for_pnl(10000, -0.48)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert fired,                          f"미발동 reason={reason}"
        assert "[MAE_WORSENING]" in reason,    f"잘못된 reason={reason}"
        ok("T17", f"MAE_WORSENING 발동 확인 ({reason[:50]})")
    except AssertionError as e:
        fail("T17", "MAE_WORSENING 발동 실패", str(e))
    except Exception as e:
        fail("T17", "예외", traceback.format_exc()[-200:])


def t18_mae_worsening_no_fire_no_mfe():
    """MAE_WORSENING: mfe_pct=None → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=11, strategy_horizon="",
                            mfe_pct=None, mae_pct=0.5)
        df  = make_df(n=10, high_pct=2.0)
        cur = current_price_for_pnl(10000, -0.48)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[MAE_WORSENING]" not in reason, \
            f"mfe=None인데 MAE_WORSENING 발동: {reason}"
        ok("T18", "MAE_WORSENING mfe_pct=None → 미발동")
    except AssertionError as e:
        fail("T18", "MAE_WORSENING None 오작동", str(e))
    except Exception as e:
        fail("T18", "예외", traceback.format_exc()[-200:])


def t19_mae_worsening_no_fire_mfe_ok():
    """MAE_WORSENING: MFE=0.5% ≥ 0.3% → MFE stagnation 조건 미충족 → 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=11, strategy_horizon="",
                            mfe_pct=0.5, mae_pct=0.5)   # MFE 충분
        df  = make_df(n=10, high_pct=2.0)
        cur = current_price_for_pnl(10000, -0.48)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        assert not fired or "[MAE_WORSENING]" not in reason, \
            f"MFE 충분한데 발동: {reason}"
        ok("T19", "MAE_WORSENING MFE≥0.3% → 미발동")
    except AssertionError as e:
        fail("T19", "MAE_WORSENING MFE 조건 오작동", str(e))
    except Exception as e:
        fail("T19", "예외", traceback.format_exc()[-200:])


def t20_big_winner_not_cut():
    """BIG_WINNER: pnl=+3% → LCL 전체 미발동"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10)
        df  = make_df(n=10, rsi_vals=[42,41,40,39,38,37,36,37,36,34],
                      vwap_above=True,
                      vol_vals=[1000,1000,1000,1000,1000,800,600,500,400,300])
        cur = current_price_for_pnl(10000, +3.0)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        lcl_triggered = any(tag in (reason or "") for tag in
                            ["[EARLY_CUT]","[TIME_STOP]","[VOL_TIGHT]","[MAE_WORSENING]"])
        assert not lcl_triggered, f"pnl>0인데 LCL 발동: {reason}"
        ok("T20", "BIG_WINNER pnl=+3% → LCL 미발동")
    except AssertionError as e:
        fail("T20", "BIG_WINNER 오컷 감지!", str(e))
    except Exception as e:
        fail("T20", "예외", traceback.format_exc()[-200:])


def t21_stock_code_nameerror():
    """stock_code NameError 버그 — overnight defer 경로"""
    try:
        el = make_exit_logic()
        # 스윙 포지션 + 어제 진입 (overnight) + 09:00~09:20 내 시뮬레이션
        # 실제 09:xx가 아니면 overnight_open_defer 블록 진입 안 함
        # 위험: stock_code 미정의인데 logger.info에서 참조됨
        # 코드에서 position.get('stock_code')로 대체했는지 확인
        import ast
        with open("trading/exit_logic_optimized.py", "r", encoding="utf-8") as f:
            source = f.read()

        # 버그 패턴: stock_code 변수가 OVERNIGHT_DEFER 로그에 직접 쓰임
        bad_pattern_1 = "OVERNIGHT_DEFER] {stock_code}"
        bad_pattern_2 = "OVERNIGHT_DEFER_SKIP] {stock_code}"
        bad_pattern_3 = "SWING_NO_STRUCTURE_STOP] {stock_code}"

        found_bugs = []
        if bad_pattern_1 in source: found_bugs.append("OVERNIGHT_DEFER stock_code")
        if bad_pattern_2 in source: found_bugs.append("OVERNIGHT_DEFER_SKIP stock_code")
        if bad_pattern_3 in source: found_bugs.append("SWING_NO_STRUCTURE_STOP stock_code")

        if found_bugs:
            warn("T21", f"stock_code NameError 버그 존재",
                 f"{found_bugs} → 운영 중 09:00~09:20 스윙 포지션에서 NameError 발생 가능")
        else:
            ok("T21", "stock_code NameError 버그 없음")
    except Exception as e:
        fail("T21", "T21 검사 오류", str(e))


def t22_lcl_no_fire_short_df():
    """df < 6행 → LCL 전체 미발동 (데이터 부족 보호)"""
    try:
        el = make_exit_logic()
        pos = make_position(elapsed_min=10)
        df  = make_df(n=4)   # 4행 (< 6)
        cur = current_price_for_pnl(10000, -1.0)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        lcl_triggered = any(tag in (reason or "") for tag in
                            ["[EARLY_CUT]","[TIME_STOP]","[VOL_TIGHT_STOP]","[MAE_WORSENING]"])
        assert not lcl_triggered, f"df<6인데 LCL 발동: {reason}"
        ok("T22", "df < 6행 → LCL 전체 미발동")
    except AssertionError as e:
        fail("T22", "df<6 보호 실패", str(e))
    except Exception as e:
        fail("T22", "예외", traceback.format_exc()[-200:])


def t23_lcl_disabled():
    """loss_control_layer.enabled: false → LCL 전체 미발동"""
    try:
        cfg = load_yaml_config()
        cfg.setdefault("risk_control", {}).setdefault("loss_control_layer", {})["enabled"] = False
        el = make_exit_logic(cfg)
        pos = make_position(elapsed_min=25)
        df  = make_df(n=10, rsi_vals=[42,41,40,39,38,37,36,37,36,34],
                      vwap_above=True,
                      vol_vals=[1000,1000,1000,1000,1000,800,600,500,400,300])
        cur = current_price_for_pnl(10000, -1.0)
        fired, reason, _ = el.check_exit_signal(pos, cur, df)
        lcl_triggered = any(tag in (reason or "") for tag in
                            ["[EARLY_CUT]","[TIME_STOP]","[VOL_TIGHT_STOP]","[MAE_WORSENING]"])
        assert not lcl_triggered, f"enabled=False인데 발동: {reason}"
        ok("T23", "LCL enabled:false → 전체 미발동")
    except AssertionError as e:
        fail("T23", "LCL enabled:false 오작동", str(e))
    except Exception as e:
        fail("T23", "예외", traceback.format_exc()[-200:])


def t24_db_connection():
    """PostgreSQL DB 연결 + trades 테이블 조회"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname="trading_system", user="postgres",
            password=os.getenv("POSTGRES_PASSWORD"), host="localhost"
        )
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM trades WHERE trade_type='SELL'")
        n = cur.fetchone()[0]
        cur.execute("""
            SELECT count(*) FROM trades
            WHERE trade_type='SELL' AND exit_time >= '2026-07-04'
        """)
        new_n = cur.fetchone()[0]
        conn.close()
        assert n > 0, f"SELL 거래 없음: {n}"
        ok("T24", f"DB 연결 정상 (총 SELL={n}건, LCL배포후={new_n}건)")
    except AssertionError as e:
        fail("T24", "DB 조회 실패", str(e))
    except Exception as e:
        fail("T24", "DB 연결 오류", str(e))


def t25_live_lcl_validation():
    """live_lcl_validation.py 실행 가능"""
    try:
        result = subprocess.run(
            ["python3", "analysis/live_lcl_validation.py"],
            capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, \
            f"returncode={result.returncode}\nSTDERR: {result.stderr[-300:]}"
        assert "LCL v2.1 Live Validation" in result.stdout, \
            f"예상 출력 없음: {result.stdout[:200]}"
        ok("T25", "live_lcl_validation.py 실행 정상")
    except AssertionError as e:
        fail("T25", "live_lcl_validation.py 실패", str(e))
    except subprocess.TimeoutExpired:
        fail("T25", "live_lcl_validation.py 타임아웃")
    except Exception as e:
        fail("T25", "예외", str(e))


def t26_backtest_156():
    """backtest_156_full_audit.py 실행 가능"""
    try:
        result = subprocess.run(
            ["python3", "analysis/backtest_156_full_audit.py"],
            capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, \
            f"returncode={result.returncode}\nSTDERR: {result.stderr[-300:]}"
        assert "PASS / FAIL" in result.stdout, \
            f"판정 섹션 없음: {result.stdout[-200:]}"
        ok("T26", "backtest_156_full_audit.py 실행 정상")
    except AssertionError as e:
        fail("T26", "backtest_156_full_audit.py 실패", str(e))
    except subprocess.TimeoutExpired:
        fail("T26", "backtest_156_full_audit.py 타임아웃")
    except Exception as e:
        fail("T26", "예외", str(e))


def t27_log_parser():
    """lcl_trigger_log_parser.py 실행 가능"""
    try:
        result = subprocess.run(
            ["python3", "ops/lcl_trigger_log_parser.py"],
            capture_output=True, text=True, timeout=15
        )
        assert result.returncode == 0, \
            f"returncode={result.returncode}\nSTDERR: {result.stderr[-300:]}"
        assert "LCL Trigger Log Report" in result.stdout, \
            f"예상 출력 없음: {result.stdout[:200]}"
        ok("T27", "lcl_trigger_log_parser.py 실행 정상")
    except AssertionError as e:
        fail("T27", "lcl_trigger_log_parser.py 실패", str(e))
    except subprocess.TimeoutExpired:
        fail("T27", "lcl_trigger_log_parser.py 타임아웃")
    except Exception as e:
        fail("T27", "예외", str(e))


# ─── 버그 수정 ────────────────────────────────────────────────────────────────

def fix_stock_code_bug():
    """stock_code NameError 버그 수정 (section 1-c, section 2)"""
    with open("trading/exit_logic_optimized.py", "r", encoding="utf-8") as f:
        src = f.read()

    fixed = src
    # 1-c 버그 수정
    fixed = fixed.replace(
        'f"[OVERNIGHT_DEFER] {stock_code} 09:00~09:{_end_min:02d} HARD_STOP 유예 "',
        'f"[OVERNIGHT_DEFER] {position.get(\'stock_code\',\'?\')} 09:00~09:{_end_min:02d} HARD_STOP 유예 "'
    )
    fixed = fixed.replace(
        'f"[OVERNIGHT_DEFER_SKIP] {stock_code} 거래량 급증 "',
        'f"[OVERNIGHT_DEFER_SKIP] {position.get(\'stock_code\',\'?\')} 거래량 급증 "'
    )
    # section 2 버그 수정
    fixed = fixed.replace(
        'f"[SWING_NO_STRUCTURE_STOP] {stock_code} 구조손절 미설정 "',
        'f"[SWING_NO_STRUCTURE_STOP] {position.get(\'stock_code\',\'?\')} 구조손절 미설정 "'
    )

    if fixed != src:
        with open("trading/exit_logic_optimized.py", "w", encoding="utf-8") as f:
            f.write(fixed)
        return True
    return False


# ─── 메인 ────────────────────────────────────────────────────────────────────

def main():
    print(SEP)
    print("  LCL v2.1 검증 테스트 스위트")
    print(f"  실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)

    # stock_code 버그 먼저 확인
    print("\n  [사전 점검] stock_code NameError 버그 체크...")
    with open("trading/exit_logic_optimized.py", "r", encoding="utf-8") as f:
        src = f.read()
    has_bug = "OVERNIGHT_DEFER] {stock_code}" in src
    if has_bug:
        print("  ⚠  stock_code NameError 버그 발견 → 자동 수정 중...")
        patched = fix_stock_code_bug()
        if patched:
            import importlib
            print("  ✅ 버그 수정 완료 (position.get('stock_code','?') 로 대체)")
        # 컴파일 확인
        result = subprocess.run(
            ["python3", "-m", "py_compile", "trading/exit_logic_optimized.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  ❌ 컴파일 오류: {result.stderr}")
            sys.exit(1)
        print("  ✅ 컴파일 확인")
    else:
        print("  ✅ stock_code 버그 없음")

    print("\n  ── 유닛 테스트 (LCL 트리거) ────────────────────────────────")
    t01_yaml_config()
    t02_early_cut_fires()
    t03_early_cut_no_fire_rsi_above()
    t04_early_cut_no_fire_vwap_only_1bar()
    t05_early_cut_no_fire_vol_not_decay()
    t06_early_cut_no_fire_positive_pnl()
    t07_early_cut_no_fire_rsi_slope_rising()
    t08_time_stop_fires()
    t09_time_stop_no_fire_swing()
    t10_time_stop_high_vol_15min()
    t11_time_stop_very_high_vol_12min()
    t12_time_stop_mfe_fires()
    t13_time_stop_mfe_no_fire_none()
    t14_vol_tight_stop_fires()
    t15_vol_tight_stop_no_fire_low_bdh()
    t16_vol_tight_stop_no_fire_swing()
    t17_mae_worsening_fires()
    t18_mae_worsening_no_fire_no_mfe()
    t19_mae_worsening_no_fire_mfe_ok()
    t20_big_winner_not_cut()
    t21_stock_code_nameerror()
    t22_lcl_no_fire_short_df()
    t23_lcl_disabled()

    print("\n  ── 통합 테스트 (DB + 스크립트) ─────────────────────────────")
    t24_db_connection()
    t25_live_lcl_validation()
    t26_backtest_156()
    t27_log_parser()

    # ── 결과 요약 ─────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    total  = len(RESULTS)
    passed = sum(1 for _, v, _ in RESULTS if v == "✅ PASS")
    warned = sum(1 for _, v, _ in RESULTS if v == "⚠  WARN")
    failed = sum(1 for _, v, _ in RESULTS if v == "❌ FAIL")

    print(f"  결과: {passed} PASS / {warned} WARN / {failed} FAIL  (총 {total}건)")
    print()

    if failed:
        print("  ❌ FAIL 목록:")
        for tid, verdict, desc in RESULTS:
            if verdict == "❌ FAIL":
                print(f"    [{tid}] {desc}")

    if warned:
        print("  ⚠  WARN 목록:")
        for tid, verdict, desc in RESULTS:
            if verdict == "⚠  WARN":
                print(f"    [{tid}] {desc}")

    if failed == 0:
        print("  ✅ 전체 PASS — 월요일 실전 투입 가능")
    else:
        print(f"  ❌ {failed}건 FAIL — 수정 후 재실행 필요")

    print(SEP)
    return failed


if __name__ == "__main__":
    sys.exit(main())
