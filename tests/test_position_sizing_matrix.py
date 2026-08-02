"""
v1.3 Position Sizing Matrix Tests — core/position_sizing.py

검증 케이스 (spec 요구):
  1. Primary + no penalty
  2. Primary + G3 penalty
  3. RAE only
  4. RAE + G3 penalty
  5. RAE + G3 + CAUTION
  6. RAE + G3 + DANGER + session_mult
  7. Hard max cap
  8. size 0 / NaN 방어 케이스
  9. HALT → size=0
 10. conservative mode
 11. TED soft penalty 중첩
"""

import pytest
from core.position_sizing import compute_final_size, ROUTE_MULT, DD_MULT


class TestPrimaryRoute:
    def test_primary_no_penalty(self):
        r = compute_final_size(base_size=0.5, route='PRIMARY')
        assert r['final_size'] == pytest.approx(0.5, abs=1e-4)
        assert r['components']['route_mult'] == 1.0
        assert r['components']['g3_mult'] == 1.0

    def test_primary_g3_penalty(self):
        r = compute_final_size(base_size=0.5, route='PRIMARY', g3_penalty=True, g3_penalty_mult=0.6)
        # 0.5 × 0.6 = 0.3
        assert r['final_size'] == pytest.approx(0.3, abs=1e-4)
        assert r['components']['g3_mult'] == 0.6

    def test_primary_conservative(self):
        r = compute_final_size(base_size=1.0, route='PRIMARY', conservative=True)
        # 1.0 × 0.5 = 0.5
        assert r['final_size'] == pytest.approx(0.5, abs=1e-4)

    def test_primary_caution(self):
        r = compute_final_size(base_size=1.0, route='PRIMARY', dd_level='CAUTION')
        # 1.0 × 0.7 = 0.7
        assert r['final_size'] == pytest.approx(0.7, abs=1e-4)


class TestRAERoute:
    def test_rae_only(self):
        r = compute_final_size(base_size=0.5, route='RAE')
        # 0.5 × 0.7 = 0.35
        assert r['final_size'] == pytest.approx(0.35, abs=1e-4)
        assert r['components']['route_mult'] == 0.7

    def test_rae_g3_penalty(self):
        r = compute_final_size(base_size=0.5, route='RAE', g3_penalty=True, g3_penalty_mult=0.6)
        # 0.5 × 0.7 × 0.6 = 0.21
        assert r['final_size'] == pytest.approx(0.21, abs=1e-4)

    def test_rae_g3_caution(self):
        r = compute_final_size(base_size=0.5, route='RAE', g3_penalty=True, g3_penalty_mult=0.6, dd_level='CAUTION')
        # 0.5 × 0.7 × 0.6 × 0.7 = 0.147
        assert r['final_size'] == pytest.approx(0.147, abs=1e-4)

    def test_rae_g3_danger_session(self):
        r = compute_final_size(
            base_size=0.5, route='RAE',
            g3_penalty=True, g3_penalty_mult=0.6,
            dd_level='DANGER', session_mult=0.8
        )
        # 0.5 × 0.7 × 0.6 × 0.4 × 0.8 = 0.0672
        expected = 0.5 * 0.7 * 0.6 * 0.4 * 0.8
        assert r['final_size'] == pytest.approx(expected, abs=1e-4)

    def test_rae_ted_soft(self):
        r = compute_final_size(base_size=0.5, route='RAE', ted_mult=0.8)
        # 0.5 × 0.7 × 0.8 = 0.28
        assert r['final_size'] == pytest.approx(0.28, abs=1e-4)


class TestEdgeCases:
    def test_halt_zero(self):
        r = compute_final_size(base_size=1.0, route='PRIMARY', dd_level='HALT')
        assert r['final_size'] == 0.0

    def test_rae_halt_zero(self):
        r = compute_final_size(base_size=1.0, route='RAE', dd_level='HALT')
        assert r['final_size'] == 0.0

    def test_hard_max_cap(self):
        r = compute_final_size(base_size=3.0, route='PRIMARY', hard_max=2.0)
        assert r['final_size'] == pytest.approx(2.0, abs=1e-4)
        assert r['clamped'] is True

    def test_base_zero(self):
        r = compute_final_size(base_size=0.0, route='RAE')
        assert r['final_size'] == 0.0

    def test_negative_base_clamped(self):
        r = compute_final_size(base_size=-0.5, route='PRIMARY')
        assert r['final_size'] == 0.0

    def test_session_mult_zero(self):
        r = compute_final_size(base_size=0.5, route='PRIMARY', session_mult=0.0)
        assert r['final_size'] == 0.0

    def test_conservative_rae_combined(self):
        r = compute_final_size(
            base_size=1.0, route='RAE',
            conservative=True, conservative_mult=0.5
        )
        # 1.0 × 0.7 × 0.5 = 0.35
        assert r['final_size'] == pytest.approx(0.35, abs=1e-4)

    def test_components_always_present(self):
        r = compute_final_size(base_size=0.4)
        for key in ('base', 'route_mult', 'g3_mult', 'dd_mult', 'session_mult', 'conservative_mult', 'ted_mult'):
            assert key in r['components'], f"components에 '{key}' 없음"

    def test_result_keys_always_present(self):
        r = compute_final_size(base_size=0.5, route='RAE', g3_penalty=True, dd_level='CAUTION')
        for key in ('final_size', 'components', 'clamped', 'route', 'dd_level'):
            assert key in r, f"결과에 '{key}' 없음"


class TestSizingMatrix:
    """스펙 요구 8개 케이스 전체 매트릭스"""

    CASES = [
        # (label,              base, route,   g3,    dd_level,  session, cons,  expected_approx)
        ('Primary+no_penalty', 0.5, 'PRIMARY', False, 'NORMAL',  1.0,   False,  0.5),
        ('Primary+G3',         0.5, 'PRIMARY', True,  'NORMAL',  1.0,   False,  0.3),
        ('RAE_only',           0.5, 'RAE',     False, 'NORMAL',  1.0,   False,  0.35),
        ('RAE+G3',             0.5, 'RAE',     True,  'NORMAL',  1.0,   False,  0.21),
        ('RAE+G3+CAUTION',     0.5, 'RAE',     True,  'CAUTION', 1.0,   False,  0.147),
        ('RAE+G3+DANGER+sess', 0.5, 'RAE',     True,  'DANGER',  0.8,   False,  0.5*0.7*0.6*0.4*0.8),
        ('HardMax_cap',        3.0, 'PRIMARY', False, 'NORMAL',  1.0,   False,  2.0),
        ('Base_zero',          0.0, 'RAE',     True,  'CAUTION', 1.0,   False,  0.0),
    ]

    @pytest.mark.parametrize("label,base,route,g3,dd,sess,cons,expected", CASES)
    def test_matrix(self, label, base, route, g3, dd, sess, cons, expected):
        r = compute_final_size(
            base_size=base, route=route,
            g3_penalty=g3, g3_penalty_mult=0.6,
            dd_level=dd, session_mult=sess, conservative=cons,
            hard_max=2.0
        )
        assert r['final_size'] == pytest.approx(expected, abs=1e-3), \
            f"[{label}] expected={expected:.4f} got={r['final_size']:.4f}"
