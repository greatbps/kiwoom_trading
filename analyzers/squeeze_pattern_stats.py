"""
Squeeze 패턴별 적응형 자동 튜닝 (Pattern Learning v2)

v1 대비 개선:
  ① 최근 20건 슬라이딩 윈도우 (과거 묻힘 방지)
  ② 패턴 쿨다운 2h (죽었다가 다시 살릴 수 있게)
  ③ 탐색 유지 10% (새로운 좋은 패턴 계속 찾기)

패턴 키 구조: {regime}|vol{vol_bucket}|bb{bb_bucket}|{cap}|{time}
예) NEUTRAL|vol23|bb2|S|MID
"""
from collections import defaultdict, deque
from datetime import datetime, timedelta
import json
import logging
import random
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_RECENT_WINDOW = 20  # 슬라이딩 윈도우 크기


class PatternStats:
    def __init__(self):
        self.data = defaultdict(lambda: {"recent": deque(maxlen=_RECENT_WINDOW)})
        self._block_until: dict = {}  # {key: datetime} — 쿨다운 (재시작 시 소멸, 의도적)

    def update(self, key: str, pnl: float):
        self.data[key]["recent"].append(pnl)

    def metrics(self, key: str) -> Optional[Tuple]:
        r = list(self.data[key]["recent"])
        if not r:
            return None
        n          = len(r)
        winrate    = sum(1 for x in r if x > 0) / n
        expectancy = sum(r) / n
        # 최근 연패 카운트 (끝에서 역순으로)
        streak = 0
        for pnl in reversed(r):
            if pnl < 0:
                streak += 1
            else:
                break
        return winrate, expectancy, n, streak

    def pattern_allowed(self, key: str, cfg: dict,
                        now: Optional[datetime] = None) -> Tuple[bool, str]:
        if now is None:
            now = datetime.now()

        min_trades  = cfg.get("min_trades", 10)
        min_winrate = cfg.get("min_winrate", 0.45)
        min_exp     = cfg.get("min_expectancy", 0.1)
        max_streak  = cfg.get("max_loss_streak", 3)
        cooldown_h  = cfg.get("cooldown_hours", 2)

        # ① 쿨다운 체크
        if key in self._block_until:
            if now < self._block_until[key]:
                remaining = int((self._block_until[key] - now).total_seconds() / 60)
                return False, f"쿨다운({remaining}분 남음)"
            else:
                del self._block_until[key]  # 쿨다운 만료 → 재탐색

        m = self.metrics(key)
        if m is None:
            return True, "초기탐색"

        winrate, exp, n, streak = m

        # 표본 부족 → 탐색 유지
        if n < min_trades:
            return True, f"표본부족({n}/{min_trades})"

        # 성과 판단 → 차단 시 쿨다운 등록
        block_reason = None
        if streak >= max_streak:
            block_reason = f"연패{streak}회>={max_streak}"
        elif winrate < min_winrate:
            block_reason = f"승률{winrate:.2f}<{min_winrate}"
        elif exp < min_exp:
            block_reason = f"기대값{exp:.3f}<{min_exp}"

        if block_reason:
            self._block_until[key] = now + timedelta(hours=cooldown_h)
            return False, f"BLOCK+쿨다운{cooldown_h}h ({block_reason})"

        return True, f"OK(WR={winrate:.2f} E={exp:+.3f} N={n})"

    def size_multiplier(self, key: str, cfg: dict) -> float:
        min_trades = cfg.get("min_trades", 10)
        m = self.metrics(key)
        if m is None:
            return 1.0
        winrate, exp, n, _ = m
        if n < min_trades:
            return 1.0
        score = (winrate - 0.5) + (exp * 2)
        return max(0.5, min(1.5, 1.0 + score))

    @staticmethod
    def exploration_override(prob: float = 0.10) -> bool:
        """차단된 패턴도 prob 확률로 탐색 허용 (새 패턴 발견용)"""
        return random.random() < prob

    def save(self, path: str = "logs/sqz_pattern_stats.json"):
        try:
            Path(path).parent.mkdir(exist_ok=True)
            serializable = {k: {"recent": list(v["recent"])} for k, v in self.data.items()}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"[PATTERN_STATS] save 실패: {e}")

    def load(self, path: str = "logs/sqz_pattern_stats.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                self.data[k]["recent"].extend(v.get("recent", [])[-_RECENT_WINDOW:])
            logger.info(f"[PATTERN_STATS] {len(self.data)}개 패턴 로드: {path}")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"[PATTERN_STATS] load 실패: {e}")

    def summary(self) -> str:
        lines = ["[PATTERN_STATS] 패턴별 통계 (최근 20건 기준)"]
        for key in sorted(self.data, key=lambda k: -(len(self.data[k]["recent"]))):
            m = self.metrics(key)
            if m is None:
                continue
            winrate, exp, n, streak = m
            cd = ""
            if key in self._block_until:
                rem = int((self._block_until[key] - datetime.now()).total_seconds() / 60)
                cd = f" [쿨다운{rem}분]"
            lines.append(
                f"  {key:45s} N={n:2d} WR={winrate:.2f} E={exp:+.3f} streak={streak}{cd}"
            )
        return "\n".join(lines)


def build_pattern_key(signal: dict, regime: str, vol_ratio: float,
                      avg_val: float, lc_threshold: float, now_hm: str) -> str:
    """패턴 키 생성 (6요소 버킷팅 — 과적합 방지)"""
    cap_bucket  = "L" if avg_val >= lc_threshold else "S"
    h           = int(now_hm.split(":")[0])
    time_bucket = "OPEN" if h < 10 else ("MID" if h < 13 else "LATE")
    vol_bucket  = int(vol_ratio * 10)                      # 2.3x → 23
    bb_bucket   = int(signal.get("bb_width_pct", 0))       # 2.1% → 2
    return f"{regime}|vol{vol_bucket}|bb{bb_bucket}|{cap_bucket}|{time_bucket}"
