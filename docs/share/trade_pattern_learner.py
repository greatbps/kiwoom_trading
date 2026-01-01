"""
거래 패턴 학습 모듈
실제 거래 데이터를 기반으로 패턴을 학습하고 예측 모델을 개선합니다.
"""
import json
import pickle
from datetime import datetime
from pathlib import Path
import numpy as np


class TradePatternLearner:
    """거래 패턴 학습 클래스"""

    def __init__(self):
        self.patterns = {
            'time_win_rate': {},  # 시간대별 승률
            'hold_time_stats': {},  # 보유 시간 통계
            'position_size_stats': {},  # 포지션 사이즈 통계
        }
        self.model_path = Path('ai/models/trade_patterns.pkl')

    def load_trade_data(self, risk_log_path='data/risk_log.json'):
        """거래 데이터 로드"""
        with open(risk_log_path) as f:
            data = json.load(f)

        # 거래 쌍 매칭
        from collections import defaultdict
        trades_by_stock = defaultdict(list)

        for trade in data['weekly_trades']:
            trades_by_stock[trade['stock_code']].append(trade)

        # 매수-매도 쌍 생성
        trade_pairs = []
        for stock_code, trades in trades_by_stock.items():
            buys = [t for t in trades if t['type'] == 'BUY']
            sells = [t for t in trades if t['type'] == 'SELL']

            for buy in buys:
                for sell in sells:
                    if abs(buy['amount'] - sell['amount']) < buy['amount'] * 0.3:
                        from datetime import datetime
                        buy_time = datetime.fromisoformat(buy['timestamp'])
                        sell_time = datetime.fromisoformat(sell['timestamp'])
                        hold_minutes = (sell_time - buy_time).total_seconds() / 60

                        pnl_pct = ((sell['price'] - buy['price']) / buy['price']) * 100

                        trade_pairs.append({
                            'buy_hour': buy_time.hour,
                            'buy_minute': buy_time.minute,
                            'hold_minutes': hold_minutes,
                            'amount': buy['amount'],
                            'pnl': sell['realized_pnl'],
                            'pnl_pct': pnl_pct,
                            'win': sell['realized_pnl'] > 0
                        })
                        break

        return trade_pairs

    def learn_patterns(self, trade_pairs):
        """패턴 학습"""
        from collections import defaultdict

        # 1. 시간대별 승률
        time_stats = defaultdict(lambda: {'wins': 0, 'total': 0})
        for t in trade_pairs:
            hour = t['buy_hour']
            time_stats[hour]['total'] += 1
            if t['win']:
                time_stats[hour]['wins'] += 1

        self.patterns['time_win_rate'] = {
            hour: stats['wins'] / stats['total'] if stats['total'] > 0 else 0
            for hour, stats in time_stats.items()
        }

        # 2. 보유 시간 통계
        wins = [t for t in trade_pairs if t['win']]
        losses = [t for t in trade_pairs if not t['win']]

        if wins:
            win_holds = [t['hold_minutes'] for t in wins if t['hold_minutes'] > 0]
            if win_holds:
                self.patterns['hold_time_stats']['win_avg'] = np.mean(win_holds)
                self.patterns['hold_time_stats']['win_std'] = np.std(win_holds)

        if losses:
            loss_holds = [t['hold_minutes'] for t in losses if t['hold_minutes'] > 0]
            if loss_holds:
                self.patterns['hold_time_stats']['loss_avg'] = np.mean(loss_holds)
                self.patterns['hold_time_stats']['loss_std'] = np.std(loss_holds)

        # 3. 핵심 인사이트
        self.patterns['insights'] = {
            'best_hour': max(self.patterns['time_win_rate'].items(), key=lambda x: x[1])[0] if self.patterns['time_win_rate'] else None,
            'worst_hour': min(self.patterns['time_win_rate'].items(), key=lambda x: x[1])[0] if self.patterns['time_win_rate'] else None,
            'min_hold_recommended': self.patterns['hold_time_stats'].get('win_avg', 30) * 0.3,  # 승리 평균의 30%
        }

    def save_model(self):
        """모델 저장"""
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, 'wb') as f:
            pickle.dump(self.patterns, f)

        print(f"✅ 패턴 모델 저장: {self.model_path}")

    def load_model(self):
        """모델 로드"""
        if self.model_path.exists():
            with open(self.model_path, 'rb') as f:
                self.patterns = pickle.load(f)
            return True
        return False

    def predict_win_probability(self, hour, hold_minutes=None):
        """승리 확률 예측"""
        # 시간대 기반 기본 확률
        base_prob = self.patterns['time_win_rate'].get(hour, 0.5)

        # 보유 시간 보정 (옵션)
        if hold_minutes and 'hold_time_stats' in self.patterns:
            win_avg = self.patterns['hold_time_stats'].get('win_avg', 60)
            if hold_minutes >= win_avg * 0.5:
                base_prob *= 1.2  # 충분히 보유하면 확률 증가

        return min(1.0, base_prob)

    def get_recommendations(self):
        """추천 사항 반환"""
        if 'insights' not in self.patterns:
            return {}

        return {
            'best_trading_hour': self.patterns['insights'].get('best_hour'),
            'avoid_hours': [
                hour for hour, win_rate in self.patterns['time_win_rate'].items()
                if win_rate < 0.3
            ],
            'min_hold_minutes': self.patterns['insights'].get('min_hold_recommended', 30),
        }


def main():
    """메인 실행"""
    print('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    print('   거래 패턴 학습 시작')
    print('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    print()

    learner = TradePatternLearner()

    # 1. 거래 데이터 로드
    print('1. 거래 데이터 로드 중...')
    trade_pairs = learner.load_trade_data()
    print(f'   총 {len(trade_pairs)}건의 거래 쌍 로드')
    print()

    # 2. 패턴 학습
    print('2. 패턴 학습 중...')
    learner.learn_patterns(trade_pairs)
    print('   학습 완료')
    print()

    # 3. 결과 출력
    print('3. 학습 결과:')
    print(f'   시간대별 승률: {learner.patterns["time_win_rate"]}')
    print(f'   핵심 인사이트: {learner.patterns["insights"]}')
    print()

    # 4. 추천 사항
    print('4. 추천 사항:')
    recommendations = learner.get_recommendations()
    print(f'   최적 거래 시간: {recommendations.get("best_trading_hour")}시')
    print(f'   회피 시간대: {recommendations.get("avoid_hours")}')
    print(f'   최소 보유 시간: {recommendations.get("min_hold_minutes", 30):.0f}분')
    print()

    # 5. 모델 저장
    print('5. 모델 저장 중...')
    learner.save_model()
    print()

    print('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    print('   학습 완료!')
    print('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')


if __name__ == '__main__':
    main()
