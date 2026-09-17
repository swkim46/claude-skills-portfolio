#!/usr/bin/env python3
"""요구사항 대조표 — 사용자가 말한 요구가 스킬에 실제로 남아 있는지 기계로 확인한다.

왜 있는가: 요구는 조용히 빠진다. 스킬을 개편하거나 규칙을 옮길 때 문장 하나가 사라져도
아무도 모르고, 몇 주 뒤에 "이거 왜 안 해?"로 돌아온다. 그래서 **요구를 로스터 행으로**
고정하고 매번 센다 — 섹터 보드에서 미조사를 행으로 남기는 것과 같은 수법이다.

두 열을 따로 본다:
  · 문서   = 스킬 문서(SKILL.md + references/)에 규칙으로 적혀 있나
  · 게이트 = 그 규칙을 체크포인트가 검문하나 — **적혀만 있으면 지켜지지 않는다**

★ 봉인 안전: 루브릭에서 키워드 **존재 여부만** 보고 문항·합격 기준은 출력하지 않는다.

사용:
    python3 req_audit.py            # 표 출력, 누락이 있으면 종료코드 1
    python3 req_audit.py --quiet    # 누락만 출력
"""
import glob
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SKILL = HERE.parent / ".claude" / "skills" / "trade-run"
STEPGATE_DIR = HERE.resolve().parent / ".claude" / "skills" / "_stepgate"
RUBRIC = STEPGATE_DIR / "rubrics" / "trade-run.json"

