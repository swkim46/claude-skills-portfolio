#!/usr/bin/env python3
"""단계 드라이버 — 각 단계가 **자기 산출 파일**을 만들게 한다.

게이트는 파일만 검사할 수 있다. 단계 결과가 문맥에만 남으면 게이트가 볼 것이 없고,
다음 단계도 이어받을 것이 없다. 그래서 1·2단은 도구 출력을 한 파일로 모은다.

  stage.py capture --market kr --days 2   # 재료 + 섹터 보드 → 캡처 파일 + 노트 뼈대
  stage.py carry   --market kr            # 트리거·회고·대응매핑·순환 → carry 파일
  stage.py render-note --stamp 261019 --market kr   # 노트 자리표 {{F-id}}·§3 섹터 보드·§12 출처 원장을 저장소에서 채운다(다시 돌려도 같다)

판단은 하지 않는다. 여기서 하는 일은 **모으고 재고 옮겨 적는 것**뿐이고,
무엇이 유망한지 고르는 것은 3·4단(사람이 아니라 그 단계의 문맥)이 한다.

원장(논지·지도·일정·감시 목록·재투자 대기·run 기록)은 저장소(`db.py`)에서 읽고 쓴다(2026-10-17).
run 시각은 `ex_run`에 남는다 — capture가 `db.run_start`·사전 백업·`stage_mark`, close가 집행 기록 이관·감사 복사·`run_close`.
사실은 저장소 사실 층(`facts.py`)에 있고 노트는 그것을 가리킨다 — 값 자리 `{{F-000731}}`·`{{F-000731.as_of}}`·`{{F-000731.line}}`을
`render-note`가 `<!--F:F-000731-->값<!--/F-->`로 채운다(교정 뒤 다시 돌리면 그 자리 값만 바뀐다). §3·§12는 렌더 자리표 사이를 통째로 채운다.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db

PROJ = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
EVID = PROJ / "data" / "run_evidence"
ANALYSIS = PROJ / "analysis"
JOURNAL = PROJ / "journal"

# 재료가 직전 run의 이 비율 아래로 줄면 경고한다. `--with-news` 누락으로 뉴스레터 구간이
# 통째로 날아간 적이 있어(58KB→4.8KB) 크기 자체를 신호로 쓴다. 발행량은 날마다 달라
# 웬만한 등락은 넘겨야 하므로 문턱을 낮게 잡는다.
MATERIAL_SHRINK_FLOOR = 0.7

# ★ 1단이 노트에 **생성해 넣는 줄**. 게이트의 기준들이 이 문자열을 글자 그대로 찾으므로
#   **계약**이다 — 영어 run이라고 번역하거나 다듬으면 그 자리에서 막힌다.
#   *실사례(2026-09-11): 미국 노트가 §6 요약 줄을 "유니버스 17종목 전수 · 조회 실패 0 —
#   all 17 universe names…"로 고쳐 써서 `note` 게이트가 막혔고, 어느 기준인지 알 수 없어
#   표기 후보 200개를 탐침하고도 못 찾았다.*
#   문구를 바꿔야 하면 여기서 바꾸고, 루브릭·crosscheck·테스트를 같이 본다.
GEN_NOTICE = "<!-- 1단 생성 — 그대로 둘 것. 영어 설명은 이 줄 밖에 붙인다 -->"


def gen_coverage_line(studied: int, total: int, unstudied: list) -> str:
    """§3 섹터 보드 요약 줄 — 생성 계약."""
    return (f"커버리지 **{studied}/{total}** — 미조사 {len(unstudied)}개"
            + (f" · {', '.join(unstudied)}" if unstudied else ""))


def gen_portfolio_line(pf: dict, market: str, one_pct: float, cur: str) -> str:
    """머리말 자본 줄 — 생성 계약. **두 시장을 같은 척도로 적는 자리다.**

    ★ 왜 이 줄이 필요한가 — 노트가 시장별 %만 적으면 두 run의 숫자를 **비교할 수 없다.**
    *실사례(2026-09-11): 9/10 한화오션 `8%`(당시 국내 분모로 80만원)와 9/11 MSFT
    `2%`(당시 미국 분모로 268만원)가 노트에 8% vs 2%로 적혀 있어, 작은 쪽이 3.3배
    큰 베팅이었다는 사실이 기록에서 사라졌다.* 그래서 **1%가 얼마인지**를 노트에 박는다.
    """
    if not pf.get("ok"):
        return (f"자본 기준 — **포트폴리오를 못 쟀다**: {pf.get('why', '')} "
                f"이 노트의 %는 {market.upper()} 계좌만을 분모로 한다.")
    parts = " + ".join(f"{m} {v['krw']:,.0f}원" for m, v in (pf.get("parts") or {}).items())
    return (f"자본 기준 — **포트폴리오 합산 {pf['krw']:,.0f}원** ({parts}) · "
            f"이 노트의 모든 %는 이 분모다 · **1% = {one_pct:,.0f} {cur}** · "
            f"환율 {pf.get('fx') or 0:,.2f} · as-of {pf.get('asof') or '이번 run'}")


def gen_universe_line(n: int) -> str:
    """§6 유니버스 요약 줄 — 생성 계약. 게이트가 `{universe}`를 끼워 대조한다."""
    return f"유니버스 **{n}종목 전수**. 행을 지우지 않는다 — 지운 종목은 판단에서 빠진 것이다."


# 노트 13절 — 순서 고정. §11은 5·6단이 채운다. (제목, 스탬프 이름, 뼈대에 남길 안내)
SECTIONS = [
    ("머리말", "머리말", "날짜 · 준비 시장 / 회고 시장 · 계좌 · 유니버스 n · 재료 목록 · 숫자의 출처 파일"),
    ("§0 오늘의 판정", "§0", "3줄 — 무엇을 하나 / 무엇을 기다리나 / 이번 run이 지도에 더한 것"),
    ("§1-A 이 시장 이어받기", "§1-A", "전날 같은 시장 노트의 확인목록·제안이 어떻게 됐나 + 근거 채점"),
    ("§1-B 다른 시장 회고 → 오늘 입력", "§1-B", "채점 + 섹터 대응 매핑 (journal/carry_*.md에서 옮겨온다)"),
    ("§2 거시 축", "§2", "2~4개. 끝에 축 → 섹터 +/− 매핑표"),
    ("§3 섹터 보드", "§3", "전수 표. 미조사도 행으로"),
    ("§4 이번 run의 주목 시장", "§4", "2~3개 심층. 슬롯 3개(일정·이상·순환). 섹터마다 `### 섹터 · <이름>`(영어 run은 "
     "`### Sector · <name>`) 머리와 ⑤ `breaks_if: …` 한 줄 — **표기는 볼드·백틱 없이 그대로**(게이트가 글자로 센다)"),
    ("§5 일정 → 섹터 → 시나리오", "§5", "D-14 / D-3 / D-DAY / D+1 강제 등장"),
    ("§6 종목", "§6", "§4에서 내려온 것만. 깊이 3층. 부모 상속 필수. 심층 종목마다 `#### 심층 · <종목명>` 머리 + "
     "항목 머리 `③ 사는 쪽` / `④ 안 사는 쪽`(영어 run은 `③ Case for` / `④ Case against`) — "
     "**볼드·백틱으로 감싸지 않는다**(9/16 `③ **사는 쪽**`이 0건으로 잡혀 게이트가 막혔다)"),
    ("§7 제안 · 대응표 · 무효 조건", "§7", "제안마다 무엇이 틀리면 무효가 되는가"),
    ("§8 결론이 어디로 갔는가", "§8", "네 출구 감사 + 유니버스 편입 대기열"),
    ("§9 지도 성장 계측", "§9", "커버리지 n/전체 · 막힌 행 n · 매핑 적중/역행"),
    ("§10 한계", "§10", "못 본 것 · 못 쟌 것 · 이 노트가 틀릴 수 있는 자리. "
     "★ 모의계좌 자금 비대칭(2026-09-11 실측 13.4배)은 risk_guard가 깎기·대기 논지 예약·회전 매도로 "
     "처리한다 — 그 결과를 §11에 적을 뿐, **사람에게 환전·입금을 묻는 항목을 만들지 않는다** "
     "(2026-09-16 사용자 지시). 이번 run의 배수는 머리말 자본 줄에서 가져온다"),
    ("§11 집행 결과", "§11", "5단이 규율 판정을, 6단이 체결을 적는다 — 4단에서는 비워둔다"),
    ("§12 출처 원장 — 세 시점", "§12",
     "`python3 stage.py render-note --stamp <YYMMDD> --market <mkt>`가 이 렌더 자리를 채운다(손으로 붙이지 않는다 · 저장소 사실 층). "
     "사실마다 as-of(그 사실이 사실인 시점) · retrieved(가져온 시점) · written(쓴 시점)"),
]

# 렌더 자리표 — 이 둘 사이는 `render-note`가 통째로 다시 쓴다(손으로 고치지 않는다 · 다시 돌리면 같은 글자).
S3_OPEN, S3_CLOSE = "<!-- §3 render -->", "<!-- /§3 render -->"
S12_OPEN, S12_CLOSE = "<!-- §12 render -->", "<!-- /§12 render -->"
# 노트의 사실 값 자리 — 쓰는 자리 `{{F-000731}}`(값) · `{{F-000731.as_of}}` · `{{F-000731.line}}` → 렌더된 자리 `<!--F:…-->값<!--/F-->`
FACT_ID = r"(?:F-\d{6}|SRC-\d+)"
FACT_REF = re.compile(r"\{\{(" + FACT_ID + r")(?:\.(\w+))?\}\}")
FACT_MARK = re.compile(r"<!--F:(" + FACT_ID + r")(?:\.(\w+))?-->(.*?)<!--/F-->")
S_CITE = re.compile(r"\[(S\d+(?:\s*[,·/]\s*S\d+)*)\]")      # 문장 끝 출처 번호 `[S3]`·`[S3, S5]` — 이번 run S 번호


def stamp_today(market: str = "") -> str:
    """이번 run의 스탬프 — **거래하는 세션의 현지 날짜**(2026-10-14 · `sessions.run_stamp` — 진행 중 run이면 그 스탬프).
    시장을 모르면 한국 날짜(예전 동작 — 시장 없는 보조 명령용)."""
    if market:
        import sessions as _ss
        return _ss.run_stamp(market)
    return datetime.now(KST).strftime("%y%m%d")


def _keep_pointer(market: str, st: str) -> None:
    """2·3단이 다른 스탬프로 불렸으면 현재 run 포인터를 그 스탬프로 맞춘다(기록 안의 날짜가 이 run을 따르게)."""
    import sessions as _ss
    if _ss.read_current(market).get("stamp") != st:
        _ss.write_current(market, st)


def session_iso(st: str, market: str) -> str:
    """스탬프 → 그 run의 세션 날짜(YYYY-MM-DD). 전환일 전 스탬프는 옛 규칙(시작 시각으로)."""
    import sessions as _ss
    return _ss.stamp_to_session(st, market, _ss.first_touch(st, market)).isoformat()


TRANSIENT = ("tokenP", "Read timed out", "ReadTimeout", "timed out", "OPSQ0003", "Connection reset",
             "RemoteDisconnected", "Temporary failure")


class StepResult(str):
    """명령 한 번의 결과(2026-10-19 · Codex U02) — 캡처 파일에 그대로 넣는 Markdown 문자열이면서 종료코드·상태·필수 여부를 단다.
    문자열이라 `blocks.append(run(...))`·`"\n".join(blocks)`는 그대로 돌고, 단계 결과(ex_stage_step)는 `.step()`으로 남긴다.
    상태: 종료코드가 ok_rc 안이면 ok · 아니면 필수는 failed, 선택은 reduced(그 자료에 기대는 판단만 제한된다)."""
    def __new__(cls, md, *, name="", cmd=None, rc=0, required=False, status=None, note=""):
        o = super().__new__(cls, md)
        o.name, o.cmd, o.rc, o.required = name, cmd, rc, required
        o.status = status or ("ok" if rc == 0 else ("failed" if required else "reduced"))
        o.observed_at = datetime.now(KST).isoformat(timespec="seconds")
        o.note = note or ("" if o.status == "ok" else (f"종료코드 {rc}" if rc not in (None, 0) else ""))
        return o

    def step(self) -> dict:
        return {"name": self.name, "cmd": self.cmd, "required": self.required, "rc": self.rc, "status": self.status,
                "observed_at": self.observed_at, "note": self.note}


def _block(fn, name: str, required: bool = False) -> StepResult:
    """도구 블록(문자열을 돌려주는 함수) 하나 → StepResult. 예외면 그 사실을 블록에 남기고 failed(필수)/reduced(선택)."""
    try:
        return StepResult(fn(), name=name, required=required)
    except db.TestGuard:
        raise
    except Exception as e:                          # noqa: BLE001
        return StepResult(f"### {name}\n\n★ 실패 — {type(e).__name__}: {e}\n", name=name, rc=1, required=required,
                          note=f"{type(e).__name__}: {str(e)[:160]}")


def run(cmd: list[str], title: str, *, required: bool = False, ok_rc=(0,)) -> StepResult:
    """서브프로세스 출력을 그대로 돌려준다 — 캡처 파일에는 **가공 없이** 담는다(StepResult — 종료코드·필수 여부를 단 문자열).

    ★ 2026-10-01: KIS 일시 오류(토큰 발급 읽기 타임아웃 · OPSQ0003 라우팅)로 1차 호출이 죽으면 run이 손으로
    다시 돌렸다(260929 US capture·carry 두 번). 일시 오류 문구가 보이면 짧게 두 번까지 다시 부른다 — 결과는 같은
    명령의 결과이고, 재시도한 사실은 출력에 남긴다.
    """
    import time as _time
    print(f"  · {title} …", file=sys.stderr)
    tries = []
    for k, wait in enumerate((0, 5, 15)):
        if wait:
            _time.sleep(wait)
        p = subprocess.run([sys.executable, *cmd], cwd=PROJ, capture_output=True, text=True)
        out_all = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0 or not any(t in out_all for t in TRANSIENT):
            break
        tries.append(f"[{k + 1}차 실패 — 일시 오류로 보고 {(5, 15, 0)[k]}초 뒤 재시도]")
    body = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")
    if tries:
        body = "\n".join(tries) + "\n" + body
    ok = p.returncode in ok_rc
    if p.returncode != 0:
        body += (f"\n[종료코드 {p.returncode} — 검사가 찾은 것이 있다(명령 실패 아님 · 위 줄을 읽는다).]" if ok else
                 f"\n[종료코드 {p.returncode} — 실패한 명령이다. '결과 없음'으로 읽지 말 것.]")
    return StepResult(f"### {title}\n\n```\n{body.rstrip()}\n```\n", name=title, cmd=cmd, rc=p.returncode, required=required,
                      status="ok" if ok else ("failed" if required else "reduced"))


# ─────────────────────────────────────────────────────────── capture

def build_board(market: str, quick: bool, record: bool = False) -> tuple[str, str, int]:
    """(사람이 읽는 텍스트 블록, 노트 §3 마크다운 표, 로스터 행 수).

    한 번만 조회한다. 장중에는 부를 때마다 값이 달라져 노트 안에서 숫자가 어긋나므로,
    캡처 파일과 노트 §3이 **같은 조회 결과**에서 나와야 한다.

    `record`는 기본 꺼둔다 — 이력 적재는 **부작용**이라 실제 run에서만 켠다.
    켜둔 채로 점검·시험 삼아 부르면 같은 날짜가 두 번 쌓여 추세 계산이 어긋난다.
    """
    sys.path.insert(0, str(PROJ))
    import market_map as mm
    from kis_client import KisClient

    c = KisClient.for_mode()
    rows, bench_name, bench = mm._fetch_board(c, market, quick)
    state = mm._sector_state(db.market_map_load())
    import sessions as _ss
    today = _ss.run_date(market).isoformat()          # 세션 날짜(현지 거래일 · 2026-10-14) — 섹터 이력 date

    buf = io.StringIO()
    with redirect_stdout(buf):
        print(f"■ 섹터 보드 — {'국내(KOSPI)' if market == 'kr' else '미국(GICS 대리지표)'}  {today}")
        print(f"  기준 {bench_name} {bench:+.2f}%   ('대비' 열 = 이 기준 대비 초과수익)\n")

    md = ["| 섹터 | 오늘 | 대비 | 5일 | 20일 | 등급 | 갱신 | 연쇄 | 상태 |",
          "|---|---|---|---|---|---|---|---|---|"]
    unstudied, stale, studied = [], [], 0
    for r in rows:
        st = state.get(r["key"], {})
        # `.get(k, 기본값)`은 키가 있고 값이 None이면 None을 그대로 준다 — 빈 값은 `or`로 잡는다.
        grade = st.get("grade") or "—"
        upd = st.get("updated") or "—"
        nchain = len(st.get("chains") or [])
        status = st.get("status") or "**미조사**"
        carried = st.get("carried_runs") or 0
        if grade == "—":
            unstudied.append(r["name"])
        else:
            studied += 1
        if carried >= mm.CARRY_LIMIT:
            stale.append(f"{r['name']}({carried}run 이월)")
        exc = r["d1"] - bench if r["d1"] is not None else None
        cells = [r["name"], mm._fmt(r["d1"]).strip(), mm._fmt(exc).strip(),
                 mm._fmt(r["d5"]).strip(), mm._fmt(r["d20"]).strip(),
                 grade, upd, str(nchain or "—"), status]
        md.append("| " + " | ".join(cells) + " |")
        with redirect_stdout(buf):
            print(f"  {r['name']:<14}{mm._fmt(r['d1'])}{mm._fmt(exc)}"
                  f"{mm._fmt(r['d5'])}{mm._fmt(r['d20'])}  {grade:<3}{upd:<11}{nchain or '—':<5}{status}")

    with redirect_stdout(buf):
        print(f"\n  커버리지 {studied}/{len(rows)} — 미조사 {len(unstudied)}개")
        if unstudied:
            print("  미조사: " + ", ".join(unstudied))
        if stale:
            print(f"  ★ {mm.CARRY_LIMIT}run 이상 이월(등급 강등 대상): " + ", ".join(stale))
    if record:
        mm._record_board(rows, market, bench, today)
    md.append("")
    md.append(GEN_NOTICE)
    md.append(gen_coverage_line(studied, len(rows), unstudied))
    return buf.getvalue(), "\n".join(md), len(rows)


def build_universe(market: str, st: str) -> tuple[str, int]:
    """§6 '표 한 줄' 층을 스냅샷에서 그대로 깐다. (마크다운, 종목 수)

    판단이 아니라 데이터라 1단이 만든다. 4단이 논지·판단 칸만 채우면 되고,
    **행을 안 지우는 한 유니버스 누락이 불가능해진다.**
    """
    snap = json.loads((PROJ / "data" / f"snapshot_{st}_{market}.json").read_text(encoding="utf-8"))
    prices = snap.get("prices") or {}
    dp = 0 if market == "kr" else 2          # 미국은 센트까지 — 트리거가 소수점에 걸린다
    axis = {i["ticker"]: (i.get("axis") or i.get("sector") or "")
            for i in db.companies(market.upper())}              # 감시 목록 = 저장소 dim_company
    items = db.theses_all()
    by_tic = {}
    for x in items:
        if x.get("status") in ("closed", "expired"):
            continue
        by_tic.setdefault(str(x.get("ticker")), []).append(f"{x.get('id')}·{x.get('status')}")
    md = ["| 종목 | 섹터 | 현재가 | 등락 | 살아있는 논지 | 오늘 판단 (4단) |",
          "|---|---|---|---|---|---|"]
    for tic, q in sorted(prices.items(), key=lambda kv: -(kv[1].get("change_pct") or -99)):
        px = q.get("price")
        ch = q.get("change_pct")
        sec = q.get("sector") or axis.get(tic) or "—"
        th_s = " / ".join(by_tic.get(tic) or ["—"])
        if isinstance(px, (int, float)) and isinstance(ch, (int, float)):
            md.append(f"| `{tic}` {q.get('name', '')} | {sec} | {px:,.{dp}f} | {ch:+.2f}% | {th_s} |  |")
        else:
            md.append(f"| `{tic}` {q.get('name', '')} | {sec} | 미확인 | 미확인 | {th_s} |  |")
    err = snap.get("price_errors") or {}
    for tic, why in err.items():
        md.append(f"| `{tic}` | — | **조회 실패** | — | {' / '.join(by_tic.get(tic) or ['—'])} "
                  f"| 미확인 — '없음'으로 바꿔 쓰지 말 것: {str(why)[:60]} |")
    n = len(prices) + len(err)
    md.append("")
    md.append(GEN_NOTICE)
    md.append(gen_universe_line(n))
    return "\n".join(md), n


def market_window_block(market: str) -> str:
    """지금 이 시장이 **열려 있는가.** 캡처에 못 박는다.

    왜: 장이 닫힌 뒤 돌린 run이 주문을 내면 브로커가 거부하는데, 그 사실을 캡처에
    적어두지 않으면 5단이 "지정가로 걸어둔다"고 판단하고 6단이 거부를 미체결로 읽는다.
    *실사례(2026-09-10): MSFT 주문이 `[40580000] 모의투자 장종료`로 거부됐다 —
    애초에 집행 불가한 시각이었고, 그 사실이 어느 파일에도 없었다.*
    """
    sys.path.insert(0, str(PROJ))
    try:
        from run_auto import market_state, session_date
        mkt = market.upper()                     # MARKET_WINDOW는 대문자 키다
        state, why = market_state(mkt)
        sess = session_date(mkt)
    except Exception as e:                                    # noqa: BLE001
        return (f"### 시장 상태\n\n```\n[판정 불가] {type(e).__name__}: {e}\n"
                f"— 집행 가능 여부를 확인하지 못했다. '열려 있다'로 가정하지 말 것.\n```\n")
    verdict = {
        "open": "**집행 가능** — 지금 낸 주문은 접수·체결 판정이 의미를 갖는다.",
        "closed": "**집행 불가(장 마감·개장 전)** — 지금 `--send`하면 브로커가 거부한다. "
                  "제안은 만들되 `dispatch`까지 하고 **집행은 다음 정규 슬롯으로 넘긴다.** "
                  "거부는 미체결이 아니다(걸려 있는 주문이 없다).",
        "weekend": "**집행 불가(주말·휴장)** — 위와 같다.",
    }[state]
    return (f"### 시장 상태 — 집행 가능한 시각인가\n\n```\n"
            f"{market.upper()} 세션일 {sess} · 상태 {state} ({why})\n"
            f"{verdict}\n```\n")


def news_window_block(market: str, days: int, st: str) -> str:
    """받은 재료의 **날짜 범위**와 `--days` 창을 대조한다.

    왜: `--days`가 직전 run과의 간극보다 작으면 그 사이 발행분이 **조용히 빠진다** —
    빠진 줄은 어디에도 안 남고, 노트는 "그 축은 재료에 없었다"로 적는다.
    """
    prev = last_session(market, st)
    gap = None
    if prev:
        try:
            d0 = datetime.strptime(prev, "%Y-%m-%d").date()
            gap = (datetime.strptime(session_iso(st, market), "%Y-%m-%d").date() - d0).days
        except ValueError:
            gap = None
    mat = PROJ / "data" / f"material_{st}_{market}.md"
    dates = sorted(set(re.findall(r"20\d{2}-\d{2}-\d{2}", mat.read_text(encoding="utf-8",
                                                                       errors="ignore"))))\
        if mat.exists() else []
    lines = [f"직전 시그널 세션 {prev or '(없음)'} · 오늘과의 간극 "
             f"{gap if gap is not None else '?'}일 · 수집 창 --days {days}"]
    if dates:
        lines.append(f"재료에 등장한 날짜 범위 {dates[0]} ~ {dates[-1]} ({len(dates)}개 날짜)")
    else:
        lines.append("재료에서 날짜를 하나도 못 찾았다 — 뉴스레터 구간이 비었을 수 있다.")
    if gap is not None and days < gap:
        lines.append(f"★ **--days {days} < 간극 {gap}일 — 그 사이 발행분이 빠졌다.** "
                     f"빠진 재료는 '없었던 것'이 아니다. `--days {gap}` 이상으로 다시 "
                     f"수집하거나, 노트 §10 한계와 시그널 `material_gaps`에 명시하라.")
    else:
        lines.append("수집 창이 간극을 덮는다.")
    return "### 수집 창 점검\n\n```\n" + "\n".join(lines) + "\n```\n"


def stamp(key: str) -> str:
    """완료 스탬프 **두 줄** — 완료 표시와 작성 시각을 **분리해서** 찍는다.

    세 시점 중 `written`을 자동으로 얻는 자리다(`as-of`는 출처가 말하는 시점이라 수동,
    `retrieved`는 `facts.py add`가 찍는다). 노트만 보고는 "삼성생명 305,000"이 언제
    값인지 알 수 없었다 — 한 노트 안에 캡처 13:30 · 재호출 15:20(값이 달라짐) ·
    종가 15:30이 섞여 있었다.

    ★ **왜 한 줄에 합치지 않는가 (2026-09-11)** — 2026-09-10에 이것을
    `<!-- ✓ §N · written … -->` 한 줄로 바꿨더니 게이트가 막혔다. 게이트의 기준들은
    `<!-- ✓ §N -->`을 **글자 그대로** 찾는데, 시각을 안에 끼워 넣으면 그 패턴이 안 맞는다.
    `capture`가 5번 FAIL했고, 미국 run은 절마다 스탬프를 두 벌 넣어 우회해야 했다.

    그래서 **`✓` 줄은 옛 형태와 바이트 단위로 같게 두고** 시각은 옆 줄로 뺀다.
    *교훈: 검사자가 글자 그대로 찾는 문자열은 계약이다. 계약을 늘리려면 덧붙이지,
    안을 고치지 않는다.*
    """
    return (f"<!-- ✓ {key} -->\n"
            f"<!-- written {key} {datetime.now(KST):%Y-%m-%d %H:%M} -->")


def write_preserving(path: Path, text: str, what: str) -> None:
    """이미 있는 파일을 **덮어쓰지 않고 옆으로 치워 보존한 뒤** 새로 쓴다.

    왜 필요한가: 이 파일들은 ① 손으로 채우는 칸을 갖고 있거나(미체결분 행선지 ·
    대응 매핑 채점 · 중단·재개) ② 이미 쓰인 노트가 "이 run 숫자의 유일한 출처"로
    지목하는 대상이다. 재실행이 조용히 덮어쓰면 **앞 run의 판단과 그 노트의 검증
    근거가 소급 소멸한다** — 노트에는 가드가 있는데 이 둘에는 없었다.

    `execute.py`의 거래기록 보존과 같은 처방이다: **지우지 말고 치우고 크게 알린다.**
    보존본은 하위 `_superseded/`로 넣는다 — 부모 폴더를 `tools_*.md`·`carry_*.md`로
    훑는 게이트 기준이 낡은 파일을 세지 않게 하기 위함이다.
    """
    if path.exists():
        aside_dir = path.parent / "_superseded"
        aside_dir.mkdir(parents=True, exist_ok=True)
        base = f"{path.stem}_{datetime.now(KST):%H%M%S}"
        # ★ 초 단위 이름은 같은 초에 두 번 치우면 충돌하고, `rename`은 **조용히 덮는다** —
        #   이력을 지키려고 만든 함수가 이력을 지우게 된다. 빈 이름을 찾을 때까지 센다.
        aside = aside_dir / f"{base}{path.suffix}"
        n = 2
        while aside.exists():
            aside = aside_dir / f"{base}_{n}{path.suffix}"
            n += 1
        path.rename(aside)
        print(f"[보존] 기존 {what}을 `_superseded/{aside.name}`로 옮겼다 — 덮어쓰지 않았다. "
              f"손으로 채운 칸이 있으면 거기서 옮겨 오라.", file=sys.stderr)
    path.write_text(text, encoding="utf-8")


def rel(p: Path) -> str:
    """프로젝트 기준 상대경로. 밖이거나 상대경로로 들어오면 그대로 보여준다."""
    try:
        return str(Path(p).resolve().relative_to(PROJ))
    except ValueError:
        return str(p)


def capital_line(market: str, st: str) -> str:
    """이번 run의 자본 줄을 `risk_guard`에서 그대로 가져온다.

    ★ 분모를 여기서 다시 계산하지 않는다 — 두 곳에서 재면 노트의 %와 실제로 집행되는
    한도가 갈린다(생성자↔검사자를 한 곳에 두는 것과 같은 이유).
    """
    try:
        import risk_guard as rg
        bal = (json.loads((PROJ / "data" / f"snapshot_{st}_{market}.json")
                          .read_text(encoding="utf-8")) or {}).get("balance") or {}
        pf = rg.portfolio_equity(market, bal)
        loc = rg.pf_local(pf, market, bal)
        return gen_portfolio_line(pf, market, loc["equity"] / 100,
                                  bal.get("currency") or ("USD" if market == "us" else "KRW"))
    except Exception as e:      # 노트 뼈대 생성이 이것 때문에 죽으면 안 된다
        return (f"자본 기준 — **못 쟀다**({type(e).__name__}: {e}). "
                f"4단이 채우기 전에 `risk_guard.py`를 직접 돌려 확인하라.")


def latest_stamped(folder: Path, prefix: str, market: str, suffix: str,
                   exclude_stamp: str = "") -> "Path | None":
    """`<prefix>_<YYMMDD>_<market><suffix>` 중 **날짜 스탬프 기준** 최신. 6자리 숫자가 아닌 스탬프
    (`REHEARSAL` 등)는 후보가 아니다.

    ★ 왜 — 파일명 정렬로 최신을 고르면 `signal_REHEARSAL_kr.json`이 `'R' > '2'`로 마지막에 온다.
    *실측: 2026-09-11 미국 run이 리허설 스냅샷을 실계좌 분모로 읽었고(risk_guard에서 고침),
    2026-09-15 국내 run이 리허설 시그널을 직전 시그널로 회고했다 — 같은 버그를 여기서는 안 고쳤었다.*
    `_v3` 같은 판 접미사는 같은 스탬프 안에서 파일명 순으로 뒤가 최신이다.
    """
    rx = re.compile(rf"^{re.escape(prefix)}_(\d{{6}})_{re.escape(market)}(?:_v\d+)?{re.escape(suffix)}$")
    cands = []
    for f in folder.glob(f"{prefix}_*_{market}*{suffix}"):
        m = rx.match(f.name)
        if not m or m.group(1) == exclude_stamp:
            continue
        cands.append((m.group(1), f.name, f))
    cands.sort()
    return cands[-1][2] if cands else None


def note_skeleton(market: str, st: str, evid: Path, board_md: str, uni_md: str = "",
                  cap_line: str = "") -> str:
    other = "us" if market == "kr" else "kr"
    cap_line = cap_line or "자본 기준 — (1단이 스냅샷에서 채운다)"
    head = (f"# 분석노트 {st} · {market.upper()}\n\n"
            f"| | |\n|---|---|\n"
            f"| 날짜 | {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST |\n"
            f"| 준비하는 시장 | **{market.upper()}** |\n"
            f"| 회고하는 시장 | **{other.upper()}** |\n"
            f"| 숫자의 출처 | `{rel(evid)}` — 이 노트의 모든 시세·지수는 여기서 왔다 |\n"
            f"| 이어받기 | `journal/carry_{st}_{market}.md` |\n\n"
            f"{GEN_NOTICE}\n{cap_line}\n\n"
            f"계좌·유니버스·재료 목록은 4단에서 채운다.\n\n{stamp('머리말')}\n")
    out = [head]
    for title, key, hint in SECTIONS[1:]:
        out.append(f"\n---\n\n## {title}\n")
        if key == "§3":
            # 렌더 자리 — 1단은 캡처한 보드를 넣고, 4단 `render-note`가 보드 기록 + 섹터 상태 + 재료 언급(mat_mention)으로 다시 채운다
            out.append(f"\n{S3_OPEN}\n{board_md}\n{S3_CLOSE}\n\n{stamp('§3')}\n")
        elif key == "§12":
            out.append(f"\n{S12_OPEN}\n<!-- TODO §12 --> {hint}\n{S12_CLOSE}\n")
        elif key == "§6" and uni_md:
            out.append("\n**표 한 줄 층 — 유니버스 전수.** 심층 5단과 섹터 비교표는 4단이 위에 덧붙인다.\n\n"
                       + uni_md + "\n\n<!-- TODO §6 --> 심층 5단 · 유니버스 밖 비교표\n")
        elif key == "§11":
            out.append("\n<!-- 5·6단이 채운다 §11 -->\n\n"
                       "| 제안 | 규율 판정 | 전송 | 체결 | 실제 |\n|---|---|---|---|---|\n"
                       "| (5단이 채운다) | | | | |\n")
        else:
            out.append(f"\n<!-- TODO {key} --> {hint}\n"
                       f"<!-- 채우면 두 줄로 스탬프한다: `<!-- ✓ {key} -->` 다음 줄에 "
                       f"`<!-- written {key} YYYY-MM-DD HH:MM -->`. "
                       f"✓ 줄은 게이트가 글자 그대로 찾으므로 **안을 고치지 말 것** -->\n")
    return "".join(out)


def regime_inputs(market: str, board_txt: str = "") -> str:
    """국면 판단 재료 한 블록 — 벤치마크(KODEX200·SPY) 5·20·60일 수익률·이동평균 대비 위치 + 보드 상승/하락 섹터 수.
    브로커가 죽어도 캡처를 막지 않는다(그 줄만 '못 쟀다')."""
    import re as _re
    lines = []
    tk = (db.benchmark_get(market.upper()) or {}).get("benchmark_ticker")   # 벤치마크 기준점(저장소 op_benchmark_anchor)
    if tk:
        try:
            from kis_client import KisClient
            from datetime import timedelta as _td
            cli = KisClient.for_mode()
            if market.lower() == "kr":
                end = datetime.now(KST).strftime("%Y%m%d")
                start = (datetime.now(KST) - _td(days=120)).strftime("%Y%m%d")
                rows = db.domestic_daily(cli, tk, start, end)          # 일봉은 저장소를 거친다(같은 run의 재조회는 표에서)
            else:
                rows = db.overseas_daily(cli, tk, "AMS" if tk in ("SPY",) else "NAS")
            closes = [float(r.get("close") or 0) for r in rows if r.get("close")]
            if len(closes) >= 61:
                c0 = closes[0]
                def chg(n):
                    return (c0 / closes[n] - 1) * 100 if len(closes) > n and closes[n] else None
                ma20 = sum(closes[:20]) / 20
                ma60 = sum(closes[:60]) / 60
                lines.append(f"벤치마크 {tk} 종가(확정) {c0:,.2f} (기준일 {rows[0].get('date')} — 마지막 닫힌 세션)")
                lines.append(f"  수익률 5일 {chg(5):+.2f}% · 20일 {chg(20):+.2f}% · 60일 {chg(60):+.2f}%")
                lines.append(f"  20일선 {ma20:,.2f} 대비 {(c0 / ma20 - 1) * 100:+.2f}% · 60일선 {ma60:,.2f} 대비 {(c0 / ma60 - 1) * 100:+.2f}%"
                             f" → {'20·60일선 위' if (c0 > ma20 and c0 > ma60) else '20일선 위·60일선 아래' if c0 > ma20 else '60일선 위·20일선 아래' if c0 > ma60 else '20·60일선 아래'}"
                             f"{' · 20일선<60일선(데드크로스 구간)' if ma20 < ma60 else ''}")
                hi60 = max(closes[:60])
                lines.append(f"  60일 고점 {hi60:,.2f} 대비 {(c0 / hi60 - 1) * 100:+.2f}%")
            else:
                lines.append(f"벤치마크 {tk}: 일별 시세 {len(closes)}행 — 60일 계산 불가")
        except Exception as e:                          # noqa: BLE001
            lines.append(f"벤치마크 {tk}: 못 쟀다({type(e).__name__}: {str(e)[:80]})")
    else:
        lines.append("벤치마크 티커 없음(저장소 op_benchmark_anchor)")
    ups = downs = 0
    for ln in (board_txt or "").splitlines():
        m = _re.search(r"([+-]\d+\.\d+)%", ln)
        if m and not ln.lstrip().startswith(("■", "섹터", "-")):
            v = float(m.group(1))
            ups += v > 0
            downs += v < 0
    if ups or downs:
        lines.append(f"섹터 보드 오늘 상승 {ups} / 하락 {downs}")
    lines.append("→ allocation.regime은 위 숫자를 인용해 적는다(국면 판단의 근거). 현금을 남기려면 명분과 해제 조건을 붙인다.")
    return "\n".join(lines)


def unfinished_prev_stamp(market: str, now=None):
    """자정을 넘겨 이어 가는 run이면 전날 stamp를 돌려준다(없으면 None).

    ★ 2026-09-30: 23:56에 멈춘 US run을 01:17에 재개하며 `capture`를 `--stamp` 없이 돌려 260930 산출 5개가
    잘못 생겼다. 전날 작업기록이 있고 `execute`·`no_trade` 스탬프가 없으면 그 run이 아직 안 끝난 것이다.
    """
    now = now or datetime.now(KST)
    if now.hour >= 8:                       # 아침 이후의 새 run은 오늘 stamp(국내 run·다음 날 미국 run)
        return None
    prev = (now - timedelta(days=1)).strftime("%y%m%d")
    wl = JOURNAL / f"worklog_{prev}_{market}.md"
    if not wl.exists():
        return None
    t = wl.read_text(encoding="utf-8")
    if "✓ 작업기록-execute" in t or "✓ 작업기록-no_trade" in t:
        return None
    return prev


def watch_loop_block(market: str) -> str:
    """★ FB-261003-8 — run 사이 장중 감시 루프(stops.py, mode=loop)가 살아 있는가. 마지막 점검 시각과 그 뒤 지난 시간.

    세션 전용 예약이 만료되면 루프가 조용히 멈춘다(09-26~10-02 기록 없음). 멈춘 동안 손절·목표는 run 시각에만 판정됐다."""
    rows = [r for r in db.log_rows("stops_log")
            if r.get("mode") == "loop" and str(r.get("market") or "").upper() == market.upper()]
    now = datetime.now(KST)
    if not rows:
        line = (f"감시 루프 마지막 점검 **기록 없음** — run 사이 손절·목표가 run 시각에만 판정된다. "
                f"예약 작업 `auto-stops`는 2026-10-11 사용자가 꺼 두었다 — 다시 켜지 않는다(보고에 '장중 감시 꺼짐' 한 줄)")
    else:
        last = rows[-1]
        try:
            gap_h = (now - datetime.fromisoformat(last["ts"])).total_seconds() / 3600
        except (KeyError, ValueError):
            gap_h = None
        line = (f"감시 루프 마지막 점검 {str(last.get('ts'))[:16]} · 그 뒤 {gap_h:.1f}시간"
                + (" — **장중 1시간 넘게 끊겼으면 공백이다**(stops.py --intraday가 그 사이 터치를 잡는다 · "
                   "예약 `auto-stops`는 2026-10-11 사용자가 꺼 두었다 — 다시 켜지 않는다)" if gap_h and gap_h > 1 else "")
                if gap_h is not None else f"감시 루프 마지막 점검 {last.get('ts')}")
    return "### 장중 감시 루프 (stops.py · mode=loop 하트비트)\n\n- " + line + "\n"


def tests_block() -> str:
    """★ 2026-10-11 — 마지막 테스트 완주 기록(`run_tests.py --last`). 테스트가 중간에 멈춰 뒤 케이스가 일주일 동안 안 돌았는데
    아무도 몰랐다(10-03~10-10). 매 run 캡처에 박아 보고까지 올라가게 한다 — ★(멈춤·실패·7일 넘은 기록)면 보고에 그대로 적는다."""
    try:
        sys.path.insert(0, str(PROJ))
        import run_tests as _rt
        line = _rt.last_line()
    except Exception as e:                        # noqa: BLE001
        line = f"★ [테스트] 기록을 못 읽었다 — {type(e).__name__}: {e}"
    return "### 테스트 (run_tests.py --last — 마지막 완주 기록)\n\n- " + line + "\n"


def _finalize_block() -> str:
    db.holidays_sync()
    from kis_client import KisClient
    res = db.bars_finalize(lambda: KisClient.for_mode())
    return (f"### 일봉 확정 (db.bars_finalize — 마감 뒤 재관측)\n\n종목 {res['subjects']} · 확정 {res['finalized']} · "
            f"브로커가 행을 안 줌 {res['missing']} · 오류 {res['errors']}\n\n{db.holidays_coverage_line()}\n")


def cmd_capture(a) -> int:
    import sessions as _ss
    st = a.stamp or _ss.run_stamp(a.market)
    fresh = _ss.session_stamp(a.market)
    if not a.stamp and st != fresh:
        print(f"※ 끝나지 않은 run(worklog_{st}_{a.market}.md · 포인터 저장소 op_current_run)을 이어 간다 — stamp {st}"
              f"(새 run이면 --stamp {fresh}를 명시)", file=sys.stderr)
    # ★ 현재 run 포인터(2026-10-14) — 기록 안의 날짜(논지·지도·일정·섹터 이력·자산 곡선)가 이 run의 세션 날짜를 쓰게.
    #   ingest보다 먼저 쓴다(ingest가 일정 `added`를 적는다).
    from kis_client import account_mode as _acct_mode
    _cur = _ss.write_current(a.market, st, account_mode=_acct_mode())
    print(f"스탬프 {st} = {a.market.upper()} 세션 {_cur.get('session_date')} (현지 거래일)", file=sys.stderr)
    # ★ run 기록(2026-10-17) — ex_run 한 행을 열고, 이 run이 무엇을 바꾸기 전에 저장소 전체를 백업한 뒤 capture 단계를 연다.
    #   계좌 계열은 시작 때 정해진다(v4 · R09 — 같은 날 다른 계열로 다시 시작하면 거부).
    try:
        db.run_start(st, a.market, account_mode=_acct_mode(), session_date=_cur.get("session_date"))
    except db.RunModeConflict as e:
        print(f"[중단] {e}", file=sys.stderr)
        return 2
    _bk = db.backup(f"pre_{st}_{a.market}")
    sid = db.stage_start(st, a.market, "capture")
    t0 = datetime.now(KST)
    print(f"저장소 백업 {_bk.name} · ex_run {db.run_id(st, a.market)} capture(시도 {db.q1('SELECT attempt FROM ex_stage WHERE id=?', (sid,))['attempt']})",
          file=sys.stderr)
    # ★ 실행 시각 점검(2026-10-15) — 정규 슬롯은 국내 09:30 · 미국 개장 5분 뒤(서머타임 22:35 · 11-02부터 23:35).
    #   개장 20분 전보다 일찍 시작하면 예약 시각이 잘못 잡힌 것이다(주문이 개장 직후 변동 구간에 떨어지고 그날 고가·저가가 없다).
    from kis_client import market_session as _msess
    _now = datetime.now(KST)
    _ms = _msess(a.market.upper(), _now)
    try:
        _slot = _ss.slot_for(a.market, datetime.fromisoformat(_cur["session_date"]).date())
    except (KeyError, ValueError, TypeError):
        _slot = None
    if not _ms["is_open"] and _now < _ms["open"] - timedelta(minutes=20) and _now.date() == _ms["open"].date():
        print(f"★ 실행 시각 — 개장({_ms['open']:%H:%M}) {int((_ms['open'] - _now).total_seconds() // 60)}분 전에 시작했다. "
              f"정규 슬롯은 {_slot:%H:%M}이다 — 예약 시각을 확인하라(서머타임이 끝나면 미국은 23:35)" if _slot else
              f"★ 실행 시각 — 개장 20분 전보다 이르다", file=sys.stderr)
    EVID.mkdir(parents=True, exist_ok=True)
    ANALYSIS.mkdir(parents=True, exist_ok=True)

    blocks = [run(["ingest.py", "--market", a.market, "--with-news",
                   "--days", str(a.days), "--stamp", st], "재료 수집 (ingest.py)", required=True)]
    # ★ 필수 — 이번 시도가 새 스냅샷을 썼는가(옛 스냅샷 위에서 이후 단계가 돌지 않게 · U02)
    snap = PROJ / "data" / f"snapshot_{st}_{a.market}.json"
    try:
        _gen = datetime.fromisoformat((json.loads(snap.read_text(encoding="utf-8")) or {}).get("generated_at") or "")
        _fresh = _gen >= t0 - timedelta(seconds=1)
        _snapj = json.loads(snap.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, TypeError):
        _gen, _fresh, _snapj = None, False, {}
    blocks.append(StepResult("" if _fresh else f"### 스냅샷\n\n★ ingest가 새 스냅샷을 쓰지 않았다({snap.name} · 생성 {_gen})\n",
                             name="스냅샷 새로 씀", rc=0 if _fresh else 1, required=True,
                             note="" if _fresh else f"ingest가 새 스냅샷을 쓰지 않았다(생성 {_gen})"))
    # 스냅샷 안의 선택 수집 실패 — 그 자료에 기대는 판단만 제한된다(노트 §10 [제한·1단])
    _ns = _snapj.get("news_status") or {}
    if _ns.get("error"):
        blocks.append(StepResult("", name="뉴스레터 수집", rc=1, note=f"수집 실패 — {str(_ns['error'])[:120]} · 재료 기반 판단 제한"))
    _dt = _snapj.get("dart") or {}
    if _dt and (_dt.get("status") in ("error", "skipped") or (_dt.get("failed") or 0) > 0):
        blocks.append(StepResult("", name="DART 공시 조회", rc=1,
                                 note=f"{_dt.get('status')} · 실패 {_dt.get('failed') or 0}/{_dt.get('asked') or '?'} — 공시 축은 '확인 안 됨'"))
    # ★ 실계좌 전환(2026-10-12): 국내 run은 미국 주간거래(KST 10:00~) 시각과 겹친다(09:30 캡처면 창 전 → close에서) — 미국 보유의 손절·추적 다리를 주간 세션 시세로
    #   판정하고, 실계좌(real)면 그 자리에서 판다(조회 전용·모의면 판정만). 정규장 밤 run까지 12시간 들고 있지 않게 한다.
    _mode = _acct_mode()
    from kis_client import us_daytime_session as _uds
    if a.market == "kr" and _mode != "paper" and _uds()["is_open"]:
        blocks.append(run(["stops.py", "--market", "us", "--daytime", "--stamp", st]
                          + (["--send"] if _mode == "real" else []),
                          "미국 주간거래 손절 점검 (stops.py --daytime — 손절·추적 다리만 · 매도 전용)"))
    elif a.market == "kr" and _mode != "paper":
        # 09:30 국내 run은 주간거래(10:00~) 전이다 — run 마지막 `stage.py close`가 돈다(2026-10-15)
        blocks.append("### 미국 주간거래 손절 점검\n\n주간거래 창 밖(10:00 전) — run 마지막 `python3 stage.py close`에서 돈다\n")
    # 실계좌 조회 전용 점검(2026-10-15 · 실전 점검표 ⑦) — 전환 주간(그림자 점검이 켜진 동안)과 실계좌 모드에서 run마다 한 번.
    #   잔고·통합증거금 구매력 칸·체결조회 TR이 run 시각에 정상인지 본다(주문 없음 · 토큰은 모의 캐시와 분리).
    try:
        _lim = json.loads((PROJ / "config" / "limits.json").read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        _lim = {}
    if _lim.get("real_shadow_check") or _mode != "paper":
        blocks.append(run(["real_probe.py", "--stamp", st, "--no-paper"],
                          "실계좌 조회 전용 점검 (real_probe.py — 주문 없음 · 실전 점검표 ⑦)"))
    # ★ **이미 가진 것**을 캡처에 박는다. 단계를 쪼개면서 "어제 그 파일을 받았다"를
    #   기억하던 경로가 사라졌는데 대체 경로를 안 만들었다 — 이 목록이 그 경로다.
    #   *실사례(2026-09-10): 3단이 "KRX 정기변경 명단 미확보 → 조사불가"로 닫았는데
    #   그 명단이 하루 전 받아둔 `_raw_sources/`에 있었고, 지목된 두 종목은 유니버스
    #   안이었으며 종가는 +7.26%·+2.00%였다.*
    blocks.append(run(["corpus.py", "index", "--brief"], "로컬 코퍼스 색인 (corpus.py — 이미 가진 자료 · 전체는 corpus.py index, 내용은 search)"))
    # ★ 밀린 세션을 캡처에 박는다. 안 보이면 '안 한 것'과 '없었던 것'이 구분되지 않고,
    #   반쪽으로 죽은 run의 회고가 아무 데도 이어지지 않는다(미국 9/8·9/9가 그랬다).
    blocks.append(run(["sessions.py", "due", "--market", a.market, "--stamp", st],
                      "밀린 일과 (sessions.py — 반쪽·미실행·시각 이탈)"))
    # 집행 가능한 시각인가 · 수집 창이 간극을 덮는가 — 둘 다 캡처에 못 박는다.
    blocks.append(_block(lambda: market_window_block(a.market), "집행 가능 시각"))
    blocks.append(_block(lambda: watch_loop_block(a.market), "장중 감시"))
    blocks.append(_block(tests_block, "테스트 완주 기록"))
    blocks.append(_block(lambda: news_window_block(a.market, a.days, st), "수집 창"))
    try:                                            # ★ 필수 — 섹터 보드(전수 로스터)
        board_txt, board_md, roster = build_board(a.market, a.quick, record=not a.no_record)
        blocks.append(StepResult("### 섹터 보드 (market_map.board — 전수)\n\n```\n" + board_txt.rstrip() + "\n```\n",
                                 name="섹터 보드", required=True))
    except db.TestGuard:
        raise
    except Exception as e:                          # noqa: BLE001
        board_txt, board_md, roster = "", "", 0
        blocks.append(StepResult(f"### 섹터 보드\n\n★ 실패 — {type(e).__name__}: {e}\n", name="섹터 보드", rc=1, required=True,
                                 note=f"{type(e).__name__}: {str(e)[:160]}"))
    # ★ 자본 기준 블록을 캡처에도 박는다 — 노트 머리말의 자본 줄 숫자(합산 자산·1%·환율)는 이 파일이
    #   출처여야 `verify_numbers`가 통과한다(2026-09-16 KR run이 이것 때문에 34건 미확인으로 4회 반복). 필수 — 못 재면 크기의 근거가 없다.
    _cap = capital_line(a.market, st)
    blocks.append(StepResult("### 자본 기준 (risk_guard.portfolio_equity — 노트 머리말 자본 줄의 출처)\n\n" + _cap + "\n",
                             name="자본 기준", rc=1 if "못 쟀다" in _cap else 0, required=True,
                             note=_cap[:160] if "못 쟀다" in _cap else ""))
    # ★ 일봉 확정(v5 · U04) — 장중에 받은 일봉(손절 감시·보드)을 마감 뒤 다시 관측해 확정한다(국내 09:30 run은 미국 마감 뒤 ·
    #   미국 22:35 run은 국내 마감 뒤). 휴장일 사본도 맞춘다 · 커버리지가 12주 안이면 ★. 선택 — 실패해도 확정 읽기는 미확정 행을 안 쓴다.
    blocks.append(_block(_finalize_block, "일봉 확정·휴장일 표"))
    # ★ 국면 판단 재료 — 배분 판단(allocation.regime)이 인용할 숫자를 기계로 넣는다(2026-09-22).
    #   벤치마크 5·20·60일 수익률과 20/60일선 대비 위치 · 보드 상승/하락 섹터 수. 모델은 이 숫자를 인용해 국면을 적는다.
    blocks.append(_block(lambda: "### 국면 재료 (regime_inputs — allocation.regime이 인용하는 숫자)\n\n```\n"
                         + regime_inputs(a.market, board_txt) + "\n```\n", "국면 재료"))
    # ★ 요인별 노출(FB-261012-4) — 축을 나눠도 요인은 하나일 수 있다. 최대 요인이 꺾이는 가지를 §7에 두게 한다.
    blocks.append(run(["market_map.py", "exposure", "--by", "driver", "--market", a.market, "--stamp", st,
                       "--out", str((EVID / f"exposure_{st}_{a.market}.md").relative_to(PROJ))],
                      "요인별 노출·요인 상태 (market_map.py exposure — 양 시장 합산 · 최대 요인 '꺾이면' 행은 §7 · "
                      "꺾인 요인은 §7 [요인 판정])"))

    try:                                            # ★ 필수 — 유니버스 표(§6 전수)
        uni_md, uni_n = build_universe(a.market, st)
        blocks.append(StepResult("", name="유니버스 표", required=True))
    except db.TestGuard:
        raise
    except Exception as e:                          # noqa: BLE001
        uni_md, uni_n = "", 0
        blocks.append(StepResult(f"### 유니버스 표\n\n★ 실패 — {type(e).__name__}: {e}\n", name="유니버스 표", rc=1,
                                 required=True, note=f"{type(e).__name__}: {str(e)[:160]}"))
    steps = [b_.step() for b_ in blocks if isinstance(b_, StepResult) and b_.name]
    status = db.stage_status(steps)
    lim = [f"[제한·1단] {x['name']} — {x['note'] or x['status']}" for x in steps if not x["required"] and x["status"] != "ok"]
    failed = [f"{x['name']} — {x['note'] or '종료코드 ' + str(x['rc'])}" for x in steps if x["required"] and x["status"] == "failed"]

    evid = EVID / f"tools_{st}_{a.market}.md"
    write_preserving(
        evid,
        f"# 도구 출력 캡처 — {st} · {a.market.upper()}\n\n"
        f"> [계좌] {_mode} — " + {"paper": "모의계좌", "real_readonly": "실계좌 조회 전용(주문 dry-run까지)",
                                  "real": "실계좌 주문"}.get(_mode, _mode) + "\n>\n"
        f"> **이 run 숫자의 유일한 출처.** 장중에는 부를 때마다 값이 달라져 노트 안에서\n"
        f"> 숫자끼리 어긋난다. 이후 단계는 브로커를 다시 부르지 않고 이 파일을 읽는다.\n"
        f"> 캡처 시각 {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST · 단계 {status}\n"
        + "".join(f"> ★ [1단 실패] {x}\n" for x in failed) + "".join(f"> {x}\n" for x in lim) + "\n"
        + "\n".join(blocks), "도구 출력 캡처")
    if status == "failed":
        # ★ 필수 단계 실패 — 도구 파일은 사실대로 남기고(실패 블록 포함) 노트 뼈대·산출물 기록은 하지 않는다. 종료 2(U02).
        wl = JOURNAL / f"worklog_{st}_{a.market}.md"
        try:
            with wl.open("a", encoding="utf-8") as fh:
                fh.write(f"\n- ★ 1단 실패 {datetime.now(KST):%H:%M} — " + " · ".join(failed) + " · capture를 다시 돌린다\n")
        except OSError:
            pass
        db.stage_finish(sid, "failed", 2, steps, None, " · ".join(failed)[:500])
        print(f"[중단] 1단 필수 단계 실패 — {' · '.join(failed)} · 캡처 {rel(evid)}(실패 기록) · capture를 다시 돌려라", file=sys.stderr)
        return 2

    wl = JOURNAL / f"worklog_{st}_{a.market}.md"
    if not wl.exists():
        wl.write_text(
            f"# 작업 기록 — {st} · {a.market.upper()}\n\n"
            f"> **각 단계가 자기가 한 일을 여기에 append한다.** 노트는 *판단*을 담고,\n"
            f"> 이 파일은 *과정*을 담는다 — 무엇을 조사했고, 무엇을 기각했고, 왜 그랬는지.\n"
            f"> 기각한 것을 안 남기면 다음 run이 같은 것을 다시 파고, 이미 버린 것을 다시 검토한다.\n\n"
            f"---\n\n## 1단 · 수집·측정\n\n"
            f"- 재료: `data/material_{st}_{a.market}.md`\n"
            f"- 캡처: `data/run_evidence/tools_{st}_{a.market}.md`\n"
            f"- 유니버스 {uni_n}종목을 §6 표로 깔았다\n\n{stamp('작업기록-capture')}\n",
            encoding="utf-8")

    note = ANALYSIS / f"분석노트_{st}_{a.market}_v{a.rev}_0.md"
    if note.exists() and not a.force:
        print(f"[유지] {note.name} 이미 있음 — 뼈대를 덮어쓰지 않는다(--force로 강제).", file=sys.stderr)
    else:
        sk = note_skeleton(a.market, st, evid, board_md, uni_md, cap_line=_cap)
        if lim:
            # 선택 수집 실패 → §10 한계에 [제한·1단] 줄(그 자료에 기대는 판단만 제한된다 · 노트가 이 줄을 지우지 않는다)
            sk = re.sub(r"(\n## §10[^\n]*\n)", lambda m_: m_.group(1) + "\n" + "\n".join(f"- {x}" for x in lim) + "\n", sk, count=1)
        note.write_text(sk, encoding="utf-8")

    prev_mat = latest_stamped(PROJ / "data", "material", a.market, ".md", exclude_stamp=st)
    cur = PROJ / "data" / f"material_{st}_{a.market}.md"
    warn = ""
    if prev_mat and cur.exists() and cur.stat().st_size < prev_mat.stat().st_size * MATERIAL_SHRINK_FLOOR:
        warn = (f"\n⚠ 재료가 직전({prev_mat.name} {prev_mat.stat().st_size:,}B)보다 "
                f"{cur.stat().st_size:,}B로 크게 줄었다 — `--with-news` 누락을 의심하고 "
                f"원인부터 확인하라. 그대로 진행하면 얇아진 재료 위에서 판단하게 된다.")

    db.stage_finish(sid, status, 0, steps, {"snapshot": snap, "material": cur, "tools": evid, "note_skeleton": note, "worklog": wl},
                    " · ".join(lim)[:500])
    print(f"\n캡처 {rel(evid)}  ·  노트 뼈대 {rel(note)}  ·  1단 {status}" + (f"(제한 {len(lim)}건 — 노트 §10)" if lim else ""))
    print(f"로스터 {roster}행 · 유니버스 {uni_n}종목 · 작업기록 {rel(wl)}")
    today = session_iso(st, a.market)                 # 게이트 인자 today = 이 run의 세션 날짜(현지 거래일 · 2026-10-14)
    print(f"게이트 인자: roster={roster} universe={uni_n} today={today} "
          f"deliverable={evid} note={note} worklog={wl}")
    print(f"  4단에서 쓸 검증 로그 경로: {EVID / f'verify_{st}_{a.market}.txt'}")
    print(f"  5단에서 쓸 시그널 경로:   {PROJ / 'signals' / f'signal_{st}_{a.market}.json'}"
          f"{warn}")
    return 0


# ─────────────────────────────────────────────────────────── carry

def last_session(market: str, st: str = "") -> str:
    """회고 대상 = 그 시장의 직전 세션.

    **시그널이 있는 날짜만 고른다** — `review.py`가 채점하는 대상이 시그널(그날의 결정)이라,
    거래 기록만 있는 날짜를 넘기면 "회고할 판단이 없다"로 실패한다.
    직전 날짜에 시그널이 없으면 **오늘 것이라도 가장 최근 시그널**을 쓴다 — 회고 자체를
    건너뛰는 것보다, 결과 판정을 보류하고 근거를 채점하는 편이 낫다.
    """
    stamps = sorted({p.stem.split("_")[1] for p in (PROJ / "signals").glob(f"signal_*_{market}.json")})
    today = st or stamp_today(market)
    if not stamps:
        return session_iso(today, market)
    past = [s for s in stamps if s < today]
    src = past[-1] if past else stamps[-1]
    return session_iso(src, market)             # 스탬프가 아니라 그 run의 세션 날짜(전환일 전 미국 스탬프는 하루 뒤였다)


def prev_outputs(pv: dict) -> str:
    """직전 run이 **무엇을 남겼는가** — 노트 발췌 · 리서치 · 지도 diff.

    ★ 왜 필요한가 — 지금까지 이어받기는 **직전 시그널 JSON만** 읽었다. 그 run이 쓴
    노트 90KB · 섹터 리서치 · 출처 원장은 `corpus.py index` 목록에만 뜨고 아무도 열지
    않았다. 판단의 결론은 시그널에 실리지만 **판단의 재료와 미결 사항은 노트에 있다.**
    """
    mkt, sess = pv.get("market") or "", pv.get("session_date") or ""
    if not (mkt and sess):
        return "### 직전 run 산출\n\n```\n직전 run이 없다 — 이어받을 산출물도 없다.\n```\n"
    st = sess[2:].replace("-", "")
    out = [f"### 직전 run 산출 — {mkt.upper()} {sess}\n"]

    notes = sorted((PROJ / "analysis").glob(f"분석노트_{st}_{mkt}_v*.md"))
    if notes:
        txt = notes[-1].read_text(encoding="utf-8", errors="ignore")
        out.append(f"**노트** `{rel(notes[-1])}` ({len(txt):,}자)\n")
        for title, key in (("§0 판정", "§0 오늘의 판정"), ("§2 거시 축", "§2 거시 축"),
                           ("§5 일정", "§5 일정"), ("§11 집행", "§11 집행 결과"),
                           ("§10 한계", "§10 한계")):
            body = _section(txt, key)
            if body:
                out.append(f"<details><summary>{title}</summary>\n\n{body[:1400]}\n\n</details>\n")
    else:
        out.append("**노트를 못 찾았다** — 그 run이 4단까지 못 갔다는 뜻이다.\n")

    res = [f for f in sorted((PROJ / "analysis").glob("*.md"))
           if st in f.name and not f.name.startswith(("분석노트", "AI클레임"))]
    out.append("**그 run이 만든 분석 파일**\n" + ("".join(f"- `{rel(f)}`\n" for f in res)
               if res else "- 없음\n"))

    # 지도·일정 diff — 그 run이 새로 넣은 축·일정이 오늘 노트에 등장해야 한다.
    added_ax = [a.get("id") for a in db.market_map_load().get("axes", [])
                if str(a.get("updated") or "").replace("-", "")[2:] == st]
    added_ev = [f"{e.get('date')} {e.get('event')}"
                for e in db.calendar_all()
                if str(e.get("added") or "").replace("-", "")[2:] == st]
    out.append(f"**그 run이 지도에 더한 것** — 축 {len(added_ax)}개"
               + (f" ({', '.join(added_ax)})" if added_ax else "")
               + f" · 일정 {len(added_ev)}건"
               + (f"\n" + "".join(f"  - {e}\n" for e in added_ev[:6]) if added_ev else "\n"))
    out.append("\n> **이 축·일정·리서치가 오늘 노트 §2·§5에 등장해야 한다** — "
               "등장하지 않으면 그 run의 조사가 하루 만에 버려진 것이다. "
               "`crosscheck.py`가 이름으로 대조한다.\n")
    return "\n".join(out)


def _section(txt: str, header: str) -> str:
    """노트에서 `## <header>` 절 본문만 뽑는다(다음 `## `까지)."""
    i = txt.find(f"## {header}")
    if i < 0:
        return ""
    j = txt.find("\n## ", i + 3)
    body = txt[i:j if j > 0 else len(txt)]
    body = "\n".join(l for l in body.splitlines()[1:] if not l.strip().startswith("<!--"))
    return body.strip()


PRICE_REJECT_WORDS = ("목표 여유 없음", "상승 여지 바닥 미달", "추격", "급등", "이미 반영", "목표가 평균 상회", "이미 오른")


def reeval_list(market: str, st: str) -> list:
    """★ FB-261003-1 — 직전 같은 시장 run이 **가격을 이유로** 거른 종목. [(ticker, 출처, 사유)].

    '오늘 급등해서 안 산다'는 그날 가격에만 맞는 판단인데, 한 번 거르면 다시 평가하는 일정이 없었다
    (09-30 '목표가 평균 상회'로 거른 장비주가 그 뒤 +51%). 출처는 둘: 시그널 `rejected_candidates`(kind=price)와
    approved `rejected`의 가격 사유."""
    out, seen = [], set()
    sig_f = latest_stamped(PROJ / "signals", "signal", market, ".json", exclude_stamp=st)
    if sig_f:
        try:
            d = json.loads(sig_f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            d = {}
        for r in d.get("rejected_candidates") or []:
            if str(r.get("kind") or "") == "price" and r.get("ticker") and r["ticker"] not in seen:
                seen.add(r["ticker"]); out.append((r["ticker"], sig_f.name, str(r.get("why") or "")[:90]))
    ap_f = latest_stamped(PROJ / "signals", "approved", market, ".json", exclude_stamp=st)
    if ap_f:
        try:
            d = json.loads(ap_f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            d = {}
        for r in d.get("rejected") or []:
            why = str(r.get("why") or "")
            if r.get("ticker") and r["ticker"] not in seen and any(w in why for w in PRICE_REJECT_WORDS):
                seen.add(r["ticker"]); out.append((r["ticker"], ap_f.name, why[:90]))
    return out


def queue_one_day(market: str, st: str) -> list:
    """★ FB-261010-6(2026-10-11 사용자 승인) — 유니버스 대기열에서 **하루만 제안된** 후보. [(ticker, 이름, 제안일, 축, 기다린 날)].

    편입은 서로 다른 이틀 제안이 있어야 한다(냉각 — 뉴스레터 인젝션 방어). 그런데 하루만 제안되고 다시 언급되지 않으면
    그 후보는 대기열에 묻혀 아무도 다시 보지 않았다(09-23 하루 제안된 수혜주 둘이 2주 넘게 멈춘 사이 +21~33%).
    그래서 매 run이 이 목록을 보고 '오늘 재료 근거로 재제안' 또는 '[내림] 이유'를 적는다. 자동 재제안은 하지 않는다."""
    q = db.pending_load()                                  # 유니버스 대기열(저장소 op_pending)
    out = []
    today = datetime.strptime("20" + st, "%Y%m%d")
    for key, v in q.items():
        mk, _, tk = str(key).partition(":")
        if mk.upper() != market.upper() or not tk:
            continue
        dates = sorted(set(v.get("dates") or []))
        if len(dates) != 1:
            continue
        try:
            waited = (today - datetime.strptime(dates[0], "%Y-%m-%d")).days
        except ValueError:
            waited = -1
        out.append((tk, str(v.get("name") or ""), dates[0], str(v.get("axis") or ""), waited))
    return sorted(out, key=lambda r: -r[4])


def capture_ok(st: str, market: str):
    """2·3단의 전제(v4 · Codex U02) — 이 run의 capture가 성공(ok·reduced)했고 그때 기록한 스냅샷·도구 파일이 지금 파일과 같은가.
    파일이 있다는 것만으로는 안 된다(실패한 capture가 남긴 옛 스냅샷 위에서 돌던 결함). 반환 오류 문장 | None."""
    rid = db.run_id(st, market)
    if not db.stage_last_ok(rid, "capture"):
        return "capture 성공 기록이 없다"
    for role, path in (("snapshot", PROJ / "data" / f"snapshot_{st}_{market}.json"), ("tools", EVID / f"tools_{st}_{market}.md")):
        if not db.stage_artifact_matches(rid, "capture", role, path):
            return f"capture 산출물({role} · {path.name})이 바뀌었거나 없다"
    return None


def _staged(st: str, market: str, stage: str, body, artifacts=None, need_capture: bool = True) -> int:
    """단계 하나를 단계 결과 행으로 감싼다 — 시작 행 → (전제) → 본문(steps에 선택 실패를 적는다) → 끝 행(모든 반환 경로)."""
    sid = db.stage_start(st, market, stage)
    if need_capture:
        err = capture_ok(st, market)
        if err:
            db.stage_finish(sid, "failed", 2, [], None, f"전제 미충족 — {err}")
            print(f"[중단] {err} — capture를 다시 돌려라({STAGE_HINT.get(stage, stage)}은 capture 성공 기록과 그 산출물 위에서만 돈다)",
                  file=sys.stderr)
            return 2
    steps = []
    try:
        rc = body(steps)
    except BaseException as e:
        db.stage_finish(sid, "failed", 1, steps, None, f"{type(e).__name__}: {str(e)[:200]}")
        raise
    status = "failed" if rc else db.stage_status(steps)
    db.stage_finish(sid, status, rc, steps, (artifacts() if artifacts else None) if status != "failed" else None)
    return rc


STAGE_HINT = {"carry": "이어받기", "map": "지도·리서치"}


def cmd_carry(a) -> int:
    st = a.stamp or stamp_today(a.market)
    return _staged(st, a.market, "carry", lambda steps: _carry(a, st, steps),
                   artifacts=lambda: {"carry": JOURNAL / f"carry_{st}_{a.market}.md"})


def _carry(a, st, steps) -> int:
    _keep_pointer(a.market, st)
    JOURNAL.mkdir(parents=True, exist_ok=True)
    snap = PROJ / "data" / f"snapshot_{st}_{a.market}.json"
    if not snap.exists():
        print(f"[중단] {snap.name}이 없다 — 1단(capture)을 먼저 돌려라.", file=sys.stderr)
        return 2

    # ★ 회고 대상은 **시간순 직전 run**이다 — 시장에서 유도하지 않는다.
    #   예전에는 `other = "us" if market == "kr" else "kr"`로 반대편 시장을 집었고,
    #   그것은 **엄격한 교대 실행을 전제**한다. run은 빠진다(아침도 저녁도).
    #   *실측(2026-09-11): run 10개 중 **8개가 회고된 적이 없다.** 오늘 국내 run은
    #   어젯밤 막힌 미국 9/11이 아니라 미국 9/10을 회고했다 — 직전 run을 건너뛴 것이다.*
    sys.path.insert(0, str(PROJ))
    import sessions as _sess
    pv = _sess.prev_run(14, a.market, st)
    # ★ 2026-09-14: 회고 대상을 "반대편 시장"에서 "시간순 직전 run"으로 바꾼 개정에서
    #   `other` 참조 3곳이 남아 NameError로 carry가 죽었다. 직전 run의 시장(prev_mkt)으로 통일한다.
    prev_mkt = pv.get("market") or ""
    # review.py는 세션 인자로 **파일을 찾는다**(스탬프) — 거래소 기준일이 아니라 스탬프 날짜를 넘긴다
    # (US 260919 run = ET 09-18 세션: 파일은 260919).
    sess = a.session or (pv.get("stamp_date") or pv.get("session_date") or "")
    same_market = bool(prev_mkt) and prev_mkt == a.market

    blocks = [
        run(["sessions.py", "prev", "--market", a.market, "--stamp", st],
            "직전 run — 언제·어떻게 돌았나 (회고 대상)"),
        # ★ 배분 판단은 원장으로 이어받는다(2026-09-22) — 직전 판단 블록에서 시작하고, 바꿀 때만 이유를 적는다.
        run(["allocation.py", "prev"], "배분 판단 — 직전 (allocation.py prev · 이번 시그널 allocation.based_on의 출처)"),
        run(["theses.py", "check", "--snapshot", str(snap.relative_to(PROJ)),
             "--market", a.market.upper()], "트리거 점검 (theses.py check) — 재료보다 먼저"),
    ]
    if prev_mkt and sess:
        blocks.append(run(["review.py", "--market", prev_mkt, "--session", sess, "--record"],
                          f"직전 run 회고 (review.py · {prev_mkt.upper()} {sess})"))
    else:
        # 폴백으로 오늘 것을 회고하지 않는다 — 자기 회고는 회고가 아니다.
        blocks.append("### 직전 run 회고\n\n```\n직전 run이 없다(최근 14일).\n"
                      "오늘 것으로 대신하지 않는다 — 그 사실을 노트 §1-A에 그대로 적는다.\n```\n")
    blocks.append(prev_outputs(pv))
    if prev_mkt and not same_market:
        blocks.append(run(["market_map.py", "bridge", "--closed", prev_mkt, "--record", "--score"],
                          f"대응 매핑 + 직전 예측 채점 (bridge --closed {prev_mkt})"))
    else:
        # 직전 run이 같은 시장이면 건너뛸 짝이 없다. **그 사실을 적는다**(침묵 금지).
        blocks.append("### 대응 매핑\n\n```\n"
                      + (f"직전 run이 같은 시장({a.market.upper()})이라 교차 매핑을 건너뛴다.\n"
                         "반대편 회고는 그 시장 run이 돌 때 이어받는다.\n"
                         if same_market else "직전 run이 없어 매핑할 것이 없다.\n")
                      + "```\n")
    # ★ 교차 시장 입력(2026-10-12 사용자: "국내장과 미국장은 독립적이지 않다") — 반대 시장이 이 시장 직전 run 이후 **판단을 바꾼 것**
    #   (전망 갱신·반증 고리·이쪽 대응이 빈 시나리오·손절 발화·요인별 방향). `bridge`는 가격만 넘기므로 같은 시장 직전이어도 돈다.
    blocks.append(run(["market_map.py", "xmarket", "--market", a.market, "--stamp", st,
                       "--out", str((EVID / f"xmarket_{st}_{a.market}.md").relative_to(PROJ))],
                      "교차 시장 입력 (xmarket — 저쪽이 바꾼 전망·반증·대응 빈 시나리오 → 노트 §1-B ④)"))
    blocks += [
        run(["market_map.py", "cycle", "--market", a.market],
            "순환 슬롯 (cycle) — 지난 관찰의 사후 · 이월 · 다음 후보"),
        # ★ 시장 필터 없이 **열린 것 전부**. 조건은 시장에 속하지 않는다 —
        #   미국 조건이 국내 대응을 지시하는 일이 실제로 있다("KR Samsung ladder e1 fires").
        run(["scenarios.py", "list", "--open", "--brief"],
            "열린 시나리오 (전 시장 — 조건은 시장에 속하지 않는다 · 이력은 `scenarios.py list --id`)"),
        # ★ 오래 열린 시나리오(FB-261012-1) — 예정일이 지나도 실현·반증되지 않은 가지 = 지연·무소식 가지가 없었다.
        run(["scenarios.py", "list", "--stale"],
            "오래 열린 시나리오 (scenarios.py list --stale — 지연 가지를 세우거나 `judge --expire`로 닫는다)"),
        # ★ 가지 완결성(v6 · J02) — 열린 사건마다 지표 공간을 가지들이 다 덮나(미커버·지연 가지·대응 다른 겹침) · 종료 3이면 그 사건에 가지를 더한다
        run(["scenarios.py", "check", "--open"],
            "가지 완결성 (scenarios.py check — 사건의 지표 공간을 가지가 다 덮나 · delayed·else 행)", ok_rc=(0, 3)),
        run(["scenarios.py", "audit", "--days", "14"],
            "회피 감사 (scenarios.py audit — 실현됐는데 대응했나)"),
        # ★ 실현됐는데 대응이 안 끝난 것 — "미국 조건 → 국내 대응"의 기계 경로다.
        run(["scenarios.py", "due", "--market", a.market],
            "집행 대기 (scenarios.py due — 실현됐는데 대응이 안 끝난 것)"),
        # ★ 조사 전에 연다 — 반대편 run이 이미 세운 as-of를 다시 파지 않게(저장소 사실 층 · facts.reuse).
        reuse_block(a.market, st),
    ]
    prev_sig_f = latest_stamped(PROJ / "signals", "signal", a.market, ".json", exclude_stamp=st)
    sc = "(직전 시그널 없음 — 첫 run이다)"
    if prev_sig_f:
        prev_sig = [prev_sig_f]
        d = json.loads(prev_sig[-1].read_text(encoding="utf-8"))
        rows = d.get("scenarios") or []
        sc = (f"출처 `{prev_sig[-1].name}` · {len(rows)}건\n\n"
              "| # | 조건 | 정해둔 대응 | 실현? | 이번 run에서 한 일 |\n|---|---|---|---|---|\n"
              + "\n".join(
                  f"| {i+1} | {str(r.get('condition') or r.get('if') or r)[:80]} "
                  f"| {str(r.get('action') or r.get('then') or '')[:60]} | ☐ | |"
                  for i, r in enumerate(rows))) if rows else \
             f"출처 `{prev_sig[-1].name}` · scenarios 0건 (직전 run이 무엇을 기다리는지 안 적었다)"

    rv = reeval_list(a.market, st)
    rv_md = ("| 종목 | 출처 | 거른 사유 | 오늘 판정(논지 id 또는 [통과] 사유) |\n|---|---|---|---|\n"
             + "\n".join(f"| {tk} | `{src}` | {why.replace('|', '·')} | |" for tk, src, why in rv)) if rv else \
        "(직전 run이 가격을 이유로 거른 종목 없음)"
    q1 = queue_one_day(a.market, st)
    q1_md = ("| 종목 | 제안일 | 연결 축 | 기다린 날 | 오늘 판정(재제안 근거 또는 [내림] 이유) |\n|---|---|---|---|---|\n"
             + "\n".join(f"| {tk} {nm} | {d} | {ax or '—'} | {w}일 | |" for tk, nm, d, ax, w in q1)) if q1 else \
        "(하루만 제안된 대기 후보 없음)"
    # 감사 소급(2026-10-13) — 직전 run의 주문 뒤 감사가 매수 근거를 뒤집어 아직 못 판 것. 이 run의 risk_guard가 자동으로 판다.
    try:
        rev_md = reversal_block(a.market)
        import facts as _f                        # 직전 run이 닫힌 뒤 도착한 주문 근거 감사(2026-10-14 · 배경 감사 미수신의 안전망)
        rev_md += _f.carry_note(a.market, st)
    except db.TestGuard:
        raise
    except Exception as e:                        # noqa: BLE001
        rev_md = f"## 감사 소급\n\n★ 원장을 읽지 못했다 — {type(e).__name__}: {e}\n"
        steps.append({"name": "감사 소급", "required": False, "status": "reduced", "note": f"원장 읽기 실패 — {type(e).__name__}"})
    # 지지 이탈(2026-10-14 · 차트스쿨 01 B) — 지지 구간 기준 보유의 직전 세션 종가가 구간 아래로 끝났으면 오늘 무효 조건을 재판정한다.
    try:
        import support_break as _sb
        sup_md = _sb.carry_block(a.market, snap)
    except Exception as e:                        # noqa: BLE001
        sup_md = f"## 지지 이탈\n\n★ 판정하지 못했다 — {type(e).__name__}: {e} · 노트 §10 한계에 적는다\n"
        steps.append({"name": "지지 이탈", "required": False, "status": "reduced", "note": f"판정 실패 — {type(e).__name__}"})
    # 재투자 대기(2026-10-15) — 직전 run의 매도 전용 pass가 판 돈이 재투자되지 않고 남은 것. 이 run의 gap에 자연히 들어가지만
    #   왜 남았는지(미룬 사유)를 §1-A에 적고 이번 run에서 채운다.
    try:
        ref_md = refill_block(a.market, st)
    except Exception as e:                        # noqa: BLE001
        ref_md = f"## 재투자 대기\n\n★ 읽지 못했다 — {type(e).__name__}: {e}\n"
        steps.append({"name": "재투자 대기", "required": False, "status": "reduced", "note": f"읽기 실패 — {type(e).__name__}"})
    out = JOURNAL / f"carry_{st}_{a.market}.md"
    write_preserving(
        out,
        f"# 이어받기 — {st} · {a.market.upper()} 준비 / {(prev_mkt or '?').upper()} 회고\n\n"
        f"> 회고는 채점표이기 전에 **오늘 준비의 첫 입력**이다. 여기서 나온 것이\n"
        f"> 노트 §1-A·§1-B로 들어가야 오늘 판단에 반영된다 — 저널에만 남기면 영향을 못 준다.\n\n"
        f"## 직전 시그널 `scenarios` — 전건 판정\n\n{sc}\n\n"
        f"**판정하지 않고 넘긴 조건이 있으면 이 단계는 실패다.** 실현됐는데 대응하지 않았으면\n"
        f"그건 판단이 아니라 연기다.\n\n"
        f"## 재평가 목록 — 직전 run이 가격을 이유로 거른 종목\n\n{rv_md}\n\n"
        f"**급등 당일 매수 금지는 그 세션에만 적용한다.** 이 목록의 종목은 오늘 노트 §6에 논지 id 또는 `[통과] <이유>`로\n"
        f"다시 판정한다 — '이미 올랐다'로 또 거르려면 기저율(자기 호흡 칸)이 기준선보다 나쁘다는 숫자를 적는다.\n"
        f"`crosscheck.py`가 목록의 이름이 노트에 들어갔는지 센다.\n\n"
        f"## 대기 후보(하루만 제안됨) — 다시 추천하거나 내린다\n\n{q1_md}\n\n"
        f"편입은 서로 다른 이틀 제안이 있어야 한다(냉각 — 인젝션 방어). 하루만 제안된 후보는 다시 언급하지 않으면 영영 묻힌다.\n"
        f"후보마다 노트 §8에 둘 중 하나를 적는다: **오늘 재료에 근거가 있으면** 시그널 `watchlist_candidates`로 재제안(근거 한 줄) ·\n"
        f"아니면 `[내림] <이유>`. 근거 없는 습관적 재제안은 금지 — 냉각이 무력해진다. `crosscheck.py`가 센다.\n\n"
        f"{rev_md}\n"
        f"{sup_md}\n"
        f"{ref_md}\n"
        f"## 미체결분 행선지\n\n"
        f"직전 run에서 접수만 되고 안 채워진 주문은 장 마감에 취소된다.\n"
        f"논지 트리거로 옮겼는지 / 대기열로 넘겼는지 적는다. 없었으면 '없음'이라고 적는다.\n\n"
        f"- \n\n"
        f"## 대응 매핑 채점\n\n"
        f"`bridge --score`가 채점하지 못하는 날(첫 기록·같은 방향 예측 없음)에도 **세는 것은\n"
        f"면제되지 않는다.** 오늘 보드와 손으로 대조해 `적중 n / 역행 n / 혼재 n`을 적는다 —\n"
        f"판정은 절대 등락 방향과 초과수익 방향이 둘 다 일치=적중, 둘 다 불일치=역행, 엇갈리면 혼재.\n\n"
        f"- 적중 n / 역행 n / 혼재 n\n\n---\n\n" + "\n".join(blocks)
        + f"\n\n## 중단·재개\n\n(이 run이 중간에 끊겨 재개한 것이면 어느 단계에서 왜 끊겼는지,\n"
          f"무엇을 다시 수집했는지 적는다. 아니면 '해당 없음'.)\n\n- 해당 없음\n",
        "이어받기 파일")
    wl = JOURNAL / f"worklog_{st}_{a.market}.md"
    # ★ 2026-10-01: 빈 뼈대에 ✓ 스탬프까지 찍어 붙였다 — 내용 없는 블록이 '완료' 표기를 가졌다.
    #   뼈대는 표기 없이 붙이고, 채운 뒤 `stage.py stamp --worklog <wl> --stage carry`로 찍는다(SKILL.md 규칙).
    if wl.exists() and "## 2단 · 이어받기" not in wl.read_text(encoding="utf-8"):
        with wl.open("a", encoding="utf-8") as fh:
            fh.write(f"\n---\n\n## 2단 · 이어받기\n\n"
                     f"- 회고 대상: {(prev_mkt or '?').upper()} {sess}\n"
                     f"- 도구 출력: `{rel(out)}`\n\n"
                     f"### 조사한 것\n\n- \n\n"
                     f"### 판단한 것과 근거\n\n- \n\n"
                     f"### 기각한 것과 이유\n\n- \n\n"
                     f"### 3단으로 넘기는 것\n\n- \n\n"
                     f"<!-- 다 채운 뒤: python3 stage.py stamp --worklog {rel(wl)} --stage carry -->\n")

    note = ANALYSIS / f"분석노트_{st}_{a.market}_v{a.rev}_0.md"
    print(f"\n이어받기 {rel(out)}  (회고 세션 {(prev_mkt or '?').upper()} {sess})")
    print(f"게이트 인자: deliverable={out} note={note} worklog={wl}")
    return 0


def gen_forecast_line(n_updated: int, n_unpriced: int, n_blank: int) -> str:
    """3단 전망 요약 줄 — 생성 계약. `map` 게이트가 글자 그대로 찾는다.

    ★ 왜 — "전망을 세웠다"는 각서는 언제나 Y다. 세운 **건수**와 미반영 수혜 **건수**를 파일에
    박아야 0건인 날이 침묵이 아니라 행으로 남는다(섹터 보드의 '미조사도 행으로'와 같은 수법).
    """
    return (f"전망 갱신 **{n_updated}건** · 미반영 수혜 **{n_unpriced}건** · "
            f"전망 미기입 축 {n_blank}개")


def gen_fill_line(summ: dict) -> str:
    """§11 체결 확정 줄 — 생성 계약. `fill.py`가 쓰고 `execute` 게이트가 `미확정 **0건**`을 찾는다.

    ★ 왜 — "체결을 확인했다"는 각서는 언제나 Y다. 전송·체결·취소·만료·거부 **건수**와 미확정
    건수를 파일에 박아야, 접수만 되고 남은 주문이 침묵이 아니라 숫자로 남는다.
    *실사례(2026-09-15 VST): 접수 상태로 게이트를 통과하고 run을 닫았다 — 이 줄이 없었다.*
    """
    at = str(summ.get("at", ""))[11:16]
    inc = summ.get("incidents") or []
    # ★ `승인 초과 **N건**`은 2026-09-21 사고(승인 46/집행 92가 게이트를 통과) 뒤 추가 — 종목·방향별
    #   집행 합이 승인 합을 넘으면 `fill.finalize`가 사고를 기록하고 그 id가 여기 붙는다. 게이트는
    #   "초과 0건" 또는 "사고 기록 INC-"만 통과시킨다.
    return (f"<!-- gen:fill -->체결 확정 — 전송 {summ.get('sent', 0)} · 체결 {summ.get('filled', 0)} · "
            f"부분 {summ.get('partial', 0)} · 취소 {summ.get('cancelled', 0)} · "
            f"만료 {summ.get('expired', 0)} · 거부 {summ.get('rejected', 0)} · "
            f"미확정 **{summ.get('open_orders', 0)}건** · 승인 초과 **{summ.get('overfill', 0)}건**"
            + (f" · 사고 기록 {', '.join(inc)}" if inc else "") + (f" ({at} 확인)" if at else ""))


def cmd_map(a) -> int:
    """3단 뼈대 — **전망·리서치**. 지도를 앞을 보는 그림으로 바꾸는 자리다.

    산출 `analysis/전망_<stamp>_<mkt>.md`: 열린 전망 현황 + 이번 run이 채울 표(1차/2차/3차 수혜)
    + 요약 생성줄. 3단이 `forecast add`·`priced`로 채운 뒤 `map` 게이트에 이 파일을 낸다.
    """
    st = a.stamp or stamp_today(a.market)
    return _staged(st, a.market, "map", lambda steps: _map(a, st, steps),
                   artifacts=lambda: {"forecast": ANALYSIS / f"전망_{st}_{a.market}.md"})


def _map(a, st, steps) -> int:
    _keep_pointer(a.market, st)
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(PROJ))
    import market_map as mm
    today = session_iso(st, a.market)                 # 축 updated는 run 포인터의 세션 날짜로 적힌다(market_map)
    # ★ 미반영은 이번 세션 측정만 센다(2026-10-19 · Codex U05) — 세기 전에 열린 방향 축을 잰다(사람 단계 없음 · 일봉은 저장소 캐시).
    #   --refresh면 이번 세션에 갱신된 축만 다시 잰다. 측정이 실패한 축은 '실패'로 보이고 미반영에 안 든다.
    m0 = db.market_map_load()
    ids = None
    if a.refresh:
        ids = [x["id"] for x in m0.get("axes") or [] if x.get("direction") and (x.get("updated") or "") == today
               and x.get("beneficiaries") and (x.get("status") or "open") == "open"]
    meas = mm.priced_measure_all(ids)
    steps.append({"name": "후보 측정(priced_measure_all)", "required": False,
                  "status": "ok" if not meas.get("failed") else "reduced",
                  "note": f"축 {meas.get('axes')} · 완전 {meas.get('complete')} · 실패 {meas.get('failed')}"})
    m = db.market_map_load()
    axes = m.get("axes") or []
    updated = [x for x in axes if x.get("direction") and (x.get("updated") or "") == today]
    blank = [x for x in axes if not x.get("direction")]
    open_dir = [x for x in axes if x.get("direction") and (x.get("status") or "open") == "open"]
    unpriced = [(x["id"], b) for x in open_dir for b in (x.get("beneficiaries") or [])
                if b.get("priced") is False and mm.measure_state(b) == "valid"]
    line = gen_forecast_line(len(updated), len(unpriced), len(blank))
    ms_n = {"valid": 0, "stale": 0, "unavailable": 0, "none": 0}
    for x in open_dir:
        for b in x.get("beneficiaries") or []:
            ms_n[mm.measure_state(b)] += 1
    try:
        from kis_client import last_session_date
        basis = f"기준 KR {last_session_date('KR').isoformat()} · US {last_session_date('US').isoformat()}"
    except Exception:                                # noqa: BLE001
        basis = "기준 세션 미상"
    ms_line = (f"측정 상태: 유효 {ms_n['valid']} · 오래됨 {ms_n['stale']} · 실패 {ms_n['unavailable']} · 미측정 {ms_n['none']} ({basis})")

    out = [f"# 전망 — {st} · {a.market.upper()}", "",
           GEN_NOTICE, line, ms_line, "",
           "> **3단이 답하는 첫 질문**: 이 재료로 볼 때 4~12주 뒤 돈이 어디로 가는가. 그 사슬의",
           "> **2차·3차 수혜** 중 아직 값에 안 들어간 것은 무엇인가. 뉴스는 1차만 말한다 —",
           "> 뉴스가 1차를 말하는 날 2차를 적는 것이 이 시스템이 앞서는 방식이다.", "",
           "## 열린 전망", ""]
    open_fc = [x for x in axes if x.get("direction") and (x.get("status") or "open") == "open"]
    if not open_fc:
        out.append("*(열린 전망 없음 — 지도에 축은 있지만 방향·기간·수혜 순서가 없다. "
                   "아래 표를 채워 `market_map.py forecast add --file <json>`으로 세운다)*")
    for x in open_fc:
        cbn, why = mm.created_before_news(x)
        out.append(f"### {x['id']} — {x.get('name', '')}")
        out.append(f"- {x['direction']} · {x.get('horizon_weeks')}주 · conf {x.get('confidence')} · "
                   f"{x.get('expected', '')} · {'선행' if cbn else ('뉴스 후' if cbn is False else '?')} ({why})")
        out.append("")
        out.append("| 차수 | 종목 | 시장 | 유니버스 | 반영 | 측정 | 근거 |")
        out.append("|---|---|---|---|---|---|---|")
        for b in sorted(x.get("beneficiaries") or [], key=lambda b: b.get("order", 9)):
            out.append(f"| {b.get('order')}차 | {b.get('ticker')} {b.get('name', '')} | "
                       f"{b.get('market', '')} | {'안' if b.get('in_universe') else '밖'} | {mm.priced_label(b)} | "
                       f"{mm.measure_label(b)[:90]} | {(b.get('why') or '')[:60]} |")
        out.append("")
    # ★ 같은 요인의 두 시장 전망(2026-10-12) — 세우기 전에 반대 시장이 같은 요인을 어떻게 보는지 먼저 본다.
    xr = mm.xmarket(a.market, st)
    out += ["## 같은 요인의 두 시장 전망 — 세우기 전에 본다", "",
            "> 전망의 단위는 시장이 아니라 요인이다. 반대 방향이면 `conflicts_with`로 잇거나 `split_why`, 수혜 표가 한 시장뿐이면 "
            "반대 시장 수혜·피해를 넣거나 `no_cross_market_why` — `forecast add`가 기계로 거부한다.", "",
            "| 요인 | 축 | 방향 | 확신도 | 시장 | 상태 |", "|---|---|---|---|---|---|"]
    for d_, rows_ in sorted(xr["drivers"].items(), key=lambda kv: -len(kv[1])):
        for x_ in rows_:
            out.append(f"| {d_} | {x_['axis']} | {x_['direction']} | {x_['confidence']} | {'·'.join(x_['markets']) or '?'} | "
                       f"{('★ 미연결: ' + ', '.join(x_['unlinked'])) if x_['unlinked'] else ''} |")
    out += [""]
    out += ["## 전망 미기입 축", ""]
    for x in blank:
        out.append(f"- `{x['id']}` {x.get('name', '')[:50]} — 방향·기간·수혜 순서를 채우거나, "
                   f"전망이 아니라 **배경 축**이면 그렇게 적는다")
    out += ["", "## 이번 run이 세우는 전망 (3단이 채운다)", "",
            "<!-- TODO 전망 --> 재료(세상공부 절·링크 원문·뉴스레터)에서 **돈이 갈 곳** 1건 이상. "
            "없으면 '없음 + 이유'.", "",
            "```json", "[{\"id\": \"<axis_id>\", \"name\": \"…\", \"thesis\": \"…\", "
            "\"direction\": \"+\", \"expected\": \"KOSPI 대비 +10%p\", \"horizon_weeks\": 8, "
            "\"confidence\": 0.6, \"driver\": \"<요인>\", \"no_cross_market_why\": \"(반대 시장 종목이 없을 때만)\", "
            "\"split_why\": \"(같은 요인 반대 방향 전망과 메커니즘이 다를 때만)\",", "  \"beneficiaries\": [{\"ticker\": \"…\", \"name\": \"…\", "
            "\"order\": 1, \"why\": \"뉴스가 말한 1차\"}, {\"ticker\": \"…\", \"order\": 2, "
            "\"why\": \"2차 — 아직 값에 안 들어간 이유\"}]}]", "```", "",
            "세운 뒤: `python3 market_map.py forecast add --file <json>` → "
            "`python3 market_map.py priced --axis <id>` → 미반영 2차·3차는 시그널 "
            "`watchlist_candidates`로.", "",
            f"{stamp('전망')}"]
    path = ANALYSIS / f"전망_{st}_{a.market}.md"
    if a.refresh and path.exists():
        # ★ 세운 뒤 **생성 머리(생성줄·열린 전망 표·미기입 축)를 지도에서 다시 만들고**, 3단이 쓴
        #   "## 이번 run이 세우는 전망" 아래는 그대로 둔다. 예전엔 생성줄만 갱신해서 `forecast add`로
        #   세운 전망이 '열린 전망' 표에 안 나타났다(2026-09-16 KR run: "본문 TODO를 안 채움").
        cur = path.read_text(encoding="utf-8")
        marker = "## 이번 run이 세우는 전망"
        head_new = "\n".join(out[:out.index("## 이번 run이 세우는 전망 (3단이 채운다)")])
        if marker in cur:
            tail = cur[cur.index(marker):]
            path.write_text(head_new + "\n" + tail, encoding="utf-8")
            print(f"전망 {rel(path)} (생성 머리 갱신 · 3단 절 보존)")
        else:
            cur = re.sub(r"^전망 갱신 \*\*\d+건\*\* · 미반영 수혜 \*\*\d+건\*\* · 전망 미기입 축 \d+개$",
                         line, cur, count=1, flags=re.M)
            cur = re.sub(r"^측정 상태: .*$", ms_line, cur, count=1, flags=re.M)
            path.write_text(cur, encoding="utf-8")
            print(f"전망 {rel(path)} (생성줄만 갱신 — 3단 절 표식이 없다)")
        print(f"  {line}")
        print(f"  {ms_line}")
        return 0
    write_preserving(path, "\n".join(out) + "\n", "전망 파일")
    print(f"전망 {rel(path)}")
    print(f"  {line}")
    print(f"  {ms_line}")
    print(f"게이트 인자: forecast={path}")
    return 0


def pick_market(now=None) -> tuple:
    """어느 시장을 준비할지 — **지금 열린 시장**, 둘 다 닫혔으면 다음에 열리는 시장. 반환 (market, open, why).

    ★ 시각 제한을 두지 않는다. 옛 루틴 지침이 "10:35·22:35 ±90분 밖이면 건너뜀"이라 한낮에 깨워도
    장이 열려 있는데 안 돌았다(2026-09-18 사용자). 판정은 `kis_client.market_session` 하나로 한다.
    """
    from kis_client import market_session, KST as _KST
    now = now or datetime.now(_KST)
    kr, us = market_session("KR", now), market_session("US", now)
    if kr["is_open"]:
        return "kr", True, f"국내 정규장 열림 ({kr['why']})"
    if us["is_open"]:
        return "us", True, f"미국 정규장 열림 ({us['why']})"
    # 다음에 열리는 시장 — 국내 마감(15:30) 전이면 kr, 마감 뒤면 그날 밤 us(예전 `hour < 16`은 15:30~16:00에 닫힌 kr을 골랐다)
    nxt = "kr" if (5 <= now.hour and now < kr["close"]) else "us"
    if nxt == "kr" and kr.get("holiday"):
        nxt = "us"                              # 국내 휴장일 낮에는 그날 밤 미국장을 준비한다
    elif nxt == "us" and us.get("holiday"):
        nxt = "kr"
    return nxt, False, (f"둘 다 닫힘 — 다음에 열리는 {nxt.upper()}를 준비한다(dispatch까지 · 전송은 "
                        f"장이 열린 실행이 5단부터 재개). KR {kr['open']:%H:%M}–{kr['close']:%H:%M} · "
                        f"US {us['open']:%H:%M}–{us['close']:%H:%M} KST")


def cmd_market(a) -> int:
    m, is_open, why = pick_market()
    print(m)
    print(f"  {why}", file=sys.stderr)
    return 0


def _find_section(lines: list, key: str):
    """절 키(`§6`·`§1-A`·`§11-집행`·`머리말`)의 `## ` 헤더 줄 번호. 헤더 토큰(둘째 단어) 정확 일치 →
    없으면 키의 `-` 앞(`§11-집행` → `§11`)으로 부모 절. `key in line` 부분일치는 쓰지 않는다 —
    `§1`이 `§10`·`§11`에 걸렸고, `§11-규율`은 헤더가 `## §11 집행 결과`라 못 찾았다(2026-09-21)."""
    def token(l):
        parts = l[3:].strip().split()
        return parts[0] if parts else ""
    for k in (key, key.split("-", 1)[0]):
        for i, l in enumerate(lines):
            if l.startswith("## ") and token(l) == k:
                return i
    return None


def cmd_stamp(a) -> int:
    """완료 스탬프를 **실제 시각으로** 찍는다 — 노트 절(`--note --sec §N`) 또는 작업기록(`--worklog --stage`).

    ★ 왜: 2026-09-16 KR run이 스탬프 시각을 손으로 추정해 적어 **미래 시각**(13:35~14:12)이 파일에
    남았다. 시각은 도구가 찍는다. 같은 절에 이미 스탬프가 있으면 그대로 두고 알린다(멱등).
    """
    targets = []
    if a.note:
        if not a.sec:
            print("--note에는 --sec <§N>이 필요하다", file=sys.stderr)
            return 2
        targets.append((Path(a.note), a.sec, "note"))
    if a.worklog:
        if not a.stage:
            print("--worklog에는 --stage <단계>가 필요하다", file=sys.stderr)
            return 2
        targets.append((Path(a.worklog), f"작업기록-{a.stage}", "worklog"))
    if not targets:
        print("--note/--sec 또는 --worklog/--stage 중 하나가 필요하다", file=sys.stderr)
        return 2
    for path, key, kind in targets:
        if not path.exists():
            print(f"파일 없음: {path}", file=sys.stderr)
            return 2
        txt = path.read_text(encoding="utf-8")
        done_mark = f"<!-- ✓ {key} -->"
        if done_mark in txt:
            print(f"이미 찍혀 있다: {path.name} {done_mark}")
            continue
        block = stamp(key)
        if kind == "note":
            # 그 절의 끝(다음 `## ` 직전)에 넣는다 — 절 밖에 찍히면 게이트가 못 센다.
            lines = txt.split("\n")
            start = _find_section(lines, key)
            if start is None:
                parent = key.split("-", 1)[0]
                print(f"절을 못 찾았다: {key} — 헤더 `## {parent} …`가 없다", file=sys.stderr)
                return 2
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            at = end
            while at > start and not lines[at - 1].strip():
                at -= 1
            lines[at:at] = ["", block]
            path.write_text("\n".join(lines), encoding="utf-8")
        else:
            path.write_text(txt.rstrip("\n") + "\n" + block + "\n", encoding="utf-8")
            # 작업기록 단계 스탬프 = 그 단계를 마친 시각 → ex_run(capture·carry·map은 이미 들어선 시각이 있으면 그대로 · note·dispatch는 여기서)
            # 도구가 따로 없는 단계(리서치·노트·검증·감사·결정)는 여기서 단계 결과 행을 남긴다(v4 · U02 — capture·carry·map·전송·체결은
            #   그 명령이 남긴다). ex_run.<단계>_at은 성공한 첫 시도의 끝(note_at·dispatch_at).
            wm = re.match(r"worklog_(\d{6})_(kr|us)\.md$", path.name)
            if wm and a.stage in ("research", "note", "verify", "audit", "dispatch", "no_trade"):
                db.stage_record(wm.group(1), wm.group(2), a.stage, "ok", artifacts={"worklog": path})
        print(f"스탬프 {path.name} ← {block.splitlines()[-1]}")
    return 0


# ─────────────────────────────────────────────────────────── render-note (4단 · 사실 층)

def latest_note(st: str, market: str):
    """그 run 노트의 마지막 판 — `분석노트_<st>_<mkt>_v<major>_<minor>.md`를 판 번호(숫자)로 정렬한다(이름 정렬이면 v1_10 < v1_9)."""
    def ver(f):
        m = re.search(r"_v(\d+)_(\d+)\.md$", f.name)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    c = sorted(ANALYSIS.glob(f"분석노트_{st}_{market}_v*.md"), key=ver)
    return c[-1] if c else None


def _next_minor(p: Path) -> Path:
    """`…_v1_0.md` → `…_v1_1.md`(판 번호가 없으면 그대로)."""
    m = re.search(r"_v(\d+)_(\d+)\.md$", p.name)
    return p.with_name(f"{p.name[:m.start()]}_v{m.group(1)}_{int(m.group(2)) + 1}.md") if m else p


def _slot_at(text: str, pos: int) -> str:
    """그 자리가 속한 절 — 바로 앞 `## ` 머리의 첫 낱말(§2·§6·§1-A), 첫 머리 전이면 '머리말'."""
    i = text.rfind("\n## ", 0, pos)
    if i < 0:
        return "머리말" if not text.startswith("## ") else text[3:].split()[0]
    head = text[i + 4:text.find("\n", i + 4)].strip()
    return head.split()[0] if head else "머리말"


def _cell_value(text: str, pos: int, v: str) -> str:
    """값 한 줄 — 줄바꿈은 빈칸으로, 표 행 안이면 '|'를 \|로(표가 깨지지 않게)."""
    v = str(v or "").replace("\r", " ").replace("\n", " ").replace("<!--", "<! --").strip()
    line_start = text.rfind("\n", 0, pos) + 1
    if text[line_start:pos].lstrip().startswith("|"):
        v = v.replace("\\|", "|").replace("|", "\\|")
    return v


def _s12_region(text: str):
    """§12 렌더 자리 (시작, 끝, 앞서 렌더한 본문). 렌더 자리표가 없으면 §12 절 본문(옛 뼈대의 TODO·손으로 붙인 표)을 자리로 삼는다 —
    절이 없으면 (끝, 끝, '') — 노트 끝(NOTE-LINT 줄 앞)에 절을 새로 붙인다."""
    i = text.find(S12_OPEN)
    j = text.find(S12_CLOSE, i + 1) if i >= 0 else -1
    if i >= 0 and j > i:
        return i, j + len(S12_CLOSE), text[i + len(S12_OPEN):j], True
    m = re.search(r"^## §12\b.*$", text, re.M)
    nl = re.search(r"^<!-- NOTE-LINT\b", text, re.M)
    if not m:
        at = nl.start() if nl else len(text)
        return at, at, "", False
    start = m.end()
    nxt = re.search(r"^## ", text[start:], re.M)
    end = start + nxt.start() if nxt else (nl.start() if nl and nl.start() > start else len(text))
    return start, end, text[start:end], False


def render_note(st: str, market: str, note: str = "") -> tuple:
    """노트를 저장소에서 채운다 — (종료코드, 메시지). 같은 저장소 상태로 다시 돌리면 같은 파일(글자 그대로)이다.
    ① 값 자리 `{{F-id}}`·`{{F-id.as_of}}`·`{{F-id.line}}`(그 밖 `.칸`은 그 사실의 칸 값) → `<!--F:F-id[.칸]-->값<!--/F-->` —
       이미 렌더된 자리는 값만 새로(교정 = 행 갱신 1회 → 다시 렌더하면 그 자리 값이 바뀐다 · 전파 없음).
    ② 가리킨 사실마다 참조(ev_claim_ref: doc_type=note · doc_id=노트 파일 이름 · slot=절) · 이 run S 번호(ev_fact_run)가 없으면 단다 ·
       노트에서 빠진 자리의 참조는 지운다(검증 큐가 노트에 없는 사실을 보지 않게).
    ③ §12 = `facts.render_s12`(안 바뀐 행은 written을 지킨다) · §3 = `facts.render_board`(보드 기록이 없으면 그대로).
    없는 사실 id가 하나라도 있으면 아무것도 쓰지 않고 2."""
    import facts as _f
    rid = db.run_id(st, market)
    p = Path(note) if note else latest_note(st, market)
    if not p or not p.exists():
        return 2, f"분석노트가 없다: {p or f'analysis/분석노트_{st}_{market}_v*.md'}"
    text = p.read_text(encoding="utf-8")
    refs = [(m.start(), m.group(1), m.group(2)) for rx in (FACT_MARK, FACT_REF) for m in rx.finditer(text)]
    ids = sorted({fid for _, fid, _ in refs})
    missing = [fid for fid in ids if not db.q1("SELECT 1 AS o FROM ev_fact WHERE id=?", (fid,))]
    if missing:
        return 2, (f"없는 사실 {len(missing)}건 — {', '.join(missing)} · `facts.py add`가 준 id인지 확인하라"
                   f"(노트는 고치지 않았다: {p.name})")
    vals, bad = {}, []
    for _, fid, fld in refs:
        if (fid, fld) in vals:
            continue
        try:
            vals[(fid, fld)] = _f.render_value(fid, fld)
        except _f.UnknownField as e:
            bad.append(str(e).strip("'\""))
    if bad:
        return 2, f"없는 칸 {len(bad)}건 — {'; '.join(bad[:6])} · 쓸 수 있는 칸: (없음)=값 · as_of · line · 그 종류 상세 칸(노트는 고치지 않았다)"

    def sub(m):
        fid, fld = m.group(1), m.group(2)
        tag = fid + (f".{fld}" if fld else "")
        return f"<!--F:{tag}-->{_cell_value(m.string, m.start(), vals[(fid, fld)])}<!--/F-->"
    new = FACT_MARK.sub(sub, text)
    # 이미 렌더된 값이 바뀌었나(교정 뒤 재렌더) — 바뀌었으면 다음 minor 판으로 재발행하고 옛 판은 analysis/이전버전/으로(stage4 5-C 7)
    value_changed = FACT_MARK.findall(text) != FACT_MARK.findall(new)
    new = FACT_REF.sub(sub, new)
    # 참조·S 번호 — 렌더한 노트 기준(자리와 절). 문장 끝 `[S n]`도 그 run S 번호의 사실을 가리킨다(§12 표는 클레임이 아니다)
    slots = {(fid, _slot_at(new, m.start())) for m in FACT_MARK.finditer(new) for fid in [m.group(1)]}
    s_refs = [(int(n), _slot_at(new, m.start())) for m in S_CITE.finditer(new) for n in re.findall(r"S(\d+)", m.group(1))]
    s_refs = [(n, slot) for n, slot in s_refs if slot != "§12"]
    if s_refs:
        by_s = {r["s_no"]: r["fact_id"] for r in db.q("SELECT s_no, fact_id FROM ev_fact_run WHERE run_id=?", (rid,))}
        slots |= {(by_s[n], slot) for n, slot in s_refs if n in by_s}
    lp = latest_note(st, market)
    is_latest = bool(lp) and lp.resolve() == p.resolve()
    out_p = _next_minor(p) if (value_changed and is_latest) else p
    n_new_s = 0
    with db.tx() as c:
        for fid, slot in sorted(slots):
            _f.add_ref(fid, rid, "note", out_p.name, slot, role="note", c=c)
        for r in c.execute("SELECT id, fact_id, slot, doc_id FROM ev_claim_ref WHERE run_id=? AND doc_type='note'", (rid,)).fetchall():
            stale_here = r["doc_id"] == out_p.name and (r["fact_id"], r["slot"]) not in slots
            other_ver = is_latest and r["doc_id"] != out_p.name and str(r["doc_id"]).startswith(f"분석노트_{st}_{market}_v")
            if stale_here or other_ver:
                c.execute("DELETE FROM ev_claim_ref WHERE id=?", (r["id"],))
        for fid in ids:
            if not c.execute("SELECT 1 FROM ev_fact_run WHERE fact_id=? AND run_id=?", (fid, rid)).fetchone():
                _f._attach_run(c, fid, rid, st, market)
                n_new_s += 1
        if ids:
            _f._write_counts(c, rid, st, market)
        db.run_start(st, market)
        c.execute("UPDATE ex_run SET rerender=0 WHERE run_id=?", (rid,))
    # §12
    a, b, prev, marked = _s12_region(new)
    body = _f.render_s12(st, market, prev=prev)
    if body.startswith("## §12"):
        body = body.split("\n", 1)[1].lstrip("\n")
    blk = f"{S12_OPEN}\n{body.rstrip()}\n{S12_CLOSE}"
    if marked:
        new = new[:a] + blk + new[b:]
    elif prev or re.search(r"^## §12\b", new, re.M):
        new = new[:a] + "\n\n" + blk + "\n\n" + new[b:].lstrip("\n")
    else:
        new = new[:a].rstrip("\n") + "\n\n---\n\n## §12 출처 원장 — 세 시점\n\n" + blk + "\n" + (("\n" + new[a:]) if new[a:] else "")
    n12 = len(re.findall(r"^\| S\d+ \|", body, re.M))
    # §3
    i, j = new.find(S3_OPEN), new.find(S3_CLOSE)
    if i >= 0 and j > i:
        board = _f.render_board(st, market)
        if board is None:
            s3 = "보드 기록 없음 — 그대로(1단 capture가 이 세션 보드를 적재하지 않았다)"
        else:
            new = new[:i] + f"{S3_OPEN}\n{board}\n{S3_CLOSE}" + new[j + len(S3_CLOSE):]
            s3 = "렌더"
    else:
        s3 = "렌더 자리표 없음 — 그대로"
    changed = new != text
    if out_p != p:
        out_p.write_text(new, encoding="utf-8")
        old_dir = ANALYSIS / "이전버전"
        old_dir.mkdir(exist_ok=True)
        p.replace(old_dir / p.name)
        return 0, (f"render-note {out_p.name} — 교정으로 값이 바뀌어 새 판으로 재발행(옛 판 {p.name} → analysis/이전버전/) · "
                   f"값 자리 {len(refs)}개(사실 {len(ids)}개 · 새 S 번호 {n_new_s}) · §12 {n12}행 · §3 {s3}")
    if changed:
        p.write_text(new, encoding="utf-8")
    return 0, (f"render-note {p.name} — 값 자리 {len(refs)}개(사실 {len(ids)}개 · 새 S 번호 {n_new_s}) · §12 {n12}행 · §3 {s3} · "
               + ("저장" if changed else "바뀐 것 없음"))


def cmd_render_note(a) -> int:
    import sessions as _ss
    st = a.stamp or _ss.run_stamp(a.market)
    rc, msg = render_note(st, a.market, a.note)
    print(msg, file=sys.stderr if rc else sys.stdout)
    return rc


def reversal_block(market: str) -> str:
    """이어받기 `## 감사 소급` 절 — 대기 행(저장소 ex_reversal_event · risk_guard.reversals_pending)이 있으면 이 run의 risk_guard가
    자동으로 판다. 도구 출처(buy_ref·fc_rows) 없는 소급 행은 risk_guard.reversals_pending이 void로 닫아 여기 뜨지 않는다(FB-261017-9)."""
    import risk_guard as _rg
    rows = _rg.reversals_pending(market)
    head = "## 감사 소급\n\n"
    if not rows:
        return head + "(대기 중인 되돌림 매도 없음)\n"
    lines = [head + "직전 run의 주문 뒤 감사가 매수 근거를 뒤집었다 — **이 run의 risk_guard가 아래를 규율 매도로 자동 전송한다**"
             "(사람 확인 없음 · 보유 0이면 void로 닫힌다).\n",
             "| id | 종목 | 남은 수량 | 사유 | 판정표 행 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r.get('ticker')} {r.get('name') or ''} | {r.get('qty')} | {str(r.get('why') or '')[:120]} "
                     f"| {', '.join(r.get('fc_rows') or []) or '—'} |")
    return "\n".join(lines) + "\n"


def reuse_block(market: str, st: str) -> str:
    """이어받기 — 다른 run이 이미 세운 사실(facts.reuse · 조사 전에 연다 — 반대편 run이 세운 as-of를 다시 파지 않게)."""
    try:
        import facts as _f
        body = _f.reuse(st, market, days=7, brief=True)
    except db.TestGuard:
        raise
    except Exception as e:                        # noqa: BLE001
        body = f"[실패 — {type(e).__name__}: {e}. '재사용 후보 없음'으로 읽지 말 것]"
    return f"### 출처 재사용 후보 (facts.py reuse — 중복 조사 방지 · 한 줄씩)\n\n```\n{body.rstrip()}\n```\n"


def refill_block(market: str, st: str) -> str:
    """이어받기 `## 재투자 대기` 절 — 지난 스탬프의 재투자 기록(저장소 ex_refill · 예전 `journal/refill_<st>_<mkt>.json`) 중
    `open`·`deferred`(이 run 것 제외). 그린 행은 `carried`로 바꾼다(같은 대기를 두 run이 이어받지 않게 · 행 단위 갱신)."""
    rows = []
    # 스탬프는 행 키에서 읽는다(with_key) — 본문에 stamp 칸이 없는 행도 있다.
    for stp, mk, d in sorted(db.refill_open(market, with_key=True), key=lambda x: x[0]):
        if stp >= st or d.get("status") not in ("open", "deferred"):
            continue
        proceeds = sum(float(e.get("proceeds") or 0) for e in d.get("events") or [])
        sold = ", ".join(f"{x.get('ticker')} {x.get('qty')}주" for e in d.get("events") or [] for x in e.get("sells") or [])
        rows.append((stp, d.get("status"), sold, proceeds, str(d.get("deferred_why") or "—")))
        d["status"] = "carried"
        d["carried_by"] = st
        db.refill_put(stp, market, d)
    head = "## 재투자 대기\n\n"
    if not rows:
        return head + "(직전 run들이 판 돈 가운데 재투자되지 않고 남은 것 없음)\n"
    return (head + "직전 run의 매도 전용 pass(손절·추적·감사 소급)가 판 돈이 재투자되지 않았다 — 이 run의 gap에 들어가 있다. "
            "§1-A에 왜 남았는지(미룬 사유)를 한 줄로 적고, 5단 제안으로 채운다.\n\n"
            "| 스탬프 | 상태 | 판 것 | 대금 | 미룬 사유 |\n|---|---|---|---|---|\n"
            + "\n".join(f"| {a} | {b} | {c} | {d_:,.2f} | {e} |" for a, b, c, d_, e in rows) + "\n")


def refill_gate(market: str, st: str) -> tuple:
    """(종료코드, 마크다운) — 이 run의 재투자 기록이 `open`이면 3(재투자 pass를 돌렸거나 미룬 사유를 적어야 닫힌다)."""
    d = db.refill_get(st, market)
    if d is None:
        return 0, ""
    if d.get("status") == "open":
        return 3, ("### ★ 재투자 미처리\n\n이 run 안에서 매도가 나간 뒤 판 돈이 재투자되지 않았다 — "
                   "`stage6_execute.md` 〈매도 뒤 재투자〉대로 채우거나, 못 채우면 "
                   f"`python3 stage.py refill-defer --market {market} --stamp {st} --why \"<사유>\"`로 미룬 사유를 적어라\n")
    return 0, (f"### 매도 뒤 재투자\n\n상태 {d.get('status')}"
               + (f" · 사유 {d.get('deferred_why')}" if d.get("deferred_why") else "") + "\n")


def _stale_open(st: str, market: str) -> list:
    """최신 TRACK1 POST(ex_signal.track1.post)의 stale_support 가운데 이 run에서 끝나지 않은 것 [{ticker, facts}] — fact_close ④."""
    rid = db.run_id(st, market)
    sig = db.q1("SELECT track1 FROM ex_signal WHERE run_id=?", (rid,))
    t1 = json.loads(sig["track1"]) if sig and sig.get("track1") else {}
    items = (t1.get("post") or {}).get("stale_support") or []
    if not items:
        return []
    started = str((db.run_get(st, market) or {}).get("started") or (t1.get("post") or {}).get("at") or "")

    def _after(ts):
        try:
            return not started or datetime.fromisoformat(str(ts)) >= datetime.fromisoformat(started)
        except ValueError:
            return str(ts) >= started
    revs = {(r.get("ticker")) for r in db.reversals_all() if str(r.get("stamp")) == st
            and str(r.get("market", "")).lower() == market.lower() and r.get("status") in ("pending", "filled")}
    rejudged = {t.get("ticker") for t in db.theses_all() for h in (t.get("history") or [])
                if "[보유 재판정]" in str(h.get("note") or "") and _after(h.get("ts"))}
    return [x for x in items if x.get("ticker") not in revs | rejudged]


def fact_close(st: str, market: str) -> dict:
    """사실 층 마감(2026-10-17) — run_close 전에. ① 참조 검사(`REFS OK`/`REFS FAIL` 줄 · 이번 run 노트·제안·보유가 가리킨 사실이
    전부 있고 url·인용·as-of가 있는가) ② 뉴스 전 사실·재료 지연(facts.before_news) ③ run 표 집계 — 신규·재사용·되풀이 사실 ·
    판정 행(이번 run ev_verdict) · 감사 회차(facts.audit_rounds — 주문 근거 감사 기록). 실패해도 마감을 막지 않고 크게 알린다.
    ④ 낡은 근거(2026-10-17 · FB-261017-9) — 최신 `TRACK1 POST`의 stale_support 종목마다 이 run에서 끝났는가: 재확인으로 post에서
    빠졌다 · 그 종목 논지에 run 시작 뒤 `[보유 재판정]` 노트가 있다 · 이 run의 되돌림 행(pending/filled)이 있다. 하나도 없으면
    `out["stale_open"]`에 넣고 크게 알린다 — cmd_close가 종료코드 4로 닫지 않는다(근거가 무너진 매수를 하루 더 들지 않는다)."""
    out = {"stale_open": [], "stale_check_ok": True, "refs_ok": None, "error": None}
    try:
        out["stale_open"] = _stale_open(st, market)
        for x in out["stale_open"]:
            print(f"★ 낡은 근거 미결 {x['ticker']}({'|'.join(x['facts'])}) — ① 사실을 다시 확인해 facts.py correct → verdict → "
                  f"decide --mode post, 또는 ② theses.py set-status --id <논지> --status held --note \"[보유 재판정] …\" / facts.py reverse")
    except db.TestGuard:
        raise
    except Exception as e:                        # noqa: BLE001 — 검사가 죽으면 '미결'로 본다(close가 종료 4 · v4 U02)
        out["stale_check_ok"] = False
        out["error"] = f"낡은 근거 검사 실패 — {type(e).__name__}: {e}"
        print(f"★ 낡은 근거 검사를 못 돌렸다({type(e).__name__}: {e}) — `python3 facts.py decide --mode post --stamp {st} --market {market}` 출력의 "
              f"stale_support를 손으로 확인한다")
    try:
        import facts as _f
        ok, line, probs = _f.check_refs(st, market)
        for pr in probs[:20]:
            print("  - " + pr)
        if len(probs) > 20:
            print(f"  … 그 밖 {len(probs) - 20}건(`python3 facts.py check-refs --stamp {st} --market {market}`)")
        print(line)
        bn = _f.before_news(st, market)
        rid = db.run_id(st, market)
        vrows = (db.q1("SELECT COUNT(*) AS n FROM ev_verdict WHERE run_id=?", (rid,)) or {}).get("n") or 0
        rounds = len(_f.audit_rounds(st, market))
        db.run_set(st, market, verified_rows=vrows, audit_rounds=rounds)
        out = {**out, "refs_ok": ok, "verified_rows": vrows, "audit_rounds": rounds, **bn}
        print(f"[사실] {rid} — 신규 {bn['facts_new']} · 재사용 {bn['facts_reused']} · 되풀이 {bn['facts_repeat']} · "
              f"뉴스 전 {bn['before_news_n']} · 재료 지연 {bn['material_delay_h'] if bn['material_delay_h'] is not None else '—'}시간 · "
              f"판정 행 {vrows} · 감사 {rounds}회 → ex_run")
        if (db.run_get(st, market) or {}).get("rerender"):
            print(f"★ [사실] 교정 뒤 노트를 다시 렌더하지 않았다 — `python3 stage.py render-note --stamp {st} --market {market}`")
    except db.TestGuard:
        raise
    except Exception as e:                        # noqa: BLE001 — 마감 검사가 죽으면 사실 층 미결(close가 종료 5 · v4 U02)
        out["refs_ok"] = None
        out["error"] = (out.get("error") or "") + f" · 사실 층 마감 검사 실패 — {type(e).__name__}: {e}"
        print(f"REFS FAIL — 사실 층 마감 검사를 못 돌렸다({type(e).__name__}: {e}) · "
              f"`python3 facts.py check-refs --stamp {st} --market {market}`")
    return out


CLOSE_CODES = {0: "닫음", 3: "재투자 미처리", 4: "낡은 근거 미결", 5: "사실 층 미결(REFS FAIL·마감 검사 실패)",
               6: "저장 단계 실패", 7: "외부 점검 명령 실패"}
_CLOSE_WHY = {3: "재투자 미처리라", 4: "낡은 근거 미결이라", 5: "사실 층 미결이라", 6: "저장 단계 실패라", 7: "외부 점검 명령 실패라"}


def cmd_close(a) -> int:
    """6단 마지막 고리(2026-10-15) — 계좌 현황 바로 앞에 돈다. ① 캡처가 미룬 미국 주간거래 손절 점검(국내 run · 실계좌 계열)
    ② 매도 뒤 재투자 대기(저장소 ex_refill의 이 run 행이 `open`이면 닫지 않는다 — 재투자 pass를 돌렸거나 미룬 사유를 적었어야 한다)
    ③ 실전 점검표(`real_check.py`) ④ 저장소 마감(2026-10-17) — 시그널·승인·체결 파일 → 집행 표(`import_run_artifacts`) ·
    게이트 감사 판정 복사(`audit_mirror`) · 종료 0일 때만 `run_close` · 렌더본 내보내기(안전망) · 원문 판 정리.
    ★ 종료코드(v4 · Codex U02 — '닫힘'은 '모름'일 수 없다): 0 닫음 · 3 재투자 미처리 · 4 낡은 근거 미결(검사가 죽어도 4) ·
    5 사실 층 미결(REFS FAIL·마감 검사 예외) · 6 저장 단계 실패(이관·감사 복사·run_close) · 7 외부 점검 명령 실패(real_check·stops).
    우선순위 6 > 7 > 3 > 4 > 5. 0이 아니면 run_close를 보류한다(6은 도달 못 함). 3·4·5는 점검표를 치운다(execute·no_trade 게이트가 본다).
    다시 돌리면 새 시도(멱등 — 이관·감사 복사는 같은 해시·같은 줄 한 번 · closed_at은 처음 값 유지)."""
    import sessions as _ss
    from kis_client import account_mode as _acct_mode, us_daytime_session as _uds
    st = a.stamp or _ss.run_stamp(a.market)
    sid = db.stage_start(st, a.market, "close")
    steps, blocks, ext_fail = [], [], []
    mode = _acct_mode()
    if a.market == "kr" and mode != "paper":
        if _uds()["is_open"]:
            r_ = run(["stops.py", "--market", "us", "--daytime", "--stamp", st] + (["--send"] if mode == "real" else []),
                     "미국 주간거래 손절 점검 (stops.py --daytime · close)")
            blocks.append(r_)
            steps.append(r_.step())
            if r_.rc:
                ext_fail.append(f"stops.py --daytime 종료코드 {r_.rc}")
        else:
            blocks.append("### 미국 주간거래 손절 점검\n\n주간거래 창 밖 — 정규장 run이 판정한다\n")
    rc, rblk = refill_gate(a.market, st)
    if rblk:
        blocks.append(rblk)
    print("\n".join(blocks))
    blocks = []
    fc = fact_close(st, a.market)
    if rc == 0 and (fc.get("stale_open") or not fc.get("stale_check_ok", True)):
        rc = 4
    if rc == 0 and fc.get("refs_ok") is not True:
        rc = 5
    steps.append({"name": "사실 층 마감", "required": True, "rc": None,
                  "status": "ok" if fc.get("refs_ok") is True and fc.get("stale_check_ok", True) and not fc.get("stale_open") else "failed",
                  "note": (fc.get("error") or "")[:300] or (f"낡은 근거 미결 {len(fc.get('stale_open') or [])}건" if fc.get("stale_open")
                                                             else ("REFS FAIL" if fc.get("refs_ok") is False else ""))})
    rcp = PROJ / "data" / "run_evidence" / f"real_check_{st}_{a.market}.md"
    if rc in (3, 4, 5):
        # 재투자 미처리 · 낡은 근거 미결 · 사실 층 미결이면 점검표를 쓰지 않고 지난 것도 치운다 — execute·no_trade 게이트가 점검표 파일의
        # 존재를 보므로 판 돈을 그대로 둔 채(2026-10-15) · 근거가 무너진 매수를 든 채(2026-10-17) · 사실이 비거나 검사가 죽은 채 run을 닫을 수 없다.
        if rcp.exists():
            rcp.unlink()
        blocks.append("### 실전 점검표\n\n" + {
            3: "재투자 미처리라 쓰지 않았다 — 채우거나 `refill-defer`로 미룬 뒤 `stage.py close`를 다시 돌린다\n",
            4: "낡은 근거 미결이라 쓰지 않았다 — 재확인(correct → verdict → decide --mode post) 또는 "
               "보유 재판정(`[보유 재판정]` 노트 / facts.py reverse) 뒤 `stage.py close`를 다시 돌린다\n",
            5: "사실 층 미결이라 쓰지 않았다 — `REFS FAIL` 목록의 빠진 칸(as_of·url·quote)을 `facts.py correct`로 채우거나 "
               "검사 오류를 고친 뒤 `stage.py close`를 다시 돌린다\n"}[rc])
    else:
        r_ = run(["real_check.py", "--stamp", st, "--market", a.market], "실전 점검표 (real_check.py)")
        blocks.append(r_)
        steps.append(r_.step())
        if r_.rc:
            ext_fail.append(f"real_check.py 종료코드 {r_.rc}")
    if ext_fail:
        rc = 7
    print("\n".join(blocks))
    # 저장소 마감 — 집행 기록 이관·감사 복사는 멱등(같은 파일 해시·같은 원장 줄은 한 번)이라 close를 다시 돌려도 된다.
    try:
        imp = db.import_run_artifacts(st, a.market, here=PROJ)
        n_audit = db.audit_mirror(st, a.market)
        if rc == 0:
            db.run_close(st, a.market, status="closed")
        db.export_all()
        print(f"[저장소] {db.run_id(st, a.market)} — 시그널 {imp['signal']} · 제안 {imp['proposals']} · 승인 {imp['approvals']} · "
              f"주문 {imp['orders']} · 체결 {imp['fills']} 이관 · 감사 판정 {n_audit}행 복사 · "
              + ("run_close" if rc == 0 else f"{_CLOSE_WHY.get(rc, str(rc))} run_close 보류"), file=sys.stderr)
        if imp.get("missing_facts"):
            mf = imp["missing_facts"]
            print(f"★ [저장소] 제안이 가리킨 사실 id가 사실 표에 없다 {len(mf)}건 — {', '.join(map(str, mf[:8]))}", file=sys.stderr)
        if imp.get("skipped_rows"):                  # v8 — 장부 제약을 어기는 행은 옮기지 않고 여기 남긴다(한 행이 마감을 막지 않게)
            sk = imp["skipped_rows"]
            print(f"★ [저장소] 장부 모양이 아닌 행 {len(sk)}건은 옮기지 않았다 — " + " / ".join(map(str, sk[:5])), file=sys.stderr)
        # 원문 판 정리(v3 · U01) — 참조 없는 판의 파일만(7일 · 사실·판정·링크가 가리킨 판은 영구). fetch는 더 지우지 않는다.
        pr = db.raw_purge()
        print(f"[원문 보관] 정리 {pr['purged']}판(파일 {pr['files_removed']}) · 참조돼 보존 {pr['kept_referenced']} · 최근 {pr['kept_recent']}",
              file=sys.stderr)
        steps.append({"name": "저장소 마감", "required": True, "status": "ok"})
    except db.TestGuard:
        raise
    except Exception as e:                        # noqa: BLE001 — 저장 단계 실패는 종료 6(점검표·재투자 판정은 그대로)
        rc = 6
        steps.append({"name": "저장소 마감", "required": True, "status": "failed", "note": f"{type(e).__name__}: {str(e)[:200]}"})
        print(f"★ [저장소] 마감 실패 — {type(e).__name__}: {e} · `python3 stage.py close`를 다시 돌린다", file=sys.stderr)
    db.stage_finish(sid, "ok" if rc == 0 else "failed", rc, steps, None, CLOSE_CODES.get(rc, ""))
    print(f"[마감] 종료코드 {rc} — {CLOSE_CODES.get(rc, rc)}" + (" · " + " · ".join(ext_fail) if ext_fail else ""), file=sys.stderr)
    return rc


def cmd_refill_defer(a) -> int:
    """매도 뒤 재투자를 이 run에서 못 채운 사유를 적는다 → `deferred`(다음 run 이어받기 `## 재투자 대기`가 받는다)."""
    import sessions as _ss
    st = a.stamp or _ss.run_stamp(a.market)
    rid = db.run_id(st, a.market)
    d = db.refill_get(st, a.market)
    if d is None:
        print(f"재투자 기록이 없다: {rid}(ex_refill)", file=sys.stderr)
        return 2
    if len(a.why.strip()) < 10:
        print("미룬 사유를 10자 이상으로 적어라(시그널 나이·장 마감·후보 없음 등 무엇 때문인지)", file=sys.stderr)
        return 2
    d.update(status="deferred", deferred_why=a.why.strip(), deferred_at=datetime.now(KST).isoformat(timespec="seconds"))
    db.refill_put(st, a.market, d)
    print(f"재투자 미룸 — {rid} · {a.why.strip()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("market", help="어느 시장을 준비할지 — 열린 시장, 없으면 다음에 열리는 시장 (kr|us 한 줄)")
    stp = sub.add_parser("stamp", help="완료 스탬프를 실제 시각으로 찍는다(손으로 시각을 적지 않는다)")
    stp.add_argument("--note", default="", help="분석노트 경로")
    stp.add_argument("--sec", default="", help="절 키 (예: §6, §11-집행, 머리말)")
    stp.add_argument("--worklog", default="", help="작업기록 경로")
    stp.add_argument("--stage", default="", help="단계 이름 (capture|carry|map|note|dispatch|execute)")
    c = sub.add_parser("capture", help="1단 — 재료 + 섹터 보드 → 캡처 파일 + 노트 뼈대")
    c.add_argument("--market", choices=["kr", "us"], default="kr")
    c.add_argument("--days", type=int, default=1, help="뉴스레터 수집 창(직전 노트 이후 일수)")
    c.add_argument("--quick", action="store_true", help="보드의 5·20일 생략(호출 1회)")
    c.add_argument("--stamp", default="", metavar="YYMMDD")
    c.add_argument("--force", action="store_true", help="노트 뼈대를 덮어쓴다")
    c.add_argument("--rev", type=int, default=1,
                   help="노트 major 판(같은 날 다시 돌 때 올린다 — 직전 판은 analysis/이전버전/으로)")
    c.add_argument("--no-record", action="store_true",
                   help="sector_history.jsonl 적재를 건너뛴다(점검·재실행용)")
    y = sub.add_parser("carry", help="2단 — 트리거·회고·대응매핑·순환 → carry 파일")
    y.add_argument("--market", choices=["kr", "us"], default="kr")
    y.add_argument("--session", default="", help="회고할 반대편 세션 YYYY-MM-DD (기본: 자동)")
    y.add_argument("--stamp", default="", metavar="YYMMDD")
    y.add_argument("--rev", type=int, default=1, help="노트 major 판(capture와 같은 값을 준다)")
    mp = sub.add_parser("map", help="3단 — 전망·리서치 뼈대 → 전망 파일")
    mp.add_argument("--market", choices=["kr", "us"], default="kr")
    mp.add_argument("--stamp", default="", metavar="YYMMDD")
    mp.add_argument("--refresh", action="store_true",
                    help="forecast add 뒤 생성줄만 다시 센다(파일은 그대로)")
    cl = sub.add_parser("close", help="6단 마지막 — 미룬 주간거래 손절 점검 · 재투자 대기 확인 · 실전 점검표(계좌 현황 바로 앞)")
    cl.add_argument("--market", choices=["kr", "us"], required=True)
    cl.add_argument("--stamp", default="", metavar="YYMMDD")
    rn = sub.add_parser("render-note", help="4단 — 노트 값 자리 {{F-id}}·§3 섹터 보드·§12 출처 원장을 저장소에서 채운다(다시 돌려도 같다)")
    rn.add_argument("--market", choices=["kr", "us"], required=True)
    rn.add_argument("--stamp", default="", metavar="YYMMDD")
    rn.add_argument("--note", default="", help="분석노트 경로(기본: 그 run 노트의 마지막 판)")
    rd = sub.add_parser("refill-defer", help="매도 뒤 재투자를 이 run에서 못 채운 사유를 적는다(다음 run이 이어받는다)")
    rd.add_argument("--market", choices=["kr", "us"], required=True)
    rd.add_argument("--stamp", default="", metavar="YYMMDD")
    rd.add_argument("--why", required=True)
    a = ap.parse_args()
    return {"capture": cmd_capture, "carry": cmd_carry, "map": cmd_map, "stamp": cmd_stamp,
            "market": cmd_market, "close": cmd_close, "refill-defer": cmd_refill_defer, "render-note": cmd_render_note}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
