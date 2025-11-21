"""
ML 학습용 데이터 추출 스크립트

DB에서 entry_context, exit_context를 추출하여 CSV/Pandas DataFrame으로 변환
"""
import sys
from pathlib import Path
import pandas as pd
import json

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.trading_db import TradingDatabase


def export_ml_training_data(db_path: str = "data/trading.db", output_path: str = "data/ml_training_data.csv"):
    """
    ML 학습용 데이터를 CSV로 추출

    Args:
        db_path: DB 파일 경로
        output_path: 출력 CSV 파일 경로

    Returns:
        pandas.DataFrame
    """
    print("=" * 80)
    print("ML 학습용 데이터 추출")
    print("=" * 80)

    db = TradingDatabase(db_path=db_path)

    # 1. 거래 데이터 조회 (context 포함)
    print("\n1. 거래 데이터 조회 중...")
    trades = db.get_trades_with_context(parse_context=True)

    if not trades:
        print("❌ 거래 데이터가 없습니다.")
        return None

    print(f"✓ 총 {len(trades)}건의 거래 조회 완료")

    # 2. 매수/매도 데이터 분리 및 병합
    print("\n2. 매수/매도 데이터 병합 중...")

    # 매수 데이터만 필터링 (entry_context 있는 것만)
    buy_trades = [t for t in trades if t['trade_type'] == 'BUY' and t.get('entry_context')]
    print(f"   - 매수 데이터: {len(buy_trades)}건")

    # 매도 데이터만 필터링 (exit_context 있는 것만)
    sell_trades = [t for t in trades if t['trade_type'] == 'SELL' and t.get('exit_context')]
    print(f"   - 매도 데이터: {len(sell_trades)}건")

    # 3. DataFrame으로 변환
    print("\n3. DataFrame 변환 중...")

    rows = []
    for buy in buy_trades:
        ec = buy.get('entry_context', {})
        fs = buy.get('filter_scores', {})

        # 매도 데이터 찾기 (같은 종목, 비슷한 시간)
        corresponding_sell = None
        for sell in sell_trades:
            if sell['stock_code'] == buy['stock_code']:
                # 간단히 시간 순서로 매칭 (개선 필요)
                corresponding_sell = sell
                break

        xc = corresponding_sell.get('exit_context', {}) if corresponding_sell else {}

        row = {
            # 메타 정보
            'trade_id': buy['trade_id'],
            'stock_code': buy['stock_code'],
            'stock_name': buy['stock_name'],
            'trade_time': buy['trade_time'],

            # === Features (진입 시점 지표) ===
            'entry_price': ec.get('price'),
            'vwap': ec.get('vwap'),
            'vwap_diff_pct': ec.get('vwap_diff_pct'),
            'ma5': ec.get('ma5'),
            'ma20': ec.get('ma20'),
            'ma60': ec.get('ma60'),
            'rsi14': ec.get('rsi14'),
            'williams_r': ec.get('williams_r'),
            'macd': ec.get('macd'),
            'macd_signal': ec.get('macd_signal'),
            'stoch_k': ec.get('stoch_k'),
            'stoch_d': ec.get('stoch_d'),
            'volume': ec.get('volume'),
            'volume_ma20': ec.get('volume_ma20'),
            'volume_ratio': ec.get('volume_ratio'),
            'atr': ec.get('atr'),
            'atr_pct': ec.get('atr_pct'),

            # 캔들 정보
            'candle_open': ec.get('candle', {}).get('open'),
            'candle_high': ec.get('candle', {}).get('high'),
            'candle_low': ec.get('candle', {}).get('low'),
            'candle_close': ec.get('candle', {}).get('close'),

            # 필터 정보
            'filter_vwap_breakout': fs.get('vwap_breakout'),
            'filter_trend': fs.get('trend_filter'),
            'filter_volume': fs.get('volume_filter'),
            'filter_williams_r': fs.get('williams_r_filter'),

            # === Labels (청산 결과) ===
            'profit_pct': corresponding_sell.get('profit_rate') if corresponding_sell else None,
            'realized_profit': corresponding_sell.get('realized_profit') if corresponding_sell else None,
            'holding_duration_min': corresponding_sell.get('holding_duration', 0) // 60 if corresponding_sell else None,
            'exit_reason': corresponding_sell.get('exit_reason') if corresponding_sell else None,

            # 청산 시점 정보
            'highest_price': xc.get('highest_price'),
            'highest_profit_pct': xc.get('highest_profit_pct'),
            'profit_preservation_pct': xc.get('profit_preservation_pct'),
            'trailing_activated': xc.get('trailing_activated'),
            'partial_exit_stage': xc.get('partial_exit_stage'),

            # 청산 시점 지표
            'exit_rsi14': xc.get('rsi14'),
            'exit_williams_r': xc.get('williams_r'),
            'exit_volume_ratio': xc.get('volume_ratio'),

            # Binary Labels
            'is_profit': 1 if (corresponding_sell and corresponding_sell.get('profit_rate', 0) > 0) else 0,
            'is_big_profit': 1 if (corresponding_sell and corresponding_sell.get('profit_rate', 0) > 2.0) else 0,
            'is_loss': 1 if (corresponding_sell and corresponding_sell.get('profit_rate', 0) < 0) else 0,
        }

        rows.append(row)

    df = pd.DataFrame(rows)
    print(f"✓ DataFrame 생성 완료: {len(df)}행 x {len(df.columns)}열")

    # 4. 데이터 통계
    print("\n4. 데이터 통계")
    print(f"   - 총 거래: {len(df)}건")
    print(f"   - 수익 거래: {df['is_profit'].sum()}건 ({df['is_profit'].mean() * 100:.1f}%)")
    print(f"   - 손실 거래: {df['is_loss'].sum()}건 ({df['is_loss'].mean() * 100:.1f}%)")
    print(f"   - 평균 수익률: {df['profit_pct'].mean():.2f}%")
    print(f"   - 평균 보유시간: {df['holding_duration_min'].mean():.1f}분")

    # 5. 결측치 확인
    print("\n5. 결측치 확인")
    missing = df.isnull().sum()
    missing = missing[missing > 0].sort_values(ascending=False)
    if len(missing) > 0:
        print("   [주요 결측치]")
        for col, count in missing.head(10).items():
            print(f"     - {col}: {count}건 ({count / len(df) * 100:.1f}%)")
    else:
        print("   ✓ 결측치 없음")

    # 6. CSV 저장
    print(f"\n6. CSV 파일 저장: {output_path}")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"✓ 저장 완료")

    print("\n" + "=" * 80)
    print("✅ ML 학습용 데이터 추출 완료!")
    print("=" * 80)
    print(f"\n📁 출력 파일: {output_path}")
    print(f"📊 데이터 shape: {df.shape}")
    print("\n🤖 다음 단계:")
    print("  1. pandas로 데이터 분석 (EDA)")
    print("  2. Scikit-learn으로 ML 모델 학습")
    print("  3. 필터 파라미터 최적화 (GridSearchCV)")
    print("  4. Feature Importance 분석")

    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='ML 학습용 데이터 추출')
    parser.add_argument('--db', default='data/trading.db', help='DB 파일 경로')
    parser.add_argument('--output', default='data/ml_training_data.csv', help='출력 CSV 경로')
    args = parser.parse_args()

    df = export_ml_training_data(args.db, args.output)

    if df is not None:
        print("\n[데이터 미리보기]")
        print(df[['stock_name', 'entry_price', 'profit_pct', 'rsi14', 'williams_r', 'volume_ratio', 'exit_reason']].head(10))
