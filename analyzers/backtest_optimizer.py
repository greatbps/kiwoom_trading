"""
백테스트 결과 최적화 엔진
- 백테스트 결과를 분석하여 가중치 조정 제안
- 파라미터 최적화 추천
- 조건검색 개선 사항 도출
"""
from typing import Dict, List, Any, Tuple
import pandas as pd
import numpy as np
from collections import defaultdict


class BacktestOptimizer:
    """백테스트 결과 기반 최적화 엔진"""

    def __init__(self):
        """초기화"""
        # 현재 가중치 (기본값)
        self.current_weights = {
            'news': 0.30,
            'technical': 0.40,
            'supply_demand': 0.15,
            'fundamental': 0.15
        }

        # 파라미터 기본값
        self.current_params = {
            'min_total_score': 65,
            'min_vwap_win_rate': 0.50,
            'min_vwap_trades': 2,
            'holding_period_days': 3,
            'take_profit_pct': 0.10,
            'stop_loss_pct': -0.05
        }

    def analyze_score_correlation(self, candidates: pd.DataFrame) -> Dict[str, Any]:
        """
        점수별 성과 상관관계 분석

        Args:
            candidates: 백테스트 결과 DataFrame
                필요 컬럼: news_score, technical_score, supply_score, fundamental_score,
                          total_score, actual_return (실제 수익률)

        Returns:
            점수별 상관계수 및 분석 결과
        """
        if 'actual_return' not in candidates.columns:
            return {
                'correlations': {},
                'recommendations': ['실제 수익률 데이터 없음 - 상관관계 분석 불가']
            }

        correlations = {}
        recommendations = []

        # 각 점수와 실제 수익률의 상관관계 계산
        score_columns = ['news_score', 'technical_score', 'supply_score', 'fundamental_score', 'total_score']

        for col in score_columns:
            if col in candidates.columns:
                # NaN 제거 후 상관계수 계산
                valid_data = candidates[[col, 'actual_return']].dropna()
                if len(valid_data) >= 5:  # 최소 5개 데이터 필요
                    corr = valid_data[col].corr(valid_data['actual_return'])
                    correlations[col] = corr
                else:
                    correlations[col] = 0.0

        # 상관관계 기반 가중치 조정 제안
        if correlations:
            # 정규화를 위해 절댓값 사용
            abs_corrs = {k: abs(v) for k, v in correlations.items() if k != 'total_score'}
            total_abs = sum(abs_corrs.values())

            if total_abs > 0:
                # 새로운 가중치 제안 (상관계수 비율로 재분배)
                suggested_weights = {}
                for score_type, abs_corr in abs_corrs.items():
                    weight_name = score_type.replace('_score', '')
                    suggested_weights[weight_name] = abs_corr / total_abs

                # 가중치 변화 분석
                for key, new_weight in suggested_weights.items():
                    old_weight = self.current_weights.get(key, 0.25)
                    diff = new_weight - old_weight

                    if abs(diff) > 0.05:  # 5% 이상 차이
                        if diff > 0:
                            recommendations.append(
                                f"💡 '{key}' 가중치 증가 권장: {old_weight:.2f} → {new_weight:.2f} "
                                f"(+{diff:.2f}) - 수익률과 높은 상관관계 (r={correlations.get(key+'_score', 0):.2f})"
                            )
                        else:
                            recommendations.append(
                                f"⚠️  '{key}' 가중치 감소 권장: {old_weight:.2f} → {new_weight:.2f} "
                                f"({diff:.2f}) - 수익률과 낮은 상관관계 (r={correlations.get(key+'_score', 0):.2f})"
                            )

                return {
                    'correlations': correlations,
                    'current_weights': self.current_weights.copy(),
                    'suggested_weights': suggested_weights,
                    'recommendations': recommendations
                }

        return {
            'correlations': correlations,
            'recommendations': ['데이터 부족 - 가중치 조정 제안 불가']
        }

    def analyze_score_range_performance(self, candidates: pd.DataFrame) -> Dict[str, Any]:
        """
        점수 구간별 성과 분석

        Args:
            candidates: 백테스트 결과 DataFrame

        Returns:
            점수 구간별 평균 수익률 및 승률
        """
        if 'actual_return' not in candidates.columns or 'total_score' not in candidates.columns:
            return {
                'ranges': [],
                'recommendations': ['점수 및 수익률 데이터 없음']
            }

        # 점수 구간 정의
        score_ranges = [
            (0, 50, "매우 낮음"),
            (50, 60, "낮음"),
            (60, 70, "보통"),
            (70, 80, "높음"),
            (80, 100, "매우 높음")
        ]

        range_analysis = []
        recommendations = []

        for min_score, max_score, label in score_ranges:
            range_data = candidates[
                (candidates['total_score'] >= min_score) &
                (candidates['total_score'] < max_score)
            ]

            if len(range_data) > 0:
                avg_return = range_data['actual_return'].mean()
                win_rate = (range_data['actual_return'] > 0).sum() / len(range_data)
                count = len(range_data)

                range_analysis.append({
                    'range': f"{min_score}-{max_score}",
                    'label': label,
                    'count': count,
                    'avg_return': avg_return,
                    'win_rate': win_rate
                })

        # 최적 구간 찾기
        if range_analysis:
            best_range = max(range_analysis, key=lambda x: x['avg_return'])

            recommendations.append(
                f"🎯 최고 성과 구간: {best_range['range']}점 ({best_range['label']}) - "
                f"평균 수익률 {best_range['avg_return']:.2%}, 승률 {best_range['win_rate']:.1%}"
            )

            # 현재 필터 점수와 비교
            best_min_score = int(best_range['range'].split('-')[0])
            if best_min_score > self.current_params['min_total_score']:
                recommendations.append(
                    f"💡 최소 점수 기준 상향 권장: {self.current_params['min_total_score']}점 → {best_min_score}점 "
                    f"(품질 향상 기대)"
                )
            elif best_min_score < self.current_params['min_total_score']:
                recommendations.append(
                    f"📊 최소 점수 기준 하향 가능: {self.current_params['min_total_score']}점 → {best_min_score}점 "
                    f"(종목 수 증가)"
                )

        return {
            'ranges': range_analysis,
            'recommendations': recommendations
        }

    def analyze_holding_period(self, candidates: pd.DataFrame) -> Dict[str, Any]:
        """
        보유 기간별 성과 분석

        Args:
            candidates: 백테스트 결과 (일별 수익률 포함)

        Returns:
            최적 보유 기간 제안
        """
        recommendations = []

        # 실제 구현 시 일별 수익률 데이터 필요
        # 현재는 간단한 통계 기반 제안
        if 'actual_return' in candidates.columns:
            positive_returns = candidates[candidates['actual_return'] > 0]
            negative_returns = candidates[candidates['actual_return'] <= 0]

            if len(positive_returns) > 0:
                avg_positive = positive_returns['actual_return'].mean()
                recommendations.append(
                    f"📈 수익 종목 평균 수익률: {avg_positive:.2%}"
                )

            if len(negative_returns) > 0:
                avg_negative = negative_returns['actual_return'].mean()
                recommendations.append(
                    f"📉 손실 종목 평균 손실률: {avg_negative:.2%}"
                )

            # 익절/손절 기준 제안
            if len(positive_returns) > 0:
                percentile_80 = positive_returns['actual_return'].quantile(0.8)
                if percentile_80 > self.current_params['take_profit_pct']:
                    recommendations.append(
                        f"💡 익절 기준 상향 권장: {self.current_params['take_profit_pct']:.1%} → {percentile_80:.1%} "
                        f"(상위 20% 수익 활용)"
                    )

            if len(negative_returns) > 0:
                percentile_20 = negative_returns['actual_return'].quantile(0.2)
                if abs(percentile_20) < abs(self.current_params['stop_loss_pct']):
                    recommendations.append(
                        f"⚠️  손절 기준 타이트하게: {self.current_params['stop_loss_pct']:.1%} → {percentile_20:.1%} "
                        f"(손실 최소화)"
                    )

        return {
            'current_holding_days': self.current_params['holding_period_days'],
            'current_take_profit': self.current_params['take_profit_pct'],
            'current_stop_loss': self.current_params['stop_loss_pct'],
            'recommendations': recommendations
        }

    def analyze_vwap_effectiveness(self, candidates: pd.DataFrame) -> Dict[str, Any]:
        """
        VWAP 필터 효과성 분석

        Args:
            candidates: VWAP 통과/미통과 종목 포함

        Returns:
            VWAP 필터 개선 제안
        """
        recommendations = []

        # VWAP 통과 여부별 성과 비교
        if 'vwap_passed' in candidates.columns and 'actual_return' in candidates.columns:
            vwap_passed = candidates[candidates['vwap_passed'] == True]
            vwap_failed = candidates[candidates['vwap_passed'] == False]

            if len(vwap_passed) > 0 and len(vwap_failed) > 0:
                passed_return = vwap_passed['actual_return'].mean()
                failed_return = vwap_failed['actual_return'].mean()

                diff = passed_return - failed_return

                if diff > 0.02:  # 2% 이상 차이
                    recommendations.append(
                        f"✅ VWAP 필터 효과적: 통과 종목 평균 수익률 {passed_return:.2%} vs "
                        f"미통과 {failed_return:.2%} (차이: +{diff:.2%})"
                    )
                elif diff < -0.02:
                    recommendations.append(
                        f"⚠️  VWAP 필터 재검토 필요: 통과 종목 오히려 낮은 수익률 "
                        f"({passed_return:.2%} vs {failed_return:.2%})"
                    )
                else:
                    recommendations.append(
                        f"➡️  VWAP 필터 중립적: 수익률 차이 미미 ({diff:.2%})"
                    )

        # VWAP 파라미터 분석
        if 'vwap_win_rate' in candidates.columns:
            win_rate_50_plus = candidates[candidates['vwap_win_rate'] >= 0.5]
            win_rate_70_plus = candidates[candidates['vwap_win_rate'] >= 0.7]

            if len(win_rate_70_plus) > 0:
                avg_return_70 = win_rate_70_plus['actual_return'].mean() if 'actual_return' in candidates.columns else 0
                recommendations.append(
                    f"💡 VWAP 승률 70% 이상 종목 ({len(win_rate_70_plus)}개) - "
                    f"평균 수익률: {avg_return_70:.2%}"
                )

                if avg_return_70 > 0.05:  # 5% 이상
                    recommendations.append(
                        f"🎯 VWAP 승률 기준 상향 권장: 50% → 70% (고품질 필터링)"
                    )

        return {
            'current_min_win_rate': self.current_params['min_vwap_win_rate'],
            'current_min_trades': self.current_params['min_vwap_trades'],
            'recommendations': recommendations
        }

    def generate_optimization_report(self, candidates: pd.DataFrame) -> Dict[str, Any]:
        """
        종합 최적화 리포트 생성

        Args:
            candidates: 백테스트 결과 DataFrame

        Returns:
            종합 분석 결과 및 추천 사항
        """
        report = {
            'score_correlation': self.analyze_score_correlation(candidates),
            'score_range_performance': self.analyze_score_range_performance(candidates),
            'holding_period': self.analyze_holding_period(candidates),
            'vwap_effectiveness': self.analyze_vwap_effectiveness(candidates)
        }

        # 전체 추천 사항 통합
        all_recommendations = []
        for section, data in report.items():
            if 'recommendations' in data:
                all_recommendations.extend(data['recommendations'])

        report['summary'] = {
            'total_stocks': len(candidates),
            'avg_return': candidates['actual_return'].mean() if 'actual_return' in candidates.columns else 0,
            'win_rate': (candidates['actual_return'] > 0).sum() / len(candidates) if 'actual_return' in candidates.columns and len(candidates) > 0 else 0,
            'all_recommendations': all_recommendations
        }

        return report

    def apply_suggested_weights(self, suggested_weights: Dict[str, float]) -> str:
        """
        제안된 가중치를 설정 파일에 적용

        Args:
            suggested_weights: 새로운 가중치 딕셔너리

        Returns:
            적용 결과 메시지
        """
        try:
            from utils.config_manager import ConfigManager

            config_manager = ConfigManager()

            # 백업 생성
            backup_path = config_manager.backup_config()
            if backup_path:
                print(f"✓ 백업 생성: {backup_path}")

            # 가중치 업데이트
            if config_manager.update_weights(suggested_weights):
                self.current_weights = suggested_weights.copy()
                return f"✅ 가중치 업데이트 완료: {suggested_weights}\n   설정 파일: {config_manager.config_path}"
            else:
                return "❌ 가중치 업데이트 실패"

        except ImportError:
            # ConfigManager 없으면 메모리에만 저장
            self.current_weights = suggested_weights.copy()
            return f"⚠️  ConfigManager 없음 - 메모리에만 저장됨: {suggested_weights}"
        except Exception as e:
            return f"❌ 가중치 적용 오류: {e}"

    def export_recommendations(self, report: Dict[str, Any], filepath: str = None) -> str:
        """
        추천 사항을 파일로 내보내기

        Args:
            report: 최적화 리포트
            filepath: 저장 경로 (None이면 콘솔 출력)

        Returns:
            내보내기 결과
        """
        output = []
        output.append("=" * 80)
        output.append("📊 백테스트 최적화 리포트")
        output.append("=" * 80)
        output.append("")

        # 요약
        summary = report.get('summary', {})
        output.append("📈 성과 요약")
        output.append(f"  • 분석 종목 수: {summary.get('total_stocks', 0)}개")
        output.append(f"  • 평균 수익률: {summary.get('avg_return', 0):.2%}")
        output.append(f"  • 승률: {summary.get('win_rate', 0):.1%}")
        output.append("")

        # 가중치 분석
        if 'score_correlation' in report:
            corr_data = report['score_correlation']
            output.append("🔍 점수-수익률 상관관계")
            if 'correlations' in corr_data:
                for score_type, corr in corr_data['correlations'].items():
                    output.append(f"  • {score_type}: {corr:.3f}")
            output.append("")

            if 'suggested_weights' in corr_data:
                output.append("💡 가중치 조정 제안")
                for key, weight in corr_data['suggested_weights'].items():
                    old = corr_data['current_weights'].get(key, 0)
                    output.append(f"  • {key}: {old:.2f} → {weight:.2f}")
                output.append("")

        # 전체 추천 사항
        output.append("🎯 종합 추천 사항")
        for i, rec in enumerate(summary.get('all_recommendations', []), 1):
            output.append(f"  {i}. {rec}")

        output.append("")
        output.append("=" * 80)

        result = "\n".join(output)

        if filepath:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(result)
            return f"✅ 리포트 저장 완료: {filepath}"
        else:
            return result