# (요구, 문서 앵커, 게이트 앵커 | None=게이트 대상 아님)
# 새 요구가 나오면 **여기에 행을 추가**한다. 행이 없으면 그 요구는 존재하지 않는 것으로 취급된다.
REQS = [
 ("① 거시→섹터→종목 순서로 접근",          r"위에서 아래로|거시 사건 → 파급", r"top_down_order"),
 ("② 파급 경로를 연쇄로 잇기",              r"연쇄는 가장 약한 고리", r"link_fields_written|breaks_if"),
 ("③ 기업 발표도 매크로에 연계",            r"기업 발표는 그 기업만의 일이 아니다", r"event_splits_sectors"),
 ("④ 연계 시장·종목까지 확장",              r"축 → 섹터|유니버스 밖 대표 종목", r"sector_names_chosen"),
 ("⑤ 놓친 분야 리서치(순환 슬롯)",          r"③ 순환|순환 슬롯", r"slot_cycle_filled"),
 ("⑥ 리서치는 매 run 필수",                 r"리서치는 \*\*매 run의 필수 단계\*\*", r"research_landed"),
 ("⑦ 막히면 루프 돌아 뚫어라",              r"사다리 3단|쿼리 소진", r"ladder_walked"),
 ("⑧ 매일 갱신·전날 이어받기",              r"§1-A 이 시장 이어받기", r"prev_scenarios_judged"),
 ("⑨ 오전 국내준비+미국회고 / 밤 반대",     r"09:35|22:35", r"review_into_note"),
 ("⑩ PER·EPS·지지선을 계산해서 쓴다",       r"price_levels\.py", r"basis_not_live_price"),
 ("⑪ 미국 주식도 추적",                     r"이중 시장 규칙", r"dual_market_rows"),
 ("⑫ 유니버스 유동 운용",                   r"universe_apply\.py|편입 대기열", r"universe_reviewed_both_ways"),
 ("⑬ 무인 운영 — 사람 확인을 게이트로",     r"무인 운영이 목표", r"unattended_no_ask"),
 ("⑭ 언젠가 결단은 내린다(기권 아님)",      r"'안 사는 것'이 기본값이면 안 된다", r"reason_is_no_view"),
 ("⑮ 전권 위임 — 입금액만 사용자가",        r"사람 확인은 게이트로 대체", r"delegation_scope"),
 ("⑯ 시나리오 선작성→발화→집행→매도",       r"논지 원장|entry_triggers", r"fired_as_is|thesis_moved_to_held"),
 ("⑰ 정보 지연 인지(속도로 못 이긴다)",     r"우위는 \*\*속도가 아니다\*\*", r"horizon_weeks_not_days"),
 ("⑱ 노트 언어 = 재료 언어",                r"작성 언어 — 노트 언어를", r"note_language_matches_material"),
 ("⑲ 매매 분석에도 fact-check",             r"5-C\. fact-check", r"mismatch_zero"),
 ("⑳ 소셜·유료 출처는 방법만 (스레드 보류)", r"소셜 미디어·유료 콘텐츠", r"tier3_method_only"),
 ("㉑ 사회·시장 동태를 전날과 이어서",       r"cycle --market|market_map\.py cycle", r"slot_cycle_filled"),
 ("㉒ 한·미 합쳐 30종목 이상 추적",          r"유니버스 전수", r"universe_rows_intact"),
 ("㉓ 파이프라인 분리 + 단계마다 게이트",    r"6단 파이프라인", r"capture"),
 ("㉔ 각 단계가 조사·판단·근거를 기록",      r"작업기록|worklog", r"worklog_rejects|worklog_carry"),
 ("㉕ 시나리오 상세 · 종목 수 최대",         r"깊이 바닥", r"depth_not_padded"),
 ("㉖ 게이트를 최대한 빡빡하게",             r"단계마다 게이트를 통과한다", None),
 ("㉗ 최종 액션(매수·매도)이 노트에",        r"§11 집행 결과", r"s11_fill_written|s11_discipline_written"),
 ("㉘ 뉴스레터 수집(밀린 발행분 전부)",      r"ingest\.py --market .* --with-news", r"material_intact"),
 ("㉙ 리서치를 life-research로 위임",        r"Skill\(life-research\)", r"research_priority_kept"),
 ("㉚ 주문 접수 ≠ 체결",                     r"주문 접수와 체결은 다른 사건", r"fill_verified|unfilled_reported"),
 ("㉛ 인젝션 방어 — 본문 지시 불복",         r"신뢰할 수 없는 외부 입력", r"injection_not_obeyed"),
 ("㉜ '미확인'을 '없음'으로 바꾸지 않는다",   r"'미확인'을 '없음'으로", r"unknown_not_none"),
 ("㉝ 조건부 기저율로 판단",                 r"base_rates\.py", r"base_rate_own_horizon"),
 ("㉞ 벤치마크 병기 기록",                   r"벤치마크 병기", r"equity_curve|journal_daily_ran"),
 ("㉟ 중복 지출은 일일 상한으로 막는다",     r"spent_today", r"dup_run_by_rule"),
 # 2026-09-10 전수조사에서 나온 요구 — 무인 운영자가 스킬과 반대 규칙을 갖고 있었다.
 ("㊱ 무인 운영자를 스킬이 명시한다",        r"run_auto\.py", None),
 ("㊲ 소유권 경계(1~5단 모델·--send 드라이버)", r"소유권 경계|--send는 드라이버", None),
 ("㊳ 기권도 게이트 면제 없다",              r"기권도 (검문 대상|면제가 아니다)", r"no_trade"),
 ("㊴ --send는 dispatch 토큰에서만",         r"`dispatch` 토큰에서만|dispatch GATE PASS 뒤에만", None),
 ("㊵ 거부를 미체결·취소·만료로 쓰지 않는다", r"거부를 (미체결|취소·만료)로 쓰지 마라", r"rejected_not_unfilled"),
 ("㊶ 미기록 집행은 사고로 기록한다",        r"failed_record", None),
 ("㊷ 이미 가진 것부터 뒤진다(사다리 0단)",   r"corpus\.py search", r"corpus_searched_before_closing"),
 ("㊸ 도구 산출 ↔ 노트를 디스크로 대조",      r"crosscheck\.py", r"crosscheck_clean"),
 ("㊹ 재실행이 이력을 덮지 않는다",           r"덮어쓰지 않았다|_superseded", None),
 ("㊺ 밀린 세션·반쪽 run을 탐지해 이어받는다", r"sessions\.py", r"missed_sessions_carried"),
 ("㊻ 시나리오를 영속 id로 추적",              r"scenarios\.py", r"scenarios_all_judged"),
 ("㊼ 회피 감사(실현↔대응)를 센다",            r"실현.*대응|회피 감사", r"미판정 0|scenarios_all_judged"),
 # 2026-09-10 (2) 2차 — 사용자 신규 요구: 정보의 세 시점
 ("㊽ 모든 정보에 세 시점(as-of·retrieved·written)", r"as-of.*retrieved.*written|세 시점",
  r"three_timestamps"),
 ("㊾ as-of는 출처에서 — 짐작 금지",           r"as-of.*기준일|짐작", r"asof_manual_not_guessed"),
 ("㊿ 장 마감 후면 집행 불가를 명시",           r"집행 불가", None),
 ("(51) 수집 창이 간극을 덮는지 대조",          r"수집 창", None),
 ("(52) 고점은 시장별로 — 반대편을 지우지 않는다", r"시장별|position_peaks", None),
 # 3차 — 죽은 상태를 되살리거나 지운다
 ("(53) 회피 감사 3지표를 주간 리포트에",       r"회피 감사 — 주간 리포트에|no_trade. 비율", None),
 ("(54) 순환 관찰을 --sectors로 기록",          r"--sectors", r"slot_cycle_filled"),
 # 2026-09-11 — 미국 run이 생성줄 하나 때문에 막혔다(진단에 네 시간)
 ("(55) 1단 생성줄은 번역·재작성 금지",         r"1단이 생성한 줄은 번역하지 않는다|1단 생성 — 그대로 둘 것", None),
 ("(56) 스탬프는 ✓ 줄과 시각 줄을 분리",        r"완료 스탬프는 두 줄이다", r"three_timestamps"),
 # 2026-09-11 (2) — 두 시장은 독립이 아니다. 포트폴리오를 한 계좌로 취급한다(D8).
 ("(57) 모든 비중의 분모는 포트폴리오 합산 자산", r"포트폴리오 합산|한 계좌로 취급",
  r"portfolio_denominator"),
 ("(58) 목표는 포트폴리오, 집행은 그 시장 현금", r"집행은 그 시장 (현금|자산)",
  r"portfolio_denominator"),
 ("(59) 반대편 대기 논지를 위한 헤드룸 예약",    r"reserve_for_other_market_pct",
  r"other_market_considered"),
 ("(60) 축 집중 상한은 양 시장 합산",            r"axis_max_pct", r"portfolio_denominator"),
 ("(61) 노트가 두 시장을 같은 척도로 적는다",    r"1% = |자본 기준", r"portfolio_denominator"),
 ("(62) 반대편 예정 진입을 고려하고 적는다",     r"반대편 예정 진입|반대편 대기 논지",
  r"other_market_considered"),
 # 2026-09-11 (3) — 두 시장을 하나의 run 흐름으로 (D1~D7)
 ("(63) 회고 대상은 반대편 시장이 아니라 **직전 run**", r"직전 run", r"prev_run_reviewed"),
 ("(64) 시나리오는 축에 걸리고 **대응만** 시장에 속한다", r"조건은 시장에 속하지 않는",
  r"scenarios_all_judged"),
 ("(65) 실현됐는데 미집행인 대응을 이어받는다", r"scenarios\.py due|집행 대기", r"prev_run_reviewed"),
 ("(66) 논지·시나리오는 축 id로 이어진다(고아 축 0)", r"axis_id", r"axis_joined"),
 ("(67) 섹터 등급은 시장 무관하게 persist된다", r"market_map\.py grade|등급이 run을 넘어",
  r"axis_joined"),
 ("(68) 직전 run의 산출(노트·리서치·지도)을 오늘 입력으로", r"직전 run 산출",
  r"prev_run_reviewed"),
 ("(69) 출처는 공용 원장에 누적하고 재사용한다", r"srcledger\.py reuse|공용 출처 원장",
  r"prev_run_reviewed"),
 # 2026-09-14 — 끝 목표: 세상을 읽고 돈이 갈 곳을 **앞서** 잡는다 (E1~E7)
 ("(70) 축은 방향·기간·수혜 순서가 있는 **전망**이다", r"forecast add|전망 원장", r"forecast_made"),
 ("(71) 뉴스가 1차를 말하는 날 2차·3차 수혜를 적는다", r"2차·3차 수혜", r"forecast_made"),
 ("(72) 반영 여부는 감이 아니라 20일 상대수익으로 잰다", r"market_map\.py priced|priced", r"forecast_made"),
 ("(73) 세상공부·링크 원문이 재료의 입력이다", r"세상공부|링크 원문", r"material_deficit"),
 ("(74) 재료 결손은 경고가 아니라 차단이다", r"재료 결손 없음", r"material_deficit"),
 ("(75) 일정에 유니버스 종목이 걸리면 논지 또는 \[통과\]가 있어야 한다", r"gaps --check|\[통과\]",
  r"forecast_made"),
 ("(76) 법이 강제하는 행동을 스크린한다(자사주 소각 창)", r"screen_treasury|소각 창", None),
 ("(77) 같은 축이라도 동인이 다르면 후행주가 아니다", r"동인이 다르면 후행주가 아니다", r"base_rates_checked"),
 ("(78) 기저율은 매수 전 게이트다", r"base_rates_<stamp>|기저율.*매수 전", r"base_rates_checked"),
 ("(79) 전망 선행률·적중률·초과수익을 매주 낸다(성공 지표 ④)", r"성공 지표 ④|선행률", None),
 ("(80) 아쉬움을 깔때기 숫자로 센다", r"regret\.py|아쉬움 깔때기", None),
 # 2026-09-14 (2) — 사용자 결정: 선물형만 배제, 레버리지·인버스 허용 (수익 기준)
 ("(81) 선물형만 배제, 레버리지·인버스는 산다", r"선물형만", None),
 ("(82) 레버리지는 호흡 ≤10일·상한÷배수·축×배수", r"호흡 ≤10일|leveraged_max_horizon_days", None),
 ("(83) 하락 견해는 SELL 또는 인버스 BUY(공매도 없음)", r"인버스 ETF BUY|인버스 BUY", None),
 # 2026-09-15 — 다른 세션이 보고한 도구 결함 4건
 ("(84) '직전'은 날짜 스탬프로 고른다 — 리허설은 운영 폴더 밖", r"latest_stamped|_rehearsal/", None),
 ("(85) 1단 산출물 없으면 run이 아니다(허깨비 회고 방지)", r"1단\s*산출물.*run으로 치지 않는다|phantom", None),
 ("(86) forecast add 재실행은 priced를 지우지 않는다", r"priced.*지우지 않는다", None),
 ("(87) universe_apply는 매 run 5단에서 돈다(무인 전용 아님)", r"universe_apply\.py --signal.*--apply",
  r"universe_applied"),
 # 2026-09-16 — 체결까지 run의 책임 · 현금 비중
 ("(88) 접수는 run의 끝이 아니다 — 체결·취소·만료로 확정될 때까지", r"접수는 run의 끝이 아니다|확정될 때까지\*\* run이 붙어 있는다",
  r"fill_confirmed"),
 ("(89) 살아 있는 주문 재전송 금지 — 재발주는 취소 확정 뒤 승인 금액 안에서", r"취소가 브로커 TR로 확정된 뒤|승인 금액을 넘지 않게 내린다",
  r"fill_confirmed"),
 ("(90) 장외 전송 차단", r"장외면\s*전송을 거부한다|장외면 `--send` 거부|--force-hours", None),
 ("(91) 시장 현금 부족은 깎아서 산다", r"현금에 맞춰 수량을 깎아서|깎아서 산다", None),
 ("(92) 미달이면 최소 건수·축·e0 강제(deficit_short)", r"미달 강제|deficit_short", r"deficit_met"),
 ("(93) 미달 배수 3.0", r"deficit_multiplier_max`는 3\.0|2\.0→3\.0", None),
 # 2026-09-16 (2) — 입금된 돈 안에서 · 손절 즉시 · 명령 규약
 ("(94) 사람에게 환전·입금을 묻지 않는다 — 깎기·예약·회전 매도", r"환전·입금을 묻지 않는다|회전 매도", None),
 ("(95) 손절·목표는 종가를 기다리지 않는다 — 장중 즉시 규율 매도", r"종가를 기다리지 않는다|장중 터치 즉시", None),
 ("(96) 명령 규약 — 새 셸·전체 경로·--batch", r"명령 규약|--batch", None),
 ("(97) 스탬프 시각은 도구가 찍는다", r"stage\.py stamp", None),
 ("(98) 링크 본문은 fetch.py", r"fetch\.py", None),
 ("(99) 표기는 장식 없이 — crosscheck가 변형을 잡는다", r"장식 변형|장식 없이 그대로", None),
 # 2026-09-17 — 사용자 지시: 끝날 때마다 계좌 현황(주식/현금 비율·수익률)을 보고 끝에
 ("(100) 보고 끝에 계좌 현황 블록(주식:현금·수익률)", r"account_status\.py", r"\{status\}"),
 # 2026-09-17 (2) — 사용자 지시: 지정가는 최신 시세로 · 취소 확정 뒤 재발주
 ("(101) 전송 직전 지정가를 최신 시세로(해외 포함)·가격 이탈이면 미전송", r"국내·해외 모두\*\* 갱신|send_refresh_max_pct",
  r"limit_refreshed"),
 ("(102) 정정 거부 서버에서는 취소→재발주", r"곧바로 취소→재발주|fill_reorder_max", r"fill_confirmed"),
]


