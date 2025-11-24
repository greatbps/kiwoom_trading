"""
거래 이력 및 분석 데이터 관리 데이터베이스
"""
import sqlite3
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path


class TradingDatabase:
    """자동매매 시스템 데이터베이스 관리"""

    def __init__(self, db_path: str = "data/trading.db"):
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
        """모든 테이블 생성"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # 1. 거래 이력 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    trade_type TEXT NOT NULL,  -- 'BUY' or 'SELL'
                    trade_time TIMESTAMP NOT NULL,
                    price REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    amount REAL NOT NULL,

                    -- 전략 정보
                    condition_name TEXT,
                    strategy_config TEXT,
                    entry_reason TEXT,
                    exit_reason TEXT,

                    -- 2차 필터링 점수
                    vwap_validation_score REAL,
                    sim_win_rate REAL,
                    sim_avg_profit REAL,
                    sim_trade_count INTEGER,
                    sim_profit_factor REAL,

                    -- 뉴스 분석
                    news_sentiment TEXT,
                    news_impact TEXT,
                    news_keywords TEXT,  -- JSON
                    news_titles TEXT,  -- JSON

                    -- 실거래 결과
                    realized_profit REAL,
                    profit_rate REAL,
                    holding_duration INTEGER,

                    -- 매매 컨텍스트 (ML 학습용)
                    entry_context TEXT,  -- JSON: 진입 시점 전체 지표 (price, vwap, ma, rsi, williams_r, volume_ratio, candle 등)
                    exit_context TEXT,   -- JSON: 청산 시점 전체 지표 (price, highest_price, trailing_info, indicators 등)
                    filter_scores TEXT,  -- JSON: 진입 필터 점수 (각 필터별 통과/차단 사유)

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. 필터링 이력 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS filter_history (
                    filter_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filter_time TIMESTAMP NOT NULL,
                    filter_type TEXT NOT NULL,  -- '1차' or '2차'
                    condition_name TEXT,

                    -- 1차 필터링 결과
                    stocks_found INTEGER,
                    stock_codes TEXT,  -- JSON

                    -- 2차 필터링 결과
                    stocks_passed INTEGER,
                    stocks_failed INTEGER,
                    passed_stocks TEXT,  -- JSON

                    -- 실행 설정
                    schedule_type TEXT,  -- 'daily', 'hourly', 'manual'
                    is_new_stock INTEGER,  -- 0 or 1

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 3. 시뮬레이션 결과 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS simulations (
                    simulation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    simulation_time TIMESTAMP NOT NULL,
                    lookback_days INTEGER NOT NULL,

                    -- 백테스트 결과
                    total_trades INTEGER,
                    win_rate REAL,
                    avg_profit_rate REAL,
                    profit_factor REAL,
                    max_profit REAL,
                    max_loss REAL,

                    -- 뉴스 정보
                    news_sentiment TEXT,
                    news_impact TEXT,
                    news_keywords TEXT,  -- JSON
                    news_titles TEXT,  -- JSON
                    news_score REAL,

                    -- 거래 상세
                    trade_details TEXT,  -- JSON

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 4. 2차 필터링 점수 상세 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS validation_scores (
                    score_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    validation_time TIMESTAMP NOT NULL,

                    -- VWAP 시뮬레이션 점수
                    vwap_win_rate REAL,
                    vwap_avg_profit REAL,
                    vwap_trade_count INTEGER,
                    vwap_profit_factor REAL,
                    vwap_max_profit REAL,
                    vwap_max_loss REAL,

                    -- 뉴스 분석 점수
                    news_sentiment_score REAL,
                    news_impact_type TEXT,  -- 'short', 'mid', 'long'
                    news_keywords TEXT,  -- JSON
                    news_titles TEXT,  -- JSON
                    news_count INTEGER,

                    -- 종합 점수
                    total_score REAL,
                    weight_vwap REAL,
                    weight_news REAL,
                    is_passed INTEGER,  -- 0 or 1

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 5. 필터링 후보 종목 테이블 (운영용 - Active Layer)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS filtered_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- 1️⃣ 기본 메타 정보
                    date_detected TIMESTAMP NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    strategy_tag TEXT,  -- 'VWAP', 'MOMENTUM', 'BREAKOUT', etc.
                    source_signal TEXT,  -- 1차 조건검색식 이름 (JSON)
                    market TEXT,  -- 'KOSPI', 'KOSDAQ'
                    is_active INTEGER DEFAULT 1,

                    -- 2️⃣ 가격 및 거래 정보 (필터링 통과 당시)
                    open_price REAL,
                    high_price REAL,
                    low_price REAL,
                    close_price REAL,
                    volume INTEGER,
                    value REAL,  -- 거래대금
                    vwap REAL,
                    atr REAL,
                    volatility_ratio REAL,  -- ATR / close

                    -- 3️⃣ 기술적 지표 (Feature Core)
                    ma5 REAL,
                    ma20 REAL,
                    ma60 REAL,
                    rsi14 REAL,
                    macd REAL,
                    macd_signal REAL,
                    stoch_k REAL,
                    stoch_d REAL,
                    boll_upper REAL,
                    boll_lower REAL,
                    supertrend REAL,
                    vwap_diff REAL,  -- (현재가 - VWAP) / VWAP
                    volume_zscore REAL,
                    momentum_5d REAL,
                    momentum_20d REAL,
                    turnover_ratio REAL,
                    foreign_ratio REAL,
                    inst_netbuy_5d REAL,
                    foreign_netbuy_5d REAL,

                    -- 4️⃣ 필터링 결과 및 판단 근거
                    pass_condition1 INTEGER,  -- 1차 조건검색 통과
                    pass_condition2 INTEGER,  -- 2차 VWAP 필터 통과
                    filter_reason TEXT,
                    score_vwap REAL,
                    score_volume REAL,
                    score_trend REAL,
                    total_score REAL,  -- 통합 점수 (0~100)

                    -- VWAP 백테스트 결과
                    vwap_win_rate REAL,
                    vwap_avg_profit REAL,
                    vwap_trade_count INTEGER,
                    vwap_profit_factor REAL,

                    -- ML 모델 예측 (Ranker)
                    ml_buy_probability REAL,
                    ml_predicted_profit REAL,
                    ml_rank INTEGER,
                    ml_last_updated TIMESTAMP,

                    -- 5️⃣ 후속 추적 (ML 라벨링용)
                    next_1d_return REAL,  -- 라벨: 1일 후 수익률
                    next_3d_return REAL,  -- 라벨: 3일 후 수익률
                    next_5d_return REAL,  -- 라벨: 5일 후 수익률
                    hit_takeprofit INTEGER,  -- 익절 도달 여부
                    hit_stoploss INTEGER,  -- 손절 도달 여부
                    hold_duration INTEGER,  -- 보유 기간 (일)
                    realized_return REAL,  -- 실제 매매 수익률

                    -- 6️⃣ 시스템 관리용
                    monitoring_status TEXT,  -- 'watching', 'signal_detected', 'position_opened', 'closed'
                    last_checked TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 6. 아카이브 후보 테이블 (학습용 - Historical Layer)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS archive_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_id INTEGER,  -- filtered_candidates의 원본 ID
                    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    used_in_training INTEGER DEFAULT 0,  -- ML 학습 사용 여부

                    -- filtered_candidates와 동일한 컬럼들
                    date_detected TIMESTAMP NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    strategy_tag TEXT,
                    source_signal TEXT,
                    market TEXT,

                    open_price REAL, high_price REAL, low_price REAL, close_price REAL,
                    volume INTEGER, value REAL, vwap REAL, atr REAL, volatility_ratio REAL,

                    ma5 REAL, ma20 REAL, ma60 REAL, rsi14 REAL, macd REAL, macd_signal REAL,
                    stoch_k REAL, stoch_d REAL, boll_upper REAL, boll_lower REAL, supertrend REAL,
                    vwap_diff REAL, volume_zscore REAL, momentum_5d REAL, momentum_20d REAL,
                    turnover_ratio REAL, foreign_ratio REAL, inst_netbuy_5d REAL, foreign_netbuy_5d REAL,

                    pass_condition1 INTEGER, pass_condition2 INTEGER, filter_reason TEXT,
                    score_vwap REAL, score_volume REAL, score_trend REAL, total_score REAL,

                    vwap_win_rate REAL, vwap_avg_profit REAL, vwap_trade_count INTEGER, vwap_profit_factor REAL,

                    ml_buy_probability REAL, ml_predicted_profit REAL, ml_rank INTEGER, ml_last_updated TIMESTAMP,

                    next_1d_return REAL, next_3d_return REAL, next_5d_return REAL,
                    hit_takeprofit INTEGER, hit_stoploss INTEGER, hold_duration INTEGER, realized_return REAL,

                    monitoring_status TEXT
                )
            """)

            # 인덱스 생성 (검색 성능 향상)
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
                CREATE VIEW IF NOT EXISTS ml_training_features AS
                SELECT
                    id,
                    stock_code,
                    date_detected,

                    -- Features (기술적 지표)
                    ma5, ma20, ma60,
                    rsi14, macd, macd_signal,
                    stoch_k, stoch_d,
                    boll_upper, boll_lower,
                    vwap_diff, volume_zscore,
                    momentum_5d, momentum_20d,
                    volatility_ratio,

                    -- VWAP 백테스트 features
                    vwap_win_rate, vwap_avg_profit, vwap_trade_count, vwap_profit_factor,

                    -- 필터링 점수
                    total_score,

                    -- Labels (학습 대상)
                    next_1d_return,
                    next_3d_return,
                    next_5d_return,
                    CASE WHEN next_3d_return > 0 THEN 1 ELSE 0 END AS label_binary,  -- 3일 후 수익 여부
                    hit_takeprofit,
                    hit_stoploss

                FROM filtered_candidates
                WHERE pass_condition2 = 1  -- VWAP 필터 통과 종목만
                  AND next_3d_return IS NOT NULL  -- 라벨이 있는 데이터만
            """)

            conn.commit()

    # ==================== 거래 이력 관리 ====================

    def insert_trade(self, trade_data: Dict[str, Any]) -> int:
        """거래 이력 추가"""
        with sqlite3.connect(self.db_path) as conn:
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(trade_data.get('news_keywords', []), ensure_ascii=False),
                json.dumps(trade_data.get('news_titles', []), ensure_ascii=False),
                trade_data.get('realized_profit'),
                trade_data.get('profit_rate'),
                trade_data.get('holding_duration'),
                trade_data.get('entry_context'),  # JSON string or None
                trade_data.get('exit_context'),   # JSON string or None
                trade_data.get('filter_scores')   # JSON string or None
            ))

            conn.commit()
            return cursor.lastrowid

    def update_trade_exit(self, trade_id: int, exit_data: Dict[str, Any]):
        """매도 시 거래 이력 업데이트"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE trades SET
                    exit_reason = ?,
                    realized_profit = ?,
                    profit_rate = ?,
                    holding_duration = ?,
                    exit_context = ?
                WHERE trade_id = ?
            """, (
                exit_data.get('exit_reason'),
                exit_data.get('realized_profit'),
                exit_data.get('profit_rate'),
                exit_data.get('holding_duration'),
                exit_data.get('exit_context'),  # JSON string or None
                trade_id
            ))

            conn.commit()

    def get_trades(self, stock_code: Optional[str] = None,
                   start_date: Optional[str] = None,
                   end_date: Optional[str] = None) -> List[Dict]:
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
                query += " AND trade_time >= ?"
                params.append(start_date)

            if end_date:
                query += " AND trade_time <= ?"
                params.append(end_date)

            query += " ORDER BY trade_time DESC"

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_stock_name(self, stock_code: str) -> Optional[str]:
        """DB에 저장된 최신 종목명을 반환 (숫자 코드만 있는 경우 제외)."""

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

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for table, column in tables:
                try:
                    cursor.execute(
                        f"""
                        SELECT {column}, stock_name
                        FROM {table}
                        WHERE stock_code = ?
                        ORDER BY {column} DESC
                        LIMIT 20
                        """,
                        (stock_code,)
                    )
                except sqlite3.OperationalError:
                    continue

                rows = cursor.fetchall()
                for _, name in rows:
                    if is_valid(name):
                        return name.strip()

        return None

    # ==================== 필터링 이력 관리 ====================

    def insert_filter_history(self, filter_data: Dict[str, Any]) -> int:
        """필터링 이력 추가"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO filter_history (
                    filter_time, filter_type, condition_name,
                    stocks_found, stock_codes,
                    stocks_passed, stocks_failed, passed_stocks,
                    schedule_type, is_new_stock
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                filter_data['filter_time'],
                filter_data['filter_type'],
                filter_data.get('condition_name'),
                filter_data.get('stocks_found'),
                json.dumps(filter_data.get('stock_codes', []), ensure_ascii=False),
                filter_data.get('stocks_passed'),
                filter_data.get('stocks_failed'),
                json.dumps(filter_data.get('passed_stocks', []), ensure_ascii=False),
                filter_data.get('schedule_type'),
                filter_data.get('is_new_stock', 0)
            ))

            conn.commit()
            return cursor.lastrowid

    def get_filter_history(self, filter_type: Optional[str] = None,
                           limit: int = 100) -> List[Dict]:
        """필터링 이력 조회"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM filter_history WHERE 1=1"
            params = []

            if filter_type:
                query += " AND filter_type = ?"
                params.append(filter_type)

            query += " ORDER BY filter_time DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # ==================== 시뮬레이션 결과 관리 ====================

    def insert_simulation(self, sim_data: Dict[str, Any]) -> int:
        """시뮬레이션 결과 추가"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO simulations (
                    stock_code, stock_name, simulation_time, lookback_days,
                    total_trades, win_rate, avg_profit_rate,
                    profit_factor, max_profit, max_loss,
                    news_sentiment, news_impact, news_keywords, news_titles, news_score,
                    trade_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(sim_data.get('news_keywords', []), ensure_ascii=False),
                json.dumps(sim_data.get('news_titles', []), ensure_ascii=False),
                sim_data.get('news_score'),
                json.dumps(sim_data.get('trade_details', []), ensure_ascii=False)
            ))

            conn.commit()
            return cursor.lastrowid

    def get_simulations(self, stock_code: Optional[str] = None,
                        limit: int = 100) -> List[Dict]:
        """시뮬레이션 결과 조회"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM simulations WHERE 1=1"
            params = []

            if stock_code:
                query += " AND stock_code = ?"
                params.append(stock_code)

            query += " ORDER BY simulation_time DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # ==================== 2차 필터링 점수 관리 ====================

    def insert_validation_score(self, score_data: Dict[str, Any]) -> int:
        """2차 필터링 점수 추가"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO validation_scores (
                    stock_code, stock_name, validation_time,
                    vwap_win_rate, vwap_avg_profit, vwap_trade_count,
                    vwap_profit_factor, vwap_max_profit, vwap_max_loss,
                    news_sentiment_score, news_impact_type, news_keywords, news_titles, news_count,
                    total_score, weight_vwap, weight_news, is_passed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(score_data.get('news_keywords', []), ensure_ascii=False),
                json.dumps(score_data.get('news_titles', []), ensure_ascii=False),
                score_data.get('news_count'),
                score_data.get('total_score'),
                score_data.get('weight_vwap'),
                score_data.get('weight_news'),
                score_data.get('is_passed', 0)
            ))

            conn.commit()
            return cursor.lastrowid

    def get_validation_scores(self, stock_code: Optional[str] = None,
                             is_passed: Optional[bool] = None,
                             limit: int = 100) -> List[Dict]:
        """2차 필터링 점수 조회"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM validation_scores WHERE 1=1"
            params = []

            if stock_code:
                query += " AND stock_code = ?"
                params.append(stock_code)

            if is_passed is not None:
                query += " AND is_passed = ?"
                params.append(1 if is_passed else 0)

            query += " ORDER BY validation_time DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # ==================== 통계 분석 ====================

    def get_trades_with_context(self, stock_code: Optional[str] = None,
                                 start_date: Optional[str] = None,
                                 end_date: Optional[str] = None,
                                 parse_context: bool = True) -> List[Dict]:
        """
        거래 이력 조회 (컨텍스트 포함, JSON 파싱 옵션)

        Args:
            stock_code: 종목코드 필터 (optional)
            start_date: 시작일 필터 (optional)
            end_date: 종료일 필터 (optional)
            parse_context: True면 JSON을 dict로 파싱, False면 원본 string 유지

        Returns:
            거래 이력 리스트 (entry_context, exit_context, filter_scores가 dict 형태)
        """
        trades = self.get_trades(stock_code=stock_code, start_date=start_date, end_date=end_date)

        if not parse_context:
            return trades

        # JSON 파싱
        for trade in trades:
            if trade.get('entry_context'):
                try:
                    trade['entry_context'] = json.loads(trade['entry_context'])
                except (json.JSONDecodeError, TypeError):
                    trade['entry_context'] = {}

            if trade.get('exit_context'):
                try:
                    trade['exit_context'] = json.loads(trade['exit_context'])
                except (json.JSONDecodeError, TypeError):
                    trade['exit_context'] = {}

            if trade.get('filter_scores'):
                try:
                    trade['filter_scores'] = json.loads(trade['filter_scores'])
                except (json.JSONDecodeError, TypeError):
                    trade['filter_scores'] = {}

        return trades

    def get_trade_statistics(self, start_date: Optional[str] = None,
                            end_date: Optional[str] = None) -> Dict:
        """거래 통계 조회"""
        with sqlite3.connect(self.db_path) as conn:
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
                query += " AND trade_time >= ?"
                params.append(start_date)

            if end_date:
                query += " AND trade_time <= ?"
                params.append(end_date)

            cursor.execute(query, params)
            row = cursor.fetchone()

            if row:
                return {
                    'total_trades': row[0] or 0,
                    'total_buys': row[1] or 0,
                    'total_sells': row[2] or 0,
                    'winning_trades': row[3] or 0,
                    'win_rate': (row[3] / row[0] * 100) if row[0] else 0,
                    'avg_profit_rate': row[4] or 0,
                    'total_profit': row[5] or 0,
                    'max_profit_rate': row[6] or 0,
                    'min_profit_rate': row[7] or 0
                }

            return {}

    # ==================== 필터링 후보 종목 관리 ====================

    def insert_candidate(self, candidate_data: Dict[str, Any]) -> int:
        """필터링 후보 종목 추가 (upsert: 이미 있으면 업데이트)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # 같은 날짜에 같은 종목이 있는지 확인
            cursor.execute("""
                SELECT id FROM filtered_candidates
                WHERE stock_code = ? AND DATE(date_detected) = DATE(?)
            """, (candidate_data['stock_code'], candidate_data['date_detected']))

            existing = cursor.fetchone()

            if existing:
                # 업데이트 (더 좋은 점수로)
                if candidate_data.get('total_score', 0) > 0:
                    cursor.execute("""
                        UPDATE filtered_candidates
                        SET
                            total_score = ?,
                            ml_buy_probability = ?,
                            ml_predicted_profit = ?,
                            ml_rank = ?,
                            ml_last_updated = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (
                        candidate_data.get('total_score'),
                        candidate_data.get('ml_buy_probability'),
                        candidate_data.get('ml_predicted_profit'),
                        candidate_data.get('ml_rank'),
                        candidate_data.get('ml_last_updated'),
                        existing[0]
                    ))
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
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?
                )
            """, (
                candidate_data['date_detected'],
                candidate_data['stock_code'],
                candidate_data['stock_name'],
                candidate_data.get('strategy_tag', 'VWAP'),
                json.dumps(candidate_data.get('source_signal', []), ensure_ascii=False),
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

            conn.commit()
            return cursor.lastrowid

    def get_active_candidates(self, limit: int = 100) -> List[Dict]:
        """활성 감시 종목 조회 (is_active=1)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM filtered_candidates
                WHERE is_active = 1
                ORDER BY total_score DESC, ml_buy_probability DESC
                LIMIT ?
            """, (limit,))

            return [dict(row) for row in cursor.fetchall()]

    def get_candidates_by_date_range(self, start_date: str, end_date: Optional[str] = None) -> List[Dict]:
        """날짜 범위로 후보 종목 조회"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if end_date:
                cursor.execute("""
                    SELECT * FROM filtered_candidates
                    WHERE created_at BETWEEN ? AND ?
                    ORDER BY created_at DESC
                """, (start_date, end_date))
            else:
                cursor.execute("""
                    SELECT * FROM filtered_candidates
                    WHERE created_at >= ?
                    ORDER BY created_at DESC
                """, (start_date,))

            return [dict(row) for row in cursor.fetchall()]

    def update_candidate_labels(self, candidate_id: int, label_data: Dict[str, Any]):
        """후속 라벨 업데이트 (N일 후 수익률)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE filtered_candidates
                SET
                    next_1d_return = ?,
                    next_3d_return = ?,
                    next_5d_return = ?,
                    hit_takeprofit = ?,
                    hit_stoploss = ?,
                    hold_duration = ?,
                    realized_return = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
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

    def get_ml_training_data(self) -> List[Dict]:
        """ML 학습용 데이터 조회 (뷰 사용)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM ml_training_features")
            return [dict(row) for row in cursor.fetchall()]

    # ==================== 데이터 생명주기 관리 ====================

    def archive_old_candidates(self, days: int = 7) -> int:
        """
        오래된 비활성 후보를 archive로 이동

        Args:
            days: 경과 일수 (기본 7일)

        Returns:
            이동된 레코드 수
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # 이동 대상 조회
            cursor.execute("""
                SELECT * FROM filtered_candidates
                WHERE is_active = 0
                  AND julianday('now') - julianday(date_detected) > ?
            """, (days,))

            candidates = cursor.fetchall()

            if not candidates:
                return 0

            # archive로 복사
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
                    FROM filtered_candidates WHERE id = ?
                """, (row[0],))

            # 원본 삭제
            cursor.execute("""
                DELETE FROM filtered_candidates
                WHERE is_active = 0
                  AND julianday('now') - julianday(date_detected) > ?
            """, (days,))

            deleted_count = cursor.rowcount
            conn.commit()

            return deleted_count

    def clean_failed_candidates(self, days: int = 3) -> int:
        """
        필터링 실패 종목 정리

        Args:
            days: 경과 일수 (기본 3일)

        Returns:
            삭제된 레코드 수
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                DELETE FROM filtered_candidates
                WHERE pass_condition2 = 0
                  AND julianday('now') - julianday(date_detected) > ?
            """, (days,))

            deleted_count = cursor.rowcount
            conn.commit()

            return deleted_count

    def clean_old_archives(self, days: int = 180) -> int:
        """
        오래된 학습 완료 아카이브 삭제

        Args:
            days: 경과 일수 (기본 180일 = 6개월)

        Returns:
            삭제된 레코드 수
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                DELETE FROM archive_candidates
                WHERE used_in_training = 1
                  AND julianday('now') - julianday(date_detected) > ?
            """, (days,))

            deleted_count = cursor.rowcount
            conn.commit()

            return deleted_count

    def mark_as_used_in_training(self, ids: List[int]):
        """ML 학습에 사용된 아카이브 레코드 마킹"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            for record_id in ids:
                cursor.execute("""
                    UPDATE archive_candidates
                    SET used_in_training = 1
                    WHERE id = ?
                """, (record_id,))

            conn.commit()

    def close(self):
        """데이터베이스 연결 종료"""
        pass  # sqlite3는 with 문으로 자동 관리
