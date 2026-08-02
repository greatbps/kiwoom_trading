"""
Governance AI — Before Deployment (on-demand)
ADR-006: Time-Cycle Layer ④ — 배포 직전

역할: "이 변경이 Constitution을 위반하는가?"
KPI:  False Approval Rate (≤10%, n≥10)
      Review Time (≤7일, 실제로는 수초)

판정:
  APPROVED    — 8개 조항 모두 PASS
  CONDITIONAL — WARN 존재, FAIL 없음 (조건부 승인)
  REJECTED    — 1개 이상 FAIL 또는 Hard Block 발동

Article 3 준수:
  - decision_log, research_notebook 수정 없음
  - research_notebook에 APPEND만

실행:
  python3 -m analysis.governance_ai \\
      --proposal "swing_runner에 RSI 필터 추가"

  python3 -m analysis.governance_ai \\
      --proposal "..." \\
      --evidence "최근 30건 백테스트: 승률 62%, MDD -4.2%"

  python3 -m analysis.governance_ai \\
      --proposal "..." --dry-run   # DB 저장 없음
"""

import os
import sys
import json
import logging
import argparse
import textwrap
import psycopg2
import anthropic

from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('governance_ai')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

DB_CONF = dict(dbname='trading_system', user='postgres',
               password=os.getenv('POSTGRES_PASSWORD'), host='localhost')

CLAUDE_MODEL = 'claude-haiku-4-5-20251001'
BASE_DIR     = Path(__file__).parent.parent


# ── 1. 하드블록 사전검사 (Claude 없이) ────────────────────────────────────────

HARD_BLOCKS = [
    ('execute_buy(',    'Article 1 + CLAUDE.md 절대 금지: execute_buy() 직접 호출 추가'),
    ('execute_sell(',   'Article 1 + CLAUDE.md 절대 금지: execute_sell() 직접 호출 추가'),
    ('sqlite',          'CLAUDE.md 절대 금지: SQLite 사용 (PostgreSQL 전용)'),
    ('intraday',        'CLAUDE.md 절대 금지: 인트라데이 전략 추가 (스윙 전용)'),
    ('단타',            'CLAUDE.md 절대 금지: 단타 전략 추가 (스윙 전용)'),
    ('UPDATE decision_log',  'Article 3: decision_log 수정 금지 (Append-Only)'),
    ('DELETE FROM decision_log', 'Article 3: decision_log 삭제 금지 (Append-Only)'),
    ('UPDATE research_notebook', 'Article 3: research_notebook 수정 금지'),
    ('DELETE FROM research_notebook', 'Article 3: research_notebook 삭제 금지'),
]

def _check_hard_blocks(proposal: str, evidence: str) -> list[str]:
    combined = (proposal + ' ' + evidence).lower()
    return [msg for kw, msg in HARD_BLOCKS if kw.lower() in combined]


# ── 2. 시스템 현황 수집 ────────────────────────────────────────────────────────

def _collect_system_state(conn) -> dict:
    cur = conn.cursor()

    cur.execute("SELECT market_regime, risk_score FROM market_context ORDER BY context_date DESC LIMIT 1")
    r = cur.fetchone()
    mc = {'regime': r[0], 'risk': r[1]} if r else {}

    cur.execute("SELECT COUNT(*) FROM knowledge_base WHERE is_active = TRUE")
    kb_active = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM scientist_predictions")
    pred_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM research_notebook")
    nb_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM decision_log")
    dl_count = cur.fetchone()[0]

    # 최근 governance review 이력
    cur.execute("""
        SELECT notebook_no, created_at::date,
               data_scope->>'verdict'
        FROM research_notebook
        WHERE tags @> ARRAY['governance_review']
        ORDER BY created_at DESC LIMIT 5
    """)
    recent_reviews = [
        {'nb': r[0], 'date': str(r[1]), 'verdict': r[2]}
        for r in cur.fetchall()
    ]

    return {
        'market_context':   mc,
        'kb_active':        kb_active,
        'predictions':      pred_count,
        'research_notebook': nb_count,
        'decision_log':     dl_count,
        'recent_reviews':   recent_reviews,
        'phase':            'Phase A (Q3 2026)',
        'operating_mode':   'Operator Mode (GD-003)',
    }


# ── 3. 프롬프트 구성 ───────────────────────────────────────────────────────────

