# 📌 Trading System Overview

## 목적
- 단기 자동매매 (5분봉 기반)
- 목표: 손실 최소화 + 진입 품질 개선

## 구조
- main_auto_trading.py → 실행 엔진
- trade_capture.py → entry/exit 기록
- trade_db.py → 데이터 병합
- analyze_trades.py → 성과 분석

## 현재 문제
- hard_stop 비율 45%
- early_fail 존재
- 진입 정확도 낮음

## 전략
- SMC + EMA9 + 거래량 기반
- regime: TREND / RANGE

## 리스크 관리
- hard_stop 3회 → 진입 차단 예정
- 승률 < 30% → 경고

## 목표
- hard_stop 제거
- entry 필터 강화
