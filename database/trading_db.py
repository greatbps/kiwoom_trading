"""
거래 이력 및 분석 데이터 관리 데이터베이스 (PostgreSQL)
"""
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import os
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

load_dotenv()


class TradingDatabase:
    """자동매매 시스템 데이터베이스 관리 (PostgreSQL)"""

    def __init__(self):
        """PostgreSQL 연결 초기화"""
        # 연결 풀 생성
        self.pool = psycopg2.pool.SimpleConnectionPool(
            1, 10,
            host=os.getenv('POSTGRES_HOST', 'localhost'),
            port=int(os.getenv('POSTGRES_PORT', 5432)),
            database=os.getenv('POSTGRES_DB', 'trading_system'),
            user=os.getenv('POSTGRES_USER', 'postgres'),
            password=os.getenv('POSTGRES_PASSWORD')
        )

        # 테이블 생성
        self._create_tables()

    def _get_conn(self):
        """연결 풀에서 연결 가져오기"""
        return self.pool.getconn()

    def _put_conn(self, conn):
        """연결을 풀로 반환"""
        self.pool.putconn(conn)

    def _create_tables(self):
        """모든 테이블 생성"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 1. 거래 이력 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id SERIAL PRIMARY KEY,
                    stock_code VARCHAR(20) NOT NULL,
                    stock_name VARCHAR(100) NOT NULL,
                    trade_type VARCHAR(10) NOT NULL,
                    trade_time TIMESTAMP NOT NULL,
                    price NUMERIC(15, 2) NOT NULL,
                    quantity INTEGER NOT NULL,
                    amount NUMERIC(20, 2) NOT NULL,

                    -- 전략 정보
                    condition_name TEXT,
                    strategy_config TEXT,
                    entry_reason TEXT,
                    exit_reason TEXT,

                    -- 2차 필터링 점수
                    vwap_validation_score NUMERIC(10, 2),
                    sim_win_rate NUMERIC(5, 2),
                    sim_avg_profit NUMERIC(10, 2),
                    sim_trade_count INTEGER,
                    sim_profit_factor NUMERIC(10, 2),

                    -- 뉴스 분석
                    news_sentiment TEXT,
                    news_impact TEXT,
                    news_keywords JSONB,
                    news_titles JSONB,

                    -- 실거래 결과
                    realized_profit NUMERIC(20, 2),
                    profit_rate NUMERIC(10, 2),
                    holding_duration INTEGER,

                    -- 매매 컨텍스트 (ML 학습용)
                    entry_context JSONB,
                    exit_context JSONB,
                    filter_scores JSONB,

                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 2. 필터링 이력 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS filter_history (
                    filter_id SERIAL PRIMARY KEY,
                    filter_time TIMESTAMP NOT NULL,
                    filter_type VARCHAR(10) NOT NULL,
                    condition_name TEXT,

                    -- 1차 필터링 결과
                    stocks_found INTEGER,
                    stock_codes JSONB,

                    -- 2차 필터링 결과
                    stocks_passed INTEGER,
                    stocks_failed INTEGER,
                    passed_stocks JSONB,

                    -- 실행 설정
                    schedule_type VARCHAR(20),
                    is_new_stock INTEGER,

                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 3. 시뮬레이션 결과 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS simulations (
                    simulation_id SERIAL PRIMARY KEY,
                    stock_code VARCHAR(20) NOT NULL,
                    stock_name VARCHAR(100) NOT NULL,
                    simulation_time TIMESTAMP NOT NULL,
                    lookback_days INTEGER NOT NULL,

                    -- 백테스트 결과
                    total_trades INTEGER,
                    win_rate NUMERIC(5, 2),
                    avg_profit_rate NUMERIC(10, 2),
                    profit_factor NUMERIC(10, 2),
                    max_profit NUMERIC(10, 2),
                    max_loss NUMERIC(10, 2),

                    -- 뉴스 정보
                    news_sentiment TEXT,
                    news_impact TEXT,
                    news_keywords JSONB,
                    news_titles JSONB,
                    news_score NUMERIC(5, 2),

                    -- 거래 상세
                    trade_details JSONB,

                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 4. 2차 필터링 점수 상세 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS validation_scores (
                    score_id SERIAL PRIMARY KEY,
                    stock_code VARCHAR(20) NOT NULL,
                    stock_name VARCHAR(100) NOT NULL,
                    validation_time TIMESTAMP NOT NULL,

                    -- VWAP 시뮬레이션 점수
                    vwap_win_rate NUMERIC(5, 2),
                    vwap_avg_profit NUMERIC(10, 2),
                    vwap_trade_count INTEGER,
                    vwap_profit_factor NUMERIC(10, 2),
                    vwap_max_profit NUMERIC(10, 2),
                    vwap_max_loss NUMERIC(10, 2),

                    -- 뉴스 분석 점수
                    news_sentiment_score NUMERIC(5, 2),
                    news_impact_type VARCHAR(20),
                    news_keywords JSONB,
                    news_titles JSONB,
                    news_count INTEGER,

                    -- 종합 점수
                    total_score NUMERIC(10, 2),
                    weight_vwap NUMERIC(5, 2),
                    weight_news NUMERIC(5, 2),
                    is_passed INTEGER,

                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 5. 필터링 후보 종목 테이블 (운영용)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS filtered_candidates (
                    id SERIAL PRIMARY KEY,

                    -- 1️⃣ 기본 메타 정보
                    date_detected TIMESTAMP NOT NULL,
                    stock_code VARCHAR(20) NOT NULL,
                    stock_name VARCHAR(100) NOT NULL,
                    strategy_tag VARCHAR(50),
                    source_signal JSONB,
                    market VARCHAR(20),
                    is_active INTEGER DEFAULT 1,

                    -- 2️⃣ 가격 및 거래 정보
                    open_price NUMERIC(15, 2),
                    high_price NUMERIC(15, 2),
                    low_price NUMERIC(15, 2),
                    close_price NUMERIC(15, 2),
                    volume BIGINT,
                    value NUMERIC(20, 2),
                    vwap NUMERIC(15, 2),
                    atr NUMERIC(15, 2),
                    volatility_ratio NUMERIC(10, 4),

                    -- 3️⃣ 기술적 지표
                    ma5 NUMERIC(15, 2),
                    ma20 NUMERIC(15, 2),
                    ma60 NUMERIC(15, 2),
                    rsi14 NUMERIC(5, 2),
                    macd NUMERIC(10, 4),
                    macd_signal NUMERIC(10, 4),
                    stoch_k NUMERIC(5, 2),
                    stoch_d NUMERIC(5, 2),
                    boll_upper NUMERIC(15, 2),
                    boll_lower NUMERIC(15, 2),
                    supertrend NUMERIC(15, 2),
                    vwap_diff NUMERIC(10, 4),
                    volume_zscore NUMERIC(10, 4),
                    momentum_5d NUMERIC(10, 2),
                    momentum_20d NUMERIC(10, 2),
                    turnover_ratio NUMERIC(10, 4),
                    foreign_ratio NUMERIC(10, 4),
                    inst_netbuy_5d NUMERIC(20, 2),
                    foreign_netbuy_5d NUMERIC(20, 2),

                    -- 4️⃣ 필터링 결과
                    pass_condition1 INTEGER,
                    pass_condition2 INTEGER,
                    filter_reason TEXT,
                    score_vwap NUMERIC(10, 2),
                    score_volume NUMERIC(10, 2),
                    score_trend NUMERIC(10, 2),
                    total_score NUMERIC(10, 2),

                    -- VWAP 백테스트 결과
                    vwap_win_rate NUMERIC(5, 2),
                    vwap_avg_profit NUMERIC(10, 2),
                    vwap_trade_count INTEGER,
                    vwap_profit_factor NUMERIC(10, 2),

                    -- ML 모델 예측
                    ml_buy_probability NUMERIC(5, 4),
                    ml_predicted_profit NUMERIC(10, 2),
                    ml_rank INTEGER,
                    ml_last_updated TIMESTAMP,

                    -- 5️⃣ 후속 추적
                    next_1d_return NUMERIC(10, 2),
                    next_3d_return NUMERIC(10, 2),
                    next_5d_return NUMERIC(10, 2),
                    hit_takeprofit INTEGER,
                    hit_stoploss INTEGER,
                    hold_duration INTEGER,
                    realized_return NUMERIC(10, 2),

                    -- 6️⃣ 시스템 관리
                    monitoring_status VARCHAR(50),
                    last_checked TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 6. 아카이브 후보 테이블 (학습용)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS archive_candidates (
                    id SERIAL PRIMARY KEY,
                    original_id INTEGER,
                    archived_at TIMESTAMP DEFAULT NOW(),
                    used_in_training INTEGER DEFAULT 0,

                    date_detected TIMESTAMP NOT NULL,
                    stock_code VARCHAR(20) NOT NULL,
                    stock_name VARCHAR(100) NOT NULL,
                    strategy_tag VARCHAR(50),
                    source_signal JSONB,
                    market VARCHAR(20),

                    open_price NUMERIC(15, 2), high_price NUMERIC(15, 2),
                    low_price NUMERIC(15, 2), close_price NUMERIC(15, 2),
                    volume BIGINT, value NUMERIC(20, 2),
                    vwap NUMERIC(15, 2), atr NUMERIC(15, 2),
                    volatility_ratio NUMERIC(10, 4),

                    ma5 NUMERIC(15, 2), ma20 NUMERIC(15, 2), ma60 NUMERIC(15, 2),
                    rsi14 NUMERIC(5, 2), macd NUMERIC(10, 4), macd_signal NUMERIC(10, 4),
                    stoch_k NUMERIC(5, 2), stoch_d NUMERIC(5, 2),
                    boll_upper NUMERIC(15, 2), boll_lower NUMERIC(15, 2),
                    supertrend NUMERIC(15, 2),
                    vwap_diff NUMERIC(10, 4), volume_zscore NUMERIC(10, 4),
                    momentum_5d NUMERIC(10, 2), momentum_20d NUMERIC(10, 2),
                    turnover_ratio NUMERIC(10, 4), foreign_ratio NUMERIC(10, 4),
                    inst_netbuy_5d NUMERIC(20, 2), foreign_netbuy_5d NUMERIC(20, 2),

                    pass_condition1 INTEGER, pass_condition2 INTEGER, filter_reason TEXT,
                    score_vwap NUMERIC(10, 2), score_volume NUMERIC(10, 2),
                    score_trend NUMERIC(10, 2), total_score NUMERIC(10, 2),

                    vwap_win_rate NUMERIC(5, 2), vwap_avg_profit NUMERIC(10, 2),
                    vwap_trade_count INTEGER, vwap_profit_factor NUMERIC(10, 2),

                    ml_buy_probability NUMERIC(5, 4), ml_predicted_profit NUMERIC(10, 2),
                    ml_rank INTEGER, ml_last_updated TIMESTAMP,

                    next_1d_return NUMERIC(10, 2), next_3d_return NUMERIC(10, 2),
                    next_5d_return NUMERIC(10, 2),
                    hit_takeprofit INTEGER, hit_stoploss INTEGER,
                    hold_duration INTEGER, realized_return NUMERIC(10, 2),

                    monitoring_status VARCHAR(50)
                )
            """)

            # 인덱스 생성
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(trade_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_filter_time ON filter_history(filter_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sim_stock ON simulations(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_scores_stock ON validation_scores(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_candidates_stock ON filtered_candidates(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_candidates_date ON filtered_candidates(date_detected)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_candidates_active ON filtered_candidates(is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_archive_stock ON archive_candidates(stock_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_archive_date ON archive_candidates(date_detected)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_archive_training ON archive_candidates(used_in_training)")

            # ML 학습용 뷰 생성
            cursor.execute("""
                CREATE OR REPLACE VIEW ml_training_features AS
                SELECT
                    id,
                    stock_code,
                    date_detected,

                    -- Features
                    ma5, ma20, ma60,
                    rsi14, macd, macd_signal,
                    stoch_k, stoch_d,
                    boll_upper, boll_lower,
                    vwap_diff, volume_zscore,
                    momentum_5d, momentum_20d,
                    volatility_ratio,

                    -- VWAP 백테스트
                    vwap_win_rate, vwap_avg_profit, vwap_trade_count, vwap_profit_factor,

                    -- 필터링 점수
                    total_score,

                    -- Labels
                    next_1d_return,
                    next_3d_return,
                    next_5d_return,
                    CASE WHEN next_3d_return > 0 THEN 1 ELSE 0 END AS label_binary,
                    hit_takeprofit,
                    hit_stoploss

                FROM filtered_candidates
                WHERE pass_condition2 = 1
                  AND next_3d_return IS NOT NULL
            """)

            conn.commit()
            cursor.close()
        finally:
            self._put_conn(conn)

    # ==================== 거래 이력 관리 ====================

    def insert_trade(self, trade_data: Dict[str, Any]) -> int:
        """거래 이력 추가"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO trades (
                    stock_code, stock_name, trade_type, trade_time,
                    price, quantity, amount,
                    condition_name, strategy_config, entry_reason, exit_reason,
                    vwap_validation_score, sim_win_rate, sim_avg_profit,
                    sim_trade_count, sim_profit_factor,
                    news_sentiment, news_impact, news_keywords, news_titles,
                    realized_profit, profit_rate, holding_duration,
                    entry_context, exit_context, filter_scores
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING trade_id
            """, (
                trade_data['stock_code'],
                trade_data['stock_name'],
                trade_data['trade_type'],
                trade_data['trade_time'],
                trade_data['price'],
                trade_data['quantity'],
                trade_data['amount'],
                trade_data.get('condition_name'),
                trade_data.get('strategy_config'),
                trade_data.get('entry_reason'),
                trade_data.get('exit_reason'),
                trade_data.get('vwap_validation_score'),
                trade_data.get('sim_win_rate'),
                trade_data.get('sim_avg_profit'),
                trade_data.get('sim_trade_count'),
                trade_data.get('sim_profit_factor'),
                trade_data.get('news_sentiment'),
                trade_data.get('news_impact'),
                json.dumps(trade_data.get('news_keywords', [])),
                json.dumps(trade_data.get('news_titles', [])),
                trade_data.get('realized_profit'),
                trade_data.get('profit_rate'),
                trade_data.get('holding_duration'),
                trade_data.get('entry_context'),
                trade_data.get('exit_context'),
                trade_data.get('filter_scores')
            ))

            trade_id = cursor.fetchone()[0]
            conn.commit()
            cursor.close()
            return trade_id
        finally:
            self._put_conn(conn)

    def update_trade_exit(self, trade_id: int, exit_data: Dict[str, Any]):
        """매도 시 거래 이력 업데이트"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE trades SET
                    exit_reason = %s,
                    realized_profit = %s,
                    profit_rate = %s,
                    holding_duration = %s,
                    exit_context = %s
                WHERE trade_id = %s
            """, (
                exit_data.get('exit_reason'),
                exit_data.get('realized_profit'),
                exit_data.get('profit_rate'),
                exit_data.get('holding_duration'),
                exit_data.get('exit_context'),
                trade_id
            ))

            conn.commit()
            cursor.close()
        finally:
            self._put_conn(conn)

    def get_trades(self, stock_code: Optional[str] = None,
                   start_date: Optional[str] = None,
                   end_date: Optional[str] = None) -> List[Dict]:
        """거래 이력 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            query = "SELECT * FROM trades WHERE 1=1"
            params = []

            if stock_code:
                query += " AND stock_code = %s"
                params.append(stock_code)

            if start_date:
                query += " AND trade_time >= %s"
                params.append(start_date)

            if end_date:
                query += " AND trade_time <= %s"
                params.append(end_date)

            query += " ORDER BY trade_time DESC"

            cursor.execute(query, params)
            results = [dict(row) for row in cursor.fetchall()]
            cursor.close()
            return results
        finally:
            self._put_conn(conn)

    def get_recent_stock_name(self, stock_code: str) -> Optional[str]:
        """DB에 저장된 최신 종목명 반환"""
        def is_valid(name: Any) -> bool:
            if not isinstance(name, str):
                return False
            text = name.strip()
            return bool(text) and not text.isdigit()

        tables = [
            ("filtered_candidates", "date_detected"),
            ("validation_scores", "validation_time"),
            ("trades", "trade_time"),
            ("simulations", "simulation_time")
        ]

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            for table, column in tables:
                try:
                    cursor.execute(f"""
                        SELECT {column}, stock_name
                        FROM {table}
                        WHERE stock_code = %s
                        ORDER BY {column} DESC
                        LIMIT 20
                    """, (stock_code,))

                    rows = cursor.fetchall()
                    for _, name in rows:
                        if is_valid(name):
                            cursor.close()
                            return name.strip()
                except Exception:
                    continue

            cursor.close()
            return None
        finally:
            self._put_conn(conn)

    # ==================== 필터링 이력 관리 ====================

    def insert_filter_history(self, filter_data: Dict[str, Any]) -> int:
        """필터링 이력 추가"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO filter_history (
                    filter_time, filter_type, condition_name,
                    stocks_found, stock_codes,
                    stocks_passed, stocks_failed, passed_stocks,
                    schedule_type, is_new_stock
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING filter_id
            """, (
                filter_data['filter_time'],
                filter_data['filter_type'],
                filter_data.get('condition_name'),
                filter_data.get('stocks_found'),
                json.dumps(filter_data.get('stock_codes', [])),
                filter_data.get('stocks_passed'),
                filter_data.get('stocks_failed'),
                json.dumps(filter_data.get('passed_stocks', [])),
                filter_data.get('schedule_type'),
                filter_data.get('is_new_stock', 0)
            ))

            filter_id = cursor.fetchone()[0]
            conn.commit()
            cursor.close()
            return filter_id
        finally:
            self._put_conn(conn)

    def get_filter_history(self, filter_type: Optional[str] = None,
                           limit: int = 100) -> List[Dict]:
        """필터링 이력 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            query = "SELECT * FROM filter_history WHERE 1=1"
            params = []

            if filter_type:
                query += " AND filter_type = %s"
                params.append(filter_type)

            query += " ORDER BY filter_time DESC LIMIT %s"
            params.append(limit)

            cursor.execute(query, params)
            results = [dict(row) for row in cursor.fetchall()]
            cursor.close()
            return results
        finally:
            self._put_conn(conn)

    # ==================== 시뮬레이션 결과 관리 ====================

    def insert_simulation(self, sim_data: Dict[str, Any]) -> int:
        """시뮬레이션 결과 추가"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO simulations (
                    stock_code, stock_name, simulation_time, lookback_days,
                    total_trades, win_rate, avg_profit_rate,
                    profit_factor, max_profit, max_loss,
                    news_sentiment, news_impact, news_keywords, news_titles, news_score,
                    trade_details
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING simulation_id
            """, (
                sim_data['stock_code'],
                sim_data['stock_name'],
                sim_data['simulation_time'],
                sim_data['lookback_days'],
                sim_data.get('total_trades'),
                sim_data.get('win_rate'),
                sim_data.get('avg_profit_rate'),
                sim_data.get('profit_factor'),
                sim_data.get('max_profit'),
                sim_data.get('max_loss'),
                sim_data.get('news_sentiment'),
                sim_data.get('news_impact'),
                json.dumps(sim_data.get('news_keywords', [])),
                json.dumps(sim_data.get('news_titles', [])),
                sim_data.get('news_score'),
                json.dumps(sim_data.get('trade_details', []))
            ))

            sim_id = cursor.fetchone()[0]
            conn.commit()
            cursor.close()
            return sim_id
        finally:
            self._put_conn(conn)

    def get_simulations(self, stock_code: Optional[str] = None,
                        limit: int = 100) -> List[Dict]:
        """시뮬레이션 결과 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            query = "SELECT * FROM simulations WHERE 1=1"
            params = []

            if stock_code:
                query += " AND stock_code = %s"
                params.append(stock_code)

            query += " ORDER BY simulation_time DESC LIMIT %s"
            params.append(limit)

            cursor.execute(query, params)
            results = [dict(row) for row in cursor.fetchall()]
            cursor.close()
            return results
        finally:
            self._put_conn(conn)

    # ==================== 2차 필터링 점수 관리 ====================

    def insert_validation_score(self, score_data: Dict[str, Any]) -> int:
        """2차 필터링 점수 추가"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO validation_scores (
                    stock_code, stock_name, validation_time,
                    vwap_win_rate, vwap_avg_profit, vwap_trade_count,
                    vwap_profit_factor, vwap_max_profit, vwap_max_loss,
                    news_sentiment_score, news_impact_type, news_keywords, news_titles, news_count,
                    total_score, weight_vwap, weight_news, is_passed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING score_id
            """, (
                score_data['stock_code'],
                score_data['stock_name'],
                score_data['validation_time'],
                score_data.get('vwap_win_rate'),
                score_data.get('vwap_avg_profit'),
                score_data.get('vwap_trade_count'),
                score_data.get('vwap_profit_factor'),
                score_data.get('vwap_max_profit'),
                score_data.get('vwap_max_loss'),
                score_data.get('news_sentiment_score'),
                score_data.get('news_impact_type'),
                json.dumps(score_data.get('news_keywords', [])),
                json.dumps(score_data.get('news_titles', [])),
                score_data.get('news_count'),
                score_data.get('total_score'),
                score_data.get('weight_vwap'),
                score_data.get('weight_news'),
                score_data.get('is_passed', 0)
            ))

            score_id = cursor.fetchone()[0]
            conn.commit()
            cursor.close()
            return score_id
        finally:
            self._put_conn(conn)

    def get_validation_scores(self, stock_code: Optional[str] = None,
                             is_passed: Optional[bool] = None,
                             limit: int = 100) -> List[Dict]:
        """2차 필터링 점수 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            query = "SELECT * FROM validation_scores WHERE 1=1"
            params = []

            if stock_code:
                query += " AND stock_code = %s"
                params.append(stock_code)

            if is_passed is not None:
                query += " AND is_passed = %s"
                params.append(1 if is_passed else 0)

            query += " ORDER BY validation_time DESC LIMIT %s"
            params.append(limit)

            cursor.execute(query, params)
            results = [dict(row) for row in cursor.fetchall()]
            cursor.close()
            return results
        finally:
            self._put_conn(conn)

    # ==================== 통계 분석 ====================

    def get_trades_with_context(self, stock_code: Optional[str] = None,
                                 start_date: Optional[str] = None,
                                 end_date: Optional[str] = None,
                                 parse_context: bool = True) -> List[Dict]:
        """거래 이력 조회 (컨텍스트 포함)"""
        trades = self.get_trades(stock_code=stock_code, start_date=start_date, end_date=end_date)

        if not parse_context:
            return trades

        # JSONB는 이미 dict로 반환됨
        return trades

    def get_trade_statistics(self, start_date: Optional[str] = None,
                            end_date: Optional[str] = None) -> Dict:
        """거래 통계 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = """
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN trade_type = 'BUY' THEN 1 ELSE 0 END) as total_buys,
                    SUM(CASE WHEN trade_type = 'SELL' THEN 1 ELSE 0 END) as total_sells,
                    SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as winning_trades,
                    AVG(profit_rate) as avg_profit_rate,
                    SUM(realized_profit) as total_profit,
                    MAX(profit_rate) as max_profit_rate,
                    MIN(profit_rate) as min_profit_rate
                FROM trades
                WHERE trade_type = 'SELL'
            """

            params = []

            if start_date:
                query += " AND trade_time >= %s"
                params.append(start_date)

            if end_date:
                query += " AND trade_time <= %s"
                params.append(end_date)

            cursor.execute(query, params)
            row = cursor.fetchone()
            cursor.close()

            if row:
                return {
                    'total_trades': row[0] or 0,
                    'total_buys': row[1] or 0,
                    'total_sells': row[2] or 0,
                    'winning_trades': row[3] or 0,
                    'win_rate': (row[3] / row[0] * 100) if row[0] else 0,
                    'avg_profit_rate': float(row[4]) if row[4] else 0,
                    'total_profit': float(row[5]) if row[5] else 0,
                    'max_profit_rate': float(row[6]) if row[6] else 0,
                    'min_profit_rate': float(row[7]) if row[7] else 0
                }

            return {}
        finally:
            self._put_conn(conn)

    # ==================== 필터링 후보 종목 관리 ====================

    def insert_candidate(self, candidate_data: Dict[str, Any]) -> int:
        """필터링 후보 종목 추가 (upsert)"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 같은 날짜에 같은 종목이 있는지 확인
            cursor.execute("""
                SELECT id FROM filtered_candidates
                WHERE stock_code = %s AND DATE(date_detected) = DATE(%s)
            """, (candidate_data['stock_code'], candidate_data['date_detected']))

            existing = cursor.fetchone()

            if existing:
                # 업데이트
                if candidate_data.get('total_score', 0) > 0:
                    cursor.execute("""
                        UPDATE filtered_candidates
                        SET
                            total_score = %s,
                            ml_buy_probability = %s,
                            ml_predicted_profit = %s,
                            ml_rank = %s,
                            ml_last_updated = %s,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (
                        candidate_data.get('total_score'),
                        candidate_data.get('ml_buy_probability'),
                        candidate_data.get('ml_predicted_profit'),
                        candidate_data.get('ml_rank'),
                        candidate_data.get('ml_last_updated'),
                        existing[0]
                    ))
                    conn.commit()
                cursor.close()
                return existing[0]

            # 새로 삽입
            cursor.execute("""
                INSERT INTO filtered_candidates (
                    date_detected, stock_code, stock_name, strategy_tag, source_signal, market,
                    open_price, high_price, low_price, close_price, volume, value,
                    vwap, atr, volatility_ratio,
                    ma5, ma20, ma60, rsi14, macd, macd_signal,
                    stoch_k, stoch_d, boll_upper, boll_lower, supertrend,
                    vwap_diff, volume_zscore, momentum_5d, momentum_20d,
                    pass_condition1, pass_condition2, filter_reason,
                    score_vwap, score_volume, score_trend, total_score,
                    vwap_win_rate, vwap_avg_profit, vwap_trade_count, vwap_profit_factor,
                    ml_buy_probability, ml_predicted_profit, ml_rank, ml_last_updated,
                    monitoring_status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s
                ) RETURNING id
            """, (
                candidate_data['date_detected'],
                candidate_data['stock_code'],
                candidate_data['stock_name'],
                candidate_data.get('strategy_tag', 'VWAP'),
                json.dumps(candidate_data.get('source_signal', [])),
                candidate_data.get('market', 'KOSPI'),
                candidate_data.get('open_price'),
                candidate_data.get('high_price'),
                candidate_data.get('low_price'),
                candidate_data.get('close_price'),
                candidate_data.get('volume'),
                candidate_data.get('value'),
                candidate_data.get('vwap'),
                candidate_data.get('atr'),
                candidate_data.get('volatility_ratio'),
                candidate_data.get('ma5'),
                candidate_data.get('ma20'),
                candidate_data.get('ma60'),
                candidate_data.get('rsi14'),
                candidate_data.get('macd'),
                candidate_data.get('macd_signal'),
                candidate_data.get('stoch_k'),
                candidate_data.get('stoch_d'),
                candidate_data.get('boll_upper'),
                candidate_data.get('boll_lower'),
                candidate_data.get('supertrend'),
                candidate_data.get('vwap_diff'),
                candidate_data.get('volume_zscore'),
                candidate_data.get('momentum_5d'),
                candidate_data.get('momentum_20d'),
                candidate_data.get('pass_condition1', 1),
                candidate_data.get('pass_condition2', 1),
                candidate_data.get('filter_reason', ''),
                candidate_data.get('score_vwap'),
                candidate_data.get('score_volume'),
                candidate_data.get('score_trend'),
                candidate_data.get('total_score'),
                candidate_data.get('vwap_win_rate'),
                candidate_data.get('vwap_avg_profit'),
                candidate_data.get('vwap_trade_count'),
                candidate_data.get('vwap_profit_factor'),
                candidate_data.get('ml_buy_probability'),
                candidate_data.get('ml_predicted_profit'),
                candidate_data.get('ml_rank'),
                candidate_data.get('ml_last_updated'),
                candidate_data.get('monitoring_status', 'watching')
            ))

            candidate_id = cursor.fetchone()[0]
            conn.commit()
            cursor.close()
            return candidate_id
        finally:
            self._put_conn(conn)

    def get_active_candidates(self, limit: int = 100) -> List[Dict]:
        """활성 감시 종목 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute("""
                SELECT * FROM filtered_candidates
                WHERE is_active = 1
                ORDER BY total_score DESC, ml_buy_probability DESC
                LIMIT %s
            """, (limit,))

            results = [dict(row) for row in cursor.fetchall()]
            cursor.close()
            return results
        finally:
            self._put_conn(conn)

    def get_candidates_by_date_range(self, start_date: str, end_date: Optional[str] = None) -> List[Dict]:
        """날짜 범위로 후보 종목 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            if end_date:
                cursor.execute("""
                    SELECT * FROM filtered_candidates
                    WHERE created_at BETWEEN %s AND %s
                    ORDER BY created_at DESC
                """, (start_date, end_date))
            else:
                cursor.execute("""
                    SELECT * FROM filtered_candidates
                    WHERE created_at >= %s
                    ORDER BY created_at DESC
                """, (start_date,))

            results = [dict(row) for row in cursor.fetchall()]
            cursor.close()
            return results
        finally:
            self._put_conn(conn)

    def update_candidate_labels(self, candidate_id: int, label_data: Dict[str, Any]):
        """후속 라벨 업데이트"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE filtered_candidates
                SET
                    next_1d_return = %s,
                    next_3d_return = %s,
                    next_5d_return = %s,
                    hit_takeprofit = %s,
                    hit_stoploss = %s,
                    hold_duration = %s,
                    realized_return = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (
                label_data.get('next_1d_return'),
                label_data.get('next_3d_return'),
                label_data.get('next_5d_return'),
                label_data.get('hit_takeprofit', 0),
                label_data.get('hit_stoploss', 0),
                label_data.get('hold_duration'),
                label_data.get('realized_return'),
                candidate_id
            ))

            conn.commit()
            cursor.close()
        finally:
            self._put_conn(conn)

    def get_ml_training_data(self) -> List[Dict]:
        """ML 학습용 데이터 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM ml_training_features")
            results = [dict(row) for row in cursor.fetchall()]
            cursor.close()
            return results
        finally:
            self._put_conn(conn)

    # ==================== 데이터 생명주기 관리 ====================

    def archive_old_candidates(self, days: int = 7) -> int:
        """오래된 비활성 후보를 archive로 이동"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 이동 대상 조회
            cursor.execute("""
                SELECT * FROM filtered_candidates
                WHERE is_active = 0
                  AND NOW() - date_detected > INTERVAL '%s days'
            """ % days)

            candidates = cursor.fetchall()

            if not candidates:
                cursor.close()
                return 0

            # archive로 복사 (bulk insert)
            for row in candidates:
                cursor.execute("""
                    INSERT INTO archive_candidates (
                        original_id, date_detected, stock_code, stock_name, strategy_tag, source_signal, market,
                        open_price, high_price, low_price, close_price, volume, value, vwap, atr, volatility_ratio,
                        ma5, ma20, ma60, rsi14, macd, macd_signal,
                        stoch_k, stoch_d, boll_upper, boll_lower, supertrend,
                        vwap_diff, volume_zscore, momentum_5d, momentum_20d,
                        turnover_ratio, foreign_ratio, inst_netbuy_5d, foreign_netbuy_5d,
                        pass_condition1, pass_condition2, filter_reason,
                        score_vwap, score_volume, score_trend, total_score,
                        vwap_win_rate, vwap_avg_profit, vwap_trade_count, vwap_profit_factor,
                        ml_buy_probability, ml_predicted_profit, ml_rank, ml_last_updated,
                        next_1d_return, next_3d_return, next_5d_return,
                        hit_takeprofit, hit_stoploss, hold_duration, realized_return,
                        monitoring_status
                    ) SELECT
                        id, date_detected, stock_code, stock_name, strategy_tag, source_signal, market,
                        open_price, high_price, low_price, close_price, volume, value, vwap, atr, volatility_ratio,
                        ma5, ma20, ma60, rsi14, macd, macd_signal,
                        stoch_k, stoch_d, boll_upper, boll_lower, supertrend,
                        vwap_diff, volume_zscore, momentum_5d, momentum_20d,
                        turnover_ratio, foreign_ratio, inst_netbuy_5d, foreign_netbuy_5d,
                        pass_condition1, pass_condition2, filter_reason,
                        score_vwap, score_volume, score_trend, total_score,
                        vwap_win_rate, vwap_avg_profit, vwap_trade_count, vwap_profit_factor,
                        ml_buy_probability, ml_predicted_profit, ml_rank, ml_last_updated,
                        next_1d_return, next_3d_return, next_5d_return,
                        hit_takeprofit, hit_stoploss, hold_duration, realized_return,
                        monitoring_status
                    FROM filtered_candidates WHERE id = %s
                """, (row[0],))

            # 원본 삭제
            cursor.execute("""
                DELETE FROM filtered_candidates
                WHERE is_active = 0
                  AND NOW() - date_detected > INTERVAL '%s days'
            """ % days)

            deleted_count = cursor.rowcount
            conn.commit()
            cursor.close()
            return deleted_count
        finally:
            self._put_conn(conn)

    def clean_failed_candidates(self, days: int = 3) -> int:
        """필터링 실패 종목 정리"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                DELETE FROM filtered_candidates
                WHERE pass_condition2 = 0
                  AND NOW() - date_detected > INTERVAL '%s days'
            """ % days)

            deleted_count = cursor.rowcount
            conn.commit()
            cursor.close()
            return deleted_count
        finally:
            self._put_conn(conn)

    def clean_old_archives(self, days: int = 180) -> int:
        """오래된 학습 완료 아카이브 삭제"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                DELETE FROM archive_candidates
                WHERE used_in_training = 1
                  AND NOW() - date_detected > INTERVAL '%s days'
            """ % days)

            deleted_count = cursor.rowcount
            conn.commit()
            cursor.close()
            return deleted_count
        finally:
            self._put_conn(conn)

    def mark_as_used_in_training(self, ids: List[int]):
        """ML 학습에 사용된 아카이브 레코드 마킹"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            for record_id in ids:
                cursor.execute("""
                    UPDATE archive_candidates
                    SET used_in_training = 1
                    WHERE id = %s
                """, (record_id,))

            conn.commit()
            cursor.close()
        finally:
            self._put_conn(conn)

    def close(self):
        """데이터베이스 연결 종료"""
        if self.pool:
            self.pool.closeall()
