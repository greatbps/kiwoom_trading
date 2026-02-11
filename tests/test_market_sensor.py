"""
Market Sensor 통합 테스트
- 2026-02-10: EF 기반 시장 상태 판별 → 진입 차단
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from unittest.mock import patch
from metrics.reentry_metrics import ReentryMetrics


def make_config(enabled=True, morning_limit=2, cutoff='12:00', risk_off_limit=3):
    return {
        'enabled': enabled,
        'morning_ef_limit': morning_limit,
        'morning_cutoff': cutoff,
        'risk_off_no_follow_limit': risk_off_limit,
    }


def test_01_disabled():
    """enabled=false → 항상 진입 허용"""
    m = ReentryMetrics()
    cfg = make_config(enabled=False)
    result = m.record_ef_event('no_follow', cfg)
    assert not result['afternoon_blocked']
    assert not result['risk_off']

    can, reason = m.can_enter_trade(cfg)
    assert can is True
    print("PASS: test_01 disabled")


def test_02_morning_ef_blocks_afternoon():
    """오전 EF 2회 → 오후 진입 차단"""
    m = ReentryMetrics()
    cfg = make_config()

    # 오전 10:30에 EF 2회 발생 시뮬레이션
    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 10, 30)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        r1 = m.record_ef_event('no_follow', cfg)
        assert not r1['afternoon_blocked']  # 1회: 아직 안됨

        r2 = m.record_ef_event('no_follow', cfg)
        assert r2['afternoon_blocked']  # 2회: 차단!
        assert 'MARKET_SENSOR' in r2['message']

    # 오전에는 진입 가능
    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 11, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        can, reason = m.can_enter_trade(cfg)
        assert can is True  # 오전은 여전히 허용

    # 오후에는 차단
    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 13, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        can, reason = m.can_enter_trade(cfg)
        assert can is False
        assert 'AFTERNOON_BLOCKED' in reason

    print("PASS: test_02 morning_ef_blocks_afternoon")


def test_03_risk_off_day():
    """no_follow 3회 → Risk OFF Day"""
    m = ReentryMetrics()
    cfg = make_config()

    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        # 시간 무관 (오전+오후 혼합)
        mock_dt.now.return_value = datetime(2026, 2, 10, 10, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m.record_ef_event('no_follow', cfg)
        m.record_ef_event('no_follow', cfg)  # 이것도 오전이니 afternoon_block도 발동

    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 13, 30)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r3 = m.record_ef_event('no_follow', cfg)
        assert r3['risk_off']
        assert 'RISK OFF DAY' in r3['message']

    # Risk OFF → 오전이든 오후든 전면 차단
    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 10, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        can, reason = m.can_enter_trade(cfg)
        assert can is False
        assert 'RISK_OFF_DAY' in reason

    print("PASS: test_03 risk_off_day")


def test_04_no_demand_counts_separately():
    """no_demand는 no_follow 카운터와 별도"""
    m = ReentryMetrics()
    cfg = make_config(risk_off_limit=3)

    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 10, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        m.record_ef_event('no_demand', cfg)
        m.record_ef_event('no_demand', cfg)
        r3 = m.record_ef_event('no_demand', cfg)
        # no_demand 3회 → risk_off는 no_follow 기준이므로 OFF
        assert not r3['risk_off']
        assert m._ms_ef_no_demand == 3
        assert m._ms_ef_no_follow == 0

    # 하지만 오전 EF 2회이므로 afternoon_block은 ON (subtype 무관)
    assert m._ms_afternoon_blocked is True

    print("PASS: test_04 no_demand separate")


def test_05_mixed_subtype():
    """no_follow 2회 + no_demand 1회 → 오전 EF 3회, risk_off 안됨"""
    m = ReentryMetrics()
    cfg = make_config(morning_limit=2, risk_off_limit=3)

    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 10, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        m.record_ef_event('no_follow', cfg)
        m.record_ef_event('no_demand', cfg)  # 2번째 오전 EF → afternoon_block
        r = m.record_ef_event('no_follow', cfg)

    assert m._ms_afternoon_blocked is True  # 오전 3회
    assert m._ms_ef_no_follow == 2
    assert m._ms_ef_no_demand == 1
    assert not r['risk_off']  # no_follow는 2회뿐, 3회 미달

    print("PASS: test_05 mixed_subtype")


def test_06_report_includes_market_sensor():
    """generate_report()에 market_sensor 섹션 포함"""
    m = ReentryMetrics()
    cfg = make_config()

    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 10, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m.record_ef_event('no_follow', cfg)

    report = m.generate_report()
    ms = report.get('market_sensor')
    assert ms is not None
    assert ms['ef_total'] == 1
    assert ms['ef_morning'] == 1
    assert ms['ef_no_follow'] == 1
    assert ms['afternoon_blocked'] is False  # 1회만이라 아직 안됨
    assert ms['risk_off'] is False

    print("PASS: test_06 report includes market_sensor")


def test_07_afternoon_ef_no_morning_count():
    """오후 EF는 오전 카운터에 포함 안됨"""
    m = ReentryMetrics()
    cfg = make_config(morning_limit=2)

    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 13, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        m.record_ef_event('no_follow', cfg)
        r2 = m.record_ef_event('no_follow', cfg)

    # 오후 EF 2회 → 오전 카운터 0, afternoon_block 안됨
    assert m._ms_ef_morning == 0
    assert not r2['afternoon_blocked']

    # 하지만 no_follow 2회 → risk_off는 3회이므로 아직 안됨
    assert not r2['risk_off']

    print("PASS: test_07 afternoon_ef_no_morning_count")


def test_08_risk_off_overrides_afternoon_ok():
    """Risk OFF 상태면 오전 시간이어도 차단"""
    m = ReentryMetrics()
    cfg = make_config(risk_off_limit=2)

    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 14, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        m.record_ef_event('no_follow', cfg)
        m.record_ef_event('no_follow', cfg)

    # Risk OFF → 오전 시간(10:00)이어도 차단
    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 10, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        can, reason = m.can_enter_trade(cfg)
        assert can is False
        assert 'RISK_OFF_DAY' in reason

    print("PASS: test_08 risk_off_overrides_afternoon_ok")


def test_09_real_scenario_feb10():
    """2/10 실매매 시나리오 재현: EF[no_follow] 3회"""
    m = ReentryMetrics()
    cfg = make_config(morning_limit=2, risk_off_limit=3)

    # 12:12 레이크머티리얼즈 EF[no_follow] → 오후 EF
    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 12, 12)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r1 = m.record_ef_event('no_follow', cfg)
    assert not r1['afternoon_blocked']  # 오후 EF는 오전 카운터 불포함

    # 12:50 에스티아이 EF[no_follow] → 오후 EF
    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 12, 50)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r2 = m.record_ef_event('no_follow', cfg)
    assert not r2['risk_off']  # 2회, 아직

    # 가정: 13:30에 3번째 EF 발생
    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 13, 30)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r3 = m.record_ef_event('no_follow', cfg)
    assert r3['risk_off']  # 3회 → RISK OFF

    # 이후 진입 시도 차단
    with patch('metrics.reentry_metrics.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 2, 10, 14, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        can, reason = m.can_enter_trade(cfg)
    assert can is False
    assert 'RISK_OFF_DAY' in reason

    print("PASS: test_09 real_scenario_feb10")


def test_10_yaml_config_load():
    """YAML market_sensor 설정 로드 확인"""
    import yaml
    with open('config/strategy_hybrid.yaml', 'r') as f:
        cfg = yaml.safe_load(f)

    ms = cfg.get('re_entry', {}).get('reentry_cooldown', {}).get('market_sensor', {})
    assert ms.get('enabled') is True, f"market_sensor not found or not enabled: {ms}"
    assert ms.get('morning_ef_limit') == 2
    assert ms.get('morning_cutoff') == '12:00'
    assert ms.get('risk_off_no_follow_limit') == 3

    print("PASS: test_10 yaml_config_load")


if __name__ == '__main__':
    test_01_disabled()
    test_02_morning_ef_blocks_afternoon()
    test_03_risk_off_day()
    test_04_no_demand_counts_separately()
    test_05_mixed_subtype()
    test_06_report_includes_market_sensor()
    test_07_afternoon_ef_no_morning_count()
    test_08_risk_off_overrides_afternoon_ok()
    test_09_real_scenario_feb10()
    test_10_yaml_config_load()
    print()
    print("=" * 50)
    print("All 10 Market Sensor tests PASSED")
    print("=" * 50)
