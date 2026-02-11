# 🗄️ Kiwoom Trading System - Database Design

## 📋 설계 원칙

### 1. 데이터 저장 원칙
**가변 데이터는 저장하지 않고 실시간 조회**
- ❌ 저장하지 않음: 현재가, 호가, 실시간 체결가, 거래량
- ✅ 저장: 매매 시점의 확정된 값, 분석 결과, 설정값
- 📡 실시간 조회: API를 통해 필요할 때마다 최신 데이터 조회

### 2. 계산 데이터 처리
**저장하지 않고 실시간 계산하여 표시**
- 익절가 = 매수가 × (1 + 목표수익률)
- 손절가 = 매수가 × (1 - 최대손실률)
- 평가금액 = 현재가 × 수량 (API 실시간 조회)
- 손익금액 = (현재가 - 매수가) × 수량
- 손익률 = (현재가 - 매수가) / 매수가 × 100

### 3. 데이터 무결성
- 모든 거래는 추적 가능해야 함
- 매수/매도 쌍으로 연결 가능해야 함
- 삭제는 지양하고 상태값으로 관리

---

## 📊 데이터베이스 스키마

### 1. 종목 기본정보 (stocks)
**목적:** 종목의 기본 정보 저장 (자주 변하지 않는 정보)

```sql
CREATE TABLE stocks (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL UNIQUE,    -- 종목코드 (예: 005930)
    stock_name VARCHAR(100) NOT NULL,          -- 종목명 (예: 삼성전자)
    market_type VARCHAR(20),                   -- 시장구분 (KOSPI/KOSDAQ)
    sector VARCHAR(50),                        -- 업종
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_stocks_code ON stocks(stock_code);
```

**저장 데이터:**
- ✅ 종목코드, 종목명 (변하지 않음)
- ✅ 시장구분, 업종 (거의 변하지 않음)
- ❌ 현재가, 거래량 등 (실시간 변동)

---

### 2. 분석 결과 (analysis_results)
**목적:** 엔진이 계산한 종목 분석 점수 저장

```sql
CREATE TABLE analysis_results (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL,           -- 종목코드
    stock_name VARCHAR(100) NOT NULL,          -- 종목명
    strategy_name VARCHAR(50) NOT NULL,        -- 전략명 (momentum, breakout 등)

    -- 점수 정보 (분석 시점의 확정값)
    total_score DECIMAL(5,2),                  -- 종합 점수 (0-100)
    technical_score DECIMAL(5,2),              -- 기술적 분석 점수
    sentiment_score DECIMAL(5,2),              -- 감성 분석 점수
    supply_demand_score DECIMAL(5,2),          -- 수급 분석 점수

    -- 분석 시점 정보
    analysis_price DECIMAL(12,2),              -- 분석 시점 가격 (참고용)
    analysis_date TIMESTAMP NOT NULL,          -- 분석 일시

    -- 상태 관리
    is_active BOOLEAN DEFAULT TRUE,            -- 활성 상태
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_analysis_stock ON analysis_results(stock_code);
CREATE INDEX idx_analysis_date ON analysis_results(analysis_date);
CREATE INDEX idx_analysis_strategy ON analysis_results(strategy_name);
```

**저장 데이터:**
- ✅ 분석 점수 (확정된 계산 결과)
- ✅ 분석 시점의 가격 (참고용)
- ✅ 사용된 전략명
- ❌ 현재가 (실시간 API로 조회)

---

### 3. 매수 주문 (buy_orders)
**목적:** 매수 주문 및 체결 정보 저장

