# 백테스트 최적화 피드백 루프

## 개요
백테스트 결과를 프로그램 개선에 반영하는 완전한 피드백 시스템입니다.

## 시스템 구성

### 1. BacktestOptimizer (`analyzers/backtest_optimizer.py`)
백테스트 결과를 분석하여 최적화 추천 제공

**주요 기능:**
- **점수-수익률 상관관계 분석**: 각 분석 요소(뉴스, 기술적, 수급, 기본)와 실제 수익률 간 상관계수 계산
- **가중치 자동 조정**: 상관계수 기반으로 최적 가중치 제안
- **점수 구간별 성과**: 점수 구간(0-50, 50-60, 60-70, 70-80, 80-100)별 평균 수익률 및 승률 분석
- **VWAP 효과성 분석**: VWAP 필터 통과 종목 vs 미통과 종목 성과 비교
- **보유 기간 최적화**: 익절/손절 기준 제안

**핵심 메서드:**
```python
def analyze_score_correlation(candidates: pd.DataFrame) -> Dict[str, Any]
    # 각 점수와 수익률의 상관관계 분석
    # 상관계수 비율로 가중치 재분배 제안

def analyze_score_range_performance(candidates: pd.DataFrame) -> Dict[str, Any]
    # 점수 구간별 성과 분석
    # 최적 필터 점수 기준 제안

def generate_optimization_report(candidates: pd.DataFrame) -> Dict[str, Any]
    # 종합 리포트 생성
    # 모든 분석 결과 통합
```

### 2. ConfigManager (`utils/config_manager.py`)
설정 파일 관리 및 자동 백업

**주요 기능:**
- JSON 형식 설정 파일 관리
- 가중치/파라미터 자동 백업
- 설정 히스토리 관리

**설정 파일 구조** (`config/analysis_weights.json`):
```json
{
  "analysis_weights": {
    "news": 0.30,
    "technical": 0.40,
    "supply_demand": 0.15,
    "fundamental": 0.15
  },
  "filter_params": {
    "min_total_score": 65,
    "min_vwap_win_rate": 0.50,
    "min_vwap_trades": 2,
    "min_chart_bars": 100
  },
  "trading_params": {
    "holding_period_days": 3,
    "take_profit_pct": 0.10,
    "stop_loss_pct": -0.05,
    "max_stocks": 10,
    "investment_per_stock": 1000000
  },
  "last_updated": "2025-11-03T10:30:00",
  "version": "2.0"
}
```

**핵심 메서드:**
```python
def update_weights(new_weights: Dict[str, float]) -> bool
    # 가중치 업데이트 (자동 정규화)
    # 백업 생성

def backup_config() -> str
    # 타임스탬프 기반 백업 생성
    # config/backups/weights_backup_YYYYMMDD_HHMMSS.json
```

### 3. AnalysisEngine 통합 (`analyzers/analysis_engine.py`)
분석 엔진이 설정 파일에서 가중치 자동 로드

**변경 사항:**
```python
def __init__(self):
    # 기존: 하드코딩된 가중치
    # self.weights = {'news': 30, 'technical': 40, ...}

    # 신규: ConfigManager에서 동적 로드
    self.weights = self._load_weights()

def _load_weights(self) -> Dict[str, float]:
    # ConfigManager에서 가중치 로드
    # 실패 시 기본값 사용
```

## 사용 워크플로우

### 1단계: 백테스트 실행
메인 메뉴에서 `[5] 백테스트` 선택
```
📊 백테스트 범위 선택:
  [1] 최근 7일
  [2] 최근 30일
  [3] 최근 90일
  [4] 전체 기간
```

### 2단계: 결과 분석 및 추천 확인
백테스트 완료 후 자동으로 최적화 분석 실행