# `stepgate.run_criterion`이 자리표시자를 **치환하는** 필드. 이 밖에 `{...}`를 쓰면
# 리터럴로 남아 그 기준은 영원히 히트 0이 된다(살아 보이지만 죽은 기준).
RESOLVED_FIELDS = {"file", "glob", "path", "pat", "cell_pat",
                   "min", "minbytes", "min_distinct", "cell_min", "max_run"}
# 자리표시자는 이름이다 — `[0-9]{4}` 같은 **정규식 수량자와 구분**해야 한다.
# (숫자만인 `{4}`를 자리표시자로 세면 내 검사기가 오탐을 만든다.)
RX_PH = re.compile(r"\{([A-Za-z_]\w*)\}")


def chain_checks(rb: dict, docs: str) -> list:
    """요구 → 게이트 사슬이 **실제로 이어지는지** 검사한다. (이름, ok, 근거)

    키워드 grep의 한계: 게이트 앵커를 문항 산문에서 우연히 찾아도 '있음'이 되고,
    워커가 제출하지 않는 자리표시자를 쓴 기준은 **파일을 못 찾아 항상 FAIL**인데
    "기준이 있다"로 세어진다. 둘 다 지금까지 실제로 일어났다(E10·E11).
    """
    out = []

    # 구조 색인 — 값이 아니라 **이름·경로·패턴의 존재**만 본다.
    obs_ids, cps, crit_fields = set(), set(rb.keys()), []
    for cp, b in rb.items():
        for o in b.get("observations", []):
            if isinstance(o, dict) and o.get("id"):
                obs_ids.add(o["id"])
        for c in b.get("criteria", []):
            if isinstance(c, dict):
                crit_fields.append((cp, c))

    # ① 게이트 앵커가 **실재하는 id/cp**인가 — 산문 우연 일치를 걸러낸다.
    weak = []
    for name, _d, gpat in REQS:
        if gpat is None:
            continue
        alts = [a for a in re.split(r"\|", gpat) if a]
        if not any(re.fullmatch(r"[\w§\- ]+", a) for a in alts):
            continue                       # 정규식 앵커는 산문 대조가 정상이다
        hit_struct = any(a in obs_ids or a in cps for a in alts)
        if not hit_struct:
            weak.append(f"{name.split()[0]}→{gpat}")
    out.append(("게이트 앵커가 실재 id·cp를 가리킨다", not weak,
                f"전 {sum(1 for r in REQS if r[2])}건이 구조에 걸린다" if not weak else
                f"**산문에만 걸리는 앵커 {len(weak)}건**: {', '.join(weak[:4])}"
                + (" …" if len(weak) > 4 else "")))

    # ② 기준이 쓰는 자리표시자를 **워커가 제출하도록 문서가 지시하는가.**
    #    안 그러면 그 기준은 파일을 못 찾아 항상 FAIL이다 — 있으나 없는 검사다.
    used = set()
    for _cp, c in crit_fields:
        for k, v in c.items():
            if isinstance(v, str):
                used.update(RX_PH.findall(v))
    documented = set(re.findall(r"(\w+)=<", docs)) | {"deliverable", "dir"}
    undoc = sorted(used - documented)
    out.append((f"기준의 자리표시자 {len(used)}종이 문서에 지시돼 있다", not undoc,
                "전건 지시됨" if not undoc else
                f"**문서가 제출을 지시하지 않는 자리표시자 {undoc}** — 그 기준은 "
                f"인자를 못 받아 항상 FAIL이고, '기준이 있다'로 세어진다"))

    # ③ 치환되지 않는 필드에 자리표시자를 쓰지 않았는가.
    dead = []
    for cp, c in crit_fields:
        for k, v in c.items():
            if k not in RESOLVED_FIELDS and isinstance(v, str) and RX_PH.search(v):
                dead.append(f"{cp}.{k}")
    out.append(("자리표시자가 치환되는 필드에만 있다", not dead,
                "위반 0" if not dead else
                f"**치환 안 되는 필드에 자리표시자 {dead}** — 리터럴로 남아 히트 0이 된다"))

    # ④ 리터럴 경로를 쓰는 기준은 **지금 실제로 히트하는가**(죽은 기준 탐지).
    sys.path.insert(0, str(STEPGATE_DIR))
    try:
        import stepgate as sg
    except ImportError as e:
        out.append(("리터럴 기준 실측", False, f"stepgate import 실패: {e}"))
        return out
    lit_fail = []
    lit_n = 0
    for cp, c in crit_fields:
        tgt = c.get("file") or c.get("glob") or c.get("path") or ""
        if not tgt or RX_PH.search(str(tgt)):
            continue                       # 워커 인자에 의존하는 기준은 여기서 못 잰다
        lit_n += 1
        ok, why = sg.run_criterion(c, {})
        if not ok:
            lit_fail.append(f"{cp}: {why[:70]}")
    out.append((f"리터럴 경로 기준 {lit_n}건이 실제로 히트한다", not lit_fail,
                "전건 히트" if not lit_fail else
                f"**히트 0인 기준 {len(lit_fail)}건**(죽은 기준일 수 있다): "
                + "; ".join(lit_fail[:3])))
    return out


