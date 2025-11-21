"""
조건검색 및 VWAP 필터링

키움 조건식 검색 + VWAP 백테스트 필터링
"""
from typing import Dict, Set, List, Optional
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich import box

from kiwoom_api import KiwoomAPI
from analyzers.pre_trade_validator import PreTradeValidator
from exceptions import handle_api_errors
from database.trading_db_v2 import TradingDatabaseV2 as TradingDatabase

console = Console()


class ConditionScanner:
    """조건검색 및 필터링 담당"""

    def __init__(
        self,
        api: KiwoomAPI,
        validator: PreTradeValidator,
        db: Optional[TradingDatabase] = None
    ):
        """
        Args:
            api: KiwoomAPI 인스턴스
            validator: PreTradeValidator 인스턴스
            db: TradingDatabase 인스턴스 (optional)
        """
        self.api = api
        self.validator = validator
        self.db = db

    @handle_api_errors(default_return=[], log_errors=True)
    def run_condition_search(self, condition_name: str) -> List[Dict]:
        """
        조건식 검색 실행

        Args:
            condition_name: 조건식 이름

        Returns:
            종목 정보 리스트
            [
                {
                    'stock_code': str,
                    'stock_name': str
                },
                ...
            ]
        """
        console.print(f"[cyan]🔍 조건식 검색 실행: {condition_name}[/cyan]")

        try:
            result = self.api.get_stock_list_by_condition(condition_name)

            if result.get('return_code') != 0:
                console.print(f"[red]❌ 조건검색 실패: {result.get('return_msg')}[/red]")
                return []

            stocks = result.get('stocks', [])
            console.print(f"[green]✓ {len(stocks)}개 종목 발견[/green]")

            return stocks

        except Exception as e:
            console.print(f"[red]❌ 조건검색 오류: {e}[/red]")
            return []

    def filter_with_vwap(
        self,
        stock_list: List[Dict],
        min_win_rate: float = 40.0,
        min_avg_profit: float = 1.0
    ) -> Dict[str, Dict]:
        """
        VWAP 백테스트 필터링

        Args:
            stock_list: 조건검색 결과 종목 리스트
            min_win_rate: 최소 승률 (%)
            min_avg_profit: 최소 평균 수익률 (%)

        Returns:
            검증된 종목 dict
            {
                stock_code: {
                    'name': str,
                    'stats': Dict,
                    'market': str
                },
                ...
            }
        """
        console.print()
        console.print(f"[cyan]📊 VWAP 백테스트 필터링 시작 (승률 >= {min_win_rate}%, 수익률 >= {min_avg_profit:+.1f}%)[/cyan]")

        validated_stocks = {}
        validated_count = 0

        for i, stock_info in enumerate(stock_list, 1):
            try:
                stock_code = stock_info.get('stock_code')
                stock_name = stock_info.get('stock_name')

                if not stock_code or not stock_name:
                    continue

                console.print(f"[dim]{i}/{len(stock_list)} {stock_name} ({stock_code}) 검증 중...[/dim]")

                # 🔧 FIX: validate_stock() → validate_trade() 호출 (문서 명세)
                # 키움 API에서 5분봉 데이터 조회
                try:
                    df_result = self.api.get_ohlcv_data(stock_code, period='m', timeframe=5, count=500)
                    if not df_result or df_result.get("return_code") != 0:
                        console.print(f"  ❌ {stock_name}: 데이터 조회 실패", style="red")
                        continue

                    import pandas as pd
                    df_data = df_result.get("data", [])
                    if not df_data or len(df_data) < 100:
                        console.print(f"  ❌ {stock_name}: 데이터 부족 ({len(df_data) if df_data else 0}개)", style="red")
                        continue

                    df = pd.DataFrame(df_data)
                    df.columns = df.columns.str.lower()
                    current_price = df['close'].iloc[-1] if 'close' in df.columns else 0

                    if current_price <= 0:
                        console.print(f"  ❌ {stock_name}: 비정상 가격", style="red")
                        continue

                    # 🔧 FIX: 30분봉 데이터 조회 (fallback validation용, 문서 명세)
                    df_30m = None
                    try:
                        df_result_30m = self.api.get_ohlcv_data(stock_code, period='m', timeframe=30, count=200)
                        if df_result_30m and df_result_30m.get("return_code") == 0:
                            df_data_30m = df_result_30m.get("data", [])
                            if df_data_30m and len(df_data_30m) >= 50:
                                df_30m = pd.DataFrame(df_data_30m)
                                df_30m.columns = df_30m.columns.str.lower()
                                console.print(f"  ✓ {stock_name}: 30분봉 {len(df_30m)}개 조회 성공", style="dim cyan")
                            else:
                                console.print(f"  ⚠️ {stock_name}: 30분봉 데이터 부족 (optional)", style="dim yellow")
                        else:
                            console.print(f"  ⚠️ {stock_name}: 30분봉 조회 실패 (optional)", style="dim yellow")
                    except Exception as e:
                        console.print(f"  ⚠️ {stock_name}: 30분봉 조회 예외 (optional) - {e}", style="dim yellow")

                    # PreTradeValidator를 통한 VWAP 백테스트 (30분봉 포함)
                    allowed, reason, stats = self.validator.validate_trade(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        historical_data=df,
                        current_price=current_price,
                        current_time=datetime.now(),
                        historical_data_30m=df_30m  # 🔧 FIX: 30분봉 데이터 전달 (문서 명세)
                    )

                    if not allowed:
                        console.print(f"  ❌ {stock_name}: {reason}", style="red")
                        continue

                except Exception as e:
                    console.print(f"  ❌ {stock_name}: 검증 오류 - {e}", style="red")
                    continue

                # 필터링 조건 체크
                if stats.get('win_rate', 0) < min_win_rate:
                    console.print(f"  ❌ {stock_name}: 승률 부족 ({stats.get('win_rate', 0):.1f}%)", style="red")
                    continue

                if stats.get('avg_profit_pct', 0) < min_avg_profit:
                    console.print(f"  ❌ {stock_name}: 수익률 부족 ({stats.get('avg_profit_pct', 0):+.1f}%)", style="red")
                    continue

                # 시장 정보 (KOSPI/KOSDAQ)
                market = 'KOSPI' if stock_code.startswith('0') else 'KOSDAQ'

                # 검증 통과
                validated_stocks[stock_code] = {
                    'name': stock_name,
                    'stats': stats,
                    'market': market,
                    'data': df  # 🔧 FIX: result.get('data') → df
                }

                validated_count += 1

                console.print(
                    f"  ✅ {stock_name}: 승률 {stats.get('win_rate', 0):.1f}%, "
                    f"수익 {stats.get('avg_profit_pct', 0):+.1f}%",
                    style="green"
                )

                # DB 저장 (optional)
                if self.db:
                    # 🔧 FIX: allowed 값을 포함한 dict 생성
                    validation_result = {'allowed': allowed, 'stats': stats}
                    self._save_validation_score(stock_code, stock_name, stats, validation_result)

            except KeyboardInterrupt:
                console.print()
                console.print("[yellow]⚠️  사용자가 중지했습니다.[/yellow]")
                break
            except Exception as e:
                console.print(f"[red]검증 오류 ({stock_code}): {e}[/red]", style="dim")
                continue

        console.print()
        console.print(f"[green]✅ 필터링 완료: {validated_count}/{len(stock_list)}개 통과[/green]")
        console.print()

        return validated_stocks

    def _save_validation_score(
        self,
        stock_code: str,
        stock_name: str,
        stats: Dict,
        result: Dict
    ) -> None:
        """
        검증 결과를 DB에 저장

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            stats: 백테스트 통계
            result: 검증 결과
        """
        if not self.db:
            return

        try:
            score_data = {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'validation_time': datetime.now().isoformat(),
                'vwap_win_rate': stats.get('win_rate'),
                'vwap_avg_profit': stats.get('avg_profit_pct'),
                'vwap_trade_count': stats.get('total_trades'),
                'vwap_profit_factor': stats.get('profit_factor'),
                'vwap_max_profit': stats.get('max_profit_pct'),
                'vwap_max_loss': stats.get('max_loss_pct'),
                'news_sentiment_score': None,
                'news_impact_type': None,
                'news_keywords': [],
                'news_titles': [],
                'news_count': 0,
                'total_score': stats.get('avg_profit_pct', 0),
                'weight_vwap': 1.0,
                'weight_news': 0.0,
                'is_passed': 1 if result.get('allowed') else 0
            }
            self.db.insert_validation_score(score_data)

        except Exception as e:
            console.print(f"[yellow]⚠️  DB 저장 실패: {e}[/yellow]")

    def display_filtered_stocks(self, validated_stocks: Dict[str, Dict]) -> None:
        """
        필터링된 종목 테이블 표시

        Args:
            validated_stocks: 검증된 종목 dict
        """
        if not validated_stocks:
            console.print("[yellow]⚠️  필터링 통과한 종목이 없습니다.[/yellow]")
            return

        table = Table(title="최종 선정 종목 (모니터링 대상)", box=box.DOUBLE)
        table.add_column("순위", style="cyan", justify="right", width=6)
        table.add_column("종목명", style="yellow", width=12)
        table.add_column("코드", style="dim", width=8)
        table.add_column("승률", justify="right", width=8)
        table.add_column("평균수익률", justify="right", style="green", width=12)
        table.add_column("거래수", justify="right", width=8)

        # 평균 수익률 순으로 정렬
        sorted_stocks = sorted(
            validated_stocks.items(),
            key=lambda x: x[1]['stats']['avg_profit_pct'],
            reverse=True
        )

        for rank, (code, info) in enumerate(sorted_stocks, 1):
            stats = info['stats']
            table.add_row(
                str(rank),
                info['name'],
                code,
                f"{stats['win_rate']:.1f}%",
                f"{stats['avg_profit_pct']:+.2f}%",
                f"{stats['total_trades']}회"
            )

        console.print(table)
        console.print()

    def load_candidates_from_db(self, limit: int = 100) -> Dict[str, Dict]:
        """
        DB에서 활성 감시 종목 로드

        Args:
            limit: 최대 로드 개수

        Returns:
            종목 정보 dict
            {
                stock_code: {
                    'name': str,
                    'market': str,
                    'stats': Dict,
                    'analysis': Dict
                },
                ...
            }
        """
        if not self.db:
            console.print("[yellow]⚠️  DB가 연결되지 않았습니다.[/yellow]")
            return {}

        try:
            candidates = self.db.get_active_candidates(limit=limit)

            if not candidates:
                console.print("  ⚠️  DB에 활성 감시 종목이 없습니다.", style="yellow")
                return {}

            console.print(f"  ✅ DB에서 {len(candidates)}개 활성 종목 로드", style="green")

            stocks_dict = {}

            for candidate in candidates:
                stock_code = candidate['stock_code']
                stock_name = candidate['stock_name']

                stocks_dict[stock_code] = {
                    'name': stock_name,
                    'market': candidate.get('market', 'KOSPI'),
                    'stats': {
                        'win_rate': candidate.get('vwap_win_rate', 0),
                        'avg_profit_pct': candidate.get('vwap_avg_profit', 0),
                        'total_trades': candidate.get('vwap_trade_count', 0),
                        'profit_factor': candidate.get('vwap_profit_factor', 0)
                    },
                    'analysis': {
                        'total_score': candidate.get('total_score', 0),
                        'recommendation': candidate.get('recommendation', '관망'),
                        'action': candidate.get('action', 'HOLD'),
                        'scores': {
                            'news': candidate.get('score_news', 50),
                            'technical': candidate.get('score_technical', 50),
                            'supply_demand': candidate.get('score_supply_demand', 50),
                            'fundamental': candidate.get('score_fundamental', 50),
                            'vwap': candidate.get('score_vwap', 0)
                        },
                        'news_sentiment': candidate.get('news_sentiment', 'neutral'),
                        'news_impact': candidate.get('news_impact', 0)
                    },
                    'ticker': f"{stock_code}.KS",
                    'db_id': candidate['id']
                }

            return stocks_dict

        except Exception as e:
            console.print(f"  ❌ DB 로드 실패: {e}", style="red")
            import traceback
            traceback.print_exc()
            return {}
