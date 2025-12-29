#!/usr/bin/env python3
"""
거래 내역 검증 및 동기화 시스템

시스템 기록과 실제 체결 내역을 비교하여 누락된 거래를 자동으로 동기화
"""

import json
from datetime import datetime
from typing import Dict, List, Tuple
from pathlib import Path
from rich.console import Console

console = Console()


class TradeReconciliation:
    """거래 내역 검증 및 동기화"""

    def __init__(self, api, risk_manager, db=None):
        """
        Args:
            api: KiwoomAPI instance
            risk_manager: RiskManager instance
            db: Database instance (optional)
        """
        self.api = api
        self.risk_manager = risk_manager
        self.db = db
        self.last_sync_time = None
        self.sync_interval = 300  # 5분마다 검증 (초 단위)

    def should_sync(self) -> bool:
        """동기화가 필요한 시점인지 확인"""
        if self.last_sync_time is None:
            return True

        elapsed = (datetime.now() - self.last_sync_time).total_seconds()
        return elapsed >= self.sync_interval

    async def reconcile_trades(self, today: str) -> Dict:
        """
        시스템 기록과 실제 체결 내역 비교 및 동기화

        Args:
            today: 오늘 날짜 (YYYYMMDD)

        Returns:
            {
                'synced': bool,  # 동기화 실행 여부
                'missing_trades': int,  # 누락된 거래 수
                'synced_trades': List[Dict],  # 동기화된 거래
                'errors': List[str]  # 오류 목록
            }
        """
        result = {
            'synced': False,
            'missing_trades': 0,
            'synced_trades': [],
            'errors': []
        }

        try:
            # 1. 실제 체결 내역 조회 (kt00007)
            actual_trades = await self._fetch_actual_trades(today)

            if actual_trades is None:
                result['errors'].append("실제 체결 내역 조회 실패")
                return result

            # 2. 시스템 기록 로드
            system_trades = self._load_system_trades()

            # 3. 비교 및 누락 거래 식별
            missing = self._find_missing_trades(actual_trades, system_trades)

            if not missing:
                console.print("[dim]✅ 거래 내역 일치 - 동기화 불필요[/dim]")
                result['synced'] = True
                return result

            # 4. 누락된 거래 동기화
            console.print()
            console.print("[bold yellow]⚠️  거래 내역 불일치 감지![/bold yellow]")
            console.print(f"[yellow]누락된 거래: {len(missing)}건[/yellow]")
            console.print()

            synced_count = 0
            for trade in missing:
                try:
                    self._sync_trade(trade)
                    result['synced_trades'].append(trade)
                    synced_count += 1

                    console.print(
                        f"[green]✅ 동기화: {trade['stock_name']} "
                        f"{trade['type']} {trade['quantity']}주 @ {trade['price']:,}원[/green]"
                    )

                except Exception as e:
                    error_msg = f"동기화 실패 ({trade['stock_code']}): {str(e)}"
                    result['errors'].append(error_msg)
                    console.print(f"[red]❌ {error_msg}[/red]")

            result['synced'] = True
            result['missing_trades'] = len(missing)

            console.print()
            console.print(f"[bold green]✅ {synced_count}건 동기화 완료[/bold green]")
            console.print()

            # 5. 동기화 후 검증
            self._verify_sync()

            self.last_sync_time = datetime.now()

        except Exception as e:
            error_msg = f"동기화 오류: {str(e)}"
            result['errors'].append(error_msg)
            console.print(f"[red]❌ {error_msg}[/red]")
            import traceback
            traceback.print_exc()

        return result

    async def _fetch_actual_trades(self, today: str) -> List[Dict]:
        """kt00007 API로 실제 체결 내역 조회"""
        try:
            access_token = self.api.get_access_token()

            url = f"{self.api.BASE_URL}/api/dostk/acnt"
            headers = {
                'Content-Type': 'application/json',
                'api-id': 'kt00007',
                'authorization': f'Bearer {access_token}'
            }

            body = {
                'ord_dt': today,
                'qry_tp': '4',  # 체결내역만
                'stk_bond_tp': '1',  # 주식
                'sell_tp': '0',  # 전체
                'dmst_stex_tp': '%'
            }

            response = self.api.session.post(url, headers=headers, json=body)
            result = response.json()

            if result.get('return_code') != 0:
                console.print(f"[red]❌ kt00007 조회 실패: {result.get('return_msg')}[/red]")
                return None

            trades = result.get('acnt_ord_cntr_prps_dtl', [])

            # 파싱
            parsed_trades = []
            for trade in trades:
                io_tp_nm = trade.get('io_tp_nm', '')
                cntr_qty = int(trade.get('cntr_qty', '0'))
                cntr_uv = int(trade.get('cntr_uv', '0'))
                ord_tm = trade.get('ord_tm', '')

                # 체결수량이 0이면 스킵 (미체결)
                if cntr_qty == 0:
                    continue

                trade_type = 'SELL' if '매도' in io_tp_nm else 'BUY'

                parsed_trades.append({
                    'timestamp': f"{today[:4]}-{today[4:6]}-{today[6:8]}T{ord_tm}",
                    'stock_code': trade.get('stk_cd', '').replace('A', ''),
                    'stock_name': trade.get('stk_nm', ''),
                    'type': trade_type,
                    'quantity': cntr_qty,
                    'price': float(cntr_uv),
                    'amount': float(cntr_qty * cntr_uv),
                    'order_no': trade.get('ord_no', ''),
                    'realized_pnl': 0.0  # 나중에 계산
                })

            return parsed_trades

        except Exception as e:
            console.print(f"[red]❌ 실제 체결 내역 조회 오류: {e}[/red]")
            import traceback
            traceback.print_exc()
            return None

    def _load_system_trades(self) -> List[Dict]:
        """시스템 기록된 거래 로드"""
        try:
            risk_log_path = Path("data/risk_log.json")
            if not risk_log_path.exists():
                return []

            with open(risk_log_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            return data.get('daily_trades', [])

        except Exception as e:
            console.print(f"[red]❌ 시스템 기록 로드 오류: {e}[/red]")
            return []

    def _find_missing_trades(self, actual: List[Dict], system: List[Dict]) -> List[Dict]:
        """누락된 거래 찾기"""

        # 시스템 기록을 키로 변환 (빠른 조회)
        system_keys = set()
        for trade in system:
            key = (
                trade['stock_code'],
                trade['type'],
                trade['quantity'],
                int(trade['price']),
                trade['timestamp'][:16]  # 분 단위까지만 비교
            )
            system_keys.add(key)

        # 실제 거래 중 시스템에 없는 것 찾기
        missing = []
        for trade in actual:
            key = (
                trade['stock_code'],
                trade['type'],
                trade['quantity'],
                int(trade['price']),
                trade['timestamp'][:16]
            )

            if key not in system_keys:
                missing.append(trade)

        return missing

    def _sync_trade(self, trade: Dict):
        """누락된 거래를 시스템에 동기화"""

        # RiskManager에 기록
        self.risk_manager.record_trade(
            stock_code=trade['stock_code'],
            stock_name=trade['stock_name'],
            trade_type=trade['type'],
            quantity=trade['quantity'],
            price=trade['price'],
            timestamp=trade['timestamp']
        )

        # DB에 기록 (있으면)
        if self.db:
            try:
                self.db.insert_trade({
                    'timestamp': trade['timestamp'],
                    'stock_code': trade['stock_code'],
                    'stock_name': trade['stock_name'],
                    'type': trade['type'],
                    'quantity': trade['quantity'],
                    'price': trade['price'],
                    'amount': trade['amount'],
                    'realized_pnl': trade['realized_pnl'],
                    'source': 'AUTO_SYNC'  # 자동 동기화 표시
                })
            except Exception as e:
                console.print(f"[yellow]⚠️  DB 기록 실패: {e}[/yellow]")

    def _verify_sync(self):
        """동기화 후 검증"""
        try:
            # risk_log.json 다시 로드하여 확인
            with open("data/risk_log.json", 'r', encoding='utf-8') as f:
                data = json.load(f)

            daily_trades = data.get('daily_trades', [])
            daily_pnl = data.get('daily_realized_pnl', 0.0)

            console.print(f"[dim]동기화 후 총 거래: {len(daily_trades)}건[/dim]")
            console.print(f"[dim]동기화 후 실현손익: {daily_pnl:+,.0f}원[/dim]")

        except Exception as e:
            console.print(f"[yellow]⚠️  검증 실패: {e}[/yellow]")

    def create_alert(self, missing_count: int, trades: List[Dict]):
        """불일치 감지 시 알림 생성"""

        alert_file = Path("data/trade_alerts.log")

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        message = f"[{timestamp}] 거래 내역 불일치: {missing_count}건 누락\n"

        for trade in trades:
            message += f"  - {trade['stock_name']} {trade['type']} {trade['quantity']}주 @ {trade['price']:,}원\n"

        message += "\n"

        try:
            with open(alert_file, 'a', encoding='utf-8') as f:
                f.write(message)

            console.print(f"[yellow]⚠️  알림 기록: {alert_file}[/yellow]")

        except Exception as e:
            console.print(f"[red]❌ 알림 기록 실패: {e}[/red]")
