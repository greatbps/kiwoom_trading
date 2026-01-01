#!/usr/bin/env python3
"""12-18 거래 데이터 가져오기"""

from api.kiwoom_rest_api import KiwoomRestAPI
import json
from datetime import datetime

api = KiwoomRestAPI()

# Get today's trades
data = {
    'stk_cd': '',
    'qry_tp': '0',
    'sell_tp': '0',
    'stex_tp': '0'
}

response = api.post('/domestic-stock/v1/trading/inquire-daily-ccld', data=data)

if response and 'output' in response:
    trades = response['output']
    today = datetime.now().strftime('%Y%m%d')
    today_trades = [t for t in trades if t.get('ord_dt', '') == today]

    print(f'📡 API에서 12-18 거래: {len(today_trades)}건')
    if today_trades:
        print(json.dumps(today_trades, indent=2, ensure_ascii=False))
else:
    print(f'❌ API 응답: {response}')
