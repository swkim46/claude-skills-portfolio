#!/usr/bin/env python3
"""단계 드라이버 — 각 단계가 **자기 산출 파일**을 만들게 한다.

게이트는 파일만 검사할 수 있다. 단계 결과가 문맥에만 남으면 게이트가 볼 것이 없고,
다음 단계도 이어받을 것이 없다. 그래서 1·2단은 도구 출력을 한 파일로 모은다.

  stage.py capture --market kr --days 2   # 재료 + 섹터 보드 → 캡처 파일 + 노트 뼈대
  stage.py carry   --market kr            # 트리거·회고·대응매핑·순환 → carry 파일

판단은 하지 않는다. 여기서 하는 일은 **모으고 재고 옮겨 적는 것**뿐이고,
무엇이 유망한지 고르는 것은 3·4단(사람이 아니라 그 단계의 문맥)이 한다.
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
     "`python3 srcledger.py render --stamp <YYMMDD> --market <mkt>`의 출력을 붙인다. "
     "사실마다 as-of(그 사실이 사실인 시점) · retrieved(가져온 시점) · written(쓴 시점)"),
]


def stamp_today() -> str:
    return datetime.now(KST).strftime("%y%m%d")


def run(cmd: list[str], title: str) -> str:
    """서브프로세스 출력을 그대로 돌려준다 — 캡처 파일에는 **가공 없이** 담는다."""
    print(f"  · {title} …", file=sys.stderr)
    p = subprocess.run([sys.executable, *cmd], cwd=PROJ, capture_output=True, text=True)
    body = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")
    if p.returncode != 0:
        body += f"\n[종료코드 {p.returncode} — 실패한 명령이다. '결과 없음'으로 읽지 말 것.]"
    return f"### {title}\n\n```\n{body.rstrip()}\n```\n"


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

    c = KisClient(svr="paper")
    rows, bench_name, bench = mm._fetch_board(c, market, quick)
    state = mm._sector_state(mm._read(mm.MAP, {"axes": []}))
    today = datetime.now(KST).strftime("%Y-%m-%d")

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
    wl = json.loads((PROJ / "config" / "watchlist.json").read_text(encoding="utf-8"))
    axis = {i["ticker"]: (i.get("axis") or i.get("sector") or "")
            for i in (wl.get(market.upper()) or [])}
    th = json.loads((JOURNAL / "theses.json").read_text(encoding="utf-8")) if (JOURNAL / "theses.json").exists() else {}
    items = th if isinstance(th, list) else (th.get("theses") or [])
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
    prev = last_session(market)
    gap = None
    if prev:
        try:
            d0 = datetime.strptime(prev, "%Y-%m-%d").date()
            gap = (datetime.now(KST).date() - d0).days
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
    `retrieved`는 `srcledger.py`가 찍는다). 노트만 보고는 "삼성생명 305,000"이 언제
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
            out.append(f"\n{board_md}\n\n{stamp('§3')}\n")
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
    try:
        bench = json.loads((PROJ / "config" / "benchmark.json").read_text(encoding="utf-8"))
        tk = (bench.get(market.upper()) or {}).get("benchmark_ticker")
    except (OSError, json.JSONDecodeError):
        tk = None
    if tk:
        try:
            from kis_client import KisClient
            from datetime import timedelta as _td
            cli = KisClient()
            if market.lower() == "kr":
                end = datetime.now(KST).strftime("%Y%m%d")
                start = (datetime.now(KST) - _td(days=120)).strftime("%Y%m%d")
                rows = cli.domestic_daily(tk, start, end)
            else:
                rows = cli.overseas_daily(tk, "AMS" if tk in ("SPY",) else "NAS")
            closes = [float(r.get("close") or 0) for r in rows if r.get("close")]
            if len(closes) >= 61:
                c0 = closes[0]
                def chg(n):
                    return (c0 / closes[n] - 1) * 100 if len(closes) > n and closes[n] else None
                ma20 = sum(closes[:20]) / 20
                ma60 = sum(closes[:60]) / 60
                lines.append(f"벤치마크 {tk} 종가 {c0:,.2f} (기준일 {rows[0].get('date')})")
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
        lines.append("벤치마크 티커 없음(config/benchmark.json)")
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


def cmd_capture(a) -> int:
    st = a.stamp or stamp_today()
    EVID.mkdir(parents=True, exist_ok=True)
    ANALYSIS.mkdir(parents=True, exist_ok=True)

    blocks = [run(["ingest.py", "--market", a.market, "--with-news",
                   "--days", str(a.days), "--stamp", st], "재료 수집 (ingest.py)")]
    # ★ **이미 가진 것**을 캡처에 박는다. 단계를 쪼개면서 "어제 그 파일을 받았다"를
    #   기억하던 경로가 사라졌는데 대체 경로를 안 만들었다 — 이 목록이 그 경로다.
    #   *실사례(2026-09-10): 3단이 "KRX 정기변경 명단 미확보 → 조사불가"로 닫았는데
    #   그 명단이 하루 전 받아둔 `_raw_sources/`에 있었고, 지목된 두 종목은 유니버스
    #   안이었으며 종가는 +7.26%·+2.00%였다.*
    blocks.append(run(["corpus.py", "index", "--brief"], "로컬 코퍼스 색인 (corpus.py — 이미 가진 자료 · 전체는 corpus.py index, 내용은 search)"))
    # ★ 밀린 세션을 캡처에 박는다. 안 보이면 '안 한 것'과 '없었던 것'이 구분되지 않고,
    #   반쪽으로 죽은 run의 회고가 아무 데도 이어지지 않는다(미국 9/8·9/9가 그랬다).
    blocks.append(run(["sessions.py", "due"], "밀린 일과 (sessions.py — 반쪽·미실행·시각 이탈)"))
    # 집행 가능한 시각인가 · 수집 창이 간극을 덮는가 — 둘 다 캡처에 못 박는다.
    blocks.append(market_window_block(a.market))
    blocks.append(news_window_block(a.market, a.days, st))
    board_txt, board_md, roster = build_board(a.market, a.quick, record=not a.no_record)
    blocks.append("### 섹터 보드 (market_map.board — 전수)\n\n```\n" + board_txt.rstrip() + "\n```\n")
    # ★ 자본 기준 블록을 캡처에도 박는다 — 노트 머리말의 자본 줄 숫자(합산 자산·1%·환율)는 이 파일이
    #   출처여야 `verify_numbers`가 통과한다(2026-09-16 KR run이 이것 때문에 34건 미확인으로 4회 반복).
    blocks.append("### 자본 기준 (risk_guard.portfolio_equity — 노트 머리말 자본 줄의 출처)\n\n"
                  + capital_line(a.market, st) + "\n")
    # ★ 국면 판단 재료 — 배분 판단(allocation.regime)이 인용할 숫자를 기계로 넣는다(2026-09-22).
    #   벤치마크 5·20·60일 수익률과 20/60일선 대비 위치 · 보드 상승/하락 섹터 수. 모델은 이 숫자를 인용해 국면을 적는다.
    blocks.append("### 국면 재료 (regime_inputs — allocation.regime이 인용하는 숫자)\n\n```\n"
                  + regime_inputs(a.market, board_txt) + "\n```\n")

    evid = EVID / f"tools_{st}_{a.market}.md"
    write_preserving(
        evid,
        f"# 도구 출력 캡처 — {st} · {a.market.upper()}\n\n"
        f"> **이 run 숫자의 유일한 출처.** 장중에는 부를 때마다 값이 달라져 노트 안에서\n"
        f"> 숫자끼리 어긋난다. 이후 단계는 브로커를 다시 부르지 않고 이 파일을 읽는다.\n"
        f"> 캡처 시각 {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST\n\n"
        + "\n".join(blocks), "도구 출력 캡처")

    try:
        uni_md, uni_n = build_universe(a.market, st)
    except (OSError, ValueError, KeyError) as e:
        uni_md, uni_n = "", 0
        print(f"[경고] 유니버스 표를 못 만들었다: {e}", file=sys.stderr)

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
        note.write_text(note_skeleton(a.market, st, evid, board_md, uni_md,
                                      cap_line=capital_line(a.market, st)), encoding="utf-8")

    prev_mat = latest_stamped(PROJ / "data", "material", a.market, ".md", exclude_stamp=st)
    cur = PROJ / "data" / f"material_{st}_{a.market}.md"
    warn = ""
    if prev_mat and cur.exists() and cur.stat().st_size < prev_mat.stat().st_size * MATERIAL_SHRINK_FLOOR:
        warn = (f"\n⚠ 재료가 직전({prev_mat.name} {prev_mat.stat().st_size:,}B)보다 "
                f"{cur.stat().st_size:,}B로 크게 줄었다 — `--with-news` 누락을 의심하고 "
                f"원인부터 확인하라. 그대로 진행하면 얇아진 재료 위에서 판단하게 된다.")

    print(f"\n캡처 {rel(evid)}  ·  노트 뼈대 {rel(note)}")
    print(f"로스터 {roster}행 · 유니버스 {uni_n}종목 · 작업기록 {rel(wl)}")
    today = datetime.now(KST).strftime("%Y-%m-%d")
    print(f"게이트 인자: roster={roster} universe={uni_n} today={today} "
          f"deliverable={evid} note={note} worklog={wl}")
    print(f"  4단에서 쓸 검증 로그 경로: {EVID / f'verify_{st}_{a.market}.txt'}")
    print(f"  5단에서 쓸 시그널 경로:   {PROJ / 'signals' / f'signal_{st}_{a.market}.json'}"
          f"{warn}")
    return 0


# ─────────────────────────────────────────────────────────── carry

def last_session(market: str) -> str:
    """회고 대상 = 그 시장의 직전 세션.

    **시그널이 있는 날짜만 고른다** — `review.py`가 채점하는 대상이 시그널(그날의 결정)이라,
    거래 기록만 있는 날짜를 넘기면 "회고할 판단이 없다"로 실패한다.
    직전 날짜에 시그널이 없으면 **오늘 것이라도 가장 최근 시그널**을 쓴다 — 회고 자체를
    건너뛰는 것보다, 결과 판정을 보류하고 근거를 채점하는 편이 낫다.
    """
    stamps = sorted({p.stem.split("_")[1] for p in (PROJ / "signals").glob(f"signal_*_{market}.json")})
    if not stamps:
        return f"20{stamp_today()[:2]}-{stamp_today()[2:4]}-{stamp_today()[4:6]}"
    today = stamp_today()
    past = [s for s in stamps if s < today]
    src = past[-1] if past else stamps[-1]
    return f"20{src[:2]}-{src[2:4]}-{src[4:6]}"


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
    added_ax = [a.get("id") for a in _json(JOURNAL / "market_map.json").get("axes", [])
                if str(a.get("updated") or "").replace("-", "")[2:] == st]
    added_ev = [f"{e.get('date')} {e.get('event')}"
                for e in _json(JOURNAL / "calendar.json").get("events", [])
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


def _json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def cmd_carry(a) -> int:
    st = a.stamp or stamp_today()
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
    blocks += [
        run(["market_map.py", "cycle", "--market", a.market],
            "순환 슬롯 (cycle) — 지난 관찰의 사후 · 이월 · 다음 후보"),
        # ★ 시장 필터 없이 **열린 것 전부**. 조건은 시장에 속하지 않는다 —
        #   미국 조건이 국내 대응을 지시하는 일이 실제로 있다("KR Samsung ladder e1 fires").
        run(["scenarios.py", "list", "--open", "--brief"],
            "열린 시나리오 (전 시장 — 조건은 시장에 속하지 않는다 · 이력은 `scenarios.py list --id`)"),
        run(["scenarios.py", "audit", "--days", "14"],
            "회피 감사 (scenarios.py audit — 실현됐는데 대응했나)"),
        # ★ 실현됐는데 대응이 안 끝난 것 — "미국 조건 → 국내 대응"의 기계 경로다.
        run(["scenarios.py", "due", "--market", a.market],
            "집행 대기 (scenarios.py due — 실현됐는데 대응이 안 끝난 것)"),
        # ★ 조사 전에 연다 — 반대편 run이 이미 세운 as-of를 다시 파지 않게.
        run(["srcledger.py", "reuse", "--stamp", st, "--market", a.market, "--days", "7", "--brief"],
            "출처 재사용 후보 (srcledger.py reuse — 중복 조사 방지 · 한 줄씩)"),
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

    out = JOURNAL / f"carry_{st}_{a.market}.md"
    write_preserving(
        out,
        f"# 이어받기 — {st} · {a.market.upper()} 준비 / {(prev_mkt or '?').upper()} 회고\n\n"
        f"> 회고는 채점표이기 전에 **오늘 준비의 첫 입력**이다. 여기서 나온 것이\n"
        f"> 노트 §1-A·§1-B로 들어가야 오늘 판단에 반영된다 — 저널에만 남기면 영향을 못 준다.\n\n"
        f"## 직전 시그널 `scenarios` — 전건 판정\n\n{sc}\n\n"
        f"**판정하지 않고 넘긴 조건이 있으면 이 단계는 실패다.** 실현됐는데 대응하지 않았으면\n"
        f"그건 판단이 아니라 연기다.\n\n"
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
    if wl.exists() and "작업기록-carry" not in wl.read_text(encoding="utf-8"):
        with wl.open("a", encoding="utf-8") as fh:
            fh.write(f"\n---\n\n## 2단 · 이어받기\n\n"
                     f"- 회고 대상: {(prev_mkt or '?').upper()} {sess}\n"
                     f"- 도구 출력: `{rel(out)}`\n\n"
                     f"### 조사한 것\n\n- \n\n"
                     f"### 판단한 것과 근거\n\n- \n\n"
                     f"### 기각한 것과 이유\n\n- \n\n"
                     f"### 3단으로 넘기는 것\n\n- \n\n{stamp('작업기록-carry')}\n")

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
    st = a.stamp or stamp_today()
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(PROJ))
    import market_map as mm
    m = mm._read(mm.MAP, {"axes": []})
    axes = m.get("axes") or []
    today = datetime.now(KST).strftime("%Y-%m-%d")
    updated = [x for x in axes if x.get("direction") and (x.get("updated") or "") == today]
    blank = [x for x in axes if not x.get("direction")]
    unpriced = [(x["id"], b) for x in axes if x.get("direction")
                for b in (x.get("beneficiaries") or []) if b.get("priced") is False]
    line = gen_forecast_line(len(updated), len(unpriced), len(blank))

    out = [f"# 전망 — {st} · {a.market.upper()}", "",
           GEN_NOTICE, line, "",
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
        out.append("| 차수 | 종목 | 시장 | 유니버스 | 반영 | 근거 |")
        out.append("|---|---|---|---|---|---|")
        for b in sorted(x.get("beneficiaries") or [], key=lambda b: b.get("order", 9)):
            pr = {True: "반영", False: "**미반영**", None: "미측정"}.get(b.get("priced"), "미측정")
            out.append(f"| {b.get('order')}차 | {b.get('ticker')} {b.get('name', '')} | "
                       f"{b.get('market', '')} | {'안' if b.get('in_universe') else '밖'} | {pr} | "
                       f"{(b.get('why') or '')[:60]} |")
        out.append("")
    out += ["## 전망 미기입 축", ""]
    for x in blank:
        out.append(f"- `{x['id']}` {x.get('name', '')[:50]} — 방향·기간·수혜 순서를 채우거나, "
                   f"전망이 아니라 **배경 축**이면 그렇게 적는다")
    out += ["", "## 이번 run이 세우는 전망 (3단이 채운다)", "",
            "<!-- TODO 전망 --> 재료(세상공부 절·링크 원문·뉴스레터)에서 **돈이 갈 곳** 1건 이상. "
            "없으면 '없음 + 이유'.", "",
            "```json", "[{\"id\": \"<axis_id>\", \"name\": \"…\", \"thesis\": \"…\", "
            "\"direction\": \"+\", \"expected\": \"KOSPI 대비 +10%p\", \"horizon_weeks\": 8, "
            "\"confidence\": 0.6,", "  \"beneficiaries\": [{\"ticker\": \"…\", \"name\": \"…\", "
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
            path.write_text(cur, encoding="utf-8")
            print(f"전망 {rel(path)} (생성줄만 갱신 — 3단 절 표식이 없다)")
        print(f"  {line}")
        return 0
    write_preserving(path, "\n".join(out) + "\n", "전망 파일")
    print(f"전망 {rel(path)}")
    print(f"  {line}")
    print(f"게이트 인자: forecast={path}")
    return 0


def pick_market(now=None) -> tuple:
    """어느 시장을 준비할지 — **지금 열린 시장**, 둘 다 닫혔으면 다음에 열리는 시장. 반환 (market, open, why).

    ★ 시각 제한을 두지 않는다. 루틴 지침이 "10:35·22:35 ±90분 밖이면 건너뜀"이라 한낮에 깨워도
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
        print(f"스탬프 {path.name} ← {block.splitlines()[-1]}")
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
    a = ap.parse_args()
    return {"capture": cmd_capture, "carry": cmd_carry, "map": cmd_map, "stamp": cmd_stamp,
            "market": cmd_market}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