```sql
CREATE TABLE buy_orders (
    id SERIAL PRIMARY KEY,

    -- 종목 정보
    stock_code VARCHAR(10) NOT NULL,           -- 종목코드
    stock_name VARCHAR(100) NOT NULL,          -- 종목명

    -- 매수 정보 (확정값)
    buy_price DECIMAL(12,2) NOT NULL,          -- 매수 체결가
    quantity INTEGER NOT NULL,                 -- 매수 수량
    total_amount DECIMAL(15,2) NOT NULL,       -- 총 매수금액 (buy_price × quantity)

    -- 전략 정보
    strategy_name VARCHAR(50) NOT NULL,        -- 사용된 전략명
    analysis_score DECIMAL(5,2),               -- 분석 점수 (매수 결정시)

    -- 주문 정보
    order_id VARCHAR(50),                      -- 키움 주문번호
    order_status VARCHAR(20) DEFAULT 'PENDING', -- 주문상태 (PENDING/FILLED/CANCELLED)

    -- 설정값 (매수 시점의 설정)
    target_profit_rate DECIMAL(5,2),           -- 목표 수익률 (%)
    stop_loss_rate DECIMAL(5,2),               -- 손절 기준 (%)

    -- 시간 정보
    ordered_at TIMESTAMP NOT NULL,             -- 주문 일시
    filled_at TIMESTAMP,                       -- 체결 일시

    -- 상태 관리
    is_sold BOOLEAN DEFAULT FALSE,             -- 매도 여부
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_buy_stock ON buy_orders(stock_code);
CREATE INDEX idx_buy_status ON buy_orders(order_status);
CREATE INDEX idx_buy_is_sold ON buy_orders(is_sold);
CREATE INDEX idx_buy_ordered_at ON buy_orders(ordered_at);
```

**저장 데이터:**
- ✅ 매수 체결가, 수량, 총액 (확정값)
- ✅ 전략명, 분석 점수 (매수 근거)
- ✅ 목표수익률, 손절률 (설정값)
- ❌ 현재가, 평가금액 (실시간 계산)

**실시간 계산 (표시용):**
```python
# 조회 시점에 계산
current_price = api.get_current_price(stock_code)  # API 호출
target_price = buy_price * (1 + target_profit_rate / 100)
stop_loss_price = buy_price * (1 - stop_loss_rate / 100)
current_value = current_price * quantity
profit_loss = (current_price - buy_price) * quantity
profit_loss_rate = (current_price - buy_price) / buy_price * 100
```

---

### 4. 매도 거래 (sell_trades)
**목적:** 매도 체결 정보 및 손익 저장

```sql
CREATE TABLE sell_trades (
    id SERIAL PRIMARY KEY,

    -- 매수 주문 연결
    buy_order_id INTEGER NOT NULL,             -- 매수 주문 ID (FK)

    -- 종목 정보
    stock_code VARCHAR(10) NOT NULL,           -- 종목코드
    stock_name VARCHAR(100) NOT NULL,          -- 종목명

    -- 매도 정보 (확정값)
    sell_price DECIMAL(12,2) NOT NULL,         -- 매도 체결가
    quantity INTEGER NOT NULL,                 -- 매도 수량
    total_amount DECIMAL(15,2) NOT NULL,       -- 총 매도금액 (sell_price × quantity)

    -- 손익 정보 (확정값 - 매도 시점 계산)
    buy_price DECIMAL(12,2) NOT NULL,          -- 매수가 (참고)
    profit_loss DECIMAL(15,2) NOT NULL,        -- 손익금액
    profit_loss_rate DECIMAL(8,4) NOT NULL,    -- 손익률 (%)

    -- 매도 사유
    sell_reason VARCHAR(50),                   -- 매도 사유 (TAKE_PROFIT/STOP_LOSS/MANUAL/STRATEGY)
    strategy_name VARCHAR(50),                 -- 전략명

    -- 주문 정보
    order_id VARCHAR(50),                      -- 키움 주문번호

    -- 시간 정보
    ordered_at TIMESTAMP NOT NULL,             -- 주문 일시
    filled_at TIMESTAMP,                       -- 체결 일시

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (buy_order_id) REFERENCES buy_orders(id)
);

CREATE INDEX idx_sell_buy_order ON sell_trades(buy_order_id);
CREATE INDEX idx_sell_stock ON sell_trades(stock_code);
CREATE INDEX idx_sell_reason ON sell_trades(sell_reason);
CREATE INDEX idx_sell_filled_at ON sell_trades(filled_at);
```

**저장 데이터:**
- ✅ 매도 체결가, 수량, 총액 (확정값)
- ✅ 손익금액, 손익률 (매도 시점 확정)
- ✅ 매도 사유, 전략명
- ✅ 매수 주문과의 연결 (추적성)

---

### 5. 포트폴리오 현황 (portfolio_holdings)
**목적:** 현재 보유 중인 종목 빠른 조회용 (뷰 테이블)

