"""
tests/unit/test_swing_runner_logic.py

swing_runner.py 핵심 로직 단위 테스트
  (fetch_daily / SignalEngine 목(mock) 처리 — 네트워크 없이 순수 필터 로직 검증)

커버 대상:
  A. 섹터 필터 (_sector_of / scan_new_signals 내부)
     A1. sector 없는 종목 → UNKNOWN 버킷으로 처리 (통과 아님)
     A2. 동일 섹터 2개 → 1개만 통과
     A3. UNKNOWN 버킷도 max_same_sector=1 제한 적용
     A4. 다른 섹터 3개 → 3개 모두 통과 (max_positions 여유 있을 때)

  B. 노출 한도 (max_total_exposure)
     B1. 기존 보유 0.7 + 신규 0.5 = 1.2 → 0.80 초과 → 신규 차단

  C. 포트폴리오 업그레이드 (find_upgrade_candidate)
     C1. 신호 점수 > 최약체 점수 → (weakest, signal) 반환
     C2. 신호 점수 <= 최약체 점수 → None 반환
     C3. 포지션 없음 → None 반환
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from unittest.mock import patch, MagicMock
from datetime import date

from analyzers.swing.state_machine import SwingPosition, SwingState


# ─── 헬퍼 ────────────────────────────────────────────────────────────────────

_BASE_CFG = {
    "swing": {
        "max_positions": 5,
        "min_score_to_enter": 5.0,
        "top_n_candidates": 3,
        "max_total_exposure": 0.80,
        "sector": {"max_same_sector": 1},
        "size": {"initial": 0.50},
        "data": {"lookback_days": 120},
    }
}


def _make_pos(code: str, score: float, size: float = 0.20) -> SwingPosition:
    p = SwingPosition(
        stock_code=code,
        stock_name=code,
        state=SwingState.HOLD,
        score=score,
    )
    p.allocated_size = size
    return p


def _mock_signal(final_score: float, size: float = 0.5) -> dict:
    """SignalEngine.run() 반환값 모의."""
    return {
        "pattern": "pullback",
        "score": final_score / 1.0,
        "final_score": final_score,
        "size": size,
        "trigger": True,
        "phase": "TRIGGER",
        "entry": 10000,
        "stop": 9700,
        "target": 11000,
        "confidence": 0.8,
        "meta": {},
    }


# ─── A. 섹터 필터 ────────────────────────────────────────────────────────────

class TestSectorFilter:

    def _run_scan(self, universe, existing_positions=None, config=None, top_n=3):
        """scan_new_signals를 fetch_daily와 SignalEngine을 목(mock)하여 실행."""
        from swing_runner import scan_new_signals
        if existing_positions is None:
            existing_positions = {}
        if config is None:
            config = _BASE_CFG

        # 모든 종목에 동일한 신호 반환 (각자 점수는 universe의 '_score' 필드로 제어)
        def fake_engine_run(self_inner):
            # universe에서 현재 종목 코드를 찾아 점수 결정
            return _mock_signal(final_score=7.0)

        with patch("swing_runner.fetch_daily") as mock_fetch, \
             patch("swing_runner.SignalEngine") as mock_engine_cls:
            mock_fetch.return_value = MagicMock()  # 유효한 DataFrame 흉내
            instance = MagicMock()
            instance.run.return_value = _mock_signal(7.0)
            mock_engine_cls.return_value = instance

            return scan_new_signals(universe, existing_positions, config, top_n=top_n)

    def test_a1_unknown_sector_treated_as_bucket(self):
        """A1: sector 없는 종목 → UNKNOWN 버킷. 첫 번째만 통과, 두 번째는 차단."""
        universe = [
            {"code": "A001", "name": "종목A", "market": "KS"},  # sector 없음
            {"code": "A002", "name": "종목B", "market": "KS"},  # sector 없음
        ]
        candidates = self._run_scan(universe, top_n=3)
        codes = [c["code"] for c in candidates]
        # UNKNOWN 버킷 max=1 → 1개만 통과
        assert len(candidates) == 1, f"UNKNOWN 버킷 1개 제한 미적용, 실제={codes}"

    def test_a2_same_sector_only_one_passes(self):
        """A2: 동일 섹터 2개 신호 → 1개만 선정."""
        universe = [
            {"code": "B001", "name": "반도체A", "sector": "반도체", "market": "KS"},
            {"code": "B002", "name": "반도체B", "sector": "반도체", "market": "KS"},
        ]
        candidates = self._run_scan(universe, top_n=3)
        assert len(candidates) == 1, "동일 섹터 2개 → 1개만 통과해야 함"
        assert candidates[0]["sector"] == "반도체"

    def test_a3_unknown_bucket_max_one(self):
        """A3: sector='' / None 모두 UNKNOWN 버킷 → 합산 1개 제한."""
        universe = [
            {"code": "C001", "name": "종목C", "sector": "", "market": "KS"},
            {"code": "C002", "name": "종목D", "market": "KS"},  # key 없음
        ]
        candidates = self._run_scan(universe, top_n=3)
        assert len(candidates) == 1, "빈/없는 섹터 모두 UNKNOWN → 1개 제한"

    def test_a4_different_sectors_all_pass(self):
        """A4: 섹터 3개 모두 다르면 노출 한도 내에서 전부 통과.

        노출 한도와 분리하기 위해 max_total_exposure=2.0 사용.
        (섹터 필터만 검증; 노출 필터는 TestExposureCap에서 별도 검증)
        """
        cfg_no_exp_limit = {
            "swing": {
                **_BASE_CFG["swing"],
                "max_total_exposure": 2.0,   # 노출 한도 비활성화
            }
        }
        universe = [
            {"code": "D001", "name": "반도체", "sector": "반도체", "market": "KS"},
            {"code": "D002", "name": "바이오",  "sector": "바이오",  "market": "KS"},
            {"code": "D003", "name": "금융",    "sector": "금융",    "market": "KS"},
        ]
        candidates = self._run_scan(universe, config=cfg_no_exp_limit, top_n=3)
        assert len(candidates) == 3, f"서로 다른 섹터 3개 → 3개 모두 통과, 실제={len(candidates)}"

    def test_a5_held_sector_blocks_same_new(self):
        """A5: 기존 보유 종목과 동일 섹터 신규 → 차단."""
        from analyzers.swing.state_machine import SwingPosition, SwingState
        existing = {
            "E000": _make_pos("E000", score=6.0),
        }
        # universe_map에 E000 sector 등록하기 위해 universe에 포함
        universe = [
            {"code": "E000", "name": "보유중", "sector": "반도체", "market": "KS"},
            {"code": "E001", "name": "신규반도체", "sector": "반도체", "market": "KS"},
        ]
        # E000은 existing_codes에 있으므로 skip, E001은 동일 섹터이나 보유 섹터와 충돌 → 차단
        candidates = self._run_scan(universe, existing_positions=existing, top_n=3)
        codes = [c["code"] for c in candidates]
        assert "E001" not in codes, "기존 보유 섹터와 동일한 신규 종목 → 차단"


# ─── B. 노출 한도 ────────────────────────────────────────────────────────────

class TestExposureCap:

    def test_b1_exposure_cap_blocks_new_entry(self):
        """B1: 기존 보유 exposure 0.7 + 신규 0.25 = 0.95 > 0.80 → 신규 차단."""
        from swing_runner import scan_new_signals

        existing = {"F000": _make_pos("F000", score=6.0, size=0.70)}
        universe = [
            {"code": "F001", "name": "신규", "sector": "IT", "market": "KS"},
        ]

        with patch("swing_runner.fetch_daily") as mock_fetch, \
             patch("swing_runner.SignalEngine") as mock_engine_cls:
            mock_fetch.return_value = MagicMock()
            instance = MagicMock()
            # size=0.5 → allocation = size(0.5) × initial(0.5) = 0.25
            instance.run.return_value = _mock_signal(7.0, size=0.5)
            mock_engine_cls.return_value = instance

            candidates = scan_new_signals(universe, existing, _BASE_CFG, top_n=3)

        assert len(candidates) == 0, "노출 0.70 + 0.25 = 0.95 > 0.80 → 신규 차단"


# ─── C. 포트폴리오 업그레이드 ─────────────────────────────────────────────────

class TestPortfolioUpgrade:

    def _run_upgrade(self, positions, universe, best_score=None):
        from swing_runner import find_upgrade_candidate

        with patch("swing_runner.fetch_daily") as mock_fetch, \
             patch("swing_runner.SignalEngine") as mock_engine_cls:
            mock_fetch.return_value = MagicMock()
            instance = MagicMock()
            if best_score is not None:
                instance.run.return_value = _mock_signal(best_score)
            else:
                instance.run.return_value = None
            mock_engine_cls.return_value = instance

            return find_upgrade_candidate(positions, universe, _BASE_CFG)

    def test_c1_upgrade_when_new_better(self):
        """C1: 신호 점수(9.0) > 최약체(5.0) → (weakest, signal) 반환."""
        positions = {
            "G001": _make_pos("G001", score=5.0),
            "G002": _make_pos("G002", score=7.0),
        }
        universe = [
            {"code": "G003", "name": "신규고점", "sector": "바이오", "market": "KS"},
        ]
        result = self._run_upgrade(positions, universe, best_score=9.0)
        assert result is not None, "더 좋은 신호 있을 때 업그레이드 후보 반환"
        weakest, signal = result
        assert weakest.stock_code == "G001", "최약체(5.0점) 교체 대상"
        assert signal["final_score"] == 9.0

    def test_c2_no_upgrade_when_not_better(self):
        """C2: 신호 점수(4.0) <= 최약체(5.0) → None."""
        positions = {"H001": _make_pos("H001", score=5.0)}
        universe = [
            {"code": "H002", "name": "약한신호", "sector": "IT", "market": "KS"},
        ]
        result = self._run_upgrade(positions, universe, best_score=4.0)
        assert result is None, "더 낮은 점수 → 업그레이드 없음"

    def test_c3_no_positions_returns_none(self):
        """C3: 보유 포지션 없음 → None (교체 대상 없음)."""
        from swing_runner import find_upgrade_candidate
        result = find_upgrade_candidate({}, [], _BASE_CFG)
        assert result is None

    def test_c4_upgrade_picks_weakest_not_random(self):
        """C4: 복수 포지션 중 score가 가장 낮은 것이 교체 대상."""
        positions = {
            "I001": _make_pos("I001", score=8.0),
            "I002": _make_pos("I002", score=6.0),
            "I003": _make_pos("I003", score=4.0),  # 최약체
        }
        universe = [
            {"code": "I004", "name": "강신호", "sector": "반도체", "market": "KS"},
        ]
        result = self._run_upgrade(positions, universe, best_score=9.5)
        assert result is not None
        weakest, _ = result
        assert weakest.stock_code == "I003", "최약체(4.0점)가 교체 대상이어야 함"


# ─── D. process_hold_positions ────────────────────────────────────────────────

class TestProcessHoldPositions:

    def _run_hold(self, positions, universe, action):
        """process_hold_positions를 HoldingManager.evaluate mock으로 실행."""
        from swing_runner import process_hold_positions

        with patch("swing_runner.fetch_daily") as mock_fetch, \
             patch("swing_runner.HoldingManager") as mock_mgr_cls:
            mock_fetch.return_value = MagicMock()
            instance = MagicMock()
            instance.evaluate.return_value = action
            instance.add_lot_size.return_value = 0.30
            mock_mgr_cls.return_value = instance

            return process_hold_positions(positions, _BASE_CFG, universe)

    def test_d1_exit_generates_sell_order(self):
        """D1: evaluate=EXIT → SELL 주문 큐에 등록."""
        positions = {"J001": _make_pos("J001", score=6.0)}
        universe  = [{"code": "J001", "name": "테스트", "market": "KS"}]
        orders = self._run_hold(positions, universe, "EXIT")
        sell = [o for o in orders if o["action"] == "SELL"]
        assert len(sell) == 1
        assert sell[0]["code"] == "J001"

    def test_d2_add_generates_add_order(self):
        """D2: evaluate=ADD → ADD 주문 큐에 등록."""
        pos = _make_pos("K001", score=7.0)
        positions = {"K001": pos}
        universe  = [{"code": "K001", "name": "테스트", "market": "KS"}]
        orders = self._run_hold(positions, universe, "ADD")
        add_orders = [o for o in orders if o["action"] == "ADD"]
        assert len(add_orders) == 1
        assert add_orders[0]["code"] == "K001"
        assert add_orders[0]["size"] == 0.30

    def test_d3_hold_generates_no_order(self):
        """D3: evaluate=HOLD → 주문 큐 비어 있음."""
        positions = {"L001": _make_pos("L001", score=6.0)}
        universe  = [{"code": "L001", "name": "테스트", "market": "KS"}]
        orders = self._run_hold(positions, universe, "HOLD")
        assert orders == []

    def test_d4_no_data_skipped(self):
        """D4: fetch_daily=None → 해당 종목 스킵, 주문 없음."""
        from swing_runner import process_hold_positions

        positions = {"M001": _make_pos("M001", score=6.0)}
        universe  = [{"code": "M001", "name": "테스트", "market": "KS"}]

        with patch("swing_runner.fetch_daily", return_value=None):
            orders = process_hold_positions(positions, _BASE_CFG, universe)

        assert orders == []


# ─── E. write_order_queue ─────────────────────────────────────────────────────

class TestWriteOrderQueue:

    def test_e1_creates_json_file(self, tmp_path):
        """E1: 주문 큐 JSON 파일 생성 및 내용 검증."""
        import json
        from swing_runner import write_order_queue

        orders = [{"code": "N001", "action": "BUY"}]
        out = write_order_queue(orders, str(tmp_path), date(2026, 5, 1))

        assert out.exists()
        payload = json.loads(out.read_text())
        assert payload["order_count"] == 1
        assert payload["orders"][0]["code"] == "N001"
        assert payload["run_date"] == "2026-05-01"

    def test_e2_empty_orders_valid_file(self, tmp_path):
        """E2: 주문 없어도 유효한 파일 생성."""
        import json
        from swing_runner import write_order_queue

        out = write_order_queue([], str(tmp_path), date(2026, 5, 1))
        payload = json.loads(out.read_text())
        assert payload["order_count"] == 0
        assert payload["orders"] == []


# ─── F. load_config / load_universe ──────────────────────────────────────────

class TestLoaders:

    def test_f1_load_config_reads_yaml(self, tmp_path):
        """F1: 유효한 YAML 파일 → 딕셔너리 반환."""
        import yaml
        from swing_runner import CONFIG_PATH
        cfg_file = tmp_path / "strategy_swing.yaml"
        cfg_file.write_text("swing:\n  max_positions: 3\n", encoding="utf-8")

        with patch("swing_runner.CONFIG_PATH", cfg_file):
            from swing_runner import load_config
            cfg = load_config()
        assert cfg.get("swing", {}).get("max_positions") == 3

    def test_f2_load_config_missing_file_returns_empty(self, tmp_path):
        """F2: 파일 없음 → 빈 딕셔너리 반환."""
        missing = tmp_path / "nonexistent.yaml"
        with patch("swing_runner.CONFIG_PATH", missing):
            from swing_runner import load_config
            cfg = load_config()
        assert cfg == {}

    def test_f3_load_universe_valid_json(self, tmp_path):
        """F3: 유효한 JSON → 리스트 반환."""
        import json
        from swing_runner import load_universe
        universe_file = tmp_path / "universe.json"
        data = [{"code": "O001", "name": "종목A"}]
        universe_file.write_text(json.dumps(data), encoding="utf-8")
        result = load_universe(str(universe_file))
        assert len(result) == 1
        assert result[0]["code"] == "O001"

    def test_f4_load_universe_missing_file(self, tmp_path):
        """F4: 파일 없음 → 빈 리스트 반환."""
        from swing_runner import load_universe
        result = load_universe(str(tmp_path / "no_file.json"))
        assert result == []


# ─── G. scan_new_signals 경계 경로 ────────────────────────────────────────────

class TestScanEdgePaths:

    def test_g1_max_positions_reached_returns_empty(self):
        """G1: 기존 포지션 >= max_positions(5) → 신규 탐색 생략."""
        from swing_runner import scan_new_signals

        positions = {f"P{i:03d}": _make_pos(f"P{i:03d}", score=6.0) for i in range(5)}
        universe = [{"code": "P099", "name": "신규", "sector": "IT", "market": "KS"}]

        with patch("swing_runner.fetch_daily") as mock_fetch:
            mock_fetch.return_value = MagicMock()
            candidates = scan_new_signals(universe, positions, _BASE_CFG, top_n=3)

        assert candidates == [], "max_positions 도달 → 빈 리스트"
        mock_fetch.assert_not_called()  # fetch 자체를 하지 않아야 함

    def test_g2_exposure_at_max_returns_empty(self):
        """G2: 기존 보유 exposure = max_total_exposure → 신규 생략."""
        from swing_runner import scan_new_signals

        existing = {"Q001": _make_pos("Q001", score=6.0, size=0.80)}
        universe = [{"code": "Q002", "name": "신규", "sector": "IT", "market": "KS"}]

        with patch("swing_runner.fetch_daily") as mock_fetch:
            candidates = scan_new_signals(universe, existing, _BASE_CFG, top_n=3)

        assert candidates == [], "exposure 한도 도달 → 빈 리스트"
        mock_fetch.assert_not_called()

    def test_g3_load_universe_json_error_returns_empty(self, tmp_path):
        """G3: JSON 파싱 오류 → 빈 리스트 반환 (crash 없음)."""
        from swing_runner import load_universe
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("THIS IS NOT JSON{{", encoding="utf-8")
        result = load_universe(str(bad_file))
        assert result == []

    def test_g4_trail_action_in_process_hold(self):
        """G4: evaluate=TRAIL → TRAIL 주문 큐에 등록."""
        from swing_runner import process_hold_positions

        positions = {"R001": _make_pos("R001", score=7.0)}
        universe  = [{"code": "R001", "name": "테스트", "market": "KS"}]

        with patch("swing_runner.fetch_daily") as mock_fetch, \
             patch("swing_runner.HoldingManager") as mock_mgr_cls:
            mock_fetch.return_value = MagicMock()
            instance = MagicMock()
            instance.evaluate.return_value = "TRAIL"
            mock_mgr_cls.return_value = instance

            orders = process_hold_positions(positions, _BASE_CFG, universe)

        trail = [o for o in orders if o["action"] == "TRAIL"]
        assert len(trail) == 1
        assert trail[0]["code"] == "R001"


# ─── H. main() 통합 목(mock) 테스트 ──────────────────────────────────────────

class TestMainIntegration:

    def test_h1_main_normal_flow(self, tmp_path):
        """H1: main() 정상 플로우 (전부 목) — 크래시 없이 완료, 주문 큐 생성."""
        import json, yaml
        from swing_runner import main

        # 설정 파일
        cfg_path = tmp_path / "strategy_swing.yaml"
        cfg_path.write_text(yaml.dump({"swing": {
            "max_positions": 5,
            "min_score_to_enter": 5.0,
            "top_n_candidates": 3,
            "max_total_exposure": 0.80,
            "sector": {"max_same_sector": 1},
            "size": {"initial": 0.50},
            "data": {
                "lookback_days": 120,
                "universe_file": str(tmp_path / "universe.json"),
                "state_file":    str(tmp_path / "positions.json"),
                "order_dir":     str(tmp_path),
            },
        }}), encoding="utf-8")

        universe = [{"code": "S001", "name": "테스트", "sector": "IT", "market": "KS"}]
        (tmp_path / "universe.json").write_text(json.dumps(universe), encoding="utf-8")

        with patch("swing_runner.CONFIG_PATH", cfg_path), \
             patch("swing_runner.fetch_daily", return_value=MagicMock()), \
             patch("swing_runner.SignalEngine") as mock_engine_cls, \
             patch("swing_runner.HoldingManager") as mock_hold_cls:

            mock_engine_cls.return_value.run.return_value = None   # 신호 없음
            mock_hold_cls.return_value.evaluate.return_value = "HOLD"

            main()  # 크래시 없이 완료해야 함

        # 주문 큐 파일 생성 확인
        order_files = list(tmp_path.glob("swing_orders_*.json"))
        assert len(order_files) == 1
        payload = json.loads(order_files[0].read_text())
        assert "orders" in payload

    def test_h2_main_empty_universe_returns_early(self, tmp_path):
        """H2: 유니버스 없음 → 조기 종료 (주문 파일 생성 안 함)."""
        import yaml
        from swing_runner import main

        cfg_path = tmp_path / "strategy_swing.yaml"
        cfg_path.write_text(yaml.dump({"swing": {
            "data": {
                "universe_file": str(tmp_path / "no_universe.json"),
                "state_file":    str(tmp_path / "positions.json"),
                "order_dir":     str(tmp_path),
            },
        }}), encoding="utf-8")

        with patch("swing_runner.CONFIG_PATH", cfg_path):
            main()

        order_files = list(tmp_path.glob("swing_orders_*.json"))
        assert len(order_files) == 0, "유니버스 없으면 주문 파일 미생성"

    def test_h3_main_with_upgrade(self, tmp_path):
        """H3: 포지션 꽉 찼을 때 upgrade 경로 실행 (2.5단계)."""
        import json, yaml
        from swing_runner import main
        from analyzers.swing.state_machine import SwingPosition, SwingState

        cfg_path = tmp_path / "strategy_swing.yaml"
        cfg_path.write_text(yaml.dump({"swing": {
            "max_positions": 1,   # 1개로 제한 → 기존 1개 = 꽉 참
            "min_score_to_enter": 5.0,
            "top_n_candidates": 3,
            "max_total_exposure": 0.80,
            "sector": {"max_same_sector": 1},
            "size": {"initial": 0.50},
            "data": {
                "lookback_days": 120,
                "universe_file": str(tmp_path / "universe.json"),
                "state_file":    str(tmp_path / "positions.json"),
                "order_dir":     str(tmp_path),
            },
        }}), encoding="utf-8")

        universe = [
            {"code": "T001", "name": "보유중", "sector": "IT", "market": "KS"},
            {"code": "T002", "name": "신규강자", "sector": "바이오", "market": "KS"},
        ]
        (tmp_path / "universe.json").write_text(json.dumps(universe), encoding="utf-8")

        # 기존 포지션: T001 (score=4.0)
        existing_pos = SwingPosition(
            stock_code="T001", stock_name="보유중",
            state=SwingState.HOLD, score=4.0,
        )
        (tmp_path / "positions.json").write_text(
            json.dumps({"T001": existing_pos.to_dict()}), encoding="utf-8"
        )

        with patch("swing_runner.CONFIG_PATH", cfg_path), \
             patch("swing_runner.fetch_daily", return_value=MagicMock()), \
             patch("swing_runner.HoldingManager") as mock_hold_cls, \
             patch("swing_runner.SignalEngine") as mock_engine_cls:

            mock_hold_cls.return_value.evaluate.return_value = "HOLD"
            # T002 신호가 T001 점수(4.0) 보다 높은 9.0
            mock_engine_cls.return_value.run.return_value = _mock_signal(9.0)

            main()

        order_files = list(tmp_path.glob("swing_orders_*.json"))
        assert len(order_files) == 1
        payload = json.loads(order_files[0].read_text())
        # PORTFOLIO_UPGRADE SELL 또는 BUY 주문이 있어야 함
        reasons = [o.get("reason", o.get("action", "")) for o in payload["orders"]]
        assert any("UPGRADE" in str(r) or "BUY" in str(r) for r in reasons), \
            f"업그레이드 경로 미실행, orders={payload['orders']}"
