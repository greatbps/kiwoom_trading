"""
tests/test_eq_model.py — EQ ML Filter 단위 테스트

테스트 대상:
    - FeatureLogger: 진입/결과 기록, 통계, 스키마 마이그레이션
    - EQModel: predict, should_block (shadow/live), train, feature_importance
    - _encode: 모든 인코딩 경계값
"""

import math
import pickle
import pytest
from pathlib import Path


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_eq.db"


@pytest.fixture
def fl(db_path):
    from ml.feature_logger import FeatureLogger
    return FeatureLogger(db_path=str(db_path))


def _make_features(**overrides):
    base = {
        'choch_grade':      'A',
        'eq_grade':         'B',
        'entry_confidence': 0.75,
        'r_pct':            2.0,
        'htf_trend':        1,
        'sweep':            1,
        'atr_pct':          1.5,
        'volume_ratio':     1.8,
        'rsi':              58.0,
        'squeeze_on':       0,
        'time_slot':        90,
        'regime':           'BULL',
        'guard_state':      'normal',
        'vwap_dist':        0.5,
        'ema_slope':        0.3,
        'vol_zscore':       1.2,
    }
    base.update(overrides)
    return base


def _make_data(n: int, win_rate: float = 0.5) -> list[dict]:
    rows = []
    for i in range(n):
        f = _make_features(rsi=40.0 + i % 30, time_slot=60 + i % 120)
        f['outcome_win'] = 1 if i < int(n * win_rate) else 0
        f['outcome_pnl_pct'] = 1.5 if f['outcome_win'] else -1.0
        rows.append(f)
    return rows


# ── FeatureLogger 테스트 ──────────────────────────────────────────