_CONSTITUTION_QUESTIONS = """
Article 1 [Execution is Sacred]
  Q: 이 변경이 Execution Engine에 AI를 개입시키는가?
  (LLM이 직접 주문을 실행하거나 주문 흐름에 개입하면 위반)

Article 2 [Every Claim Needs Evidence]
  Q: 이 변경은 증거 없이 결론을 만드는가?
  (knowledge_base 갱신/전략 변경 시 측정 가능한 근거가 있어야 함)

Article 3 [History Cannot Be Rewritten]
  Q: 이 변경이 기존 기록을 수정하거나 삭제하는가?
  (decision_log, strategy_versions, knowledge_base, research_notebook 수정·삭제)

Article 4 [Unknown is a Valid State]
  Q: 표본이 충분하지 않은 상태에서 결론을 강제하는가?
  (거래 건수, 백테스트 기간, 통계적 유의성 체크)

Article 5 [AI is a Researcher, Not a Trader]
  Q: AI에게 배포 권한을 직접 또는 간접으로 부여하는가?
  (Scientist L0~L4 어느 레벨도 실주문 금지)

Article 6 [Evolution Must Be Measured]
  Q: 성공 지표를 사전 선언하지 않고 변경을 적용하는가?
  (승률, 기대값, MDD, 샤프 비율 등 사전 선언 여부)

Article 7 [Everything is an Experiment Until Proven Otherwise]
  Q: 검증 없이 Production에 직접 반영된 변경이 있는가?
  (shadow test, dry-run, 백테스트 없이 바로 production 적용)

Article 8 [Separation of Concerns]
  Q: 세 엔진(Execution/Research/Governance) 중 하나가 다른 역할을 수행하는가?
  (Execution이 연구하거나, Research가 주문하거나, Governance가 실험을 직접 수행)
"""

def _build_prompt(proposal: str, evidence: str, state: dict) -> str:
    reviews_text = '\n'.join(
        f"  {r['nb']} ({r['date']}): {r['verdict']}"
        for r in state['recent_reviews']
    ) or '  (없음)'

    return f"""You are the Governance AI for a Korean swing-trading system.

ROLE: Constitution Compliance Judge
QUESTION: "이 변경 제안이 Trading OS Constitution(8개 조항)을 위반하는가?"

═══ CURRENT SYSTEM STATE ═══
  Phase:          {state['phase']}
  Operating Mode: {state['operating_mode']}
  Market Context: {state['market_context'].get('regime', '없음')} (Risk: {state['market_context'].get('risk', '?')})
  KB Active:      {state['kb_active']}건
  Predictions:    {state['predictions']}건
  Decision Log:   {state['decision_log']}건
  Research NB:    {state['research_notebook']}건
  Recent Reviews:
{reviews_text}

═══ CHANGE PROPOSAL ═══
{proposal}

═══ SUPPORTING EVIDENCE ═══
{evidence if evidence else '(제공 없음 — Article 2, 6 관련 검토 필요)'}

═══ CONSTITUTION CHECKLIST ═══
{_CONSTITUTION_QUESTIONS}

═══ PHASE A RESTRICTIONS (GD-003, until 2026 Q3 end) ═══
  금지: 새로운 AI Agent / LLM 연결 / Dashboard / 전략 / DB 테이블 추가
  (예외: GD-005에 의해 사전 승인된 Research Layer 구성요소)

───────────────────────────────────────────────────────
INSTRUCTIONS:
  1. 각 Article에 대해 PASS / WARN / FAIL 판정
  2. WARN = 우려 있으나 조건부 수용 가능, FAIL = 명확한 위반
  3. 전체 판정: APPROVED(모두PASS) / CONDITIONAL(WARN≥1,FAIL=0) / REJECTED(FAIL≥1)
  4. CONDITIONAL이면 반드시 충족 조건 명시
  5. 한국어로 작성

Return ONLY valid JSON:
{{
  "verdict": "APPROVED | CONDITIONAL | REJECTED",
  "article_results": [
    {{
      "article": 1,
      "title": "Execution is Sacred",
      "status": "PASS | WARN | FAIL",
      "reason": "<1문장 판단 근거 (한국어)>"
    }}
  ],
  "blocking_issues": ["<FAIL 항목 요약>"],
  "conditions": ["<CONDITIONAL인 경우 충족 조건>"],
  "phase_a_check": "PASS | FAIL",
  "phase_a_note": "<Phase A 제한 관련 메모>",
  "summary": "<전체 판정 요약 2문장 (한국어)>",
  "confidence": <0~100>
}}"""


# ── 4. Claude API 호출 ─────────────────────────────────────────────────────────

def _call_claude(proposal: str, evidence: str, state: dict) -> dict:
    client  = anthropic.Anthropic()
    prompt  = _build_prompt(proposal, evidence, state)
    resp    = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    text = resp.content[0].text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    return json.loads(text)


# ── 5. 콘솔 출력 ──────────────────────────────────────────────────────────────