**출력 예시:**
```
🎯 최적화 분석 & 추천
════════════════════════════════════════════════════

1️⃣ 점수-수익률 상관관계:
┌──────────────┬────────────┐
│ 점수 타입    │ 상관계수   │
├──────────────┼────────────┤
│ news_score   │ 0.452      │ ← 높은 상관관계 (녹색)
│ technical    │ 0.312      │
│ supply       │ 0.089      │ ← 낮은 상관관계 (흰색)
│ fundamental  │ 0.156      │
└──────────────┴────────────┘

2️⃣ 가중치 조정 제안:
┌─────────────┬────────┬────────┬──────────┐
│ 요소        │ 현재   │ 제안   │ 변화     │
├─────────────┼────────┼────────┼──────────┤
│ news        │ 30.00% │ 37.50% │ +7.50%   │ ← 증가 권장 (녹색)
│ technical   │ 40.00% │ 35.00% │ -5.00%   │ ← 감소 권장 (빨강)
│ supply_dem. │ 15.00% │ 12.50% │ -2.50%   │
│ fundamental │ 15.00% │ 15.00% │ +0.00%   │
└─────────────┴────────┴────────┴──────────┘

3️⃣ 점수 구간별 성과:
┌────────────┬──────────┬──────────────┬────────┐
│ 점수 구간  │ 종목 수  │ 평균 수익률  │ 승률   │
├────────────┼──────────┼──────────────┼────────┤
│ 60-70점    │ 15       │ +1.2%        │ 53.3%  │
│ 70-80점    │ 25       │ +3.5%        │ 64.0%  │ ← 최고 성과 (녹색)
│ 80-100점   │ 10       │ +2.8%        │ 60.0%  │
└────────────┴──────────┴──────────────┴────────┘

4️⃣ 종합 추천 사항:
  1. 💡 'news' 가중치 증가 권장: 0.30 → 0.37 (+0.07) - 수익률과 높은 상관관계 (r=0.45)
  2. ⚠️  'technical' 가중치 감소 권장: 0.40 → 0.35 (-0.05) - 수익률과 낮은 상관관계 (r=0.31)
  3. 🎯 최고 성과 구간: 70-80점 (높음) - 평균 수익률 +3.50%, 승률 64.0%
  4. 💡 최소 점수 기준 상향 권장: 65점 → 70점 (품질 향상 기대)
  5. 📈 수익 종목 평균 수익률: +4.2%
  6. 💡 익절 기준 상향 권장: 10.0% → 12.5% (상위 20% 수익 활용)
```

### 3단계: 가중치 적용
```
💡 가중치 조정을 적용하시겠습니까?
적용하려면 'y' 입력 (기본: n): y

✓ 백업 생성: ./config/backups/weights_backup_20251103_103045.json
✅ 가중치 업데이트 완료: {'news': 0.375, 'technical': 0.35, ...}
   설정 파일: ./config/analysis_weights.json

💡 적용된 가중치는 다음 분석부터 자동으로 반영됩니다.
```

### 4단계: 리포트 저장 (선택)
```
리포트를 파일로 저장하시겠습니까?
저장하려면 'y' 입력 (기본: n): y

✅ 리포트 저장 완료: ./reports/optimization_20251103_103050.txt
```

### 5단계: 다음 분석부터 자동 반영
- 조건검색 필터링 (메뉴 2번)
- 자동매매 (메뉴 1번)
- 다음 백테스트

모두 새로운 가중치를 자동으로 사용합니다.

## 피드백 루프 다이어그램

```
┌──────────────────┐
│ 조건검색 필터링  │
│  (Menu #2)       │
└────────┬─────────┘
         │ 종목 선정
         ▼
┌──────────────────┐
│ VWAP 백테스트    │
│  (2차 필터)      │
└────────┬─────────┘
         │ VWAP 통과
         ▼
┌──────────────────┐
│ AI 종합 분석     │
│  (3차 필터)      │◄──────┐
└────────┬─────────┘       │
         │ DB 저장         │
         ▼                 │
┌──────────────────┐       │
│ 실전 투자 or     │       │
│ 전략 백테스트    │       │
│  (Menu #5)       │       │
└────────┬─────────┘       │
         │ 실제 수익률     │
         ▼                 │
┌──────────────────┐       │
│ BacktestOptimizer│       │
│  - 상관관계 분석 │       │
│  - 가중치 제안   │       │
└────────┬─────────┘       │
         │                 │
         ▼                 │
┌──────────────────┐       │
│ ConfigManager    │       │
│  - 가중치 저장   │       │
│  - 백업 생성     │       │
└────────┬─────────┘       │
         │                 │
         ▼                 │
┌──────────────────┐       │
│ AnalysisEngine   │       │
│  - 새 가중치 로드│───────┘
└──────────────────┘
```

## 분석 알고리즘 상세