def main() -> int:
    quiet = "--quiet" in sys.argv
    docs = ""
    for p in [SKILL / "SKILL.md"] + sorted(SKILL.glob("references/*.md")):
        docs += p.read_text(encoding="utf-8")
    if not docs:
        print(f"스킬 문서를 못 찾았다: {SKILL}", file=sys.stderr)
        return 2

    rb = json.loads(RUBRIC.read_text(encoding="utf-8"))
    # cp 이름 + 관찰 id/문항 + 기준의 경로·패턴만 모은다(합격 기준은 출력하지 않는다)
    gate = " ".join(rb.keys())
    for _cp, b in rb.items():
        for o in b.get("observations", []):
            gate += " " + o.get("id", "") + " " + " ".join(v.get("ask", "") for v in o.get("variants", []))
        for c in b.get("criteria", []):
            gate += " " + " ".join(str(c.get(k, "")) for k in ("pat", "glob", "file", "path"))

    rows, gaps = [], []
    for name, dpat, gpat in REQS:
        din = bool(re.search(dpat, docs))
        gin = None if gpat is None else bool(re.search(gpat, gate))
        rows.append((name, din, gin))
        if not din or gin is False:
            gaps.append((name, din, gin))

    if not quiet:
        print(f"{'요구사항':<40}{'문서':>6}{'게이트':>8}")
        print("─" * 56)
        for name, din, gin in rows:
            g = "—" if gin is None else ("있음" if gin else "**없음**")
            print(f"{name:<40}{'있음' if din else '**없음**':>6}{g:>8}")
        print("─" * 56)

    # ★ 사슬 검사 — 키워드 grep이 "있음"이라고 한 것이 실제로 이어지는지 본다.
    chain = chain_checks(rb, docs)
    n_chain_fail = sum(1 for _, ok, _ in chain if not ok)
    if not quiet:
        print()
        print("사슬 검사 — 요구 → 게이트 항목 → 디스크 기준이 실제로 이어지는가")
        print("─" * 56)
        for name, ok, why in chain:
            print(f"  {'✓' if ok else '**✗**'} {name}")
            print(f"      {why}")
        print("─" * 56)

    nd = sum(1 for _, d, _ in rows if not d)
    ng = sum(1 for _, _, g in rows if g is False)
    print(f"요구 {len(rows)}건 · 문서 누락 {nd}건 · 게이트 누락 {ng}건 "
          f"· 사슬 결함 {n_chain_fail}건")
    if n_chain_fail and quiet:
        for name, ok, why in chain:
            if not ok:
                print(f"  ✗ 사슬: {name} — {why}")
    if gaps:
        print("\n★ 누락 — 요구가 빠졌거나 검문되지 않는다:")
        for name, din, gin in gaps:
            why = []
            if not din:
                why.append("문서에 규칙이 없다")
            if gin is False:
                why.append("게이트가 검문하지 않는다")
            print(f"  · {name} — {' / '.join(why)}")
        return 1
    print("전 요구가 문서에 있고 게이트가 검문한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
