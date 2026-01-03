"""
거래 이력 및 분석 데이터 관리 데이터베이스 V2
AI 학습, 백테스트, 실시간 모니터링 최적화 버전
"""
import sqlite3
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path


class TradingDatabaseV2:
    """자동매매 시스템 데이터베이스 V2 (AI/ML 최적화)"""

    def __init__(self, db_path: str = "data/trading_v2.db"):
        """
        Args:
            db_path: 데이터베이스 파일 경로
        """
        self.db_path = db_path

        # 데이터 디렉토리 생성
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 테이블 생성
        self._create_tables()

    def _create_tables(self):
        """모든 테이블 생성 (AI/ML 최적화 구조)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # ========================================
            # 1. trades - 실제 매매 기록
            # ========================================
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- 기본 정보
                    stock_code TEXT NOT NULL,
                    stock_name TEXT,
                    strategy TEXT,                    -- 어떤 조건식으로 검색됐는지

                    -- 매수 정보
                    entry_date DATETIME NOT NULL,     -- 매수일시
                    entry_price REAL NOT NULL,        -- 매수가
                    quantity INTEGER NOT NULL,        -- 수량
                    entry_amount REAL NOT NULL,       -- 매수금액

                    -- 매수 당시 시장 상황
                    entry_vwap REAL,
                    entry_ma20 REAL,
                    entry_volume_surge REAL,          -- 거래량 급등률
                    entry_z_score REAL,               -- Z-score
                    entry_trade_value_ratio REAL,     -- 거래대금 비율

                    -- 매수 결정 근거 (AI 학습용)
                    vwap_condition BOOLEAN,
                    ma20_condition BOOLEAN,
                    volume_condition BOOLEAN,
                    signal_strength REAL,             -- 진입 당시 종합 점수

                    -- VWAP 백테스트 결과 (매수 당시)
                    vwap_backtest_winrate REAL,
                    vwap_backtest_avg_profit REAL,
                    vwap_backtest_max_profit REAL,
                    vwap_backtest_max_loss REAL,
                    vwap_backtest_trades INTEGER,
                    vwap_backtest_score REAL,

                    -- 매도 정보
                    exit_date DATETIME,
                    exit_price REAL,
                    exit_reason TEXT,                 -- 손절/익절/시간손절 등
                    exit_vwap REAL,
                    exit_ma20 REAL,

                    -- 손익
                    profit_loss REAL,                 -- 실현손익 (금액)
                    profit_loss_pct REAL,             -- 수익률 (%)
                    unrealized_pnl REAL,              -- 미실현 손익 (보유 중)

                    -- 보유 기간
                    hold_minutes INTEGER,

                    -- 추가 메타 정보
                    position_type TEXT DEFAULT 'long', -- long/short/test
                    market_regime TEXT,               -- 상승장/하락장/횡보장

                    -- 타임스탬프
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ========================================
            # 2. trade_market_snapshot - 거래 시점 시장 스냅샷 (정규화)
            # ========================================
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_market_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    snapshot_time DATETIME NOT NULL,
                    snapshot_type TEXT NOT NULL,      -- 'entry' or 'exit'

                    -- 시장 데이터
                    vwap REAL,
                    ma20 REAL,
                    volume REAL,
                    volume_ma5 REAL,
                    volume_ma20 REAL,
                    z_score REAL,
                    trade_value REAL,

                    -- 거래량 분석
                    short_term_surge BOOLEAN,
                    statistical_spike BOOLEAN,
                    trade_value_surge BOOLEAN,

                    -- 시장 전체
                    market_hour INTEGER,
                    kospi_change REAL,
                    kosdaq_change REAL,

                    FOREIGN KEY (trade_id) REFERENCES trades(id)
                )
            """)

            # ========================================
            # 3. monitoring_signals - 모니터링 시그널 기록 (AI 학습 핵심)
            # ========================================
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monitoring_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- 기본 정보
                    check_datetime DATETIME NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT,
                    strategy TEXT,

                    -- 시장 데이터
                    current_price REAL,
                    current_vwap REAL,
                    current_ma20 REAL,
                    current_volume REAL,

                    -- 거래량 분석 (E안 기반)
                    volume_ma5 REAL,
                    volume_ma20 REAL,
                    volume_change_pct REAL,
                    volume_z_score REAL,
                    short_term_surge BOOLEAN,
                    statistical_spike BOOLEAN,
                    trade_value_surge BOOLEAN,

                    -- 조건 충족 여부
                    vwap_condition BOOLEAN,
                    ma20_condition BOOLEAN,
                    volume_condition BOOLEAN,

                    -- 시그널
                    signal TEXT,                      -- '매수', '대기', '제외'
                    conditions_met INTEGER,           -- 충족 조건 개수
                    signal_score REAL,                -- 종합 점수

                    -- AI 학습용 추가 feature
                    vwap_distance REAL,               -- 현재가와 VWAP 거리 (%)
                    trend_strength REAL,              -- 추세 강도
                    volatility REAL,                  -- 변동성
                    predicted_winrate REAL,           -- AI 모델 예측 승률

                    -- VWAP 백테스트
                    vwap_winrate REAL,
                    vwap_avg_profit REAL,

                    -- 실제 매수 여부
                    was_bought BOOLEAN DEFAULT 0,
                    buy_trade_id INTEGER,

                    -- 시간 정보
                    market_hour INTEGER,

                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (buy_trade_id) REFERENCES trades(id)
                )
            """)

            # ========================================
            # 4. condition_search_results - 조건검색 결과 (1차 필터링)
            # ========================================
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS condition_search_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    search_datetime DATETIME NOT NULL,
                    condition_name TEXT NOT NULL,
                    condition_seq INTEGER,

                    stock_code TEXT NOT NULL,
                    stock_name TEXT,

                    -- 필터링 통과 여부
                    passed_vwap_filter BOOLEAN,
                    passed_volume_filter BOOLEAN,

                    -- VWAP 백테스트
                    vwap_winrate REAL,
                    vwap_avg_profit REAL,

                    -- 검색식 점수
                    signal_score REAL,

                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ========================================
            # 5. market_conditions - 시장 전체 상황
            # ========================================
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_conditions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    check_datetime DATETIME NOT NULL,

                    -- 전체 시장
                    total_stocks_monitored INTEGER,
                    total_signals_buy INTEGER,
                    total_signals_wait INTEGER,
                    total_signals_exclude INTEGER,

                    -- 평균 지표
                    avg_volume_surge REAL,
                    avg_z_score REAL,
                    avg_vwap_distance REAL,
                    avg_rsi REAL,

                    -- 시간대
                    market_hour INTEGER,

                    -- 시장 지수
                    kospi_change REAL,
                    kosdaq_change REAL,

                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ========================================
            # 인덱스 생성 (검색 성능 향상)
            # ========================================
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_entry_date ON trades(entry_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_trade ON trade_market_snapshot(trade_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_time ON trade_market_snapshot(snapshot_time)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_stock ON monitoring_signals(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_time ON monitoring_signals(check_datetime)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_bought ON monitoring_signals(was_bought)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_condition_stock ON condition_search_results(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_condition_time ON condition_search_results(search_datetime)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_time ON market_conditions(check_datetime)")

            # ========================================
            # AI 학습용 뷰 생성
            # ========================================
            cursor.execute("""
                CREATE VIEW IF NOT EXISTS ai_training_dataset AS
                SELECT
                    m.stock_code,
                    m.stock_name,
                    m.check_datetime,
                    m.signal_score,
                    m.vwap_distance,
                    m.volume_z_score,
                    m.short_term_surge,
                    m.statistical_spike,
                    m.trade_value_surge,
                    m.vwap_condition,
                    m.ma20_condition,
                    m.volume_condition,
                    m.trend_strength,
                    m.volatility,
                    m.market_hour,
                    t.profit_loss_pct,
                    t.hold_minutes,
                    t.exit_reason,
                    CASE WHEN t.profit_loss_pct > 0 THEN 1 ELSE 0 END as is_profitable
                FROM monitoring_signals m
                LEFT JOIN trades t ON m.buy_trade_id = t.id
                WHERE m.was_bought = 1
            """)

            conn.commit()

    # ==================== 거래 이력 관리 ====================

    def insert_trade(self, trade_data: Dict[str, Any]) -> int:
        """거래 이력 추가"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO trades (
                    stock_code, stock_name, strategy,
                    entry_date, entry_price, quantity, entry_amount,
                    entry_vwap, entry_ma20, entry_volume_surge, entry_z_score, entry_trade_value_ratio,
                    vwap_condition, ma20_condition, volume_condition, signal_strength,
                    vwap_backtest_winrate, vwap_backtest_avg_profit, vwap_backtest_max_profit,
                    vwap_backtest_max_loss, vwap_backtest_trades, vwap_backtest_score,
                    position_type, market_regime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data['stock_code'],
                trade_data['stock_name'],
                trade_data.get('strategy'),
                trade_data['entry_date'],
                trade_data['entry_price'],
                trade_data['quantity'],
                trade_data['entry_amount'],
                trade_data.get('entry_vwap'),
                trade_data.get('entry_ma20'),
                trade_data.get('entry_volume_surge'),
                trade_data.get('entry_z_score'),
                trade_data.get('entry_trade_value_ratio'),
                trade_data.get('vwap_condition'),
                trade_data.get('ma20_condition'),
                trade_data.get('volume_condition'),
                trade_data.get('signal_strength'),
                trade_data.get('vwap_backtest_winrate'),
                trade_data.get('vwap_backtest_avg_profit'),
                trade_data.get('vwap_backtest_max_profit'),
                trade_data.get('vwap_backtest_max_loss'),
                trade_data.get('vwap_backtest_trades'),
                trade_data.get('vwap_backtest_score'),
                trade_data.get('position_type', 'long'),
                trade_data.get('market_regime')
            ))

            trade_id = cursor.lastrowid
            conn.commit()
            return trade_id

    def update_trade_exit(self, trade_id: int, exit_data: Dict[str, Any]):
        """매도 시 거래 이력 업데이트"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE trades SET
                    exit_date = ?,
                    exit_price = ?,
                    exit_reason = ?,
                    exit_vwap = ?,
                    exit_ma20 = ?,
                    profit_loss = ?,
                    profit_loss_pct = ?,
                    hold_minutes = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                exit_data.get('exit_date'),
                exit_data.get('exit_price'),
                exit_data.get('exit_reason'),
                exit_data.get('exit_vwap'),
                exit_data.get('exit_ma20'),
                exit_data.get('profit_loss'),
                exit_data.get('profit_loss_pct'),
                exit_data.get('hold_minutes'),
                trade_id
            ))

            conn.commit()

    def update_unrealized_pnl(self, trade_id: int, unrealized_pnl: float):
        """미실현 손익 업데이트 (실시간 모니터링용)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE trades SET unrealized_pnl = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (unrealized_pnl, trade_id))
            conn.commit()

    def insert_market_snapshot(self, snapshot_data: Dict[str, Any]) -> int:
        """거래 시점 시장 스냅샷 추가"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO trade_market_snapshot (
                    trade_id, snapshot_time, snapshot_type,
                    vwap, ma20, volume, volume_ma5, volume_ma20, z_score, trade_value,
                    short_term_surge, statistical_spike, trade_value_surge,
                    market_hour, kospi_change, kosdaq_change
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot_data['trade_id'],
                snapshot_data['snapshot_time'],
                snapshot_data['snapshot_type'],
                snapshot_data.get('vwap'),
                snapshot_data.get('ma20'),
                snapshot_data.get('volume'),
                snapshot_data.get('volume_ma5'),
                snapshot_data.get('volume_ma20'),
                snapshot_data.get('z_score'),
                snapshot_data.get('trade_value'),
                snapshot_data.get('short_term_surge'),
                snapshot_data.get('statistical_spike'),
                snapshot_data.get('trade_value_surge'),
                snapshot_data.get('market_hour'),
                snapshot_data.get('kospi_change'),
                snapshot_data.get('kosdaq_change')
            ))

            conn.commit()
            return cursor.lastrowid

    # ==================== 모니터링 시그널 관리 ====================

    def insert_monitoring_signal(self, signal_data: Dict[str, Any]) -> int:
        """모니터링 시그널 추가"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO monitoring_signals (
                    check_datetime, stock_code, stock_name, strategy,
                    current_price, current_vwap, current_ma20, current_volume,
                    volume_ma5, volume_ma20, volume_change_pct, volume_z_score,
                    short_term_surge, statistical_spike, trade_value_surge,
                    vwap_condition, ma20_condition, volume_condition,
                    signal, conditions_met, signal_score,
                    vwap_distance, trend_strength, volatility, predicted_winrate,
                    vwap_winrate, vwap_avg_profit,
                    market_hour
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_data['check_datetime'],
                signal_data['stock_code'],
                signal_data['stock_name'],
                signal_data.get('strategy'),
                signal_data.get('current_price'),
                signal_data.get('current_vwap'),
                signal_data.get('current_ma20'),
                signal_data.get('current_volume'),
                signal_data.get('volume_ma5'),
                signal_data.get('volume_ma20'),
                signal_data.get('volume_change_pct'),
                signal_data.get('volume_z_score'),
                signal_data.get('short_term_surge'),
                signal_data.get('statistical_spike'),
                signal_data.get('trade_value_surge'),
                signal_data.get('vwap_condition'),
                signal_data.get('ma20_condition'),
                signal_data.get('volume_condition'),
                signal_data.get('signal'),
                signal_data.get('conditions_met'),
                signal_data.get('signal_score'),
                signal_data.get('vwap_distance'),
                signal_data.get('trend_strength'),
                signal_data.get('volatility'),
                signal_data.get('predicted_winrate'),
                signal_data.get('vwap_winrate'),
                signal_data.get('vwap_avg_profit'),
                signal_data.get('market_hour')
            ))

            conn.commit()
            return cursor.lastrowid

    def update_signal_bought(self, signal_id: int, trade_id: int):
        """시그널 매수 여부 업데이트"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE monitoring_signals SET was_bought = 1, buy_trade_id = ?
                WHERE id = ?
            """, (trade_id, signal_id))
            conn.commit()

    # ==================== 조건검색 결과 관리 ====================

    def insert_condition_search(self, search_data: Dict[str, Any]) -> int:
        """조건검색 결과 추가"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO condition_search_results (
                    search_datetime, condition_name, condition_seq,
                    stock_code, stock_name,
                    passed_vwap_filter, passed_volume_filter,
                    vwap_winrate, vwap_avg_profit, signal_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                search_data['search_datetime'],
                search_data['condition_name'],
                search_data.get('condition_seq'),
                search_data['stock_code'],
                search_data['stock_name'],
                search_data.get('passed_vwap_filter'),
                search_data.get('passed_volume_filter'),
                search_data.get('vwap_winrate'),
                search_data.get('vwap_avg_profit'),
                search_data.get('signal_score')
            ))

            conn.commit()
            return cursor.lastrowid

    # ==================== 시장 상황 관리 ====================

    def insert_market_condition(self, market_data: Dict[str, Any]) -> int:
        """시장 상황 추가"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO market_conditions (
                    check_datetime,
                    total_stocks_monitored, total_signals_buy, total_signals_wait, total_signals_exclude,
                    avg_volume_surge, avg_z_score, avg_vwap_distance, avg_rsi,
                    market_hour, kospi_change, kosdaq_change
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_data['check_datetime'],
                market_data.get('total_stocks_monitored'),
                market_data.get('total_signals_buy'),
                market_data.get('total_signals_wait'),
                market_data.get('total_signals_exclude'),
                market_data.get('avg_volume_surge'),
                market_data.get('avg_z_score'),
                market_data.get('avg_vwap_distance'),
                market_data.get('avg_rsi'),
                market_data.get('market_hour'),
                market_data.get('kospi_change'),
                market_data.get('kosdaq_change')
            ))

            conn.commit()
            return cursor.lastrowid

    # ==================== 조회 메서드 ====================

    def get_open_positions(self) -> List[Dict]:
        """현재 보유 중인 포지션 조회"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM trades
                WHERE exit_date IS NULL
                ORDER BY entry_date DESC
            """)

            return [dict(row) for row in cursor.fetchall()]

    def get_trades(self, stock_code: Optional[str] = None,
                   start_date: Optional[str] = None,
                   end_date: Optional[str] = None,
                   limit: int = 100) -> List[Dict]:
        """거래 이력 조회"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM trades WHERE 1=1"
            params = []

            if stock_code:
                query += " AND stock_code = ?"
                params.append(stock_code)

            if start_date:
                query += " AND entry_date >= ?"
                params.append(start_date)

            if end_date:
                query += " AND entry_date <= ?"
                params.append(end_date)

            query += " ORDER BY entry_date DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_ai_training_data(self, limit: int = 1000) -> List[Dict]:
        """AI 학습용 데이터셋 조회"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(f"""
                SELECT * FROM ai_training_dataset
                ORDER BY check_datetime DESC
                LIMIT ?
            """, (limit,))

            return [dict(row) for row in cursor.fetchall()]

    # ==================== 통계 분석 ====================

    def get_trade_statistics(self) -> Dict:
        """거래 통계 조회"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN exit_date IS NOT NULL THEN 1 ELSE 0 END) as closed_trades,
                    SUM(CASE WHEN profit_loss_pct > 0 THEN 1 ELSE 0 END) as winning_trades,
                    AVG(profit_loss_pct) as avg_profit_pct,
                    SUM(profit_loss) as total_profit,
                    MAX(profit_loss_pct) as max_profit_pct,
                    MIN(profit_loss_pct) as min_profit_pct,
                    AVG(hold_minutes) as avg_hold_minutes
                FROM trades
                WHERE exit_date IS NOT NULL
            """)

            row = cursor.fetchone()

            if row and row[1]:  # closed_trades > 0
                return {
                    'total_trades': row[0] or 0,
                    'closed_trades': row[1] or 0,
                    'winning_trades': row[2] or 0,
                    'win_rate': (row[2] / row[1] * 100) if row[1] else 0,
                    'avg_profit_pct': row[3] or 0,
                    'total_profit': row[4] or 0,
                    'max_profit_pct': row[5] or 0,
                    'min_profit_pct': row[6] or 0,
                    'avg_hold_minutes': row[7] or 0
                }

            return {}

    def close(self):
        """데이터베이스 연결 종료"""
        pass  # sqlite3는 with 문으로 자동 관리