### 상관관계 기반 가중치 재분배
```python
# 1. 각 점수와 수익률의 상관계수 계산
correlations = {
    'news_score': 0.452,
    'technical_score': 0.312,
    'supply_score': 0.089,
    'fundamental_score': 0.156
}

# 2. 절댓값 기준 정규화
abs_corrs = {k: abs(v) for k, v in correlations.items()}
# {'news_score': 0.452, 'technical_score': 0.312, ...}

total_abs = sum(abs_corrs.values())
# 0.452 + 0.312 + 0.089 + 0.156 = 1.009

# 3. 새 가중치 = 상관계수 비율
new_weights = {
    'news': 0.452 / 1.009 = 0.448 (44.8%)
    'technical': 0.312 / 1.009 = 0.309 (30.9%)
    'supply_demand': 0.089 / 1.009 = 0.088 (8.8%)
    'fundamental': 0.156 / 1.009 = 0.155 (15.5%)
}
```

### 점수 구간별 성과 분석
```python
# 구간 정의
score_ranges = [
    (0, 50, "매우 낮음"),
    (50, 60, "낮음"),
    (60, 70, "보통"),
    (70, 80, "높음"),
    (80, 100, "매우 높음")
]

# 각 구간별 계산
for min_score, max_score, label in score_ranges:
    range_stocks = candidates[
        (candidates['total_score'] >= min_score) &
        (candidates['total_score'] < max_score)
    ]

    avg_return = range_stocks['actual_return'].mean()
    win_rate = (range_stocks['actual_return'] > 0).sum() / len(range_stocks)

# 최고 성과 구간 찾기
best_range = max(ranges, key=lambda x: x['avg_return'])

# 필터 점수 조정 제안
if best_min_score > current_min_score:
    recommend("최소 점수 기준 상향: {current} → {best}")
```

## 백업 및 복원

### 자동 백업
가중치 변경 시 자동으로 백업 생성:
```
./config/backups/
├── weights_backup_20251101_093022.json
├── weights_backup_20251102_140533.json
└── weights_backup_20251103_103045.json  ← 최신
```

### 수동 복원
```python
from utils.config_manager import ConfigManager

manager = ConfigManager()
manager.restore_from_backup('./config/backups/weights_backup_20251102_140533.json')
```

## 예상 효과

### 단기 (1-2주)
- **데이터 기반 의사결정**: 감이 아닌 통계로 가중치 조정
- **투명한 성과 추적**: 어떤 분석이 실제 수익에 기여했는지 명확히 파악

### 중기 (1-3개월)
- **점진적 성능 향상**: 백테스트 → 최적화 → 적용 사이클 반복
- **시장 변화 적응**: 시장 상황에 따라 유효한 분석 요소가 변화하는 것을 자동 반영

### 장기 (3개월+)
- **전략 고도화**: 축적된 최적화 히스토리로 시장 사이클별 최적 설정 도출
- **ML 모델 개선**: Ranker 모델에도 동일한 피드백 루프 적용 가능

## 주의사항

1. **과최적화 방지**
   - 최소 30개 이상 종목 데이터로 분석
   - 단기 데이터만으로 가중치 변경 지양
   - 백업 보관으로 롤백 가능

2. **시장 변동성 고려**
   - 특정 시장 상황(급등/급락장)에서만 나온 결과는 일반화 주의
   - 여러 시장 국면 데이터 종합 분석 권장

3. **상관관계 vs 인과관계**
   - 상관관계가 높다고 인과관계는 아님
   - 추천은 참고용, 최종 판단은 사용자

4. **점진적 조정**
   - 한 번에 큰 폭으로 변경하지 말 것
   - 5-10% 범위 내 조정 권장

## 파일 구조
```
kiwoom_trading/
├── analyzers/
│   ├── backtest_optimizer.py      # 백테스트 최적화 엔진 (신규)
│   └── analysis_engine.py         # ConfigManager 통합 (수정)
├── utils/
│   └── config_manager.py          # 설정 관리 (신규)
├── config/
│   ├── analysis_weights.json      # 현재 설정 (자동 생성)
│   └── backups/                   # 백업 디렉토리 (자동 생성)
│       └── weights_backup_*.json
├── reports/
│   └── optimization_*.txt         # 최적화 리포트 (선택 저장)
└── main_menu.py                   # 백테스트 UI 통합 (수정)
```

## 다음 단계 확장 아이디어

1. **A/B 테스트 기능**
   - 두 가지 설정을 병렬 테스트
   - 통계적 유의성 검정

2. **자동 최적화 모드**
   - 사용자 확인 없이 자동 적용
   - 안전 장치: 성과 악화 시 자동 롤백

3. **시장 국면별 프로파일**
   - 강세장/약세장/횡보장 별도 설정 저장
   - 시장 국면 자동 감지 → 자동 전환

4. **앙상블 전략**
   - 여러 가중치 조합을 동시 운영
   - 포트폴리오 수준에서 분산 투자