class TestFeatureLogger:
    def test_log_entry_returns_id(self, fl):
        row_id = fl.log_entry('005930', '삼성전자', 70000.0, _make_features())
        assert row_id > 0

    def test_log_entry_increments(self, fl):
        id1 = fl.log_entry('005930', '삼성전자', 70000.0, _make_features())
        id2 = fl.log_entry('000660', 'SK하이닉스', 140000.0, _make_features())
        assert id2 == id1 + 1

    def test_log_outcome_updates_win(self, fl):
        fl.log_entry('005930', '삼성전자', 70000.0, _make_features())
        fl.log_outcome('005930', pnl_pct=1.5, exit_reason='trailing_stop')
        s = fl.stats()
        assert s['labeled'] == 1
        assert s['win_rate'] == 100.0

    def test_log_outcome_loss(self, fl):
        fl.log_entry('005930', '삼성전자', 70000.0, _make_features())
        fl.log_outcome('005930', pnl_pct=-1.0, exit_reason='hard_stop')
        s = fl.stats()
        assert s['win_rate'] == 0.0

    def test_stats_zero_when_empty(self, fl):
        s = fl.stats()
        assert s['total'] == 0
        assert s['labeled'] == 0
        assert s['win_rate'] == 0

    def test_load_labeled_returns_empty_below_min(self, fl):
        fl.log_entry('005930', '삼성전자', 70000.0, _make_features())
        fl.log_outcome('005930', 1.0, 'trailing_stop')
        data = fl.load_labeled(min_samples=5)
        assert data == []

    def test_load_labeled_returns_data_above_min(self, fl):
        for i in range(3):
            fl.log_entry(f'00{i:04d}', f'종목{i}', 10000.0, _make_features())
            fl.log_outcome(f'00{i:04d}', 1.0, 'trailing_stop')
        data = fl.load_labeled(min_samples=2)
        assert len(data) == 3

    def test_outcome_does_not_update_unmatched_stock(self, fl):
        fl.log_entry('005930', '삼성전자', 70000.0, _make_features())
        fl.log_outcome('000660', 1.0, 'trailing_stop')  # 다른 종목
        s = fl.stats()
        assert s['labeled'] == 0

    def test_new_columns_stored(self, fl):
        fl.log_entry('005930', '삼성전자', 70000.0, _make_features(
            vwap_dist=0.5, ema_slope=0.3, vol_zscore=1.2
        ))
        fl.log_outcome('005930', 1.0, 'trailing_stop')
        data = fl.load_labeled(min_samples=1)
        row = data[0]
        assert row['vwap_dist'] == pytest.approx(0.5)
        assert row['ema_slope'] == pytest.approx(0.3)
        assert row['vol_zscore'] == pytest.approx(1.2)

    def test_migration_adds_columns_to_existing_table(self, tmp_path):
        """이미 존재하는 DB에 신규 컬럼 추가 확인"""
        import sqlite3
        db = tmp_path / "old.db"
        # 구버전 스키마 (신규 컬럼 없음)
        conn = sqlite3.connect(str(db))
        conn.execute("""
            CREATE TABLE entry_features (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                entry_price REAL,
                outcome_win INTEGER,
                outcome_pnl_pct REAL,
                exit_reason TEXT,
                exit_timestamp TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

        from ml.feature_logger import FeatureLogger
        fl2 = FeatureLogger(db_path=str(db))  # _init_table 실행
        cols = [r[1] for r in sqlite3.connect(str(db))
                .execute('PRAGMA table_info(entry_features)').fetchall()]
        assert 'vwap_dist' in cols
        assert 'ema_slope' in cols
        assert 'vol_zscore' in cols


# ── _encode 테스트 ────────────────────────────────────────────────

class TestEncode:
    def test_encode_length(self):
        from ml.eq_model import _encode, FEATURES
        row = _make_features()
        result = _encode(row)
        assert len(result) == len(FEATURES)

    def test_encode_choch_grade_A(self):
        from ml.eq_model import _encode
        assert _encode(_make_features(choch_grade='A'))[9] == 2
        assert _encode(_make_features(choch_grade='A+'))[9] == 2

    def test_encode_choch_grade_B(self):
        from ml.eq_model import _encode
        assert _encode(_make_features(choch_grade='B'))[9] == 1

    def test_encode_choch_grade_C(self):
        from ml.eq_model import _encode
        assert _encode(_make_features(choch_grade='C'))[9] == 0

    def test_encode_unknown_choch_defaults_to_1(self):
        from ml.eq_model import _encode
        assert _encode(_make_features(choch_grade=None))[9] == 1
        assert _encode(_make_features(choch_grade=''))[9] == 1

    def test_encode_regime_bull(self):
        from ml.eq_model import _encode
        assert _encode(_make_features(regime='BULL'))[11] == 2
        assert _encode(_make_features(regime='TREND'))[11] == 2

    def test_encode_regime_bear(self):
        from ml.eq_model import _encode
        assert _encode(_make_features(regime='BEAR'))[11] == 0
        assert _encode(_make_features(regime='REVERSAL'))[11] == 0

    def test_encode_regime_sideways(self):
        from ml.eq_model import _encode
        assert _encode(_make_features(regime='SIDEWAYS'))[11] == 1
        assert _encode(_make_features(regime='UNKNOWN'))[11] == 1

    def test_encode_guard_enc(self):
        from ml.eq_model import _encode
        assert _encode(_make_features(guard_state='normal'))[12] == 0
        assert _encode(_make_features(guard_state='lsg'))[12] == 1
        assert _encode(_make_features(guard_state='conservative'))[12] == 2

    def test_encode_new_features_nan_when_none(self):
        from ml.eq_model import _encode
        row = _make_features(vwap_dist=None, ema_slope=None, vol_zscore=None)
        result = _encode(row)
        assert math.isnan(result[13])  # vwap_dist
        assert math.isnan(result[14])  # ema_slope
        assert math.isnan(result[15])  # vol_zscore

    def test_encode_new_features_values(self):
        from ml.eq_model import _encode
        row = _make_features(vwap_dist=0.5, ema_slope=0.3, vol_zscore=1.2)
        result = _encode(row)
        assert result[13] == pytest.approx(0.5)
        assert result[14] == pytest.approx(0.3)
        assert result[15] == pytest.approx(1.2)


# ── EQModel 테스트 ────────────────────────────────────────────────

class TestEQModel:
    def _config(self, shadow=True, enabled=True, threshold=0.40):
        return {'eq_ml_filter': {
            'enabled': enabled,
            'shadow_mode': shadow,
            'threshold': threshold,
            'min_train_samples': 5,
            'retrain_interval_trades': 10,
        }}

    def test_predict_returns_half_without_model(self, tmp_path, monkeypatch):
        from ml import eq_model
        monkeypatch.setattr(eq_model, 'MODEL_PATH', tmp_path / 'nonexistent.pkl')
        m = eq_model.EQModel(config=self._config())
        assert m._model is None
        assert m.predict(_make_features()) == pytest.approx(0.5)

    def test_should_block_false_when_disabled(self):
        from ml.eq_model import EQModel
        m = EQModel(config=self._config(enabled=False))
        block, p = m.should_block(_make_features())
        assert block is False

    def test_should_block_false_in_shadow_mode(self, tmp_path):
        from ml.eq_model import EQModel
        m = EQModel(config=self._config(shadow=True))
        m.min_samples = 1
        m.train(_make_data(20, win_rate=0.3))
        block, p = m.should_block(_make_features())
        assert block is False  # shadow mode → never block

    def test_train_requires_min_samples(self, tmp_path, monkeypatch):
        from ml import eq_model
        monkeypatch.setattr(eq_model, 'MODEL_PATH', tmp_path / 'eq_model.pkl')
        m = eq_model.EQModel(config=self._config())
        m.min_samples = 50
        ok = m.train(_make_data(10))
        assert ok is False
        assert m._model is None

    def test_train_succeeds_with_enough_data(self, tmp_path, monkeypatch):
        from ml import eq_model
        monkeypatch.setattr(eq_model, 'MODEL_PATH', tmp_path / 'eq_model.pkl')
        m = eq_model.EQModel(config=self._config())
        m.min_samples = 1
        ok = m.train(_make_data(30))
        assert ok is True
        assert m._model is not None

    def test_predict_after_train_returns_probability(self, tmp_path, monkeypatch):
        from ml import eq_model
        monkeypatch.setattr(eq_model, 'MODEL_PATH', tmp_path / 'eq_model.pkl')
        m = eq_model.EQModel(config=self._config())
        m.min_samples = 1
        m.train(_make_data(40))
        p = m.predict(_make_features())
        assert 0.0 <= p <= 1.0

    def test_should_block_live_mode(self, tmp_path, monkeypatch):
        from ml import eq_model
        monkeypatch.setattr(eq_model, 'MODEL_PATH', tmp_path / 'eq_model.pkl')
        m = eq_model.EQModel(config=self._config(shadow=False, threshold=0.99))
        m.min_samples = 1
        # 모든 샘플을 패배로 → p_win 낮아짐 → threshold=0.99 초과 차단
        data = _make_data(40, win_rate=0.0)
        for d in data:
            d['outcome_win'] = 0
        m.train(data)
        block, p = m.should_block(_make_features())
        assert block is True

    def test_should_not_block_live_with_low_threshold(self, tmp_path, monkeypatch):
        from ml import eq_model
        monkeypatch.setattr(eq_model, 'MODEL_PATH', tmp_path / 'eq_model.pkl')
        # threshold=0.0 → p_win(>=0) 항상 통과
        m = eq_model.EQModel(config=self._config(shadow=False, threshold=0.0))
        m.min_samples = 1
        m.train(_make_data(40, win_rate=0.5))
        block, p = m.should_block(_make_features())
        assert block is False

    def test_model_save_and_load(self, tmp_path, monkeypatch):
        from ml import eq_model
        monkeypatch.setattr(eq_model, 'MODEL_PATH', tmp_path / 'eq_model.pkl')

        m1 = eq_model.EQModel(config=self._config())
        m1.min_samples = 1
        m1.train(_make_data(20))
        p1 = m1.predict(_make_features())

        m2 = eq_model.EQModel(config=self._config())
        p2 = m2.predict(_make_features())
        assert p1 == pytest.approx(p2, abs=1e-6)

    def test_feature_importance_after_train(self, tmp_path, monkeypatch):
        from ml import eq_model
        monkeypatch.setattr(eq_model, 'MODEL_PATH', tmp_path / 'eq_model.pkl')
        m = eq_model.EQModel(config=self._config())
        m.min_samples = 1
        m.train(_make_data(30))
        imp = m.feature_importance()
        assert len(imp) == 16  # 13 + 3 new features
        assert all(v >= 0 for v in imp.values())

    def test_maybe_retrain_triggers_on_interval(self, tmp_path, db_path, monkeypatch):
        from ml import eq_model
        from ml.feature_logger import FeatureLogger
        monkeypatch.setattr(eq_model, 'MODEL_PATH', tmp_path / 'eq_model.pkl')

        fl = FeatureLogger(db_path=str(db_path))
        for i in range(10):
            fl.log_entry(f'00{i:04d}', f'종목{i}', 10000.0, _make_features())
            fl.log_outcome(f'00{i:04d}', 1.0 if i % 2 == 0 else -1.0, 'trailing_stop')

        m = eq_model.EQModel(config=self._config())
        m.min_samples = 5
        m.retrain_every = 3

        # 1st, 2nd call: count=1,2 → no trigger
        m.maybe_retrain(fl)
        m.maybe_retrain(fl)
        assert m._model is None

        # 3rd call: count=3, 3%3==0 → triggers
        m.maybe_retrain(fl)
        assert m._model is not None
