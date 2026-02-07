"""
Re-entry Cooldown Trigger Report
- 쿨다운 차단 이벤트 수집 및 운영 통계 리포트 생성
- 2026-02-07
"""
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict
from collections import Counter


# exit_reason 원본 문자열 → 표준 카테고리 매핑
_EXIT_REASON_KEYWORDS = [
    ('early_failure', ['early failure', 'early_failure', 'ef ']),
    ('stop_loss',     ['hard stop', 'hard_stop', '손절', '구조 손절', 'structure_stop', 'stop_loss']),
    ('trailing_stop', ['트레일링', 'trailing', 'atr 트레일링']),
    ('time_exit',     ['시간 기반', 'time_exit', '시간기반', '강제청산', '생명주기']),
    ('take_profit',   ['부분익절', 'take_profit', 'squeeze']),
    ('partial_exit',  ['부분청산', 'partial']),
]


def categorize_exit_reason(raw_reason: str) -> str:
    """원본 exit_reason 문자열을 표준 카테고리로 변환"""
    if not raw_reason:
        return 'default'
    lower = raw_reason.lower()
    for category, keywords in _EXIT_REASON_KEYWORDS:
        for kw in keywords:
            if kw in lower:
                return category
    return 'default'


@dataclass
class ReentryBlockedEvent:
    timestamp: datetime
    symbol: str
    symbol_name: str
    direction: str            # 'long' / 'short'
    elapsed_min: float        # 마지막 청산 후 경과 시간
    cooldown_min: int         # 적용된 쿨다운 시간
    is_loss_cooldown: bool    # 손절 쿨다운 여부
    exit_reason: str          # 직전 청산 사유 (EF, Hard Stop 등)


class ReentryMetrics:
    """Re-entry Cooldown 운영 통계 수집기"""

    def __init__(self):
        self.events: List[ReentryBlockedEvent] = []
        self.total_entry_signals: int = 0
        self.session_date: str = datetime.now().strftime('%Y-%m-%d')

    def record_entry_signal(self):
        """진입 시도 카운트 (쿨다운 체크 직전)"""
        self.total_entry_signals += 1

    def record_blocked(self, event: ReentryBlockedEvent):
        """쿨다운 차단 이벤트 기록"""
        self.events.append(event)

    def generate_report(self) -> Dict:
        """6개 핵심 지표 리포트 생성"""
        blocked = len(self.events)
        total = self.total_entry_signals

        # ① Re-entry Block Count
        block_count = blocked

        # ② Re-entry Block Ratio
        block_ratio = (blocked / total * 100) if total > 0 else 0.0

        # ③ Cooldown Avg Elapsed (차단 시점 평균 경과시간)
        avg_elapsed = 0.0
        if blocked > 0:
            avg_elapsed = sum(e.elapsed_min for e in self.events) / blocked

        # ④ Loss Cooldown Ratio (손절 쿨다운 비율)
        loss_cooldown_count = sum(1 for e in self.events if e.is_loss_cooldown)
        loss_cooldown_ratio = (loss_cooldown_count / blocked * 100) if blocked > 0 else 0.0

        # ⑤ Top Blocked Symbols (종목별 차단 빈도)
        symbol_counter = Counter(
            (e.symbol, e.symbol_name) for e in self.events
        )
        top_blocked = [
            (name, count) for (_, name), count in symbol_counter.most_common(5)
        ]

        # ⑥ EF-triggered Cooldown Count
        ef_count = sum(
            1 for e in self.events
            if 'EARLY_FAILURE' in e.exit_reason.upper()
        )

        # ⑦ Exit Reason별 차단 분포
        reason_counter = Counter(
            categorize_exit_reason(e.exit_reason) for e in self.events
        )
        blocked_by_reason = dict(reason_counter.most_common())

        # 상태 판단
        ratio = blocked / total if total > 0 else 0
        if ratio <= 0.25:
            status = "건강"
            status_icon = "V"
        elif ratio <= 0.35:
            status = "주의 (진입 과다 가능성)"
            status_icon = "!"
        else:
            status = "경고 (SMC 신호 과잉 or EF 민감도 과도)"
            status_icon = "X"

        return {
            'date': self.session_date,
            'total_entry_signals': total,
            'reentry_blocked_count': block_count,
            'blocked_ratio_pct': round(block_ratio, 1),
            'avg_elapsed_min': round(avg_elapsed, 1),
            'loss_cooldown_count': loss_cooldown_count,
            'loss_cooldown_ratio_pct': round(loss_cooldown_ratio, 1),
            'ef_triggered_count': ef_count,
            'top_blocked_symbols': top_blocked,
            'blocked_by_reason': blocked_by_reason,
            'status': status,
            'status_icon': status_icon,
        }

    def print_report(self):
        """콘솔 출력"""
        report = self.generate_report()

        print()
        print(f"[REENTRY COOLDOWN REPORT - {report['date']}]")
        print("=" * 50)
        print(f"  Total Entry Signals     : {report['total_entry_signals']}")
        print(f"  Re-entry Blocked        : {report['reentry_blocked_count']}")
        print(f"  Blocked Ratio           : {report['blocked_ratio_pct']}%  [{report['status_icon']}] {report['status']}")
        print(f"  Avg Elapsed (Blocked)   : {report['avg_elapsed_min']} min")
        print(f"  Loss Cooldown Ratio     : {report['loss_cooldown_ratio_pct']}% ({report['loss_cooldown_count']}/{report['reentry_blocked_count']})")
        print(f"  EF-triggered Cooldowns  : {report['ef_triggered_count']}")
        print()

        if report['blocked_by_reason']:
            print("  Blocked by Exit Reason:")
            for reason, count in report['blocked_by_reason'].items():
                pct = (count / report['reentry_blocked_count'] * 100) if report['reentry_blocked_count'] > 0 else 0
                print(f"   - {reason}: {count} ({pct:.0f}%)")
            print()

        if report['top_blocked_symbols']:
            print("  Top Blocked Symbols:")
            for i, (name, count) in enumerate(report['top_blocked_symbols'], 1):
                print(f"   {i}. {name} ({count})")
            print()

        print("=" * 50)
        print()

    def save_daily(self, log_dir: str = "logs"):
        """일일 데이터 JSON 저장"""
        os.makedirs(log_dir, exist_ok=True)

        report = self.generate_report()

        # 이벤트 상세 데이터 추가
        events_data = []
        for e in self.events:
            events_data.append({
                'timestamp': e.timestamp.isoformat(),
                'symbol': e.symbol,
                'symbol_name': e.symbol_name,
                'direction': e.direction,
                'elapsed_min': round(e.elapsed_min, 1),
                'cooldown_min': e.cooldown_min,
                'is_loss_cooldown': e.is_loss_cooldown,
                'exit_reason': e.exit_reason,
            })

        report['events'] = events_data

        filepath = os.path.join(log_dir, f"reentry_report_{self.session_date}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"  [Reentry Report] saved: {filepath}")
