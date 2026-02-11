# 프로젝트 정리 아카이브 (2026-01-04)

## 정리 내용

이 디렉토리는 2026년 1월 4일 프로젝트 정리 작업 중 생성된 아카이브입니다.
오래된 백업 파일과 로그 파일들을 보관하고 있습니다.

## 아카이브 구조

### old_backups/ (6.4MB)
- `database/` - 데이터베이스 백업 (8KB)
- `exit_logic_integration_20251115_065124/` - 2025년 11월 15일 exit logic 통합 백업
- `exit_logic_integration_20251116_051020/` - 2025년 11월 16일 exit logic 통합 백업 #1
- `exit_logic_integration_20251116_051924/` - 2025년 11월 16일 exit logic 통합 백업 #2
- `pre-refactoring-20251108_155314/` - 2025년 11월 8일 리팩토링 전 백업 (5.9MB)

### old_logs/ (48KB)
- `summary_20251025_*.json` - 2025년 10월 25일 거래 요약 파일
- `trade_log_20251025_*.jsonl` - 2025년 10월 25일 거래 로그 파일

### old_data/ (132KB)
- `risk_log.json.backup` - 2026년 1월 4일 중복 거래 정리 전 백업 (255건 → 12건 정리 작업 전 원본)

## 삭제된 파일

### Python 캐시
- `./analyzers/__pycache__/` - Python 컴파일 캐시 파일들 (자동 재생성 가능)

## 보관된 최신 파일들 (프로젝트에 유지)

### 로그 파일
- `logs/signal_orchestrator.log` - 818KB (2026-01-03)
- `logs/auto_trading_errors.log` - 53KB (2025-12-22)
- `logs/direct_run.log` - 2.1KB (2025-12-11)
- `logs/auto_trading_20251211_053550.log` - 213B (2025-12-11)
- `data/trade_alerts.log` - 17KB (2026-01-02)

## 참고사항

- 이 아카이브는 필요 시 삭제 가능합니다
- 중요한 백업은 `old_data/risk_log.json.backup` (중복 제거 전 원본)
- 나머지는 2개월 이상 된 백업 파일들입니다
