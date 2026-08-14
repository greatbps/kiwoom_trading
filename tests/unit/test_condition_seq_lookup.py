"""
tests/unit/test_condition_seq_lookup.py — HTS 조건검색식 idx(배열위치) → seq(고정ID) 전환 검증

HTS 실측(2026-08-11): CNSRLST 응답의 배열 위치(idx)는 매 호출마다 "현재 살아있는
조건식을 seq 오름차순으로 재배열한 순번"으로 새로 계산된다 — 앞쪽 조건식이 삭제되면
뒤쪽 idx가 밀려 코드가 조용히 다른 조건식을 가리키게 된다(seq 1/11/15/20/22/25/28/31
결번 확인, 과거 삭제 이력). seq는 조건식 생성 시 한 번 부여되면 불변이다.

`get_condition_list()`/`run_condition_filtering()`은 거대한 비동기 메서드 내부라
독립호출이 안 된다 — WI-3/WI-8과 동일하게 실제 조회 로직
(`{c[0]: c for c in self.condition_list if c}` + `.get(str(cond_seq))`)을
mirror로 재현해 검증한다.
"""
from __future__ import annotations


def _build_condition_by_seq(condition_list: list) -> dict:
    """main_auto_trading.py:2948-2950 / get_condition_list() 그대로 재현."""
    return {c[0]: c for c in condition_list if c}


def _lookup_by_seq(condition_by_seq: dict, cond_seq) -> tuple | None:
    """main_auto_trading.py:3133-3134(run_condition_filtering) 그대로 재현."""
    return condition_by_seq.get(str(cond_seq))


def _lookup_by_idx_old(condition_list: list, idx: int) -> tuple | None:
    """수정 전 방식(배열 인덱싱) — 비교용."""
    if idx < len(condition_list):
        return condition_list[idx]
    return None


def test_condition_by_seq_keys_are_seq_strings():
    condition_list = [['0', '기본조건양식'], ['21', '알고리즘추출_1110'], ['32', 'Momentum 전략']]
    d = _build_condition_by_seq(condition_list)
    assert set(d.keys()) == {'0', '21', '32'}
    assert d['32'] == ['32', 'Momentum 전략']


def test_seq_lookup_finds_correct_condition_regardless_of_position():
    """핵심 회귀: 리스트 순서가 바뀌어도(=조건식이 삭제/추가돼 배열이 재배치돼도)
    seq 기준 조회는 항상 같은 조건식을 찾는다."""
    order_a = [['5', '신고가(GreatBPS)'], ['32', 'Momentum 전략'], ['38', 'Bottom 전략']]
    order_b = [['32', 'Momentum 전략'], ['38', 'Bottom 전략'], ['5', '신고가(GreatBPS)']]  # 순서만 다름

    d_a = _build_condition_by_seq(order_a)
    d_b = _build_condition_by_seq(order_b)

    assert _lookup_by_seq(d_a, 32) == _lookup_by_seq(d_b, 32) == ['32', 'Momentum 전략']
    assert _lookup_by_seq(d_a, 38) == _lookup_by_seq(d_b, 38) == ['38', 'Bottom 전략']


def test_old_idx_based_lookup_breaks_when_earlier_condition_deleted():
    """버그 재현: idx 기반이면 앞쪽 조건식이 하나 삭제됐을 때 뒤 idx가 밀려서
    엉뚱한 조건식을 가리킨다 — seq 기반은 영향받지 않는다는 것과 대조."""
    before_deletion = [['5', 'A'], ['21', 'B'], ['32', 'Momentum 전략'], ['38', 'Bottom 전략']]
    # idx=2 였던 'Momentum 전략'을 찾으려던 코드가 있었다고 가정
    assert _lookup_by_idx_old(before_deletion, 2) == ['32', 'Momentum 전략']

    after_deletion = [['21', 'B'], ['32', 'Momentum 전략'], ['38', 'Bottom 전략']]  # 'A'(seq 5) 삭제
    # 같은 idx=2 인데 이제 다른 조건식('Bottom 전략')을 가리킨다 — 버그 재현
    assert _lookup_by_idx_old(after_deletion, 2) == ['38', 'Bottom 전략']

    # seq 기준이면 삭제와 무관하게 항상 올바른 조건식을 찾는다
    d_before = _build_condition_by_seq(before_deletion)
    d_after = _build_condition_by_seq(after_deletion)
    assert _lookup_by_seq(d_before, 32) == _lookup_by_seq(d_after, 32) == ['32', 'Momentum 전략']


def test_seq_lookup_missing_condition_returns_none_safely():
    """존재하지 않거나 삭제된 seq를 조회하면 크래시 없이 None."""
    d = _build_condition_by_seq([['32', 'Momentum 전략']])
    assert _lookup_by_seq(d, 999) is None


def test_momentum_seq_list_matches_hts_actual_names():
    """config/strategy_hybrid.yaml의 momentum.condition_indices=[32..39]가 HTS
    실측(2026-08-11) 이름과 정확히 일치하는지 — 하드코딩 값 오타 방지."""
    hts_actual = {
        32: 'Momentum 전략', 33: 'Breakout 전략', 34: 'EOD 전략',
        35: 'Supertrend + EMA + RSI 전략', 36: 'VWAP 전략',
        37: 'Squeeze Momentum Pro', 38: 'Bottom 전략', 39: 'ITS',
    }
    condition_list = [[str(k), v] for k, v in hts_actual.items()]
    d = _build_condition_by_seq(condition_list)

    momentum_seqs = [32, 33, 34, 35, 36, 37, 38, 39]  # config/strategy_hybrid.yaml:1284
    for seq in momentum_seqs:
        found = _lookup_by_seq(d, seq)
        assert found is not None, f'seq {seq} 조회 실패'
        assert found[1] == hts_actual[seq]