```sql
CREATE VIEW portfolio_holdings AS
SELECT
    bo.id as buy_order_id,
    bo.stock_code,
    bo.stock_name,
    bo.buy_price,
    bo.quantity,
    bo.total_amount as buy_amount,
    bo.strategy_name,
    bo.analysis_score,
    bo.target_profit_rate,
    bo.stop_loss_rate,
    bo.filled_at as buy_date
FROM buy_orders bo
WHERE bo.order_status = 'FILLED'
  AND bo.is_sold = FALSE;
```

**사용 방법:**
```python
# DB에서 보유 종목 조회
holdings = session.query(PortfolioHoldings).all()

for holding in holdings:
    # 실시간 정보는 API로 조회
    current_price = api.get_current_price(holding.stock_code)

    # 실시간 계산
    target_price = holding.buy_price * (1 + holding.target_profit_rate / 100)
    stop_loss_price = holding.buy_price * (1 - holding.stop_loss_rate / 100)
    current_value = current_price * holding.quantity
    profit_loss = (current_price - holding.buy_price) * holding.quantity
    profit_loss_rate = (current_price - holding.buy_price) / holding.buy_price * 100

    print(f"{holding.stock_name}: {profit_loss_rate:.2f}%")
```

---

### 6. 모니터링 종목 (monitoring_stocks)
**목적:** 실시간 모니터링할 종목 관리

```sql
CREATE TABLE monitoring_stocks (
    id SERIAL PRIMARY KEY,

    -- 종목 정보
    stock_code VARCHAR(10) NOT NULL,           -- 종목코드
    stock_name VARCHAR(100) NOT NULL,          -- 종목명

    -- 전략 정보
    strategy_name VARCHAR(50) NOT NULL,        -- 모니터링 전략
    analysis_score DECIMAL(5,2),               -- 분석 점수

    -- 진입 조건 (설정값)
    target_entry_price DECIMAL(12,2),          -- 목표 진입가
    entry_condition TEXT,                      -- 진입 조건 설명

    -- 상태 관리
    is_active BOOLEAN DEFAULT TRUE,            -- 활성 상태
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,                      -- 만료 일시

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_monitoring_active ON monitoring_stocks(is_active);
CREATE INDEX idx_monitoring_stock ON monitoring_stocks(stock_code);
CREATE INDEX idx_monitoring_expires ON monitoring_stocks(expires_at);
```

---

### 7. 시스템 설정 (system_settings)
**목적:** 시스템 전역 설정 저장

```sql
CREATE TABLE system_settings (
    id SERIAL PRIMARY KEY,
    setting_key VARCHAR(100) NOT NULL UNIQUE,  -- 설정 키
    setting_value TEXT,                        -- 설정 값
    setting_type VARCHAR(20),                  -- 값 타입 (STRING/INTEGER/FLOAT/BOOLEAN/JSON)
    description TEXT,                          -- 설명
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 기본 설정값 예시
INSERT INTO system_settings (setting_key, setting_value, setting_type, description) VALUES
('MAX_POSITION_COUNT', '5', 'INTEGER', '최대 동시 보유 종목 수'),
('DEFAULT_TARGET_PROFIT_RATE', '3.0', 'FLOAT', '기본 목표 수익률 (%)'),
('DEFAULT_STOP_LOSS_RATE', '2.0', 'FLOAT', '기본 손절 기준 (%)'),
('MAX_DAILY_LOSS', '100000', 'INTEGER', '일일 최대 손실 한도 (원)'),
('TRADING_MODE', 'SIMULATION', 'STRING', '거래 모드 (REAL/SIMULATION)');
```

---

## 📈 데이터 흐름

### 매수 프로세스
```
1. 종목 분석
   ↓
2. analysis_results에 분석 결과 저장 (점수, 전략명)
   ↓
3. 매수 신호 발생
   ↓
4. buy_orders에 주문 생성 (PENDING)
   ↓
5. API 주문 실행
   ↓
6. buy_orders 상태 업데이트 (FILLED)
   ↓
7. portfolio_holdings 뷰에 자동 반영
```

### 매도 프로세스
```
1. 보유 종목 실시간 모니터링 (API로 현재가 조회)
   ↓
2. 익절/손절 조건 체크 (실시간 계산)
   ↓
3. 매도 신호 발생
   ↓
4. sell_trades에 주문 생성
   ↓
5. API 주문 실행
   ↓
6. sell_trades에 체결 정보 저장 (손익 확정)
   ↓
7. buy_orders의 is_sold = TRUE 업데이트
   ↓
8. portfolio_holdings에서 자동 제외
```