_STATUS_ICON = {'PASS': '✓', 'WARN': '!', 'FAIL': '✗'}
_VERDICT_LINE = {
    'APPROVED':    '▶ APPROVED    — Constitution 위반 없음',
    'CONDITIONAL': '▶ CONDITIONAL — 조건 충족 시 승인 가능',
    'REJECTED':    '▶ REJECTED    — Constitution 위반 확인',
}

def _print_result(proposal: str, result: dict, hard_blocks: list,
                  elapsed: float, dry_run: bool):
    v = result.get('verdict', 'REJECTED')
    v_icon = {'APPROVED': '[ PASS ]', 'CONDITIONAL': '[ WARN ]', 'REJECTED': '[ FAIL ]'}[v]

    print(f"\n{'='*65}")
    print(f"  GOVERNANCE AI — {date.today()}")
    print(f"{'='*65}")
    print(f"  Proposal: {textwrap.shorten(proposal, 55)}")
    print()

    if hard_blocks:
        print("  ⛔ HARD BLOCK (사전 검사):")
        for b in hard_blocks:
            print(f"     • {b}")
        print()

    for ar in result.get('article_results', []):
        icon = _STATUS_ICON.get(ar['status'], '?')
        print(f"  [{icon}] Article {ar['article']:1d}: {ar['title']}")
        print(f"       {ar['reason']}")

    if result.get('blocking_issues'):
        print(f"\n  ⛔ 차단 사유:")
        for b in result['blocking_issues']:
            print(f"     • {b}")

    if result.get('conditions'):
        print(f"\n  📋 조건 (CONDITIONAL 충족 조건):")
        for c in result['conditions']:
            print(f"     • {c}")

    pa = result.get('phase_a_check', '?')
    if pa != 'PASS' or result.get('phase_a_note'):
        print(f"\n  Phase A: [{pa}] {result.get('phase_a_note', '')}")

    print(f"\n  {v_icon} {_VERDICT_LINE[v]}")
    print(f"  신뢰도: {result.get('confidence')}  |  검토 시간: {elapsed:.1f}초")
    print(f"\n  요약: {result.get('summary', '')}")
    if dry_run:
        print("\n  [DRY RUN — DB/문서 저장 없음]")
    print()


# ── 6. research_notebook 저장 ──────────────────────────────────────────────────

def _next_notebook_no(cur) -> str:
    cur.execute("""
        SELECT MAX(CAST(SUBSTRING(notebook_no FROM 4) AS INTEGER))
        FROM research_notebook WHERE notebook_no ~ '^NB-[0-9]+$'
    """)
    return f"NB-{(cur.fetchone()[0] or 0) + 1:03d}"


def _save_notebook(conn, proposal: str, evidence: str,
                   result: dict, hard_blocks: list, elapsed: float) -> tuple:
    cur  = conn.cursor()
    nb_no = _next_notebook_no(cur)
    v    = result.get('verdict', 'REJECTED')

    cur.execute("SELECT id FROM research_environment WHERE status='active' LIMIT 1")
    row   = cur.fetchone()
    re_id = row[0] if row else None

    title = (
        f"[Governance] {v} — "
        f"{textwrap.shorten(proposal, 40)} — {date.today()}"
    )

    data_scope = {
        'proposal':        proposal,
        'evidence':        evidence,
        'verdict':         v,
        'article_results': result.get('article_results', []),
        'blocking_issues': result.get('blocking_issues', []),
        'conditions':      result.get('conditions', []),
        'hard_blocks':     hard_blocks,
        'phase_a_check':   result.get('phase_a_check'),
        'phase_a_note':    result.get('phase_a_note'),
        'elapsed_sec':     round(elapsed, 1),
    }

    fail_count = sum(1 for ar in result.get('article_results', []) if ar['status'] == 'FAIL')
    warn_count = sum(1 for ar in result.get('article_results', []) if ar['status'] == 'WARN')

    tags = ['governance_review', f'verdict_{v.lower()}']
    if hard_blocks:
        tags.append('hard_block')
    if fail_count > 0:
        tags.append(f'fail_count_{fail_count}')
    if warn_count > 0:
        tags.append(f'warn_count_{warn_count}')

    cur.execute("""
        INSERT INTO research_notebook
          (notebook_no, title, research_question,
           data_scope, findings, conclusion,
           confidence, tags, model_used,
           research_environment_id, session_id,
           created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NOW(),NOW())
        RETURNING id
    """, (
        nb_no, title,
        '이 변경 제안이 Trading OS Constitution을 위반하는가?',
        json.dumps(data_scope, ensure_ascii=False, default=str),
        result.get('summary', ''),
        f"{v}: {'; '.join(result.get('blocking_issues', [result.get('summary', '')[:80]]))}",
        result.get('confidence'),
        tags, CLAUDE_MODEL,
        re_id,
    ))
    nb_id = cur.fetchone()[0]
    conn.commit()
    return nb_id, nb_no


