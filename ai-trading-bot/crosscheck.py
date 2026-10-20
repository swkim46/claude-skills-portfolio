#!/usr/bin/env python3
"""도구가 준 것 ↔ 노트가 다룬 것을 **디스크에서 대조한다.**

왜 있는가: 사고 원장의 더 위험한 절반은 "자료가 없어서 놓친 것"이 아니라
**"자료를 손에 들고 놓친 것"**이었다. 도구가 이미 파일로 지목했는데 다음 단계가
그 파일을 안 읽어서 생긴 누락이고, 게이트는 그것을 **각서로만** 물었다 —
"우선순위를 지켰는가?"라는 질문은 지키지 않았어도 Y가 나온다.

*실사례(2026-09-10)*
- 1단이 깐 §6 유니버스 표의 상승 1·2위가 `주성엔지니어링 +10.41%`·`한미반도체 +3.59%`였는데,
  섹터 보드만 보고 슬롯을 골랐다(그 업종 지수는 −0.84%로 밋밋했다). 종가는 +7.26%·+2.00%.
- `cycle`이 "② 미해결 이월(최우선)" 2건을 지목했는데 목록에 없는 섹터를 골랐다.

그래서 **각서를 디스크 대조로 바꾼다.** 이 스크립트는 판단하지 않는다 —
도구 출력과 노트를 읽어 **이름이 실제로 들어갔는지만** 센다.

사용:
    python3 crosscheck.py --market kr --stamp 260910 \
        --out data/run_evidence/crosscheck_260910_kr.md

종료코드 0=대조 실패 0건 · 1=실패 있음 · 2=대조 자체를 못 함(입력 없음).

원장(지도·논지·일정·접속 기록·사실·재료 언급)은 저장소(`db.py`)에서 읽는다(2026-10-17) — 리서치 확장은 이번 run이 `facts.py add`로
세운 사실(ex_run.facts_new·facts_repeat), 되풀이는 사실의 repeat 칸, §3 보드 전수는 재료 언급(mat_mention: 발행분 × 섹터).
섹터 합본(`analysis/섹터_*.md`)은 2026-10-17부터 만들지 않는다 — 그 항목 줄은 '해당 없음'으로 남긴다(게이트가 줄을 센다).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import db  # noqa: E402

KST = timezone(timedelta(hours=9))
OUTLIERS_N = 3          # 상·하위 몇 종목까지 "다뤄야 하는가"


def tool(cmd: list) -> tuple:
    """도구를 돌려 출력을 받는다. 실패는 **실패로** 돌려준다('결과 없음' 아님)."""
    p = subprocess.run([sys.executable, *cmd], cwd=HERE, capture_output=True, text=True)
    if p.returncode != 0:
        return "", f"종료코드 {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}"
    return p.stdout, None


def note_path(market: str, stamp: str, given: str = "") -> Path:
    if given:
        return Path(given)
    cand = sorted((HERE / "analysis").glob(f"분석노트_{stamp}_{market}_v*.md"))
    return cand[-1] if cand else HERE / "analysis" / f"분석노트_{stamp}_{market}_없음.md"


def section(text: str, head: str, nxt: str) -> str:
    """노트에서 한 절만 잘라낸다 — 절을 안 가르면 §6 표에 있는 이름이 §4를 통과시킨다."""
    i = text.find(head)
    if i < 0:
        return ""
    j = text.find(nxt, i + len(head))
    return text[i:j if j > 0 else len(text)]


# ───────────────────────────────────────────────────────────── 개별 대조

def check_outliers(market: str, stamp: str, note: str) -> list:
    """유니버스 등락 상·하위 N종목이 노트 §4(슬롯 선정)나 §6(종목 표)에 들어갔는가."""
    snap = HERE / "data" / f"snapshot_{stamp}_{market}.json"
    if not snap.exists():
        return [("유니버스 이상치", False, f"스냅샷이 없다: {snap.name}")]
    try:
        prices = json.loads(snap.read_text(encoding="utf-8")).get("prices") or {}
    except (json.JSONDecodeError, OSError) as e:
        return [("유니버스 이상치", False, f"스냅샷을 읽을 수 없다: {e}")]
    rows = [(t, v.get("name") or t, v.get("change_pct"))
            for t, v in prices.items() if isinstance(v.get("change_pct"), (int, float))]
    if len(rows) < 2 * OUTLIERS_N:
        return [("유니버스 이상치", False, f"등락을 잰 종목이 {len(rows)}개뿐이다")]
    rows.sort(key=lambda r: r[2], reverse=True)
    picked = rows[:OUTLIERS_N] + rows[-OUTLIERS_N:]
    out = []
    for t, name, chg in picked:
        hit = (t in note) or (name in note)
        out.append((f"이상치 {name}({t}) {chg:+.2f}%", hit,
                    "노트에 등장" if hit else
                    "**노트에 이름이 없다** — 도구가 1·2위로 지목했는데 아무 절도 다루지 않았다"))
    return out


def check_prev_carried(market: str, stamp: str, note: str) -> list:
    """**직전 run이 새로 만든 축·일정·리서치가 오늘 노트에 등장하는가.**

    각서가 아니라 **이름으로 센다** — "이어받았다"는 답은 언제나 Y로 나오지만,
    그 run이 세운 축 이름이 오늘 노트에 한 번도 안 나오면 이어받지 않은 것이다.
    *실측(2026-09-11): 직전 run 10개 중 8개가 회고된 적이 없었다.*
    """
    sys.path.insert(0, str(HERE))
    try:
        import sessions as _s, stage as _st
    except ImportError as e:
        return [("직전 run 산출 반영", False, f"모듈을 못 읽었다: {e}")]
    pv = _s.prev_run(14, market, stamp)
    if not (pv.get("market") and pv.get("session_date")):
        return [("직전 run 산출 반영", True, "직전 run이 없다 — 이어받을 것이 없다")]
    pst = pv["session_date"][2:].replace("-", "")
    rows = []
    axes = [a for a in db.market_map_load().get("axes") or []
            if str(a.get("updated") or "").replace("-", "")[2:] == pst]
    if axes:
        miss = [a["id"] for a in axes
                if a["id"] not in note and (a.get("name") or "×") not in note]
        rows.append(("직전 run의 새 축이 §2에 등장", not miss,
                     f"축 {len(axes)}개 전부 등장" if not miss else
                     f"**등장하지 않은 축 {len(miss)}개**: {', '.join(miss)} — "
                     f"직전 run({pv['market'].upper()} {pv['session_date']})이 세운 축이 "
                     f"오늘 노트에 한 번도 안 나온다. 조사가 하루 만에 버려진 것이다"))
    evs = [e for e in db.calendar_all()
           if str(e.get("added") or "").replace("-", "")[2:] == pst]
    if evs:
        miss = [e["event"] for e in evs if str(e.get("event"))[:14] not in note]
        rows.append(("직전 run의 새 일정이 §5에 등장", not miss,
                     f"일정 {len(evs)}건 전부 등장" if not miss else
                     f"**등장하지 않은 일정 {len(miss)}건**: {'; '.join(x[:40] for x in miss)}"))
    # 리서치 = 결론이 담긴 조사 파일. `AI클레임_*`은 fact-check 작업 파일이라 제외한다
    # — 인용 대상이 아니라 검증 이력이다(오탐이 나면 진짜 누락이 묻힌다).
    res = [f for f in sorted((HERE / "analysis").glob("*.md"))
           if pst in f.name and not f.name.startswith(("분석노트", "AI클레임"))]
    if res:
        miss = [f.name for f in res if f.stem[:24] not in note and f.name not in note]
        rows.append(("직전 run의 리서치가 인용됨", not miss,
                     f"리서치 {len(res)}건 인용됨" if not miss else
                     f"**인용되지 않은 리서치 {len(miss)}건**: {', '.join(miss)} — "
                     f"결론이 파일에만 남고 판단에 안 들어갔다"))
    if not rows:
        rows.append(("직전 run 산출 반영", True,
                     f"직전 run({pv['market'].upper()} {pv['session_date']})이 "
                     f"새로 만든 축·일정·리서치가 없다"))
    return rows


def check_generated_lines(market: str, stamp: str, note: str) -> list:
    """**1단이 생성해 넣은 줄이 노트에 그대로 있는가.**

    게이트의 기준들은 이 줄들을 *글자 그대로* 찾는다 — 즉 계약이다. 그런데 노트를
    쓰는 단계는 "노트 언어를 재료 언어에 맞춘다"는 규칙을 따르느라 영어 run에서
    생성줄까지 다듬을 수 있고, 그러면 **불투명한 루브릭 FAIL**로 나타난다.

    *실사례(2026-09-11): 미국 노트가 §6 요약 줄을 영어 설명으로 바꿔 써서 `note`
    게이트가 `hits=0` 하나로 막혔다. 어느 기준인지 알 수 없어 표기 후보 200개를
    탐침하고도 특정하지 못했고, 그 run은 거기서 끝났다.*

    그래서 **그 실패를 여기서 읽을 수 있는 형태로** 먼저 잡는다 — 기대한 줄과
    노트에 있는 줄을 나란히 보여준다.
    """
    sys.path.insert(0, str(HERE))
    try:
        import stage                       # 생성 문구의 원본은 stage.py 한 곳뿐이다
    except ImportError as e:
        return [("1단 생성줄 보존", False, f"stage.py를 못 읽었다: {e}")]

    rows = []
    # §6 유니버스 요약 — 종목 수는 스냅샷에서 그대로 센다(노트가 아니라 원본에서).
    snap = HERE / "data" / f"snapshot_{stamp}_{market}.json"
    if snap.exists():
        try:
            s = json.loads(snap.read_text(encoding="utf-8"))
            n = len(s.get("prices") or {}) + len(s.get("price_errors") or {})
            want = stage.gen_universe_line(n)
            rows.append(("§6 유니버스 생성줄", want in note, _why(note, want, "유니버스")))
        except (json.JSONDecodeError, OSError) as e:
            rows.append(("§6 유니버스 생성줄", False, f"스냅샷을 읽을 수 없다: {e}"))
    else:
        rows.append(("§6 유니버스 생성줄", False, f"스냅샷이 없다: {snap.name}"))

    # 머리말 자본 줄 — **두 시장을 같은 척도로 적었는가.** 분모는 장중에 변하므로
    # 금액을 다시 맞추지 않고, `1% = …` 병기가 살아 있는지만 본다.
    cap_ok = ("자본 기준 —" in note
              and ("1% = " in note or "못 쟀다" in note))
    rows.append(("머리말 자본 생성줄", cap_ok,
                 "포트폴리오 분모와 `1% = …` 병기가 있다" if cap_ok else
                 "**자본 줄이 지워졌거나 바뀌었다** — 1단이 깐 "
                 "`자본 기준 — **포트폴리오 합산 …** · **1% = …**` 줄을 그대로 둔다. "
                 "이 줄이 없으면 노트의 %가 어느 분모인지 알 수 없어 "
                 "두 시장의 숫자를 비교할 수 없다(8% vs 2%가 뒤집힌 적이 있다)"))

    # §3 커버리지 요약 — 숫자를 여기서 다시 세지 않고, 형태만 본다(보드는 장중에 변한다).
    pat = re.compile(r"^커버리지 \*\*\d+/\d+\*\* — 미조사 \d+개", re.M)
    hit = bool(pat.search(note))
    rows.append(("§3 커버리지 생성줄", hit,
                 "생성 형태 그대로 있다" if hit else
                 "**생성 형태가 아니다** — `커버리지 **n/m** — 미조사 k개` 줄을 "
                 "1단이 깔아 준 그대로 두어야 한다(굵은 표기 포함)"))
    return rows


# 게이트가 **글자 그대로** 세는 표기와, 모델이 자주 만드는 장식 변형(볼드·백틱·헤더 레벨 오기).
MARKERS = [
    ("### 섹터 · ", r"^### (섹터|Sector) · ", r"\*\*(섹터|Sector) · |^(?:#{1,2}|#{4,}) (섹터|Sector) · |`(섹터|Sector) · "),
    ("#### 심층 · ", r"^#### (심층|Deep) · ", r"\*\*(심층|Deep) · |^(?:#{1,3}|#{5,}) (심층|Deep) · |`(심층|Deep) · "),
    ("③ 사는 쪽", r"③ (사는 쪽|Case for)", r"③ \*\*(사는 쪽|Case for)|③ `(사는 쪽|Case for)"),
    ("④ 안 사는 쪽", r"④ (안 사는 쪽|Case against)", r"④ \*\*(안 사는 쪽|Case against)|④ `(안 사는 쪽|Case against)"),
    ("breaks_if", r"breaks_if", r"breaks[ -]if|\*\*breaks_if\*\*"),
    ("축 → 섹터", r"(축|Axis) → (섹터|Sector)", r"(축|Axis) -> (섹터|Sector)|(축|Axis)→(섹터|Sector)"),
    ("D-n", r"\bD-\d+\b", r"\bD -\d+|\bD- \d+|\bD ?−\d+"),
]


def check_markers(note: str) -> list:
    """**게이트가 세는 표기가 그 글자로 있는가** — 있는데 장식(볼드·백틱)으로 감싸 0건이 된 것을 이름 지어 준다.

    *실사례(2026-09-16): `③ **사는 쪽**`으로 써서 `note` 게이트가 hits=0, `breaks_if` 표기 0건으로 `map`
    게이트 FAIL. 둘 다 내용은 있었고 표기만 어긋났다 — 불투명한 FAIL을 읽을 수 있는 실패로 바꾼다.*
    """
    rows = []
    for name, ok_pat, bad_pat in MARKERS:
        n_ok = len(re.findall(ok_pat, note, flags=re.M))
        n_bad = len(re.findall(bad_pat, note, flags=re.M))
        # 장식 변형은 정상 표기와 겹칠 수 있다(`③ **사는 쪽**`은 ok에 안 잡힌다) — bad만 따로 센다.
        ok = n_ok > 0 and n_bad == 0
        why = f"정상 표기 {n_ok}건" + (f" · **장식 변형 {n_bad}건** — 볼드·백틱·헤더 레벨을 벗겨 그대로 써라" if n_bad else "")
        if n_ok == 0 and n_bad == 0:
            why = "**0건** — 이 표기가 없으면 게이트가 내용을 못 센다(문구를 글자 그대로)"
        rows.append((f"표기 `{name}`", ok, why))
    return rows


def check_fill_line(note: str, trades_path: Path) -> list:
    """**6단 체결 확정 줄이 파일 숫자와 같은가.** `fill.py`가 §11에 쓴 생성줄(`<!-- gen:fill -->`)의
    건수가 `trades_*.json`의 `fill_confirmed`와 일치하고 미확정이 0건인지 본다 — 노트가 말로
    "체결됐다"고 쓰는 것과 파일이 종결 상태인 것은 다른 사실이다.
    """
    sys.path.insert(0, str(HERE))
    try:
        import stage
    except ImportError as e:
        return [("§11 체결 확정줄", False, f"stage.py를 못 읽었다: {e}")]
    try:
        rec = json.loads(Path(trades_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [("§11 체결 확정줄", False, f"거래 기록을 읽을 수 없다: {e}")]
    summ = rec.get("fill_confirmed")
    if not summ:
        return [("§11 체결 확정줄", False,
                 "**`fill_confirmed`가 없다** — `fill.py <trades> --note <노트>`를 아직 안 돌렸다")]
    want = stage.gen_fill_line(summ)
    ok = want in note and int(summ.get("open_orders", 1)) == 0
    return [("§11 체결 확정줄", ok,
             "생성줄이 파일 숫자 그대로 있고 미확정 0건" if ok else
             ("**미확정 %d건** — 종결될 때까지 `fill.py`를 다시 돌린다" % int(summ.get("open_orders", 0))
              if int(summ.get("open_orders", 0)) else _why(note, want, "체결 확정")))]


def check_reconcile(trades_path: Path) -> list:
    """**집행 수량이 승인 안인가.** `fill.finalize`가 쓴 `reconcile`을 본다 — 초과 0건이거나,
    초과가 있으면 사고(`INC-…`)가 기록돼 있어야 통과. 2026-09-21 승인 46/집행 92가 그대로 통과한 자리."""
    try:
        rec = json.loads(Path(trades_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [("승인 대조(초과)", False, f"거래 기록을 읽을 수 없다: {e}")]
    r = rec.get("reconcile")
    if not r:
        return [("승인 대조(초과)", False, "**`reconcile`이 없다** — `fill.py <trades>`를 다시 돌려 대조를 남긴다")]
    n = int(r.get("overfill") or 0)
    unk = r.get("unknown_refs") or []
    if n == 0:
        return [("승인 대조(초과)", True,
                 f"종목·방향 {len(r.get('executed') or {})}건 전부 승인 안"
                 + (f" (승인 미상 참조 {', '.join(unk)} — 그 건은 대조 밖)" if unk else ""))]
    ids = r.get("incidents") or []
    ok = bool(r.get("incident_recorded")) and bool(ids)
    detail = "; ".join(f"{o['ticker']} {o['action']} 승인 {o['approved']}/집행 {o['executed']}"
                       for o in r.get("overfill_list") or [])
    return [("승인 대조(초과)", ok,
             (f"**승인 초과 {n}건 — 사고 기록 {', '.join(ids)}**: {detail}" if ok else
              f"**승인 초과 {n}건인데 사고 기록이 없다**: {detail} — `fill.py <trades>`를 돌려 기록을 남긴다"))]


def _why(note: str, want: str, key: str) -> str:
    """기대한 줄과 노트에 실제로 있는 줄을 나란히 — 추측하지 않게."""
    if want in note:
        return "생성 형태 그대로 있다"
    near = [l.strip() for l in note.splitlines() if key in l and "|" not in l][:2]
    # 표 한 칸 안에 들어가야 하므로 줄바꿈은 `<br>`로 — 실제 개행을 넣으면 표가 깨진다.
    return ("**생성줄이 바뀌었다.**<br>기대: `" + want + "`<br>"
            + "<br>".join(f"노트: `{l[:100]}`" for l in near or ["(비슷한 줄 없음)"])
            + "<br>→ 1단이 깔아 준 줄은 영어 run에서도 **그대로 둔다**. "
              "영어 설명은 그 줄 밖에 붙인다.")


def check_cycle(note: str) -> list:
    """`cycle`의 '② 미해결 이월(최우선)'을 §4에서 다뤘거나 **명시적으로 연기**했는가.

    통과 조건이 둘인 이유: 최우선 이월을 오늘 또 못 풀 수는 있다. 그러나 그때는
    **왜 못 풀었고 언제 푸는지**가 노트에 적혀야 한다(`예정일`·`resolves_when`).
    지나가는 말로 이름만 스치는 것은 다룬 것이 아니다 — 그래서 표제와 연기 근거만 본다.
    """
    out, err = tool(["market_map.py", "cycle"])
    if err:
        return [("순환 슬롯 이월", False, f"cycle을 돌리지 못했다 — {err}")]
    blk = section(out, "미해결 이월", "③")
    names = re.findall(r"^\s{4,}(\S+)\s{2,}등급", blk, re.M)
    if not names:
        return [("순환 슬롯 이월", True, "이월 0건 — 대조할 것이 없다")]
    heads = " / ".join(re.findall(r"###\s*섹터\s*·\s*(.+)", note))
    rows = []
    for n in names:
        if n in heads:
            rows.append((f"이월 {n}", True, "§4 섹터 표제로 다뤘다"))
            continue
        deferred = any(
            re.search(r"(예정일|resolves_when)", note[max(0, m.start() - 400):m.end() + 400])
            for m in re.finditer(re.escape(n), note))
        rows.append((f"이월 {n}", deferred,
                     "§4 표제는 아니지만 **예정일·resolves_when과 함께 연기**로 적혔다" if deferred
                     else f"**다루지도, 연기 근거를 적지도 않았다** — §4 표제: {heads or '(없음)'}"))
    return rows


def check_research_is_today(market: str, stamp: str) -> list:
    """(2026-10-17 중단) 섹터 합본 파일(`analysis/섹터_*.md`)은 더 만들지 않는다 — 리서치는 저장소 사실(`facts.py add`)과
    갈래 파일(`data/run_evidence/research_branch*`)로 남는다. 항목 줄은 게이트가 세므로 '해당 없음'으로 남긴다."""
    return [(f"오늘자 섹터 리서치({stamp})", True, "해당 없음(2026-10-17 중단)")]


EXPANSION_HEAD = "## 재료 확장"


def _research_files(market: str, stamp: str) -> list:
    """이번 run 리서치 파일 — 갈래 파일(`data/run_evidence/research_branch*_<st>_<mkt>.md`) + 옛 섹터 합본(있으면 · 중단 전 run)."""
    return sorted({*(HERE / "data" / "run_evidence").glob(f"research_branch*_{stamp}_{market}*.md"),
                   *(HERE / "analysis").glob(f"섹터_*_{stamp}_*.md")})


def _run_facts(market: str, stamp: str) -> tuple:
    """이번 run이 세운 조사 사실 — (리서치 신규[행], 되풀이[행], 재사용 수, 재료에서 꺼낸 행 수). 도구 사실(origin=tool)은 세지 않는다.
    재료에서 꺼낸 사실(origin issue·link·ws_event·dart)은 뉴스레터·공시가 이미 준 것이라 확장이 아니다 — 따로 센다."""
    rid = db.run_id(stamp, market)
    rows = db.q("SELECT f.id, f.url, f.repeat, f.created_run, f.origin FROM ev_fact_run r JOIN ev_fact f ON f.id=r.fact_id"
                " WHERE r.run_id=? AND COALESCE(f.origin,'')<>'tool'", (rid,))
    made = [r for r in rows if r["created_run"] == rid]
    if not rows:                                       # 이번 run에 단 S 번호가 없어도 이번 run이 만든 행은 센다
        made = db.q("SELECT id, url, repeat, created_run, origin FROM ev_fact WHERE created_run=? AND COALESCE(origin,'')<>'tool'",
                    (rid,))
    research = [r for r in made if (r["origin"] or "research") == "research"]
    new = [r for r in research if not r["repeat"]]
    rep_ = [r for r in research if r["repeat"]]
    return new, rep_, len([r for r in rows if r["created_run"] != rid]), len(made) - len(research)


def check_research_expansion(market: str, stamp: str) -> list:
    """**리서치가 재료를 확장했는가.** 이번 run이 `facts.py add`로 세운 사실(ex_run.facts_new · 없으면 사실 표에서 센다) 가운데
    재료(이번 run 발행분의 링크 mat_link)를 되풀이하지 않은 것이 있어야 한다 — 되풀이는 등록 때 사실의 repeat 칸에 찍힌다.
    리서치는 출처 확인이 아니라 **더 많은 사실·상황을 아는 것**이다(2026-09-22 사용자) — 확장이 0이면 그 run은 뉴스레터만 읽고 판단한 것이다."""
    name = "리서치 재료 확장"
    new, rep_, reused, from_mat = _run_facts(market, stamp)
    r = db.run_get(stamp, market) or {}
    n_new, n_rep = len(new), len(rep_)                 # ex_run.facts_new는 재료에서 꺼낸 행까지 센다 — 확장은 리서치 행만
    if not n_new and not n_rep:
        return [(name, False, "**이번 run이 세운 리서치 사실이 0건이다**"
                 + (f"(재료에서 꺼낸 사실 {from_mat}건은 확장이 아니다)" if from_mat else "")
                 + " — 3단 리서치 결과를 `facts.py add`로 종류별 행으로 등록하라"
                 "(사실 · 출처(등급 · as-of) · 원문 인용 — 확장 0건은 리서치가 아니다)")]
    if not n_new:
        return [(name, False, f"**{n_rep}행 전부 재료에 이미 있는 링크의 되풀이** — 확장이 아니다")]
    try:
        import facts as _f
        norm = _f._norm_url
    except Exception:                                  # noqa: BLE001
        norm = str
    urls = {norm(x["url"]) for x in new if x.get("url")}
    return [(name, True, f"확장 {n_new}행(되풀이 {n_rep}행 · 재료에서 꺼낸 {from_mat}행 제외) · 새 출처 {len(urls)}건 · 재사용 {r.get('facts_reused') if r.get('facts_reused') is not None else reused}건(저장소 ev_fact)")]


def _cites_research(text: str, stems: set, urls: set, ids: set = frozenset()) -> bool:
    t = text or ""
    if "재료 확장" in t or "[확장" in t:
        return True
    if any(st in t for st in stems):
        return True
    if any(i in t for i in ids):
        return True
    return any(u in t for u in urls)


def check_research_cited(market: str, stamp: str) -> list:
    """**확장이 판단에 실렸는가.** 이번 run이 새로 세우거나 갱신한 전망(축 `updated`=오늘)·논지(`created`=오늘)가 리서치를
    인용해야 한다 — 이번 run 사실 id(F-…)·그 URL·리서치 파일명·`재료 확장` 중 하나(논지 근거가 사실 id를 가리키면 그것이 인용이다).
    인용이 0이면 리서치가 판단에 안 들어간 것이다."""
    name = "리서치 인용(전망·논지)"
    today = f"20{stamp[:2]}-{stamp[2:4]}-{stamp[4:6]}"
    files = _research_files(market, stamp)
    stems = {f.stem for f in files} | {f.name for f in files}
    _rows, urls, _n = _expansion_rows(files)
    new, rep_, _reused, _from_mat = _run_facts(market, stamp)
    urls |= {x["url"] for x in new if x.get("url")}
    ids = {x["id"] for x in new + rep_} | {r["fact_id"] for r in db.q("SELECT fact_id FROM ev_fact_run WHERE run_id=?",
                                                                       (db.run_id(stamp, market),))}
    uncited, checked = [], 0
    for ax in db.market_map_load().get("axes") or []:
        if ax.get("updated") != today and ax.get("opened") != today:
            continue
        checked += 1
        blob = json.dumps({k: ax.get(k) for k in ("thesis", "chain", "origin", "indicators", "beneficiaries", "breaks_if", "research")},
                          ensure_ascii=False)
        if not _cites_research(blob, stems, urls, ids):
            uncited.append(f"전망 {ax.get('id')}")
    for t in db.theses_all():
        if not str(t.get("created") or "").startswith(today):
            continue
        # 2026-10-14부터 국내·미국 run이 같은 세션 날짜를 쓴다 — 논지는 시장에 속하므로 반대 시장 run이 그날 세운 논지는 이 run 몫이 아니다
        # (실사례 261019: US run이 KR run의 kr-lowpbr-hana-261019를 '오늘 세운 논지'로 셌다). 전망(축)은 요인 단위라 시장 필터를 걸지 않는다.
        if str(t.get("market") or "").upper() not in ("", market.upper()):
            continue
        checked += 1
        blob = json.dumps({k: t.get(k) for k in ("thesis", "evidence", "axis_why", "invalidation", "research")}, ensure_ascii=False)
        if not _cites_research(blob, stems, urls, ids):
            uncited.append(f"논지 {t.get('id')}")
    if not checked:
        return [(name, True, "오늘 새로 세운 전망·논지 없음 — 대조할 것이 없다")]
    if uncited:
        return [(name, False, f"**리서치 인용 없음 {len(uncited)}/{checked}**: " + ", ".join(uncited)
                 + " — 전망 thesis/chain·논지 thesis/evidence에 이번 run 사실 id(F-…)·리서치 파일명 또는 `재료 확장` 행(URL)을 인용하라")]
    return [(name, True, f"오늘 세운 전망·논지 {checked}건 전부 리서치 인용")]


def _expansion_rows(files: list) -> tuple:
    """리서치 파일들의 `## 재료 확장` 절 → (행 목록, URL 집합, 절이 있는 파일 수) — 인용 대조용(확장 수는 저장소 사실에서 센다)."""
    rows, urls, n_sec = [], set(), 0
    for f in files:
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if EXPANSION_HEAD not in txt:
            continue
        n_sec += 1
        sec = txt.split(EXPANSION_HEAD, 1)[1]
        sec = re.split(r"\n## ", sec, 1)[0]
        for ln in sec.splitlines():
            t = ln.strip()
            if not t or t.startswith(("|---", "| ---", "|:")):
                continue
            if t.startswith(("- ", "* ", "|")) and not re.match(r"^\|\s*(사실|항목|번호|#)", t):
                rows.append(t)
                urls.update(re.findall(r"https?://[^\s)\]>`|]+", t))
    return rows, urls, n_sec


def check_board_mentions(market: str, stamp: str) -> list:
    """**§3 섹터 보드가 재료 전수인가**(2026-10-17) — 이번 run 발행분마다 섹터 로스터(1단 보드 기록) 전부에 재료 언급 행(mat_mention)이
    있어야 한다. 다루지 않은 섹터도 '해당 없음' 행으로 남긴다(누락을 침묵이 아니라 행으로 · `facts.py mention`의 섹터 '*' 한 행)."""
    name = "§3 섹터 보드 전수(재료 언급)"
    rid = db.run_id(stamp, market)
    issues = db.material_issues_for_run(rid) or [r["id"] for r in db.q(
        "SELECT id FROM mat_issue WHERE run_id=? OR first_run=? ORDER BY id", (rid, rid))]
    if not issues:
        return [(name, True, "이번 run 재료 발행분 0통 — 대조할 것이 없다")]
    try:
        import facts as _f
        roster = _f.board_roster(stamp, market)[0]
        ments = _f.board_mentions(stamp, market, roster)
    except Exception as e:                             # noqa: BLE001
        return [(name, False, f"재료 언급을 못 읽었다 — {type(e).__name__}: {e}")]
    if not roster:
        return [(name, False, "**섹터 로스터가 없다** — 1단 capture가 이 세션 보드를 적재하지 않았다(--no-record?)")]
    have = {(m.get("eff_issue"), code) for code, ms in ments.items() for m in ms}   # 링크 언급은 그 링크의 발행분으로
    miss = {}
    for iid in issues:
        gap = [r["name"] for r in roster if (iid, r["key"]) not in have]
        if gap:
            miss[iid] = gap
    n_m = sum(len(ms) for ms in ments.values())
    if not miss:
        return [(name, True, f"발행분 {len(issues)}통 × 섹터 {len(roster)}개 전부 행 있음(언급 행 {n_m})")]
    n_gap = sum(len(v) for v in miss.values())
    ex = "; ".join(f"#{k}: {', '.join(v[:4])}" + (" …" if len(v) > 4 else "") for k, v in list(miss.items())[:4])
    return [(name, False, f"**빈 칸 {n_gap}개(발행분 {len(miss)}/{len(issues)}통)** — {ex} · `facts.py mention`으로 발행분마다 섹터 전부에 행을 적어라"
             "(다루지 않은 섹터는 `{\"issue_id\": N, \"subject_type\": \"sector\", \"subject_id\": \"*\", \"board_verdict\": \"해당 없음\"}` 한 행)")]


def check_corpus(market: str, stamp: str, note: str) -> list:
    """`조사불가`로 닫았으면 **안을 먼저 뒤진 로그**가 있어야 하고 읽기 실패가 0이어야 한다."""
    n_unres = len(re.findall(r"조사불가", note))
    log = HERE / "data" / "run_evidence" / f"corpus_{stamp}_{market}.md"
    if not n_unres:
        return [("코퍼스 대조", True, "노트에 `조사불가` 0건 — 대조할 것이 없다")]
    if not log.exists():
        return [(f"코퍼스 대조(조사불가 {n_unres}건)", False,
                 f"**{log.name}이 없다** — 밖만 찾고 닫았다. "
                 f"`corpus.py search <키워드> --out {log.relative_to(HERE)}`")]
    txt = log.read_text(encoding="utf-8", errors="ignore")
    n_search = len(re.findall(r"■ 코퍼스 검색", txt))
    # ★ 실패 건수는 corpus.py가 찍는 **헤더**에서 읽는다 (2026-09-11 수정).
    #   예전에는 로그 안의 `✗ ` 개수를 셌는데, 그 로그에는 **검색 히트로 인용된 본문**이
    #   같이 들어간다. 노트·저널에 `✗ 미체결`·`✗ 끊긴 연쇄` 같은 표기가 흔해서,
    #   읽기 실패가 0인데도 인용문 때문에 실패로 잡혔다(실측: 한화오션 검색 시 9/10 노트의
    #   `✗ 미체결` 3건이 그대로 집계). 검사 대상은 **검색 결과의 내용이 아니라 도구의 상태**다.
    n_fail = sum(int(m) for m in re.findall(r"★ 읽지 못한 파일 (\d+)개", txt))
    return [
        (f"코퍼스 검색 로그(조사불가 {n_unres}건)", n_search >= 1,
         f"검색 {n_search}회 기록됨" if n_search else "**로그에 검색 기록이 없다**"),
        ("코퍼스 읽기 실패 0", n_fail == 0,
         "읽기 실패 0" if not n_fail else
         f"**읽지 못한 파일 {n_fail}개** — 그 파일에 답이 있을 수 있으니 '없다'로 닫을 수 없다"),
    ]


PDF_URL = re.compile(r"https?://[^\s)\]>`|\"']+?\.pdf(?:\?[^\s)\]>`|\"']*)?(?=[\s)\]>`|\"',.;]|$)", re.I)


def _fetch_log() -> dict:
    """시도 기록(저장소 ev_fetch · `db.fetch_rows()` — 옛 op_fetch 행도 이전 v3가 옮겼다) → url → [행].
    `fetch.py`가 여는 링크마다 한 행씩 남긴다(행에 url·kind·chars가 있다)."""
    out = {}
    for r in db.fetch_rows():
        out.setdefault(r.get("url", ""), []).append(r)
    return out


def check_pdf_sources(market: str, stamp: str, note: str) -> list:
    """**PDF 출처를 `fetch.py`로 열었는가.** 리서치·노트가 인용한 PDF 주소마다 `fetch_log`에 본문이 나온 기록이
    있어야 한다. 기록이 없으면 WebFetch·내장 브라우저로 연 것이다 — 둘 다 사용자 화면에 다운로드 카드를 띄우고
    본문은 못 읽는다(2026-09-24~10-02 16회 전부 실패). 못 열었으면 노트 §10에 '열지 못한 것'으로 적으면 통과한다."""
    name = "PDF 출처 → fetch.py 열기 기록"
    files = sorted({*_research_files(market, stamp), *(HERE / "analysis").glob(f"*_{stamp}_{market}*.md")})
    texts = [note] + [f.read_text(encoding="utf-8", errors="ignore") for f in files]
    urls = sorted({u for t in texts for u in PDF_URL.findall(t)})
    if not urls:
        return [(name, True, "인용된 PDF 주소 0건")]
    log = _fetch_log()
    sec10 = section(note, "## §10", "## §11")
    disclosed = lambda u: u in sec10 or any(u in ln for ln in note.splitlines() if re.search(r"열지 못한|못 연|못 열", ln))
    bad, tried = [], []
    for u in urls:
        rows = log.get(u) or []
        if any(r.get("kind") == "pdf" and (r.get("chars") or 0) > 0 for r in rows):
            continue
        if disclosed(u):
            continue
        (tried if rows else bad).append(u)
    if not bad and not tried:
        return [(name, True, f"PDF {len(urls)}건 전부 fetch.py 기록 있음(또는 §10에 '열지 못한 것'으로 공개)")]
    why = []
    if bad:
        why.append(f"**열기 기록 없음 {len(bad)}건** — WebFetch·브라우저로 열었을 가능성(사용자 화면에 다운로드 카드): "
                   + " · ".join(bad[:4]))
    if tried:
        why.append(f"**fetch.py로 열었으나 본문 0 {len(tried)}건** — §10에 '열지 못한 것'으로 적어라: " + " · ".join(tried[:4]))
    return [(name, False, " / ".join(why))]


THESIS_ID = re.compile(r"\b(?:kr|us)-[a-z0-9-]+-\d{6}\b")


def _names(market: str) -> dict:
    """유니버스 {종목: 이름} — 저장소 dim_company(config/watchlist.json은 그 렌더본)."""
    return {r["ticker"]: r.get("name", "") for r in db.companies(market.upper()) if r.get("ticker")}


def _judged_lines(text: str, ticker: str, name: str) -> bool:
    """그 종목이 나오는 줄에 논지 id 또는 `[통과]`가 같이 있는가 — 유니버스 표 행에 이름만 있는 것은 판정이 아니다."""
    for ln in text.splitlines():
        if ticker in ln or (len(name or "") >= 2 and name in ln):
            if "[통과]" in ln or THESIS_ID.search(ln):
                return True
    return False


def check_reeval(market: str, stamp: str, note: str) -> list:
    """**재평가 목록(직전 run이 가격을 이유로 거른 종목)을 오늘 다시 판정했는가**(FB-261003-1).
    급등 당일 매수 금지는 그 세션에만 적용한다 — 다음 run은 논지 id 또는 `[통과] <이유>`로 다시 판정해야 한다."""
    name = "재평가 목록 → 노트 재판정"
    try:
        sys.path.insert(0, str(HERE))
        import stage
        rv = stage.reeval_list(market, stamp)
    except Exception as e:                        # noqa: BLE001
        return [(name, False, f"재평가 목록을 만들지 못했다 — {type(e).__name__}: {e}")]
    if not rv:
        return [(name, True, "직전 run이 가격 사유로 거른 종목 0건")]
    nm = _names(market)
    sec = (section(note, "## §6", "## §7") or "") + (section(note, "## §1-A", "## §1-B") or "")
    miss = [tk for tk, _src, _why in rv if not _judged_lines(sec, tk, nm.get(tk, ""))]
    return [(name, not miss, f"{len(rv)}건 전부 재판정(논지 id 또는 [통과])" if not miss else
             f"**재판정 안 된 {len(miss)}건: {', '.join(miss)}** — §6에 논지 id 또는 `[통과] <이유>`를 적어라")]


def check_support_break(market: str, stamp: str, note: str) -> list:
    """**지지 이탈 종목을 오늘 재판정했는가**(2026-10-14 · 차트스쿨 01 B). 이어받기 `## 지지 이탈` 표의 종목마다
    노트 §6에 `[지지 이탈]`이 든 그 종목 줄(무효 조건 재판정: 유지·축소·청산과 이유)이 있어야 한다."""
    name = "지지 이탈 → §6 재판정"
    cp = HERE / "journal" / f"carry_{stamp}_{market}.md"
    try:
        txt = cp.read_text(encoding="utf-8")
    except OSError:
        return [(name, False, f"이어받기 파일이 없다: {cp.name}")]
    sec = section(txt, "## 지지 이탈", "\n## ")
    if not sec:
        return [(name, False, "이어받기에 `## 지지 이탈` 절이 없다 — stage.py carry를 다시 돌려라")]
    rows = [ln for ln in sec.splitlines() if ln.startswith("| ") and not ln.startswith("| 논지") and not ln.startswith("|---")]
    tks = [ln.split("|")[2].strip().split(" ")[0] for ln in rows if len(ln.split("|")) > 3]
    if not tks:
        return [(name, True, "지지 이탈 종목 0건")]
    s6 = section(note, "## §6", "## §7") or ""
    miss = [tk for tk in tks if not any("[지지 이탈]" in ln and tk in ln for ln in s6.splitlines())]
    return [(name, not miss, f"{len(tks)}건 전부 §6에 [지지 이탈] 재판정" if not miss else
             f"**재판정 줄이 없는 {len(miss)}건: {', '.join(miss)}** — §6에 `[지지 이탈] <종목> — <유지·축소·청산과 이유>`를 적어라")]


def check_queue_one_day(market: str, stamp: str, note: str) -> list:
    """**하루만 제안된 유니버스 대기 후보를 오늘 다시 추천했거나 내렸는가**(FB-261010-6 · 2026-10-11 사용자 승인).
    다시 언급하지 않으면 냉각(서로 다른 이틀)을 영영 못 채워 묻힌다 — 261010: 하루 제안된 수혜주 둘이 2주 넘게 멈춘 사이 +21~33%.
    판정 = 오늘 시그널 `watchlist_candidates`에 다시 올렸거나, 노트 §8의 그 이름 줄에 `[내림]`이 있다."""
    name = "대기 후보(하루만 제안됨) → 재제안·[내림]"
    try:
        sys.path.insert(0, str(HERE))
        import stage
        q1 = stage.queue_one_day(market, stamp)
    except Exception as e:                        # noqa: BLE001
        return [(name, False, f"대기 후보 목록을 만들지 못했다 — {type(e).__name__}: {e}")]
    if not q1:
        return [(name, True, "하루만 제안된 대기 후보 0건")]
    re_prop = set()
    for f in sorted((HERE / "signals").glob(f"signal_{stamp}_{market}*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        re_prop |= {str(c.get("ticker") or "") for c in d.get("watchlist_candidates") or []}
    sec = section(note, "## §8", "## §9") or ""
    miss = []
    for tk, nm, _d, _ax, _w in q1:
        if tk in re_prop:
            continue
        if any((tk in ln or (len(nm) >= 2 and nm in ln)) and "[내림]" in ln for ln in sec.splitlines()):
            continue
        miss.append(f"{tk} {nm}".strip())
    return [(name, not miss, f"{len(q1)}건 전부 재제안 또는 [내림]" if not miss else
             f"**판정 없는 {len(miss)}건: {', '.join(miss[:8])}" + (" …" if len(miss) > 8 else "") + "** — "
             f"오늘 재료에 근거가 있으면 `watchlist_candidates`로 재제안, 아니면 §8에 `[내림] <이유>`")]


DART_INPUT = re.compile(r"잠정|실적|매출액또는손익구조|단일판매|공급계약|자기주식|유상증자|무상증자|합병|분할|최대주주변경")


def check_dart_read(market: str, stamp: str, note: str) -> list:
    """**판정 입력 공시를 읽었는가**(FB-261017-4) — 이번 run 1단 공시 목록(`mat_dart` · 처음 받은 run이 이 run)에 뜬 유니버스 종목의
    잠정실적·주요 공시(공급계약·자기주식·증자·합병 등)마다 노트에 그 종목 이름(또는 코드)과 그 공시(접수번호 또는 보고서 낱말 — 예 `잠정`)가
    한 줄에 있어야 한다. 실사례(2026-10-12~16): 유니버스 종목의 잠정실적이 공시 목록에 떴는데 세 run이 열지 않고 '조사불가 · 3Q 뒤 판정'으로
    미뤘고, 그 결과로 오른 상승을 놓쳤다."""
    name = "공시 목록 → 노트 판정(잠정실적·주요 공시)"
    if market != "kr":
        return [(name, True, "해당 없음(국내 공시 피드)")]
    rid = db.run_id(stamp, market)
    try:
        uni = {c["ticker"]: c.get("name") or "" for c in db.companies("KR")}
        rows = db.q("SELECT rcp_no, ticker, corp_name, report_nm FROM mat_dart WHERE run_id=? ORDER BY rcept_dt, rcp_no", (rid,))
    except Exception as e:                        # noqa: BLE001
        return [(name, False, f"공시 목록을 읽지 못했다 — {type(e).__name__}: {e}")]
    want = [r for r in rows if r["ticker"] in uni and DART_INPUT.search(r["report_nm"] or "")]
    if not want:
        return [(name, True, f"이번 run 공시 {len(rows)}건 중 판정 입력(잠정실적·주요 공시) 0건")]
    lines = note.splitlines()
    miss = []
    for r in want:
        nm = r["corp_name"] or uni.get(r["ticker"]) or ""
        key = DART_INPUT.search(r["report_nm"] or "").group(0)
        ok = any((r["ticker"] in ln or (len(nm) >= 2 and nm in ln)) and (r["rcp_no"] in ln or key in ln) for ln in lines)
        if not ok:
            miss.append(f"{r['ticker']} {nm} '{(r['report_nm'] or '')[:40]}' ({r['rcp_no']})")
    return [(name, not miss, f"{len(want)}건 전부 노트에서 판정" if not miss else
             f"**안 읽은 {len(miss)}건: {'; '.join(miss[:6])}" + (" …" if len(miss) > 6 else "") + "** — "
             f"`python3 fetch.py <공시 URL>`로 열어 그 종목 줄에 결과와 판정(논지 갱신·[통과] 사유)을 적어라 — 접수번호나 보고서 낱말을 같은 줄에")]


def check_forecast_beneficiaries(market: str, stamp: str, note: str) -> list:
    """**열린 전망의 2·3차 수혜 중 유니버스 종목마다 논지 또는 거른 이유가 있는가**(FB-261003-1).
    첫 피드백: 전망이 2차 수혜로 이름을 적은 장비주들이 논지 없이 +30~50% 올랐다 — 적어 놓고 평가하지 않았다."""
    name = "열린 전망 2·3차 수혜 → 논지·[통과]"
    m = db.market_map_load()
    nm = _names(market)
    want = {}
    for ax in m.get("axes") or []:
        if not ax.get("direction") or (ax.get("status") or "open") != "open":
            continue
        for b in ax.get("beneficiaries") or []:
            tk = str(b.get("ticker") or "")
            if tk in nm and int(b.get("order") or 0) in (2, 3):
                want.setdefault(tk, ax.get("id"))
    if not want:
        return [(name, True, "열린 전망의 유니버스 2·3차 수혜 0건")]
    sec = section(note, "## §6", "## §7") or note
    miss = [f"{tk}({ax})" for tk, ax in want.items() if not _judged_lines(sec, tk, nm.get(tk, ""))]
    return [(name, not miss, f"{len(want)}건 전부 논지 id 또는 [통과]" if not miss else
             f"**판정 없는 {len(miss)}건: {', '.join(miss[:8])}" + (" …" if len(miss) > 8 else "") + "** — "
             f"§6에 논지 id 또는 `[통과] <이유>`를 적어라(전망에 적어 놓고 평가하지 않으면 놓친다)")]


# ── FB-261012-2·3·4 — 시나리오를 짜는 방식의 구멍 셋을 노트에서 센다 ──────────────────────────

DROP_BASIS = re.compile(r"retrace_|support_|low_\d|되돌림|지지|저점")


def _fired_entries(market: str, stamp: str) -> list:
    """오늘 1단 `theses.py check`가 **발화**로 찍은 매수 다리 — carry 파일(없으면 tools 파일)의 발화 블록에서.
    반환 [(ticker, thesis_id, check, basis)]. 원장 `fired_at`은 체결 뒤에만 찍혀 발화했지만 못 산 다리를 놓친다."""
    src = ""
    for f in [*sorted(HERE.glob(f"journal/carry_{stamp}_{market}*.md")), HERE / "data" / "run_evidence" / f"tools_{stamp}_{market}.md"]:
        if f.exists():
            src = f.read_text(encoding="utf-8", errors="ignore")
            if "★ 발화" in src:
                break
    m = re.search(r"★ 발화[^\n]*\n(.*?)(?:\n\s*\n|\Z)", src, re.S)
    if not m:
        return []
    th = {t["id"]: t for t in db.theses_all() if t.get("id")}
    out = []
    for tk, cond, tid in re.findall(r"\[매수\]\s+(\S+)[^\n]*?조건\s+([^\(\n]+?)\s+\(.*?\n.*?논지\s+(\S+)", m.group(1)):
        t = th.get(tid) or {}
        g = next((x for x in t.get("entry_triggers") or [] if str(x.get("check") or "").strip() == cond.strip()), {})
        out.append((tk, tid, cond.strip(), str(g.get("basis") or "") + " " + str(g.get("when") or "")))
    return out


def check_drop_cause(market: str, stamp: str, note: str) -> list:
    """**발화한 되돌림·지지 매수 다리마다 '왜 빠졌나'를 판정했는가**(FB-261012-2).
    실사례(2026-10-06~12): '급등 전 가격까지 내려오면 산다'는 다리가 업종 전체가 새 정보(매출 전환 지연)를 반영하는
    하락에서 연달아 발화했다 — 하락을 할인으로 읽었다. 판정 결과가 논지 동인이면 그것이 곧 무효(invalidation)다."""
    name = "발화한 되돌림·지지 다리 → [하락 원인]"
    rows = [r for r in _fired_entries(market, stamp) if DROP_BASIS.search(r[3])]
    if not rows:
        return [(name, True, "오늘 발화한 되돌림·지지 매수 다리 0건")]
    nm = _names(market)
    sec = section(note, "## §6", "## §7") or note
    lines = [ln for ln in sec.splitlines() if "[하락 원인]" in ln]
    # 꺾인 요인(1단 요인 상태 · FB-261017-7)의 종목이면 그 줄에 요인 상태를 붙인다 — '회사 뉴스 없음 → 집행'이 아니라 요인 판정이 다스린다
    fired_of = {}
    for st in db.factor_state(db.run_id(stamp, market)):
        if st.get("fired"):
            for m in st.get("members") or []:
                fired_of.setdefault(str(m.get("ticker")), []).append(st["driver"])
    miss = []
    nofac = []
    for tk, tid, cond, _b in rows:
        nmk = nm.get(tk, "")
        mine = [ln for ln in lines if tk in ln or (len(nmk) >= 2 and nmk in ln)]
        if not mine:
            miss.append(f"{tk}({cond})")
        elif tk in fired_of and not any("요인" in ln for ln in mine):
            nofac.append(f"{tk}({', '.join(fired_of[tk])})")
    why = []
    if miss:
        why.append(f"**[하락 원인] 없는 {len(miss)}건: {', '.join(miss)}** — §6에 `[하락 원인] <종목> — 업종 동반 y/n · 회사 뉴스 "
                   f"있음/없음 · 원인 … · 논지 동인 해당/비해당 → 집행/무효`를 적어라(동인이면 그것이 무효 조건이다)")
    if nofac:
        why.append(f"**꺾인 요인 종목인데 [하락 원인] 줄에 요인 상태가 없다 {len(nofac)}건: {', '.join(nofac)}** — 줄 끝에 "
                   f"`· 요인 <요인> <상태>(§7 [요인 판정])`를 붙여라(요인이 꺾였으면 요인 판정의 결정이 다스린다)")
    return [(name, not why, f"{len(rows)}건 전부 판정" if not why else " · ".join(why))]


def check_axis_conflicts(market: str, stamp: str, note: str) -> list:
    """**두 시장 전망이 같은 메커니즘에 반대 부호면 그 충돌이 대응표에 있는가**(FB-261012-3).
    대상은 보유 논지·오늘 BUY 종목이 `conflicts_with`가 달린 축의 **전망 수혜 표**에 있을 때다."""
    name = "충돌 축(conflicts_with) → §7 충돌 가지"
    m = db.market_map_load()
    th = db.theses_all()
    mine = {str(t.get("ticker")) for t in th if t.get("status") == "held" and str(t.get("market") or "").lower() == market}
    sp = HERE / "signals" / f"signal_{stamp}_{market}.json"
    if sp.exists():
        try:
            for pr in json.loads(sp.read_text(encoding="utf-8")).get("proposals") or []:
                if str(pr.get("action") or "").upper() == "BUY":
                    mine.add(str(pr.get("ticker")))
        except (OSError, json.JSONDecodeError):
            pass
    sec7 = section(note, "## §7", "## §8") or ""
    need = []
    for ax in m.get("axes") or []:
        cw = ax.get("conflicts_with") or []
        if not cw or (ax.get("status") or "open") != "open":
            continue
        hit = sorted(mine & {str(b.get("ticker")) for b in ax.get("beneficiaries") or []})
        if hit:
            for other in cw:
                if other not in sec7:
                    need.append(f"{ax['id']}↔{other}({', '.join(hit)})")
    return [(name, not need, "충돌 축에 걸린 보유·매수 0건 또는 전부 §7에 있음" if not need else
             f"**§7에 충돌 가지 없음 {len(need)}건: {'; '.join(need)}** — 상대 축 id를 넣은 행으로 '그쪽 전망이 맞으면 → 대응'을 적어라")]


FACTOR_DECISION_RX = re.compile(r"저점매수|유지|정리")


def _factor_blocks(sec7: str) -> dict:
    """§7의 `[요인 판정] <요인>` 블록 {요인: 본문} — 다음 `[요인 판정]`·`### `·`## ` 앞까지."""
    out, cur, buf = {}, None, []
    for ln in sec7.splitlines():
        m = re.search(r"\[요인 판정\]\s*`?([A-Za-z0-9_\-]+)`?", ln)
        if m or ln.startswith("### ") or ln.startswith("## "):
            if cur:
                out[cur] = "\n".join(buf)
            cur, buf = (m.group(1), [ln]) if m else (None, [])
            continue
        if cur:
            buf.append(ln)
    if cur:
        out[cur] = "\n".join(buf)
    return out


def check_driver_branch(market: str, stamp: str, note: str) -> list:
    """① **보유가 가장 많이 걸린 요인(driver)의 '꺾이면' 행이 대응표에 있는가**(FB-261012-4) — 상한이 아니라 대응.
    ② **꺾인(또는 unknown) 요인마다 §7 `[요인 판정] <요인>` 블록이 있는가**(FB-261017-7 · 방향이 아니라 판정) — 1단 요인 상태
    (저장소 `mk_factor_state`)가 있어야 하고, 블록에 `원인:` · `상태: intact|broken|unknown` · 바스켓 보유 종목마다 결정(저점매수|유지|정리)."""
    name = "최대 요인(driver) → §7 '꺾이면' 행"
    name2 = "꺾인 요인 → §7 [요인 판정](원인·상태·보유 종목별 결정)"
    try:
        sys.path.insert(0, str(HERE))
        import market_map as mm
        res = mm.exposure_by_driver(market.upper(), stamp, no_db=True)
    except Exception as e:                        # noqa: BLE001
        return [(name, False, f"요인 노출을 못 쟀다 — {type(e).__name__}: {e}")]
    sec7 = section(note, "## §7", "## §8") or ""
    out = []
    if not res.get("rows"):
        out.append((name, True, "요인에 걸린 보유 0건"))
    else:
        top = res["rows"][0]
        ok = any(top["driver"] in ln and "꺾이면" in ln for ln in sec7.splitlines())
        out.append((name, ok, f"최대 요인 {top['driver']} {top['total_pct']:.1f}% — §7에 행 있음" if ok else
                    f"**최대 요인 `{top['driver']}` {top['total_pct']:.1f}%(양 시장)인데 §7에 '꺾이면' 행이 없다** — "
                    f"`{top['driver']}가 꺾이면 — <바스켓 20세션 고점 대비 ≥ 일중폭×3 또는 같은 요인 규칙 매도> → [요인 판정]` "
                    f"한 행을 적어라(대응 방향은 미리 정하지 않는다)"))
    states = db.factor_state(db.run_id(stamp, market))
    if not states and res.get("factor_state") and not res.get("rows"):
        # 쟀는데 잴 바스켓이 없다(보유 0 · 창 안 같은 요인 규칙 매도 0 — 보유가 없는 첫 run 등) — '안 쟀다'와 가른다(2026-10-19)
        out.append((name2, True, "요인 상태 — 잴 바스켓 0(보유 0 · 창 안 규칙 매도 0)"))
        return out
    if not states:
        out.append((name2, False, f"**요인 상태 없음** — 1단 `python3 market_map.py exposure --by driver --market {market} "
                                  f"--stamp {stamp}`가 이 run을 재지 않았다(저장소 mk_factor_state)"))
        return out
    need = [st for st in states if st.get("state") in ("fired", "unknown")]
    if not need:
        out.append((name2, True, f"꺾인·unknown 요인 0 ({len(states)}개 요인 유지)"))
        return out
    blocks = _factor_blocks(sec7)
    nm = {**_names("kr"), **_names("us")}
    bad = []
    for st in need:
        d = st["driver"]
        b = blocks.get(d)
        if b is None:
            bad.append(f"`{d}`({st['state']}) 블록 없음")
            continue
        lack = []
        if "원인:" not in b:
            lack.append("원인:")
        if not re.search(r"상태:\s*`?(intact|broken|unknown)", b):
            lack.append("상태: intact|broken|unknown")
        for m in st.get("members") or []:
            tk, n = str(m.get("ticker")), nm.get(str(m.get("ticker")), "")
            if not any((tk in ln or (len(n) >= 2 and n in ln)) and FACTOR_DECISION_RX.search(ln) for ln in b.splitlines()):
                lack.append(f"{tk} 결정")
        if lack:
            bad.append(f"`{d}` 블록에 {', '.join(lack)} 없음")
    out.append((name2, not bad, f"꺾인·unknown 요인 {len(need)}개 전부 판정" if not bad else
                f"**[요인 판정] 미비 {len(bad)}건: {'; '.join(bad)}** — §7 표 아래 `[요인 판정] <요인>` 블록에 발화 근거 · `원인:`(사실 id) · "
                f"`상태: intact|broken|unknown` · 바스켓 보유 전부와 이 run 후보의 `저점매수|유지|정리`와 사유를 적고 시그널 "
                f"factor_judgments[]에 같은 값을 넣어라(방향을 미리 정하지 않는다)"))
    return out


# ── 2026-10-12 사용자: "국내장과 미국장은 독립적이지 않다 — 무조건 상호 연관해서 전망과 시나리오를 짜라" ─────────

def check_forecast_cross_market(market: str, stamp: str, note: str) -> list:
    """**전망은 요인 단위** — ① 이번 run이 갱신한 축마다 노트 §2에 `교차:` 줄(반대 시장 전망 id·방향·일치/충돌/없음)
    ② 지도의 열린 전망 전부가 교차 검증(반대 시장 수혜 또는 사유 · 요인 안 반대 방향은 연결 또는 사유)을 통과."""
    name = "전망 교차(요인 단위) → §2 `교차:` · 한 시장 전망 0"
    try:
        sys.path.insert(0, str(HERE))
        import market_map as mm
        m = mm._read(mm.MAP, {"axes": []})
    except Exception as e:                        # noqa: BLE001
        return [(name, False, f"지도를 못 읽었다 — {type(e).__name__}: {e}")]
    axes = m.get("axes") or []
    bad = [f"{a['id']}({mm.forecast_validate(a, axes)[0][:40]}…)" for a in axes
           if a.get("direction") and (a.get("status") or "open") == "open" and mm.forecast_validate(a, axes)]
    upd = [it.get("id") for it in (json.loads((HERE / "data" / "run_evidence" / f"forecast_update_{stamp}_{market}.json")
                                              .read_text(encoding="utf-8"))
                                   if (HERE / "data" / "run_evidence" / f"forecast_update_{stamp}_{market}.json").exists() else [])
           if isinstance(it, dict) and it.get("id")]
    sec2 = section(note, "## §2", "## §3") or ""
    blocks = re.split(r"\n(?=### )", sec2)
    miss = [aid for aid in upd if not any(aid in b and "교차:" in b for b in blocks)]
    ok = not bad and not miss
    why = []
    if miss:
        why.append(f"**§2에 `교차:` 줄 없는 갱신 축 {len(miss)}건: {', '.join(miss)}** — 축 블록마다 `교차: <반대 시장 전망 id> "
                   f"<방향> — 일치/충돌/없음 · 이쪽에 무슨 뜻`")
    if bad:
        why.append(f"**교차 검증 실패 전망 {len(bad)}건: {'; '.join(bad[:6])}** — 반대 시장 수혜 또는 `no_cross_market_why`, "
                   f"반대 방향은 `conflicts_with` 또는 `split_why`")
    return [(name, ok, f"갱신 축 {len(upd)}건 전부 §2 `교차:` · 열린 전망 교차 검증 통과" if ok else " / ".join(why))]


_KR = re.compile(r"\[KR(?:\]|\s)")
_US = re.compile(r"\[US(?:\]|\s)")


def check_scenario_both_markets(market: str, stamp: str, note: str) -> list:
    """**시나리오는 이벤트 단위** — §7 시나리오 행마다 `[KR] …`과 `[US] …` 대응이 둘 다 있거나, 없는 쪽에
    `[KR 영향 없음 — 이유]`·`[US 영향 없음 — 이유]`가 있어야 한다."""
    name = "§7 시나리오 행 → [KR]·[US] 대응(또는 영향 없음 사유)"
    sec7 = section(note, "## §7", "## §8") or ""
    rows = [ln for ln in sec7.splitlines() if re.match(r"\|\s*S\d+\s*\|", ln)]
    if not rows:
        return [(name, True, "§7 시나리오 행 0")]
    miss = []
    for ln in rows:
        sid = re.match(r"\|\s*(S\d+)", ln).group(1)
        lack = [mk for mk, rx in (("KR", _KR), ("US", _US)) if not rx.search(ln)]
        if lack:
            miss.append(f"{sid}(−{'·'.join(lack)})")
    return [(name, not miss, f"{len(rows)}행 전부 두 시장" if not miss else
             f"**한 시장 대응뿐인 행 {len(miss)}건: {', '.join(miss)}** — 대응 칸에 `[KR] …` `[US] …`, 영향이 없으면 "
             f"`[US 영향 없음 — 이유]`(국내 이야기라서는 이유가 아니다 — 환율·외국인 수급·미국 동종 중 무엇을 봤나)")]


def check_xmarket_responses(market: str, stamp: str, note: str) -> list:
    """**반대 시장이 연 시나리오에 이쪽 대응이 있는가** — `market_map.py xmarket`의 '대응 빈 시나리오'마다
    `scenarios.py respond`로 이 시장 대응을 더했거나, 노트 §7에 그 id와 `[<시장> 영향 없음 — 이유]`가 있어야 한다."""
    mk = market.upper()
    name = f"반대 시장 시나리오 → {mk} 대응(respond) 또는 [{mk} 영향 없음]"
    try:
        sys.path.insert(0, str(HERE))
        import market_map as mm
        r = mm.xmarket(market, stamp)
    except Exception as e:                        # noqa: BLE001
        return [(name, False, f"교차 시장 입력을 못 만들었다 — {type(e).__name__}: {e}")]
    sec7 = section(note, "## §7", "## §8") or note
    rx = re.compile(rf"\[{mk}\s*영향\s*없음")
    miss = [x["id"] for x in r["scenarios_missing_here"]
            if not any(x["id"] in ln and rx.search(ln) for ln in sec7.splitlines())]
    n = len(r["scenarios_missing_here"])
    return [(name, not miss, f"{n}건 전부 처리(since {r['since']})" if not miss else
             f"**{mk} 대응 없는 반대 시장 시나리오 {len(miss)}건: {', '.join(miss[:10])}** — `scenarios.py respond --id <id> "
             f"--market {market} --what \"…\"` 또는 §7에 `<id> [{mk} 영향 없음 — 이유]`")]


def check_gaps(note: str) -> list:
    """`gaps`가 지목한 예정 이벤트가 노트 **§5 일정 표**에 행으로 들어갔는가.

    이름이 아니라 **날짜**로 대조한다 — 이벤트 이름은 표기가 갈리지만(`FOMC (9/15~16)`
    vs `FOMC`) 날짜는 갈리지 않는다. §5는 `MM-DD`로 쓰므로 그 형식으로 본다.
    """
    out, err = tool(["market_map.py", "gaps", "--days", "14"])
    if err:
        return [("재료 공백", False, f"gaps를 돌리지 못했다 — {err}")]
    # `★ D-0 2026-09-10 [axis] 이벤트 이름`
    evs = re.findall(r"★\s*D-\d+\s+(\d{4})-(\d{2})-(\d{2})\s*\[[^\]]*\]\s*(.+)", out)
    if not evs:
        return [("재료 공백", True, "gaps 지목 0건 — 대조할 것이 없다")]
    sec5 = section(note, "## §5", "## §6") or note
    missing = sorted({f"{mm}-{dd}" for _, mm, dd, _ in evs if f"{mm}-{dd}" not in sec5})
    return [(f"gaps 지목 {len(evs)}건 → §5 일정", not missing,
             f"지목 날짜 전건이 §5에 행으로 있다" if not missing else
             f"**§5에 없는 날짜: {', '.join(missing)}** — 예정된 이벤트를 일정표에 안 넣으면 "
             f"그날 즉흥으로 판단하게 된다")]


# ───────────────────────────────────────────────────────────── 실행

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", required=True, choices=["kr", "us"])
    ap.add_argument("--stamp", required=True, help="YYMMDD")
    ap.add_argument("--note", default="", help="분석노트 경로(기본: 그 날짜의 최신본)")
    ap.add_argument("--out", default="", help="대조 결과를 이 파일에 쓴다(게이트가 읽는다)")
    ap.add_argument("--trades", default="", help="6단 뒤: trades_*.json을 주면 §11 체결 확정줄도 대조한다")
    a = ap.parse_args()

    np_ = note_path(a.market, a.stamp, a.note)
    if not np_.exists():
        print(f"대조 불가 — 분석노트가 없다: {np_}", file=sys.stderr)
        return 2
    note = np_.read_text(encoding="utf-8", errors="ignore")

    rows = []
    rows += check_generated_lines(a.market, a.stamp, note)
    rows += check_board_mentions(a.market, a.stamp)
    rows += check_prev_carried(a.market, a.stamp, note)
    rows += check_outliers(a.market, a.stamp, note)
    rows += check_cycle(note)
    rows += check_research_is_today(a.market, a.stamp)
    rows += check_research_expansion(a.market, a.stamp)
    rows += check_research_cited(a.market, a.stamp)
    rows += check_corpus(a.market, a.stamp, note)
    rows += check_pdf_sources(a.market, a.stamp, note)
    rows += check_reeval(a.market, a.stamp, note)
    rows += check_support_break(a.market, a.stamp, note)
    rows += check_forecast_beneficiaries(a.market, a.stamp, note)
    rows += check_dart_read(a.market, a.stamp, note)
    rows += check_queue_one_day(a.market, a.stamp, note)
    rows += check_drop_cause(a.market, a.stamp, note)
    rows += check_axis_conflicts(a.market, a.stamp, note)
    rows += check_driver_branch(a.market, a.stamp, note)
    rows += check_forecast_cross_market(a.market, a.stamp, note)
    rows += check_scenario_both_markets(a.market, a.stamp, note)
    rows += check_xmarket_responses(a.market, a.stamp, note)
    rows += check_gaps(note)
    rows += check_markers(note)
    if a.trades:
        rows += check_fill_line(note, Path(a.trades))
        rows += check_reconcile(Path(a.trades))

    n_fail = sum(1 for _, ok, _ in rows if not ok)
    buf = io.StringIO()
    print(f"# 도구 ↔ 노트 대조 — {a.stamp} · {a.market.upper()}\n", file=buf)
    print(f"> 각서가 아니라 **파일 대조**다. 도구가 지목한 이름이 노트에 실제로 들어갔는지만 센다.",
          file=buf)
    # '실행' 날짜는 이 run의 세션 날짜(2026-10-14 · 현지 거래일 — map 게이트가 그 날짜를 대조한다) · 실제 시각은 '기록'
    import sessions as _ss
    print(f"> 대상 노트 `{np_.name}` · 실행 {_ss.session_iso(a.stamp, a.market)} 세션 · "
          f"기록 {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST\n", file=buf)
    print(f"[대조] 검사 {len(rows)}건 · 실패 {n_fail}건\n", file=buf)
    print("| 검사 | 결과 | 근거 |\n|---|---|---|", file=buf)
    for name, ok, why in rows:
        print(f"| {name} | {'✓' if ok else '**✗**'} | {why} |", file=buf)
    if n_fail:
        print(f"\n**실패 {n_fail}건 — 도구가 준 것을 노트가 안 다뤘다.** "
              f"노트를 고치거나, 왜 제외했는지 노트에 명시적으로 적어라 "
              f"(제외 이유가 적히면 그 이름이 노트에 등장하므로 이 대조가 통과한다).", file=buf)
    out = buf.getvalue()
    print(out)
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(out, encoding="utf-8")
        print(f"→ {a.out}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
