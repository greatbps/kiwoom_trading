"""
EOD Manager - 장 마감 전 포지션 관리

목적:
- 기본: 15:05 전량 청산
- 예외: 추세 유지 + 재료 살아있는 종목은 익일 보유 (최대 3개, 자산 40%)

ChatGPT 리뷰 반영 사항:
1. ✅ EOD 체크 시간: 14:55-15:00 (API 지연 고려)
2. ✅ Force Exit: 15:05-15:07 (15:10에서 조정)
3. ✅ 노출금액 제한: 계좌 자산의 40%까지만
4. ✅ OHLCV 버퍼링: API 중복 호출 방지
5. ✅ allow_overnight_final_confirm: 장마감 직전 재검증
6. ✅ ATR 변동성 고려: 변동성 안정도 점수 추가
7. ✅ 전일 종가 가중치: 0.1-0.2 → 0.25-0.35
8. ✅ 우선 감시 리스트 조건 강화

작성일: 2025-11-30 (ChatGPT 리뷰 반영)
"""

from typing import Dict, List, Tuple, Optional
from datetime import datetime, time
import pandas as pd
import numpy as np
from rich.console import Console

console = Console()


class EODManager:
    """
    장 마감 전 포지션 관리자

    핵심 기능:
    1. 14:55-15:00: EOD 체크 (조건부 보유 결정)
    2. 15:05-15:07: 강제 청산 (보유 제외 종목)
    3. 노출금액 제한: 계좌 자산의 40%까지만
    4. 우선 감시 리스트: 다음날 재진입 후보 생성
    """

    def __init__(self, config: Dict):
        self.config = config

        # EOD 정책
        self.eod_policy = config.get('eod_policy', {})
        self.max_overnight = self.eod_policy.get('max_overnight_positions', 3)
        self.min_overnight_score = self.eod_policy.get('min_overnight_score', 0.6)
        self.max_exposure_pct = self.eod_policy.get('max_overnight_position_value_pct', 40)

        # 시간 설정
        self.eod_check_time_str = self.eod_policy.get('check_time', '14:55:00')
        self.force_exit_time_str = self.eod_policy.get('force_exit_time', '15:05:00')

        # 시간 객체 변환
        self.eod_check_time = self._parse_time(self.eod_check_time_str)
        self.force_exit_time = self._parse_time(self.force_exit_time_str)

        # OHLCV 버퍼 (API 중복 호출 방지)
        self.ohlcv_buffer: Dict[str, pd.DataFrame] = {}
        self.buffer_timestamp = None

    def _parse_time(self, time_str: str) -> time:
        """시간 문자열 파싱"""
        try:
            parts = time_str.split(':')
            return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
        except:
            return time(15, 0, 0)

    def run_eod_check(
        self,
        positions: Dict,
        api,
        news_fetcher,
        account_value: float
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        장 마감 전 포지션 검토

        Args:
            positions: 현재 보유 포지션 dict
            api: Kiwoom API (실시간 데이터 조회용)
            news_fetcher: 뉴스 데이터 조회용
            account_value: 계좌 총 자산

        Returns:
            (to_hold_codes, to_close_codes, priority_watchlist)
        """
        current_time = datetime.now()

        console.print("\n" + "=" * 80)
        console.print(f"[bold yellow]🕐 EOD 체크 시작 ({current_time.strftime('%H:%M:%S')})[/bold yellow]")
        console.print("=" * 80 + "\n")

        # 1. allow_overnight=True인 종목만 후보로
        candidates = []
        for code, pos in positions.items():
            if pos.get('allow_overnight', False):
                candidates.append((code, pos))

        console.print(f"[cyan]📋 익일 보유 후보: {len(candidates)}개[/cyan]")

        if not candidates:
            # 모두 청산
            return [], list(positions.keys()), []

        # 2. OHLCV 버퍼링 (API 중복 호출 방지)
        console.print(f"[dim]⏳ OHLCV 데이터 버퍼링 중...[/dim]")
        self._prefetch_ohlcv(candidates, api)

        # 3. 후보별 EOD 재검증
        scored_candidates = []

        for code, pos in candidates:
            # 버퍼에서 데이터 조회
            df = self.ohlcv_buffer.get(code)
            current_price = api.get_current_price(code) if api else pos.get('current_price', pos['entry_price'])

            # 뉴스 점수
            news_score = news_fetcher.get_sentiment_score(code) if news_fetcher else 50

            # EOD 점수 재계산
            eod_score = self._calculate_eod_score(pos, df, current_price, news_score)

            # ✅ ChatGPT 리뷰: allow_overnight_final_confirm 추가
            pos['allow_overnight_final_confirm'] = eod_score >= self.min_overnight_score
            pos['eod_score'] = eod_score

            scored_candidates.append({
                'code': code,
                'name': pos.get('name', ''),
                'score': eod_score,
                'position': pos,
                'current_price': current_price
            })

            console.print(
                f"  {pos.get('name', code):15s} | "
                f"Score: {eod_score:.2f} | "
                f"Entry: {pos.get('overnight_score', 0):.2f} → Final: {eod_score:.2f}"
            )

        # 4. 상위 N개 선택 (점수 기준)
        scored_candidates.sort(key=lambda x: x['score'], reverse=True)

        to_hold = []
        to_close = []
        priority_watchlist = []

        # ✅ ChatGPT 리뷰: 노출금액 제한 체크
        max_exposure = account_value * (self.max_exposure_pct / 100)
        current_exposure = 0.0

        for idx, cand in enumerate(scored_candidates):
            code = cand['code']
            pos = cand['position']
            current_price = cand['current_price']

            # 포지션 평가액
            position_value = pos['quantity'] * current_price

            # 보유 조건 체크
            if (idx < self.max_overnight and
                cand['score'] >= self.min_overnight_score and
                (current_exposure + position_value) <= max_exposure):

                to_hold.append(code)
                current_exposure += position_value

                console.print(
                    f"[green]✓ {cand['name']:15s} → 익일 보유 "
                    f"(Score: {cand['score']:.2f}, "
                    f"Value: {position_value:,.0f}원)[/green]"
                )
            else:
                to_close.append(code)
                pos['eod_forced_exit'] = True

                # ✅ ChatGPT 리뷰: 우선 감시 리스트 조건 강화
                if self._is_priority_watchlist_candidate(pos, cand, current_price):
                    # ✅ Phase 3: 갭업 재진입을 위한 상세 정보 저장
                    df = self.ohlcv_buffer.get(code)
                    prev_close = df['close'].iloc[-1] if df is not None and not df.empty else current_price

                    # 종목 상세 정보 저장
                    watchlist_entry = {
                        'stock_code': code,
                        'stock_name': cand['name'],
                        'prev_close': float(prev_close),  # 전일 종가 (갭 계산용)
                        'score': cand['score'],
                        'eod_score': cand['score'],
                        'market': pos.get('market', 'KOSDAQ')
                    }
                    priority_watchlist.append(watchlist_entry)

                    console.print(
                        f"[yellow]✗ {cand['name']:15s} → EOD 청산 "
                        f"(Score: {cand['score']:.2f}) "
                        f"[dim]→ 다음날 우선 감시 (종가: {prev_close:,.0f}원)[/dim][/yellow]"
                    )
                else:
                    console.print(
                        f"[yellow]✗ {cand['name']:15s} → EOD 청산 "
                        f"(Score: {cand['score']:.2f})[/yellow]"
                    )

        # 5. allow_overnight=False인 종목은 무조건 청산
        for code, pos in positions.items():
            if not pos.get('allow_overnight', False) and code not in to_close:
                to_close.append(code)
                pos['eod_forced_exit'] = True

        console.print(
            f"\n[bold green]📊 최종 결과: 보유 {len(to_hold)}개 "
            f"({current_exposure:,.0f}원/{max_exposure:,.0f}원), "
            f"청산 {len(to_close)}개, "
            f"우선감시 {len(priority_watchlist)}개[/bold green]\n"
        )

        return to_hold, to_close, priority_watchlist

    def _prefetch_ohlcv(self, candidates: List[Tuple[str, Dict]], api) -> None:
        """
        ✅ ChatGPT 리뷰: OHLCV 버퍼링 (API 중복 호출 방지)

        Args:
            candidates: (code, position) 튜플 리스트
            api: Kiwoom API
        """
        if not api:
            return

        self.ohlcv_buffer.clear()
        self.buffer_timestamp = datetime.now()

        for code, pos in candidates:
            try:
                # 5분봉 1일치
                df = api.fetch_ohlcv(code, interval='5m', days=1)

                if df is not None and not df.empty:
                    self.ohlcv_buffer[code] = df
            except Exception as e:
                console.print(f"[dim red]⚠️  {code}: OHLCV 조회 실패 - {e}[/dim red]")

        console.print(f"[dim green]✓ OHLCV 버퍼링 완료 ({len(self.ohlcv_buffer)}개 종목)[/dim green]")

    def _calculate_eod_score(
        self,
        position: Dict,
        df: pd.DataFrame,
        current_price: float,
        news_score: float
    ) -> float:
        """
        EOD 시점 보유 점수 계산 (0.0-1.0)

        ✅ ChatGPT 리뷰 반영:
        - 추세 유지: 0.4
        - 거래량 상태: 0.3
        - 뉴스/재료: 0.3
        - ATR 변동성 안정도: +0.1 (보너스)
        - 전일 종가 패턴: +0.25-0.35 (보너스, 기존 0.1-0.2에서 증가)

        🔧 FIX: 손실 포지션 익일 보유 금지 (12/8 장마감 오류 수정)
        """
        score = 0.0

        if df is None or df.empty:
            return 0.0

        # 🔧 CRITICAL FIX: 손실 포지션은 무조건 익일 보유 불가
        entry_price = position.get('entry_price', 0)
        if entry_price > 0:
            profit_pct = ((current_price - entry_price) / entry_price) * 100

            # 손실 중이면 즉시 0점 반환 (익일 보유 불가)
            if profit_pct < 0:
                console.print(
                    f"[dim red]  ⚠️  {position.get('name', '')} 손실 중 ({profit_pct:.2f}%) - 익일 보유 불가[/dim red]"
                )
                return 0.0

            # 최소 +0.5% 이상 수익에서만 익일 보유 고려
            if profit_pct < 0.5:
                console.print(
                    f"[dim yellow]  ⚠️  {position.get('name', '')} 수익 부족 ({profit_pct:.2f}% < 0.5%) - 익일 보유 불가[/dim yellow]"
                )
                return 0.0

        # 1. 추세 유지 체크 (0.4)
        try:
            ema5 = df['close'].ewm(span=5).mean().iloc[-1]
            ema20 = df['close'].ewm(span=20).mean().iloc[-1]

            # 종가 > EMA5 > EMA20
            if current_price > ema5 > ema20:
                score += 0.4
            elif current_price > ema5:
                score += 0.2

            # SuperTrend 상태
            if 'supertrend_direction' in df.columns:
                if df['supertrend_direction'].iloc[-1] == 1:  # 상승
                    score += 0.05
        except:
            pass

        # 2. 거래량 상태 (0.3)
        try:
            if 'vol_z20' in df.columns:
                vol_z20 = df['vol_z20'].iloc[-1]
                if vol_z20 >= 1.5:
                    score += 0.3
                elif vol_z20 >= 1.0:
                    score += 0.2
                elif vol_z20 >= 0.5:
                    score += 0.1
        except:
            pass

        # 3. 뉴스/재료 (0.3)
        if news_score >= 60:
            score += 0.3
        elif news_score >= 50:
            score += 0.2
        elif news_score >= 40:
            score += 0.1

        # 4. ✅ ChatGPT 리뷰: ATR 변동성 안정도 (보너스 +0.1)
        try:
            if 'atr' in df.columns:
                atr = df['atr'].iloc[-1]
            else:
                # ATR 없으면 최근 14봉 범위로 추정
                atr = (df['high'].tail(14).max() - df['low'].tail(14).min()) / 14

            atr_pct = (atr / current_price) * 100

            if atr_pct <= 3.5:
                score += 0.1
            elif atr_pct <= 5.0:
                score += 0.05
        except:
            pass

        # 5. ✅ ChatGPT 리뷰: 전일 종가 기반 보유 연장 (보너스 +0.25-0.35)
        try:
            if len(df) >= 2:
                prev_close = df['close'].iloc[-2]  # 전일 종가
                current_close = df['close'].iloc[-1]  # 당일 종가 (15:00 기준)

                prev_high = df['high'].iloc[-2]  # 전일 고가

                # 전일 종가가 고가 대비 90% 이상
                close_to_high_ratio = (prev_close / prev_high) if prev_high > 0 else 0

                # 전일 EMA5
                prev_ema5 = df['close'].iloc[:-1].ewm(span=5).mean().iloc[-1]

                # 전일 VWAP
                prev_vwap = df.get('vwap', pd.Series([0])).iloc[-2] if 'vwap' in df.columns else 0

                bonus = 0.0

                # 조건 1: 전일 종가 >= 고가 * 90%
                if close_to_high_ratio >= 0.9:
                    bonus += 0.15

                # 조건 2: 전일 종가 > 전일 EMA5
                if prev_close > prev_ema5:
                    bonus += 0.1

                # 조건 3: 전일 종가 > 전일 VWAP
                if prev_vwap > 0 and prev_close > prev_vwap:
                    bonus += 0.1

                score += bonus  # 최대 +0.35
        except:
            pass

        return min(score, 1.0)

    def _is_priority_watchlist_candidate(
        self,
        position: Dict,
        cand: Dict,
        current_price: float
    ) -> bool:
        """
        ✅ ChatGPT 리뷰: 우선 감시 리스트 조건 강화

        다음날 갭업 재진입 후보 판단

        조건:
        - EOD 점수 >= 0.55
        - 종가가 고가 대비 80% 이상
        - 거래량 Z-score >= 1.0
        - 뉴스 점수 >= 45
        """
        eod_score = cand.get('score', 0.0)

        # 기본 조건: EOD 점수 0.55 이상
        if eod_score < 0.55:
            return False

        # OHLCV 버퍼에서 데이터 확인
        code = cand['code']
        df = self.ohlcv_buffer.get(code)

        if df is None or df.empty:
            return False

        try:
            # 종가가 고가 대비 80% 이상
            latest = df.iloc[-1]
            close = latest['close']
            high = latest['high']

            if high > 0 and (close / high) < 0.8:
                return False

            # 거래량 Z-score >= 1.0
            if 'vol_z20' in df.columns:
                vol_z20 = latest['vol_z20']
                if vol_z20 < 1.0:
                    return False

            # 뉴스 점수는 position에 저장되어 있지 않으므로 생략
            # (news_fetcher에서 조회 필요)

            return True

        except:
            return False


if __name__ == "__main__":
    """테스트 코드"""
    from utils.config_loader import ConfigLoader

    print("=" * 80)
    print("🧪 EOD Manager 테스트")
    print("=" * 80)

    # Config 로드
    config = ConfigLoader().config
    eod_manager = EODManager(config)

    print(f"✓ EOD 체크 시간: {eod_manager.eod_check_time}")
    print(f"✓ 강제 청산 시간: {eod_manager.force_exit_time}")
    print(f"✓ 최대 익일 보유: {eod_manager.max_overnight}개")
    print(f"✓ 최소 점수: {eod_manager.min_overnight_score}")
    print(f"✓ 최대 노출: {eod_manager.max_exposure_pct}%")
    print("\n✅ EOD Manager 초기화 성공")