# ── 7. Governance Decision Record 자동 생성 (APPROVED 시) ─────────────────────

def _create_gd_record(proposal: str, result: dict, nb_no: str) -> str | None:
    """APPROVED 결정을 docs/governance/GD-NNN.md 초안으로 저장."""
    gov_dir = BASE_DIR / 'docs' / 'governance'
    gov_dir.mkdir(parents=True, exist_ok=True)

    # 다음 GD 번호 산정
    existing = sorted(gov_dir.glob('GD-*.md'))
    nums     = []
    for p in existing:
        try:
            nums.append(int(p.stem.split('-')[1]))
        except (IndexError, ValueError):
            pass
    gd_num = (max(nums) + 1) if nums else 6
    gd_id  = f"GD-{gd_num:03d}"
    path   = gov_dir / f"{gd_id}-governance-review.md"

    conditions_text = '\n'.join(
        f"- {c}" for c in result.get('conditions', [])
    ) or '없음 (APPROVED)'

    content = (
        f"# {gd_id}: Governance Review — APPROVED\n\n"
        f"**날짜**: {date.today()}  \n"
        f"**판정**: {result['verdict']}  \n"
        f"**검토 도구**: Governance AI ({nb_no})  \n\n"
        f"---\n\n"
        f"## 제안 내용\n\n{proposal}\n\n"
        f"## 판정 근거\n\n{result.get('summary', '')}\n\n"
        f"## 조건\n\n{conditions_text}\n\n"
        f"---\n\n"
        f"*{gd_id} — {date.today()} (Governance AI 자동 생성 초안, 사람이 검토 후 확정)*\n"
    )
    path.write_text(content, encoding='utf-8')
    return str(path)


# ── Main ───────────────────────────────────────────────────────────────────────

def run_governance(proposal: str, evidence: str = '',
                   dry_run: bool = False) -> dict:
    t0 = datetime.now()
    logger.info(f"[GOVERNANCE] 검토 시작: {textwrap.shorten(proposal, 40)}")

    # ── Hard Block 사전 검사 ─────────────────────────────────────────────────
    hard_blocks = _check_hard_blocks(proposal, evidence)
    if hard_blocks:
        logger.warning(f"[GOVERNANCE] Hard Block {len(hard_blocks)}개 발동")

    # ── 시스템 상태 수집 ─────────────────────────────────────────────────────
    conn  = psycopg2.connect(**DB_CONF)
    state = _collect_system_state(conn)

    # Hard Block이 있어도 Claude에서 완전한 분석 수행 (기록 목적)
    result = _call_claude(proposal, evidence, state)

    # Hard Block이 있으면 무조건 REJECTED 강제
    if hard_blocks:
        result['verdict']         = 'REJECTED'
        result['blocking_issues'] = hard_blocks + result.get('blocking_issues', [])

    elapsed = (datetime.now() - t0).total_seconds()
    _print_result(proposal, result, hard_blocks, elapsed, dry_run)

    nb_id = nb_no = gd_path = None
    if not dry_run:
        nb_id, nb_no = _save_notebook(conn, proposal, evidence,
                                      result, hard_blocks, elapsed)
        logger.info(f"[GOVERNANCE] research_notebook {nb_no} (id={nb_id}) 저장")

        if result['verdict'] in ('APPROVED', 'CONDITIONAL'):
            gd_path = _create_gd_record(proposal, result, nb_no)
            if gd_path:
                logger.info(f"[GOVERNANCE] GD 초안 생성: {gd_path}")
                print(f"  📄 GD 초안 저장: {gd_path}")
                print(f"     (사람 검토 후 CONSTITUTION.md 테이블에 추가 필요)\n")

    conn.close()
    return {
        'verdict':       result.get('verdict'),
        'nb_no':         nb_no,
        'nb_id':         nb_id,
        'gd_path':       gd_path,
        'hard_blocks':   hard_blocks,
        'article_fails': [ar for ar in result.get('article_results', [])
                          if ar['status'] == 'FAIL'],
        'confidence':    result.get('confidence'),
        'elapsed_sec':   elapsed,
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser(
        description='Governance AI — Constitution Compliance Judge'
    )
    ap.add_argument('--proposal',  required=True,
                    help='변경 제안 내용 (텍스트)')
    ap.add_argument('--evidence',  default='',
                    help='뒷받침 증거 (선택)')
    ap.add_argument('--dry-run',   action='store_true',
                    help='DB/문서 저장 없이 판정만')
    args = ap.parse_args()

    run_governance(
        proposal=args.proposal,
        evidence=args.evidence,
        dry_run=args.dry_run,
    )