### 실시간 조회 프로세스
```
1. DB에서 보유 종목 조회 (portfolio_holdings)
   ↓
2. 각 종목의 현재가 API 조회
   ↓
3. 실시간 계산
   - 평가금액 = 현재가 × 수량
   - 손익금액 = (현재가 - 매수가) × 수량
   - 손익률 = 손익금액 / 매수금액 × 100
   - 익절가 = 매수가 × (1 + 목표수익률)
   - 손절가 = 매수가 × (1 - 손절률)
   ↓
4. 화면 표시
```

---

## 🔍 주요 쿼리 예시

### 1. 현재 보유 종목 조회
```sql
SELECT
    stock_code,
    stock_name,
    buy_price,
    quantity,
    total_amount,
    strategy_name,
    target_profit_rate,
    stop_loss_rate,
    filled_at
FROM buy_orders
WHERE order_status = 'FILLED'
  AND is_sold = FALSE
ORDER BY filled_at DESC;
```

### 2. 당일 거래 내역 조회
```sql
SELECT
    st.stock_name,
    st.buy_price,
    st.sell_price,
    st.quantity,
    st.profit_loss,
    st.profit_loss_rate,
    st.sell_reason,
    st.filled_at
FROM sell_trades st
WHERE DATE(st.filled_at) = CURRENT_DATE
ORDER BY st.filled_at DESC;
```

### 3. 전략별 수익률 통계
```sql
SELECT
    strategy_name,
    COUNT(*) as trade_count,
    SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as win_count,
    AVG(profit_loss_rate) as avg_profit_rate,
    SUM(profit_loss) as total_profit
FROM sell_trades
GROUP BY strategy_name
ORDER BY total_profit DESC;
```

### 4. 분석 점수가 높은 종목 조회
```sql
SELECT
    ar.stock_code,
    ar.stock_name,
    ar.strategy_name,
    ar.total_score,
    ar.analysis_date
FROM analysis_results ar
WHERE ar.is_active = TRUE
  AND ar.total_score >= 70
  AND ar.analysis_date >= NOW() - INTERVAL '1 day'
ORDER BY ar.total_score DESC, ar.analysis_date DESC
LIMIT 20;
```

---

## 💾 저장 vs 계산 정리표

| 항목 | 저장 여부 | 이유 |
|------|----------|------|
| 종목코드, 종목명 | ✅ 저장 | 변하지 않음 |
| 매수가, 매수수량 | ✅ 저장 | 확정된 값 |
| 매도가, 손익 | ✅ 저장 | 매도 시점 확정 |
| 분석점수 | ✅ 저장 | 분석 시점 확정 |
| 전략명 | ✅ 저장 | 매매 근거 |
| 목표수익률, 손절률 | ✅ 저장 | 설정값 |
| 현재가 | ❌ 저장 안함 | 실시간 변동 → API 조회 |
| 평가금액 | ❌ 저장 안함 | 계산: 현재가 × 수량 |
| 손익금액 | ❌ 저장 안함 | 계산: (현재가 - 매수가) × 수량 |
| 손익률 | ❌ 저장 안함 | 계산: 손익금액 / 매수금액 × 100 |
| 익절가 | ❌ 저장 안함 | 계산: 매수가 × (1 + 목표수익률) |
| 손절가 | ❌ 저장 안함 | 계산: 매수가 × (1 - 손절률) |

---

## 🎯 핵심 원칙 요약

1. **저장하는 것**: 확정된 과거 데이터, 설정값, 분석 결과
2. **저장하지 않는 것**: 실시간 변동 데이터
3. **실시간 조회**: API를 통해 필요할 때마다 최신 데이터 획득
4. **실시간 계산**: 저장된 값과 조회된 값으로 필요한 지표 계산
5. **추적성**: 모든 거래는 매수-매도 쌍으로 추적 가능

이러한 설계로 인해:
- 💾 저장 공간 효율적 사용
- 🔄 항상 최신 정보 표시
- 📊 정확한 손익 계산
- 🎯 데이터 무결성 유지
