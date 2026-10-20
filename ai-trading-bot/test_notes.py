#!/usr/bin/env python3
"""
노트 파이프라인 × 사실 층 테스트(2026-10-17 · 2파) — 노트가 사실을 id로 가리키고, 값·§3·§12는 저장소에서 렌더된다.

왜: 리서치 사실을 노트·§12·판정표·교정 지시서에 문장으로 복사했고(한 사실의 사본 12곳 · 교정 전파 대상 32파일), 교정 한 번이
파일 수십 개의 손 편집이 됐다. 새 구조에서 노트는 `{{F-000731}}` 자리로 사실을 가리키고 `stage.py render-note`가 값을 채운다 —
교정은 행 갱신 1회 → 다시 렌더하면 그 자리 값만 바뀐다. 검사기(note_lint·crosscheck·verify_numbers·corpus)는 원장 파일 대신
저장소를 읽는다. 게이트는 노트·증거 파일의 글자를 세므로 형식(§3 머리줄·생성줄·§12 표·NOTE-LINT 줄)은 그대로여야 한다 —
그래서 렌더 결과를 1단이 만들던 표와 글자 단위로 비교한다.

규칙: 케이스마다 빈 임시 저장소(`testkit.db_case`) · 산출 파일은 임시 폴더 · 네트워크 없음(브로커·링크는 가짜) · 실제 analysis/·journal/에 쓰지 않는다.

    python3 test_notes.py
"""
import atexit
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).absolute().parent
sys.path.insert(0, str(HERE))
if not os.environ.get("AITB_DB"):                          # db를 부르기 전에 — 실제 저장소를 열지 않게
    os.environ["AITB_DB"] = str(Path(tempfile.mkdtemp(prefix="notes_boot_")) / "trading.db")

import db                    # noqa: E402
import facts                 # noqa: E402
import stage                 # noqa: E402
from testkit import db_case, seed   # noqa: E402

KST = timezone(timedelta(hours=9))
PASS, FAIL = "PASS", "FAIL"
results = []
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"TEST-HALTED file=test_notes.py ran={n} failed={nf} last={_STATE['current']}")
    sys.stdout.flush()


atexit.register(_halt_banner)


def _safe(fn):
    _STATE["current"] = fn.__name__
    try:
        with db_case():                      # 케이스마다 빈 저장소
            facts._COLS.clear()
            fn()
    except KeyboardInterrupt:
        raise
    except BaseException as e:               # noqa: BLE001
        results.append((FAIL, f"{fn.__name__} — 중단", f"{type(e).__name__}: {e}"))
        print(f"[{FAIL}] {fn.__name__} — 중단({type(e).__name__}: {str(e)[:300]})")


def report(label, problems):
    status = FAIL if problems else PASS
    results.append((status, label, "; ".join(problems)))
    print(f"[{status}] {label}" + (f" — {'; '.join(problems)}" if problems else ""))


def quiet(fn, *a, **k):
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        r = fn(*a, **k)
    return r, o.getvalue(), e.getvalue()


ST, MK = "261019", "kr"
RID = db.run_id(ST, MK)
DAY = "2026-10-19"
URL_A = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261014000123"
Q_A = "3분기 연결 영업이익은 12조 3,456억원으로 전년 동기 대비 41.2% 증가하였습니다"


def earn(**kw):
    r = {"kind": "earnings", "ticker": "005930", "period": "2026Q3", "metric": "영업이익", "actual": 12.3456, "unit": "조원",
         "as_of": "2026-10-14", "url": URL_A, "quote": Q_A, "tier": 1, "source": "DART 잠정실적"}
    r.update(kw)
    return r


def analyst(**kw):
    r = {"kind": "analyst", "ticker": "005930", "house": "KB증권", "date": "2026-10-15", "action": "상향", "target_old": 120000,
         "target_new": 135000, "currency": "KRW", "url": "https://example.com/research/kb-005930-1015",
         "quote": "목표주가를 120,000원에서 135,000원으로 상향한다 — 메모리 가격 상승 반영", "tier": 2, "source": "KB증권 리포트"}
    r.update(kw)
    return r


def add(row, run=(ST, MK)):
    r, _, err = quiet(facts.add_fact, row, run)
    return r


def _tmp(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


@contextlib.contextmanager
def stage_dirs():
    """stage.py의 산출 폴더를 임시 폴더로 — 실제 analysis/·journal/에 쓰지 않는다."""
    tmp = _tmp("notes_stage_")
    saved = (stage.ANALYSIS, stage.PROJ, stage.JOURNAL, stage.EVID)
    try:
        stage.ANALYSIS, stage.PROJ, stage.JOURNAL, stage.EVID = tmp / "analysis", tmp, tmp / "journal", tmp / "data" / "run_evidence"
        for d in (stage.ANALYSIS, stage.JOURNAL, stage.EVID):
            d.mkdir(parents=True, exist_ok=True)
        # 실전 점검표 대역(v4 · close는 외부 점검 명령의 종료코드를 본다) — STUB_REAL_CHECK_RC(기본 0)로 끝나고, 0이면 점검표 파일을 쓴다
        (tmp / "real_check.py").write_text(
            "import os, sys\n"
            "a = sys.argv\nst, mk = a[a.index('--stamp') + 1], a[a.index('--market') + 1]\n"
            "rc = int(os.environ.get('STUB_REAL_CHECK_RC', '0'))\n"
            "if rc == 0:\n"
            "    p = os.path.join('data', 'run_evidence', f'real_check_{st}_{mk}.md')\n"
            "    open(p, 'w', encoding='utf-8').write('점검표(대역)')\n"
            "print('실전 점검표(대역)')\nsys.exit(rc)\n", encoding="utf-8")
        yield tmp
    finally:
        stage.ANALYSIS, stage.PROJ, stage.JOURNAL, stage.EVID = saved
        shutil.rmtree(tmp, ignore_errors=True)


def skeleton(tmp: Path, board_md: str = "(1단 보드)") -> str:
    return stage.note_skeleton(MK, ST, tmp / "data" / "run_evidence" / f"tools_{ST}_{MK}.md", board_md, cap_line="자본 기준 — 테스트")


def put_in(note: str, sec: str, text: str) -> str:
    """그 절의 TODO 줄 앞에 본문을 넣는다."""
    return note.replace(f"<!-- TODO {sec} -->", text + f"\n<!-- TODO {sec} -->", 1)


BOARD_ROWS = [  # 1단 보드 기록 모양(market_map._fetch_board 행 — 등락률 내림차순)
    {"key": "0013", "name": "전기·전자", "d1": 2.31, "d5": -4.1, "d20": 7.25, "turnover": 1, "close": 3100.5},
    {"key": "0021", "name": "금융", "d1": 0.4, "d5": 1.0, "d20": None, "turnover": 1, "close": 800.0},
    {"key": "0007", "name": "종이·목재", "d1": -1.05, "d5": None, "d20": -2.2, "turnover": 1, "close": 300.0},
]
SECTORS = [
    {"code": "0013", "name": "전기·전자", "market": "KR", "grade": "A", "status": "A 유지 — 메모리 현물 상승 · resolves_when 10-28 컨콜",
     "updated": "2026-10-16", "chains": [{"step": 1}, {"step": 2}], "names": []},
    {"code": "0021", "name": "금융", "market": "KR", "grade": "B", "status": "B 유지 — 밸류업 공시 대기", "updated": "2026-10-15",
     "chains": [], "names": []},
    {"code": "0007", "name": "종이·목재", "market": "KR", "grade": "—", "status": None, "updated": None, "chains": [], "names": []},
]


def record_board(day=DAY, rows=None, bench=1.6):
    db.log_append("sector_history", {"date": day, "market": MK, "benchmark_pct": bench,
                                     "sectors": [{k: r[k] for k in ("key", "name", "d1", "d5", "d20", "close")} for r in rows or BOARD_ROWS]})


def issue(subject="반도체 가격 상승", body="DRAM 현물가가 11세션 연속 올랐다. 매출 12조3456억 전망.", source="머니레터"):
    c = db.connect()
    iid = c.execute("INSERT INTO mat_issue (run_id, sender, source, topic, day, subject, body, content_sha, first_run)"
                    " VALUES (?,?,?,?,?,?,?,?,?)", (RID, "머니레터 발신", source, "경제·금융", DAY, subject, body,
                                                     f"sha-{subject}", RID)).lastrowid
    db.material_issue_run_put(RID, db.material_issues_for_run(RID) + [iid])
    return iid


# ───────────────────────────── 케이스 ─────────────────────────────

def t1_render_note_values_and_rerender():
    """render-note — {{F-id}}·.as_of·.line → <!--F:…-->값<!--/F--> · 참조(ev_claim_ref note·절)·S 번호 · §12 렌더 자리 ·
    같은 상태로 다시 돌리면 글자 그대로 · facts.correct 뒤 다시 돌리면 그 값만 바뀐다 · 없는 id·없는 칸은 아무것도 안 쓰고 2."""
    p = []
    db.run_start(ST, MK, session_date=DAY)
    a = add(earn())["id"]
    b = add(analyst())["id"]
    with stage_dirs() as tmp:
        note = skeleton(tmp)
        note = put_in(note, "§2", f"- 삼성전자 3Q 영업이익 {{{{{a}}}}}(기준 {{{{{a}.as_of}}}}) [AI] [S1]\n- 근거: {{{{{b}.line}}}}")
        note = put_in(note, "§6", f"| 005930 | 목표가 {{{{{b}}}}} |")
        note = put_in(note, "§5", "- 일정 근거는 증권사 노트다 [AI] [S2]")     # 값 자리 없이 출처 번호만 — 그것도 참조다
        path = stage.ANALYSIS / f"분석노트_{ST}_{MK}_v1_0.md"
        path.write_text(note, encoding="utf-8")
        (rc, msg), _, _ = quiet(stage.render_note, ST, MK)
        t1 = path.read_text(encoding="utf-8")
        if rc != 0:
            p.append(f"render rc={rc}: {msg}")
        want = [f"<!--F:{a}-->12.3456 조원<!--/F-->", f"<!--F:{a}.as_of-->2026-10-14<!--/F-->",
                f"<!--F:{b}.line-->{facts.render_line(b)}<!--/F-->", f"<!--F:{b}-->135000 KRW<!--/F-->"]
        miss = [w for w in want if w not in t1]
        if miss or "{{F-" in t1:
            p.append(f"값 자리: 빠진 것 {miss}")
        if stage.S12_OPEN not in t1 or stage.S12_CLOSE not in t1 or not re.search(rf"^\| S1 \| .* · {a} \|$", t1, re.M) \
                or "<!-- ✓ §12 -->" not in t1 or "<!-- TODO §12 -->" in t1:
            p.append("§12 렌더 자리(머리줄·S1 행·✓ 스탬프)가 아니다")
        refs = {(r["fact_id"], r["slot"]) for r in db.q("SELECT fact_id, slot FROM ev_claim_ref WHERE run_id=? AND doc_type='note'"
                                                          " AND doc_id=?", (RID, path.name))}
        if refs != {(a, "§2"), (b, "§2"), (b, "§6"), (b, "§5")}:
            p.append(f"참조(ev_claim_ref): {sorted(refs)}")
        (rc2, msg2), _, _ = quiet(stage.render_note, ST, MK)
        if path.read_text(encoding="utf-8") != t1 or "바뀐 것 없음" not in msg2:
            p.append(f"다시 렌더하면 같아야 한다: {msg2}")
        quiet(facts.correct, a, {"actual": "12.4"}, "원문 표 다시 읽음 — 12조 4천억")
        if not (db.run_get(ST, MK) or {}).get("rerender"):
            p.append("교정 뒤 rerender 표시가 없다")
        (rc3, msg3), _, _ = quiet(stage.render_note, ST, MK)
        # 교정으로 렌더된 값이 바뀌면 다음 minor 판으로 재발행 · 옛 판은 analysis/이전버전/ · 참조는 새 판 이름으로(stage4 5-C 7)
        p11 = stage.ANALYSIS / f"분석노트_{ST}_{MK}_v1_1.md"
        if not p11.exists() or path.exists() or not (stage.ANALYSIS / "이전버전" / path.name).exists() or "새 판" not in msg3:
            p.append(f"교정 뒤 재렌더가 v1_1로 재발행되지 않았다: {msg3}")
        else:
            got = {r["doc_id"] for r in db.q("SELECT DISTINCT doc_id FROM ev_claim_ref WHERE run_id=? AND doc_type='note'", (RID,))}
            if got != {p11.name}:
                p.append(f"재발행 뒤 참조 문서: {got}")
            path = p11
        t3 = path.read_text(encoding="utf-8")
        if f"<!--F:{a}-->12.4 조원<!--/F-->" not in t3 or "12.3456" in t3.split("## §12")[0]:
            p.append("교정 값이 그 자리에 들어가지 않았다")
        if (db.run_get(ST, MK) or {}).get("rerender"):
            p.append("렌더 뒤 rerender가 남았다")
        row_b = [ln for ln in t1.splitlines() if ln.startswith("| S2 |")]
        if not row_b or row_b[0] not in t3:
            p.append("안 바뀐 §12 행의 written까지 바뀌었다")
        # 없는 사실 · 없는 칸 → 쓰지 않는다
        bad = t3.replace(f"{{{{{a}}}}}", "") + "\n- 없는 것 {{F-999999}}\n"
        path.write_text(bad, encoding="utf-8")
        (rc4, msg4), _, _ = quiet(stage.render_note, ST, MK)
        if rc4 != 2 or "F-999999" not in msg4 or path.read_text(encoding="utf-8") != bad:
            p.append(f"없는 id: rc={rc4} {msg4}")
        path.write_text(t3 + f"\n- {{{{{a}.nope}}}}\n", encoding="utf-8")
        (rc5, msg5), _, _ = quiet(stage.render_note, ST, MK)
        if rc5 != 2 or "nope" not in msg5:
            p.append(f"없는 칸: rc={rc5} {msg5}")
    report("(1) render-note — 값 자리·참조·S 번호·§12 렌더 · 재렌더 멱등 · 교정 뒤 그 값만 · 없는 id·칸은 쓰지 않음", p)


def t2_old_s12_placeholder_gets_markers():
    """옛 뼈대(§12 TODO만 있는 노트)도 렌더 자리표를 만들어 채우고, 그다음부터는 자리표 안만 바뀐다 · NOTE-LINT 줄은 그대로 끝에."""
    p = []
    db.run_start(ST, MK, session_date=DAY)
    a = add(earn())["id"]
    with stage_dirs():
        old = (f"# 분석노트 {ST}\n\n## §2 거시 축\n\n- 영업이익 {{{{{a}}}}} [AI] [S1]\n\n---\n\n## §12 출처 원장 — 세 시점\n\n"
               "<!-- TODO §12 --> `python3 srcledger.py render --stamp <YYMMDD> --market <mkt>`의 출력을 붙인다.\n\n"
               "<!-- NOTE-LINT OK (note_lint.py note 2026-10-19 10:00 · x · 위반 0 · 출처 경고 0) -->\n")
        path = stage.ANALYSIS / f"분석노트_{ST}_{MK}_v1_1.md"
        path.write_text(old, encoding="utf-8")
        (rc, msg), _, _ = quiet(stage.render_note, ST, MK)
        t = path.read_text(encoding="utf-8")
        if rc != 0 or t.count(stage.S12_OPEN) != 1 or "srcledger" in t or "| S1 |" not in t \
                or not t.rstrip().endswith("-->") or "NOTE-LINT OK" not in t.splitlines()[-1] or t.count("## §12") != 1:
            p.append(f"옛 자리: rc={rc} {msg} · 끝 {t[-160:]!r}")
        (rc2, msg2), _, _ = quiet(stage.render_note, ST, MK)
        if path.read_text(encoding="utf-8") != t:
            p.append(f"자리표가 생긴 뒤 재렌더가 달라졌다: {msg2}")
    report("(2) 옛 §12 자리 → 렌더 자리표 생성 · 다시 돌리면 같다 · NOTE-LINT 줄 유지", p)


def t3_board_render_matches_capture():
    """§3 섹터 보드 — 1단 build_board(가짜 브로커)가 쓰는 표와 render_board가 글자 그대로 같다(재료 언급이 없을 때 · 요약 줄 한 줄만 더) ·
    언급이 붙으면 그 섹터 상태 칸 뒤에만 붙고, 다루지 않은 섹터도 행으로 남는다 · 실제 노트 §3 머리줄과 같다 · 섹터 '*'는 빈 칸만 채운다."""
    import kis_client
    import market_map as mm
    import sessions as ss
    p = []
    day = ss.run_date(MK).isoformat()
    st = day[2:].replace("-", "")
    rid = db.run_id(st, MK)
    db.run_start(st, MK, session_date=day)
    seed(sectors=SECTORS)
    saved = (mm._fetch_board, kis_client.KisClient.for_mode)
    try:
        mm._fetch_board = lambda c, market, quick: ([dict(r) for r in BOARD_ROWS], "KOSPI 종합", 1.6)
        kis_client.KisClient.for_mode = classmethod(lambda cls, *a, **k: object())
        (txt, md, n), _, _ = quiet(stage.build_board, MK, False, True)
    finally:
        mm._fetch_board, kis_client.KisClient.for_mode = saved
    out = facts.render_board(st, MK)
    if out is None:
        report("(3) §3 섹터 보드 렌더", ["보드 기록이 있는데 None"])
        return
    lines = out.splitlines()
    if "\n".join(lines[:-1]) != md:
        p.append("1단 표와 다르다:\n" + "\n".join(f"  1단 {x!r}\n  렌더 {y!r}" for x, y in zip(md.splitlines(), lines) if x != y)[:600])
    if not lines[-1].startswith("재료 언급 — 발행분 0통"):
        p.append(f"요약 줄: {lines[-1]!r}")
    real = sorted(HERE.glob("analysis/분석노트_261016_kr_v*.md"))
    if real:
        sec = real[-1].read_text(encoding="utf-8").split("## §3", 1)[1]
        head = [ln for ln in sec.splitlines() if ln.startswith("|")][:2]
        if head != lines[:2]:
            p.append(f"실제 노트 §3 머리줄과 다르다: {head} vs {lines[:2]}")
    if not re.search(r"^커버리지 \*\*\d+/\d+\*\* — 미조사 \d+개", out, re.M) or stage.GEN_NOTICE not in out:
        p.append("생성줄(GEN_NOTICE·커버리지)이 없다")
    # 재료 언급
    iid = issue()
    f1 = add(earn(), run=(st, MK))["id"]
    rows = [{"issue_id": iid, "subject_type": "sector", "subject_id": "전기·전자", "stance": "+", "headline": "DRAM 현물 11세션 상승",
             "board_verdict": "새 정보", "fact_ids": [f1]},
            {"issue_id": iid, "subject_type": "sector", "subject_id": "*", "board_verdict": "해당 없음"}]
    rc, _, err = quiet(facts.add_mentions, rows, (st, MK))
    n_m = db.q1("SELECT COUNT(*) AS n FROM mat_mention WHERE run_id=? AND issue_id=?", (rid, iid))["n"]
    if rc != 0 or n_m != 3:
        p.append(f"섹터 '*' 펴기: rc={rc} 행 {n_m}(3이어야 — 이미 다룬 전기·전자는 빼고) {err[:120]!r}")
    quiet(facts.add_mentions, [{"issue_id": iid, "subject_type": "sector", "subject_id": "*"}], (st, MK))
    if db.q1("SELECT COUNT(*) AS n FROM mat_mention WHERE run_id=? AND issue_id=?", (rid, iid))["n"] != 3:
        p.append("'*'를 다시 넣자 빈 칸이 아닌 섹터까지 늘었다")
    # 발행분 번호(issue_no)로 가리킨 '*' 행은 발행분마다 따로 편다 — 번호 해석이 펴기보다 늦으면 첫 '*'만 펴지고 나머지 발행분이 빈다
    iid2, iid3 = issue(subject="둘째 발행분"), issue(subject="셋째 발행분")
    db.connect().execute("DELETE FROM mat_issue_run WHERE run_id=?", (rid,))
    db.material_issue_run_put(rid, [iid, iid2, iid3])
    quiet(facts.add_mentions, [{"issue_no": 2, "subject_type": "sector", "subject_id": "*"},
                               {"issue_no": 3, "subject_type": "sector", "subject_id": "*"}], (st, MK))
    per = {i: db.q1("SELECT COUNT(*) AS n FROM mat_mention WHERE run_id=? AND issue_id=?", (rid, i))["n"] for i in (iid2, iid3)}
    if per != {iid2: 3, iid3: 3}:
        p.append(f"issue_no '*' 펴기가 발행분마다 로스터 전수가 아니다: {per}")
    db.connect().execute("DELETE FROM mat_mention WHERE run_id=? AND issue_id IN (?,?)", (rid, iid2, iid3))
    db.connect().execute("DELETE FROM mat_issue_run WHERE run_id=? AND issue_id IN (?,?)", (rid, iid2, iid3))
    out2 = facts.render_board(st, MK)
    l2 = out2.splitlines()
    ee = next((x for x in l2 if x.startswith("| 전기·전자 |")), "")
    if not ee.endswith(f"· 재료 1건: 머니레터 + DRAM 현물 11세션 상승(새 정보) {f1} |") or ee.count(" | ") != 8:
        p.append(f"언급 칸: {ee!r}")
    others = [x for x in l2 if x.startswith(("| 금융 |", "| 종이·목재 |"))]
    if len(others) != 2 or any("재료" in x for x in others) or not others[1].endswith("| **미조사** |"):
        p.append(f"다루지 않은 섹터 행: {others}")
    if not l2[-1].startswith("재료 언급 — 발행분 1통 · 언급 3행 · 다룬 섹터 1/3"):
        p.append(f"요약 줄: {l2[-1]!r}")
    if facts.render_board("261001", MK) is not None:
        p.append("보드 기록이 없는 run에 다른 날 시세로 표를 그렸다")
    # render-note가 §3 자리표 사이를 채운다
    with stage_dirs():
        path = stage.ANALYSIS / f"분석노트_{st}_{MK}_v1_0.md"
        path.write_text(stage.note_skeleton(MK, st, stage.EVID / "t.md", md, cap_line="자본 기준 — 테스트"), encoding="utf-8")
        quiet(stage.render_note, st, MK)
        t = path.read_text(encoding="utf-8")
        blk = t[t.index(stage.S3_OPEN):t.index(stage.S3_CLOSE) + len(stage.S3_CLOSE)]
        if blk != f"{stage.S3_OPEN}\n{out2}\n{stage.S3_CLOSE}" or "<!-- ✓ §3 -->" not in t.split("## §4")[0]:
            p.append("render-note가 §3 자리표를 렌더로 채우지 않았다(✓ 스탬프는 자리표 밖에 남는다)")
    report("(3) §3 섹터 보드 렌더 = 1단 표(글자 그대로) · 실제 노트 머리줄 · 언급은 상태 칸 뒤 · 미언급 섹터도 행 · '*'는 빈 칸만", p)


def t4_note_lint_reads_store():
    """note_lint — [S n] → 이번 run S 번호 → 사실(url·인용·as-of) · 새 T1 사실에 인용이 없으면 위반 · 값 자리만 있는 [AI] 줄은 출처로 센다 ·
    NOTE-LINT OK 줄은 노트 끝에 하나."""
    import note_lint as nl
    p = []
    db.run_start(ST, MK, session_date=DAY)
    a = add(earn())["id"]
    b = add(earn(ticker="000660", quote=None, minor=True, url="https://example.com/hynix-3q"))["id"]
    # minor로 들어간 b를 load-bearing T1로 바꿔 '인용 없는 새 사실'을 만든다
    db.connect().execute("UPDATE ev_fact SET load_bearing=1, retrieved=? WHERE id=?", ("2026-10-19 10:00", b))
    with stage_dirs() as tmp:
        note = skeleton(tmp)
        note = note.replace("<!-- TODO §0 -->", "목표 투자 비중 72%\n<!-- TODO §0 -->", 1)
        note = put_in(note, "§7", "| S1 | 조건 → 대응 | [KR] x [US] y |")
        note = put_in(note, "§2", f"- 영업이익 [AI] [S1]\n- 하이닉스 [AI] [S2]\n- 값 자리만 [AI] {{{{{a}}}}}")
        path = stage.ANALYSIS / f"분석노트_{ST}_{MK}_v1_0.md"
        path.write_text(note, encoding="utf-8")
        quiet(stage.render_note, ST, MK)
        txt = path.read_text(encoding="utf-8")
        e, w = nl.lint_note(txt, True, ST, MK)
        if not any("S2" in x and b in x and "원문 인용" in x for x in e) or any(f"S1({a})" in x for x in e) \
                or any("[S번호]가 없다" in x for x in e + w):
            p.append(f"S2 위반·S1 통과·값 자리 출처 인정: errs={e} warns={w}")
        db.connect().execute("UPDATE ev_fact SET quote=? WHERE id=?", ("SK hynix 3분기 영업이익은 시장 예상을 웃돌았다고 밝혔다", b))
        saved = nl.HERE
        try:
            nl.HERE = tmp
            (tmp / "config").mkdir(exist_ok=True)
            (tmp / "config" / "limits.json").write_text(json.dumps({"note_sources_required": True}), encoding="utf-8")
            r1, o1, _ = quiet(nl.main, ["note", "--stamp", ST, "--market", MK])
            r2, o2, _ = quiet(nl.main, ["note", "--stamp", ST, "--market", MK])
        finally:
            nl.HERE = saved
        t2 = path.read_text(encoding="utf-8")
        if r1 != 0 or r2 != 0 or "NOTE-LINT OK" not in o2 or t2.count("<!-- NOTE-LINT OK") != 1 or not t2.rstrip().endswith("-->"):
            p.append(f"NOTE-LINT 줄: rc {r1}/{r2} {o1[-200:]!r}")
    report("(4) note_lint — [S n]→저장소 사실 품질 · 값 자리 = 출처 · NOTE-LINT OK 줄 하나", p)


def t5_crosscheck_from_store():
    """crosscheck — 리서치 확장 = 이번 run 사실(신규·되풀이) · 섹터 합본 항목은 '해당 없음' · §3 보드 전수 = 발행분 × 섹터 언급 ·
    논지·전망 인용 = 사실 id · PDF 열기 기록 = 저장소 접속 기록."""
    import crosscheck as cc
    p = []
    db.run_start(ST, MK, session_date=DAY)
    record_board()
    seed(sectors=SECTORS)
    iid = issue()
    c = db.connect()
    c.execute("INSERT INTO mat_link (issue_id, ord, url, run_id) VALUES (?,?,?,?)", (iid, 0, "https://news.example.com/dram-1019", RID))
    r_none = cc.check_research_expansion(MK, ST)[0]
    # 재료에서 꺼낸 사실(origin=link)만 있으면 확장이 아니다 — 뉴스레터가 이미 준 것이다
    add(analyst(house="KB", origin="link", url="https://news.example.com/dram-1019",
                quote="KB증권은 목표주가를 13만원으로 올렸다고 발행분이 전했다"))
    r_mat = cc.check_research_expansion(MK, ST)[0]
    if r_mat[1] or "재료에서 꺼낸 사실 1건" not in r_mat[2]:
        p.append(f"재료에서 꺼낸 사실만으로 확장 통과: {r_mat}")
    a = add(earn())["id"]
    add(analyst())
    rep = add(analyst(house="NH", url="https://news.example.com/dram-1019", quote="NH투자증권은 목표주가를 13만5천원으로 올렸다고 보도"))
    exp = cc.check_research_expansion(MK, ST)[0]
    if r_none[1] or not exp[1] or "확장 2행(되풀이 1행 · 재료에서 꺼낸 1행 제외)" not in exp[2] or rep["status"] != "되풀이":
        p.append(f"리서치 확장: 0건 {r_none} · {exp} · {rep['status']}")
    c.execute("UPDATE ex_run SET facts_new=NULL, facts_repeat=NULL WHERE run_id=?", (RID,))
    if "확장 2행(되풀이 1행 · 재료에서 꺼낸 1행 제외)" not in cc.check_research_expansion(MK, ST)[0][2]:
        p.append("ex_run 칸이 비면 사실 표에서 세야 한다")
    only_rep = db.q("SELECT id FROM ev_fact WHERE repeat=0 AND created_run=?", (RID,))
    c.execute(f"UPDATE ev_fact SET repeat=1 WHERE id IN ({','.join('?' * len(only_rep))})", tuple(r["id"] for r in only_rep))
    c.execute("UPDATE ex_run SET facts_new=0, facts_repeat=3 WHERE run_id=?", (RID,))
    r3 = cc.check_research_expansion(MK, ST)[0]
    if r3[1] or "되풀이" not in r3[2]:
        p.append(f"전부 되풀이인데 통과: {r3}")
    today = cc.check_research_is_today(MK, ST)[0]
    if today != (f"오늘자 섹터 리서치({ST})", True, "해당 없음(2026-10-17 중단)"):
        p.append(f"섹터 합본 항목: {today}")
    bm = cc.check_board_mentions(MK, ST)[0]
    if bm[1] or "빈 칸 3개" not in bm[2]:
        p.append(f"언급 없는 보드가 통과: {bm}")
    quiet(facts.add_mentions, [{"link_id": db.q1("SELECT id FROM mat_link")["id"], "subject_type": "sector", "subject_id": "0013",
                                "stance": "+", "headline": "DRAM"},
                               {"issue_id": iid, "subject_type": "sector", "subject_id": "*"}], (ST, MK))
    bm2 = cc.check_board_mentions(MK, ST)[0]
    if not bm2[1] or "발행분 1통 × 섹터 3개 전부 행 있음" not in bm2[2]:
        p.append(f"전수 언급인데 실패: {bm2} (링크 언급은 그 발행분으로 센다)")
    seed(theses=[{"id": "kr-005930-ai-261019", "ticker": "005930", "market": "KR", "status": "armed", "created": DAY,
                  "thesis": f"메모리 이익 회복 — {a}", "entry_triggers": [], "exit_triggers": []},
                 {"id": "kr-000660-x-261019", "ticker": "000660", "market": "KR", "status": "armed", "created": DAY,
                  "thesis": "근거 없음", "entry_triggers": [], "exit_triggers": []}])
    cit = cc.check_research_cited(MK, ST)[0]
    if cit[1] or "논지 kr-000660-x-261019" not in cit[2] or "kr-005930" in cit[2]:
        p.append(f"인용 대조: {cit}")
    pdf = "https://www.example.com/ir/q3-2026.pdf"
    note = f"## §6 종목\n\n근거 {pdf}\n\n## §10 한계\n\n## §11 집행\n"
    # 리서치·분석 파일은 빈 임시 폴더에서 읽는다 — 테스트 스탬프와 같은 날짜의 실제 run 산출물(data/run_evidence·analysis)이
    # 생기면 그 PDF 주소까지 섞여 실패했다(2026-10-19)
    saved_here, cc.HERE = cc.HERE, _tmp("notes_pdf_")
    try:
        r_pdf = cc.check_pdf_sources(MK, ST, note)[0]
        db.evidence_record(pdf, "IR 본문 " * 600, "", {"kind": "pdf", "pages": 3})          # 시도·판(v3 · ev_fetch)
        r_pdf2 = cc.check_pdf_sources(MK, ST, note)[0]
    finally:
        shutil.rmtree(cc.HERE, ignore_errors=True)
        cc.HERE = saved_here
    if r_pdf[1] or not r_pdf2[1]:
        p.append(f"PDF 열기 기록(저장소 ev_fetch): {r_pdf} → {r_pdf2}")
    report("(5) crosscheck — 확장·되풀이는 사실 표 · 합본 '해당 없음' · §3 보드 전수 = 발행분×섹터 · 인용 = 사실 id · PDF = 저장소 접속 기록", p)


def t6_verify_numbers_db_corpus():
    """verify_numbers — 말뭉치에 이번 run 발행분 본문·사실 한 줄이 들어간다 · 값 자리 표식과 사실 id는 숫자로 세지 않는다."""
    import verify_numbers as vn
    p = []
    db.run_start(ST, MK, session_date=DAY)
    iid = issue(body="SK하이닉스 3분기 매출 24조1,800억원 · 영업이익률 47.3%")
    db.connect().execute("INSERT INTO mat_link (issue_id, ord, url, run_id, body, fetched) VALUES (?,?,?,?,?,1)",   # v3 전 옛 행 모양(받은 본문)
                         (iid, 0, "https://x.example/a", RID, "링크 원문: HBM 비중 61.5%"))
    a = add(earn())["id"]
    tmp = _tmp("notes_vn_")
    try:
        note = tmp / "n.md"
        note.write_text(f"- 매출 24조1,800억원, 이익률 47.3%, HBM 61.5%\n- 영업이익 <!--F:{a}-->12.3456 조원<!--/F--> {{{{{a}}}}}\n",
                        encoding="utf-8")
        corpus = vn.db_corpus(ST, MK)
        miss = vn.check(note, [], (corpus,))
        bare = {m[1] for m in vn.check(note, [], ())}
        if miss:
            p.append(f"저장소 말뭉치로 확인돼야 할 수치: {[m[1] for m in miss]}")
        if not {"24조1,800억", "47.3", "61.5", "12.3456"} <= bare:
            p.append(f"말뭉치 없이도 통과했다(대조가 안 된다): {bare}")
        if any(x in bare for x in ("000001", "1")):
            p.append(f"사실 id를 숫자로 셌다: {bare}")
        if "재료 확장" in corpus or Q_A not in corpus:
            p.append("사실 원문 인용이 말뭉치에 없다")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report("(6) verify_numbers — 말뭉치 = 이번 run 발행분·링크 원문·사실 한 줄·인용 · 값 자리 표식·사실 id 제외", p)


def t7_fetch_upserts_source_and_corpus():
    """fetch.py(v3) — 시도마다 ev_fetch 한 행 · 본문은 내용 해시 판(보관 폴더 = 임시 저장소 옆) · 출처는 정규화 URL 한 행(제목) ·
    실패도 시도(출처 행은 있고 판은 없다) · 같은 내용을 다시 받으면 같은 판(n_fetch 2)·마지막 성공 시각만 새로 ·
    corpus.py search가 저장소 문서(발행분 본문)도 뒤진다."""
    import fetch as fe
    import ingest
    import corpus
    p = []
    tmp = _tmp("notes_fe_")
    saved = (ingest._fetch_text_meta, corpus.HERE)
    try:
        ingest._fetch_text_meta = lambda url: (("삼성전자 3분기 실적 발표\n본문 " * 50) if "ok" in url else "",
                                               "" if "ok" in url else "403", {"kind": "html"})
        quiet(fe.fetch, "https://WWW.Example.com/ok/report/?utm_source=x")
        r1 = db.q1("SELECT * FROM ev_source WHERE url=?", ("https://example.com/ok/report",))
        v1 = db.q1("SELECT * FROM ev_source_version WHERE source_id=?", ((r1 or {}).get("id"),))
        quiet(fe.fetch, "https://example.com/blocked")
        blk = db.q1("SELECT * FROM ev_source WHERE url LIKE '%blocked%'")
        if not r1 or r1.get("title") != "삼성전자 3분기 실적 발표" or not v1 or not db.raw_abs(v1["path"]).is_file() \
                or db.raw_dir() == db.HERE / "data" / "_raw_fc":
            p.append(f"출처·판(임시 보관 폴더): {r1} · {v1}")
        if not blk or db.q1("SELECT COUNT(*) AS n FROM ev_source_version WHERE source_id=?", (blk["id"],))["n"] != 0 \
                or (db.q1("SELECT status, error FROM ev_fetch WHERE source_id=?", (blk["id"],)) or {}).get("error") != "403":
            p.append(f"실패한 열기: 출처 행은 있고 판은 없고 시도는 error여야 한다 — {blk}")
        db.connect().execute("UPDATE ev_source SET last_fetched_at='2026-01-01T00:00:00' WHERE id=?", (r1["id"],))
        quiet(fe.fetch, "https://example.com/ok/report")
        r2 = db.q1("SELECT * FROM ev_source WHERE url=?", ("https://example.com/ok/report",))
        v2 = db.q("SELECT * FROM ev_source_version WHERE source_id=?", (r1["id"],))
        if r2["last_fetched_at"] <= "2026-01-01T00:00:00" or r2["title"] != r1["title"] or len(v2) != 1 or v2[0]["n_fetch"] != 2:
            p.append(f"다시 받기(같은 판 n_fetch 2 · 마지막 성공 시각): {r2} · {v2}")
        if len(db.fetch_rows()) != 3:
            p.append(f"시도 행 수 {len(db.fetch_rows())}(3이어야)")
        # corpus — 파일 0개여도 저장소 발행분 본문을 찾는다
        db.run_start(ST, MK, session_date=DAY)
        issue(body="정제마진이 배럴당 18.4달러로 올랐다")
        corpus.HERE = tmp / "empty"
        corpus.HERE.mkdir()
        a = types.SimpleNamespace(keywords=["정제마진"], max_per_file=6, out="")
        rc, out, _ = quiet(corpus.cmd_search, a)
        if rc != 0 or "── db:mat_issue/" not in out or "정제마진이 배럴당 18.4달러" not in out or "합계 1건" not in out:
            p.append(f"코퍼스 저장소 검색: {out[:300]!r}")
        a2 = types.SimpleNamespace(titles=False, brief=True, out="")
        _, out2, _ = quiet(corpus.cmd_index, a2)
        if "── db(저장소)/  (사실 0행 · 발행분 1통" not in out2:
            p.append(f"코퍼스 색인 저장소 줄: {out2[-200:]!r}")
    finally:
        ingest._fetch_text_meta, corpus.HERE = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(7) fetch(v3) → 시도·본문 판·출처(정규화 URL·제목) · 실패도 시도 · 같은 내용은 같은 판 · corpus가 저장소 발행분도 뒤진다", p)


def t8_close_counts_and_refs():
    """stage close — run_close 전에 REFS 줄 · 뉴스 전 사실 · run 표 집계(신규·재사용·되풀이·판정 행·감사 회차)."""
    import kis_client
    p = []
    st, mk = "261019", "us"
    rid = db.run_id(st, mk)
    db.run_start(st, mk, session_date=DAY)
    a = add(earn(ticker="BAC", period="2026Q3", metric="NII", actual=16.4, unit="B USD", url="https://www.sec.gov/x/bac.htm",
                 quote="NII of $16.4B increased $1.2B, or 8%; net interest yield of 2.16% increased 15 bps"), run=(st, mk))["id"]
    add(analyst(ticker="BAC", house="JPM", currency="USD"), run=(st, mk))
    other = add(earn(ticker="JPM", url="https://www.sec.gov/x/jpm.htm", quote="Net interest income of $27.1B up 13% year over year"),
                run=("261016", "us"))["id"]
    facts._attach_run(db.connect(), other, rid, st, mk)
    facts.add_ref(a, (st, mk), "note", f"분석노트_{st}_{mk}_v1_0.md", "§2", role="note")
    db.connect().execute("INSERT INTO ev_verdict (fact_id, run_id, batch, round, verdict, tier, fact_rev) VALUES (?,?,?,?,?,?, 1)",
                         (a, rid, "A", 1, "MATCH", 1))
    tmp = _tmp("notes_close_")
    led = tmp / "promotion_ledger.jsonl"
    led.write_text(json.dumps({"skill": "fact-check", "type": "finding", "run": f"AI클레임_{st}_{mk}_주문근거_판정.md", "verdict": "FAIL",
                               # 감사 줄은 run 시작 **뒤**여야 이 run의 감사다(v4 U03 — 시작보다 이른 줄은 다른 run·예행으로 본다).
                               # 시각을 고정하면 벽시계가 그 시각을 넘는 순간 시험이 깨진다(2026-10-19 23시 이후 실패) — 지금 기준으로 둔다
                               "ts": (datetime.now(KST) + timedelta(minutes=5)).isoformat(timespec="seconds")},
                              ensure_ascii=False) + "\n", encoding="utf-8")
    old_env, old_mode = os.environ.get("AITB_STEPGATE_LEDGER"), kis_client.account_mode
    try:
        os.environ["AITB_STEPGATE_LEDGER"] = str(led)
        kis_client.account_mode = lambda cfg=None: "paper"
        with stage_dirs():
            ca = types.SimpleNamespace(market=mk, stamp=st)
            rc, out, err = quiet(stage.cmd_close, ca)
    finally:
        kis_client.account_mode = old_mode
        if old_env is None:
            os.environ.pop("AITB_STEPGATE_LEDGER", None)
        else:
            os.environ["AITB_STEPGATE_LEDGER"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)
    r = db.run_get(st, mk) or {}
    if not re.search(r"^REFS OK \(facts\.py check-refs .* · 참조 1 · 사실 1\)$", out, re.M):
        p.append(f"REFS 줄: {out[-400:]!r}")
    if (r.get("facts_new"), r.get("facts_reused"), r.get("facts_repeat"), r.get("verified_rows"), r.get("audit_rounds")) != (2, 1, 0, 1, 1):
        p.append(f"ex_run 집계: {[r.get(k) for k in ('facts_new', 'facts_reused', 'facts_repeat', 'verified_rows', 'audit_rounds')]}")
    if r.get("before_news_n") != 2 or not r.get("closed_at") or "[사실] " not in out:
        p.append(f"뉴스 전·run_close: {r.get('before_news_n')} {r.get('closed_at')}")
    # REFS FAIL — 노트가 url 없는 사실을 가리키면
    db.connect().execute("UPDATE ev_fact SET url=NULL WHERE id=?", (a,))
    out2 = quiet(stage.fact_close, st, mk)[1]
    if not re.search(r"^REFS FAIL — 문제 1건", out2, re.M) or f"{a}: url 없음" not in out2:
        p.append(f"REFS FAIL 줄: {out2[:300]!r}")
    # 낡은 근거(FB-261017-9) — 최신 TRACK1 POST의 stale_support가 이 run에서 안 끝났으면 rc 4 · 점검표 없음 · run_close 보류
    st4 = "261020"
    db.run_start(st4, mk, session_date=DAY)
    t1 = {"post": {"stale_support": [{"ticker": "BAC", "facts": [a]}], "at": "2026-10-20T23:50:00+09:00"}}
    db.connect().execute("INSERT INTO ex_signal (run_id, track1) VALUES (?,?)", (db.run_id(st4, mk), json.dumps(t1)))
    seed(theses=[{"id": "us-bac-old", "ticker": "BAC", "market": "US", "status": "held",
                  "history": [{"ts": "2026-10-01T10:00:00+09:00", "status": "held", "note": "[보유 재판정] BAC — 지난 run의 메모"}]}])
    old_mode = kis_client.account_mode
    try:
        kis_client.account_mode = lambda cfg=None: "paper"
        with stage_dirs() as sd:
            ca4 = types.SimpleNamespace(market=mk, stamp=st4)
            rcp = sd / "data" / "run_evidence" / f"real_check_{st4}_{mk}.md"
            rcp.write_text("지난 점검표", encoding="utf-8")
            rc4, out4, err4 = quiet(stage.cmd_close, ca4)
            if rc4 != 4 or f"낡은 근거 미결 BAC({a})" not in out4 or rcp.exists() or (db.run_get(st4, mk) or {}).get("closed_at") \
                    or "낡은 근거 미결이라 run_close 보류" not in err4:
                p.append(f"낡은 근거 미결 close: rc={rc4} 점검표={rcp.exists()} closed={(db.run_get(st4, mk) or {}).get('closed_at')} "
                         f"{out4[-300:]!r} {err4[-200:]!r}")
            # ② 보유 재판정 노트(run 시작 뒤) → 닫힌다
            db.thesis_set_status("us-bac-old", "held", note="[보유 재판정] BAC — 낡은 사실 확인 불가 · 다른 받치는 사실 MATCH로 유지")
            rc5, out5, _ = quiet(stage.cmd_close, ca4)
            if rc5 != 0 or "낡은 근거 미결" in out5 or not (db.run_get(st4, mk) or {}).get("closed_at"):
                p.append(f"보유 재판정 뒤 close: rc={rc5} {out5[-200:]!r}")
    finally:
        kis_client.account_mode = old_mode
    # v4(U02) — 외부 점검 명령이 실패하면 종료 7 · run_close 보류 · 사실이 비면(REFS FAIL) 종료 5 · 점검표 치움
    old_mode = kis_client.account_mode
    try:
        kis_client.account_mode = lambda cfg=None: "paper"
        with stage_dirs() as sd:
            st7 = "261022"
            db.run_start(st7, mk, session_date=DAY)
            os.environ["STUB_REAL_CHECK_RC"] = "1"
            rc7, out7, err7 = quiet(stage.cmd_close, types.SimpleNamespace(market=mk, stamp=st7))
            os.environ.pop("STUB_REAL_CHECK_RC", None)
            if rc7 != 7 or (db.run_get(st7, mk) or {}).get("closed_at") or "외부 점검 명령 실패라 run_close 보류" not in err7:
                p.append(f"외부 점검 실패 close: rc={rc7} closed={(db.run_get(st7, mk) or {}).get('closed_at')} {err7[-200:]!r}")
            rc7b, _, _ = quiet(stage.cmd_close, types.SimpleNamespace(market=mk, stamp=st7))
            att = db.q1("SELECT MAX(attempt) AS n FROM ex_stage WHERE run_id=? AND stage='close'", (db.run_id(st7, mk),))["n"]
            if rc7b != 0 or not (db.run_get(st7, mk) or {}).get("closed_at") or att != 2:
                p.append(f"고친 뒤 close(새 시도 · 닫힘): rc={rc7b} 시도 {att}")
            st8 = "261023"
            db.run_start(st8, mk, session_date=DAY)
            b8 = add(earn(ticker="C", url="https://www.sec.gov/x/c.htm", quote="Citigroup net interest income rose to $14.0B"),
                     run=(st8, mk))["id"]
            facts.add_ref(b8, (st8, mk), "note", f"분석노트_{st8}_{mk}_v1_0.md", "§2", role="note")
            db.connect().execute("UPDATE ev_fact SET url=NULL WHERE id=?", (b8,))
            rcp8 = sd / "data" / "run_evidence" / f"real_check_{st8}_{mk}.md"
            rcp8.write_text("지난 점검표", encoding="utf-8")
            rc8, out8, err8 = quiet(stage.cmd_close, types.SimpleNamespace(market=mk, stamp=st8))
            if rc8 != 5 or rcp8.exists() or (db.run_get(st8, mk) or {}).get("closed_at") or "사실 층 미결" not in out8:
                p.append(f"REFS FAIL close: rc={rc8} 점검표={rcp8.exists()} {out8[-200:]!r}")
            # 저장 단계 실패(집행 기록 이관 예외) → 종료 6 · run_close 보류 · 단계 행 failed · 고친 뒤 다시 돌리면 0·닫힘(Codex U02 close_*)
            st9 = "261026"
            db.run_start(st9, mk, session_date=DAY)
            orig_imp = db.import_run_artifacts

            def _boom(*a_, **k_):
                raise RuntimeError("이관 실패(시험)")
            db.import_run_artifacts = _boom
            try:
                rc9, out9, err9 = quiet(stage.cmd_close, types.SimpleNamespace(market=mk, stamp=st9))
            finally:
                db.import_run_artifacts = orig_imp
            row9 = db.q1("SELECT status, rc FROM ex_stage WHERE run_id=? AND stage='close' ORDER BY attempt DESC LIMIT 1", (db.run_id(st9, mk),)) or {}
            if rc9 != 6 or (db.run_get(st9, mk) or {}).get("closed_at") or "마감 실패" not in err9 or row9.get("status") != "failed":
                p.append(f"저장 단계 실패 close: rc={rc9} closed={(db.run_get(st9, mk) or {}).get('closed_at')} 단계 {dict(row9)} {err9[-200:]!r}")
            rc9b, _, _ = quiet(stage.cmd_close, types.SimpleNamespace(market=mk, stamp=st9))
            if rc9b != 0 or not (db.run_get(st9, mk) or {}).get("closed_at"):
                p.append(f"이관을 고친 뒤 close: rc={rc9b}")
            # 낡은 근거 검사 자체가 죽으면 '미결'로 본다 → 종료 4 · run_close 보류(Codex U02 close_stale_checker_error)
            st10 = "261027"
            db.run_start(st10, mk, session_date=DAY)
            orig_so = stage._stale_open

            def _crash(*a_, **k_):
                raise RuntimeError("낡은 근거 검사 오류(시험)")
            stage._stale_open = _crash
            try:
                rc10, out10, _ = quiet(stage.cmd_close, types.SimpleNamespace(market=mk, stamp=st10))
            finally:
                stage._stale_open = orig_so
            if rc10 != 4 or (db.run_get(st10, mk) or {}).get("closed_at") or "낡은 근거 검사를 못 돌렸다" not in out10:
                p.append(f"낡은 근거 검사 오류 close: rc={rc10} closed={(db.run_get(st10, mk) or {}).get('closed_at')} {out10[-200:]!r}")
    finally:
        kis_client.account_mode = old_mode
        os.environ.pop("STUB_REAL_CHECK_RC", None)
    # ③ 되돌림 행 · ① 재확인으로 post에서 빠짐 → 끝난 것으로 본다
    st6 = "261021"
    db.run_start(st6, mk, session_date=DAY)
    db.connect().execute("INSERT INTO ex_signal (run_id, track1) VALUES (?,?)",
                         (db.run_id(st6, mk), json.dumps({"post": {"stale_support": [{"ticker": "MET", "facts": [a]},
                                                                                     {"ticker": "JPM", "facts": [other]}]}})))
    o6 = [x["ticker"] for x in stage._stale_open(st6, mk)]
    seed(reversals=[{"id": f"REV-{st6}-us-1", "stamp": st6, "market": "US", "ticker": "MET", "qty": 1, "kind": "hold_overturned",
                     "status": "pending"}])
    o7 = [x["ticker"] for x in stage._stale_open(st6, mk)]
    db.connect().execute("UPDATE ex_signal SET track1=? WHERE run_id=?", (json.dumps({"post": {"stale_support": []}}), db.run_id(st6, mk)))
    o8 = stage._stale_open(st6, mk)
    if o6 != ["MET", "JPM"] or o7 != ["JPM"] or o8:
        p.append(f"되돌림 행·재확인 해소: {o6} → {o7} → {o8}")
    report("(8) close — REFS 줄 · 뉴스 전 · ex_run 집계 · run_close · 낡은 근거 미결 rc 4 · 외부 점검 실패 rc 7 · REFS FAIL rc 5 · 저장 단계 실패 rc 6 · 검사 오류 rc 4 · 다시 돌리면 새 시도", p)


def t9_carry_blocks():
    """carry — 재사용 후보(facts.reuse) 절 · 감사 소급(저장소 대기 행) 절 · 늦게 온 주문 근거 감사(facts.carry_note) ·
    옛 모듈(srcledger·reversals·fc_track1)을 부르지 않는다."""
    p = []
    st_prev, st = "261016", "261019"
    f1 = add(earn(), run=(st_prev, MK))["id"]
    rb = stage.reuse_block(MK, st)
    if not rb.startswith("### 출처 재사용 후보 (facts.py reuse") or f1 not in rb or "```" not in rb:
        p.append(f"재사용 절: {rb[:200]!r}")
    seed(reversals=[{"id": "REV-261016-kr-1", "stamp": st_prev, "market": "KR", "ticker": "005930", "name": "삼성전자", "qty": 3,
                     "why": "감사 재채점 — 근거 MISMATCH", "fc_rows": ["ev_verdict:7"], "buy_ref": "approved_261016_kr.json",
                     "status": "pending"},
                    {"id": "REV-261016-kr-2", "stamp": st_prev, "market": "KR", "ticker": "000660", "qty": 2,
                     "why": "판단으로 되판 행(도구 출처 없음)", "status": "pending", "kind": "buy_unsupported"},
                    {"id": "REV-261016-us-1", "stamp": st_prev, "market": "US", "ticker": "BAC", "qty": 1, "status": "pending"}])
    rv = quiet(stage.reversal_block, MK)[0]
    if "| REV-261016-kr-1 | 005930 삼성전자 | 3 |" not in rv or "BAC" in rv or not rv.startswith("## 감사 소급"):
        p.append(f"감사 소급 절: {rv!r}")
    if "000660" in rv or {r["id"]: r["status"] for r in db.reversals_all()}.get("REV-261016-kr-2") != "void":
        p.append(f"도구 출처 없는 소급 행이 이어받기에 떴거나 void가 안 됐다: {rv!r}")
    db.run_start(st_prev, MK)
    db.connect().execute("UPDATE ex_run SET started='2026-10-16T09:00:00+09:00' WHERE run_id=?", (db.run_id(st_prev, MK),))   # 감사보다 먼저(v4)
    db.connect().execute("INSERT INTO ex_signal (run_id, track1) VALUES (?,?)",
                         (db.run_id(st_prev, MK), json.dumps({"rounds": 0, "post": {"rounds": 0, "stale_support": [
                             {"ticker": "005930", "facts": ["F-000009"]}]}})))
    tmp = _tmp("notes_carry_")
    led = tmp / "promotion_ledger.jsonl"
    led.write_text(json.dumps({"skill": "fact-check", "type": "finding", "run": f"AI클레임_{st_prev}_{MK}_주문근거_판정.md",
                               "verdict": "PASS", "ts": "2026-10-17T01:00:00"}, ensure_ascii=False) + "\n", encoding="utf-8")
    old = os.environ.get("AITB_STEPGATE_LEDGER")
    try:
        os.environ["AITB_STEPGATE_LEDGER"] = str(led)
        cn = facts.carry_note(MK, st)
    finally:
        if old is None:
            os.environ.pop("AITB_STEPGATE_LEDGER", None)
        else:
            os.environ["AITB_STEPGATE_LEDGER"] = old
        shutil.rmtree(tmp, ignore_errors=True)
    if f"python3 facts.py decide --mode post --stamp {st_prev} --market {MK}` — 감사 1회 · 반영 0회" not in cn:
        p.append(f"늦게 온 감사: {cn!r}")
    if f"### 낡은 근거(stale_support) — 보유 재판정 (직전 run {st_prev} TRACK1 POST)" not in cn or "| 005930 | F-000009 |" not in cn:
        p.append(f"직전 run 미결 낡은 근거: {cn[-400:]!r}")
    src = (HERE / "stage.py").read_text(encoding="utf-8")
    if re.search(r"import (srcledger|reversals|fc_track1)|\"srcledger\.py\"", src) or "reuse_block(a.market, st)" not in src \
            or "_f.carry_note(a.market, st)" not in src:
        p.append("stage.py carry가 옛 모듈을 부르거나 새 절을 안 붙인다")
    report("(9) carry — 재사용 후보 절(facts.reuse) · 감사 소급 절(저장소 · 도구 출처 없는 행 void) · 늦게 온 감사·낡은 근거(carry_note) · "
           "옛 모듈 안 부름", p)


def t10_dart_fetch_offline():
    """DART — 단일 공시 viewer는 MS949(밝힌 문자셋으로 읽는다) · main.do 틀은 목차 노드마다 viewer를 받아 잇는다 · --section ·
    목차가 없으면 첫 viewDoc 호출(2026-10-17 예행 D8). 네트워크 없이 가짜 응답으로."""
    import fetch
    import ingest
    p = []
    kr = "주식 소각 결정 - 소각할 주식의 종류와 수".encode("cp949")
    if ingest.decode_html(kr, "MS949") != "주식 소각 결정 - 소각할 주식의 종류와 수":
        p.append("머리의 MS949를 따르지 않는다")
    if ingest.decode_html(b'<meta charset="euc-kr">' + kr, "").endswith("주식 소각 결정 - 소각할 주식의 종류와 수") is False:
        p.append("<meta charset=euc-kr>를 따르지 않는다")
    if ingest.decode_html("유틸 utf-8 본문".encode("utf-8"), "") != "유틸 utf-8 본문":
        p.append("문자셋 없는 utf-8")
    if ingest.decode_html(kr, "") != "주식 소각 결정 - 소각할 주식의 종류와 수":
        p.append("문자셋 없는 cp949(utf-8 실패 뒤 cp949)")
    shell = """<script>function makeToc(){
        var node1 = {}; node1['text'] = "분 기 보 고 서"; node1['rcpNo'] = "20260515000478"; node1['dcmNo'] = "11381831";
        node1['eleId'] = "1"; node1['offset'] = "905"; node1['length'] = "4513"; node1['dtd'] = "dart4.xsd";
        var node2 = {}; node2['text'] = "III. 재무에 관한 사항"; node2['rcpNo'] = "20260515000478"; node2['dcmNo'] = "11381831";
        node2['eleId'] = "29"; node2['offset'] = "646980"; node2['length'] = "58833"; node2['dtd'] = "dart4.xsd";
        }</script>"""
    nodes = fetch.dart_nodes(shell)
    if [(n["text"], n["eleId"], n["offset"]) for n in nodes] != [("분 기 보 고 서", "1", "905"), ("III. 재무에 관한 사항", "29", "646980")]:
        p.append(f"목차 노드: {nodes}")
    single = fetch.dart_nodes('x viewDoc(rcpNo, dcmNo) y viewDoc("20260724800552", "11494195", "0", "0", "0", "HTML", "")')
    if [(n["dcmNo"], n["dtd"]) for n in single] != [("11494195", "HTML")]:
        p.append(f"목차 없는 단일 공시의 viewDoc: {single}")
    asked = []

    def fake_get(url, referer):
        asked.append(url)
        if "main.do" in url:
            return shell
        ele = url.split("eleId=")[1].split("&")[0]
        return f"<STYLE>p{{padding:0px}}</STYLE><p>절 {ele} 본문 윤활부문 영업이익 166,560 백만원 " + "가" * 200 + "</p>"
    saved = fetch._dart_get
    fetch._dart_get = fake_get
    try:
        body, err, meta = fetch.dart_main("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515000478")
        if err or "■ 분 기 보 고 서" not in body or "■ III. 재무에 관한 사항" not in body or "padding" in body \
                or len([u for u in asked if "viewer.do" in u]) != 2 or "eleId=29&offset=646980&length=58833&dtd=dart4.xsd" not in asked[-1]:
            p.append(f"main.do → 목차마다 viewer: err={err} asked={asked[-2:]} body={body[:120]!r}")
        asked.clear()
        body2, err2, _ = fetch.dart_main("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515000478", section="재무에 관한")
        if err2 or len([u for u in asked if "viewer.do" in u]) != 1 or "분 기 보 고 서" in body2:
            p.append(f"--section: {err2} {asked}")
        _, err3, _ = fetch.dart_main("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515000478", section="없는 절")
        if "없다" not in err3:
            p.append(f"없는 절: {err3}")
    finally:
        fetch._dart_get = saved
    report("(10) DART 열기 — MS949·meta 문자셋 · 목차 노드마다 viewer · --section · 단일 공시 viewDoc", p)


def t11_crosscheck_dart_read():
    """FB-261017-4 — 이번 run 공시 목록(mat_dart)의 유니버스 종목 잠정실적·주요 공시는 노트 한 줄에 종목과 공시(접수번호·보고서 낱말)가
    같이 있어야 통과 · 유니버스 밖·판정 입력이 아닌 공시(임원 소유 보고 등)는 세지 않는다 · 미국 run은 해당 없음."""
    import crosscheck as cc
    p = []
    db.run_start(ST, MK, session_date=DAY)
    seed(watchlist={"_comment": "", "schema_version": "1.0",
                    "KR": [{"ticker": "068270", "name": "셀트리온", "aliases": []}, {"ticker": "005930", "name": "삼성전자", "aliases": []}],
                    "US": [], "benchmarks": {}})
    c = db.connect()
    for rcp, tk, nm, rep in (("20261012800001", "068270", "셀트리온", "연결재무제표기준영업(잠정)실적(공정공시)"),
                             ("20261016000492", "005930", "삼성전자", "임원ㆍ주요주주특정증권등소유상황보고서"),
                             ("20261016000999", "999999", "밖종목", "단일판매ㆍ공급계약체결")):
        c.execute("INSERT INTO mat_dart (rcp_no, ticker, corp_name, report_nm, rcept_dt, run_id) VALUES (?,?,?,?,?,?)",
                  (rcp, tk, nm, rep, rcp[:8], RID))
    r0 = cc.check_dart_read(MK, ST, "## §6\n| 068270 | 셀트리온 | 논지 없음 |\n")[0]
    if r0[1] or "068270" not in r0[2] or "005930" in r0[2] or "999999" in r0[2]:
        p.append(f"안 읽은 잠정실적이 통과했거나 판정 입력이 아닌 공시를 셌다: {r0}")
    r1 = cc.check_dart_read(MK, ST, "## §6\n| 068270 | 셀트리온 | 3Q 잠정 영업이익 확인(20261012800001) — [통과] 이미 반영 |\n")[0]
    if not r1[1] or "1건 전부" not in r1[2]:
        p.append(f"접수번호와 함께 판정한 줄이 있는데 실패: {r1}")
    if not cc.check_dart_read("us", ST, "")[0][1]:
        p.append("미국 run에 국내 공시 검사를 걸었다")
    report("(11) crosscheck 공시 → 노트 판정 — 유니버스 종목 잠정실적·주요 공시만 · 종목과 공시가 한 줄에", p)


def t12_crosscheck_factor_judged():
    """FB-261017-7 — 꺾인(또는 unknown) 요인마다 노트 §7 `[요인 판정] <요인>` 블록(원인: · 상태: · 바스켓 보유마다 결정) ·
    1단 요인 상태가 없으면 FAIL · 꺾인 요인 종목의 [하락 원인] 줄엔 요인 상태 · note_lint.signal이 노트에 없는 요인 판정을 잡는다."""
    import crosscheck as cc
    import market_map as mm
    import note_lint as nl
    p = []
    seed(watchlist={"_comment": "", "schema_version": "1.0",
                    "KR": [{"ticker": "005930", "name": "삼성전자", "aliases": []}, {"ticker": "000660", "name": "SK하이닉스", "aliases": []}],
                    "US": [], "benchmarks": {}})
    db.factor_state_put(RID, [
        {"driver": "ai_capex", "state": {"fired": True, "fired_sell": True, "state": "fired", "sells_n": 1, "sells_tickers": ["000660"],
                                         "members": [{"ticker": "005930", "market": "KR", "held": True, "sign": 1, "has_bars": True},
                                                     {"ticker": "000660", "market": "KR", "held": True, "sign": 1, "has_bars": True}]}},
        {"driver": "rates_up", "state": {"fired": False, "state": "calm", "members": []}}])
    tmp = _tmp("notes_factor_")
    saved = (mm.MATERIAL_DIR, cc._fired_entries)
    try:
        mm.MATERIAL_DIR = tmp                       # 실제 data/ 스냅샷을 읽지 않는다
        head = "## §7 제안\n| S3 | ai_capex가 꺾이면 — 바스켓 고점 대비 ≥ 일중폭×3 또는 규칙 매도 | [추정] | [KR]·[US] 요인 판정 |\n"
        r0 = cc.check_driver_branch(MK, ST, head + "## §8\n")
        if len(r0) != 2 or r0[1][1] or "`ai_capex`(fired) 블록 없음" not in r0[1][2]:
            p.append(f"블록 없음 FAIL: {r0}")
        blk = ("[요인 판정] ai_capex — 발화: 규칙 매도 1(000660)\n원인: 대형 고객 투자 하향 [S3]\n상태: broken — 동인 꺾임\n"
               "| 종목 | 보유/후보 | 결정 | 사유 |\n|---|---|---|---|\n| 005930 삼성전자 | 보유 | 유지 | 메모리 가격 동인 무관 |\n")
        r1 = cc.check_driver_branch(MK, ST, head + blk + "| 000660 SK하이닉스 | 보유 | 정리 | 동인 직격 |\n## §8\n")
        if not r1[1][1]:
            p.append(f"블록+멤버 전부인데 FAIL: {r1[1]}")
        r2 = cc.check_driver_branch(MK, ST, head + blk + "## §8\n")
        if r2[1][1] or "000660 결정" not in r2[1][2]:
            p.append(f"멤버 결정 빠짐을 못 짚었다: {r2[1]}")
        r3 = cc.check_driver_branch(MK, "261020", head + "## §8\n")
        if r3[1][1] or "요인 상태 없음" not in r3[1][2]:
            p.append(f"요인 상태 없는 run: {r3[1]}")
        # [하락 원인] — 꺾인 요인 종목이면 줄에 '요인'
        cc._fired_entries = lambda m, s: [("005930", "kr-x", "price <= 60000", "retrace_50 되돌림")]
        d0 = cc.check_drop_cause(MK, ST, "## §6\n[하락 원인] 삼성전자 — 업종 동반 y · 회사 뉴스 없음 → 집행\n## §7\n")[0]
        d1 = cc.check_drop_cause(MK, ST, "## §6\n[하락 원인] 삼성전자 — 업종 동반 y · 회사 뉴스 없음 · 요인 ai_capex broken(§7 [요인 판정])\n## §7\n")[0]
        if d0[1] or "요인 상태가 없다" not in d0[2] or not d1[1]:
            p.append(f"[하락 원인] 요인 꼬리: {d0} / {d1}")
    finally:
        mm.MATERIAL_DIR, cc._fired_entries = saved
        shutil.rmtree(tmp, ignore_errors=True)
    sig = {"factor_judgments": [{"driver": "fuel_supply_shock", "state": "intact", "cause": "x", "sources": ["S2"],
                                 "decisions": [{"ticker": "010950", "decision": "유지"}]}]}
    errs, _ = nl.lint(sig, "## §7\n[요인 판정] ai_capex\n", True)
    if not any("요인 판정 fuel_supply_shock가 노트에 없다" in e for e in errs) or not any("010950" in e for e in errs):
        p.append(f"note_lint.signal 요인 판정: {errs}")
    report("(12) crosscheck 요인 판정 — 블록·원인·상태·보유별 결정 · 요인 상태 없으면 FAIL · [하락 원인] 요인 꼬리 · note_lint", p)


# ───────────────────────────── 2026-10-19 Codex U01·S01 — 원문 판(저장소 판 v3) ─────────────────────────────

def _fake_fetch(pages: dict):
    """ingest._fetch_text_meta 대역 — url → (본문, 오류). 테스트 중 바꿔 끼운다(사이트 응답이 바뀐 것을 흉내)."""
    import ingest
    saved = ingest._fetch_text_meta
    ingest._fetch_text_meta = lambda url: (pages.get(url, ("", "404"))[0], pages.get(url, ("", "404"))[1], {"kind": "html"})
    return saved


def t13_fetch_versions():
    """(13) 리뷰 재현 fetch_rewrites_referenced_body의 올바른 동작 — 본문 A로 사실 등록 뒤 사이트가 B로 바뀌어 다시 받으면 판 2개·파일 2개 ·
    사실은 판 A(인용 일치 → recorded) · lint가 대조하는 원문 = A · A를 다시 받으면 같은 판(n_fetch 2)."""
    import fetch as fe
    import ingest
    p = []
    url = "https://ir.example.com/2026q3/release"       # DART main.do는 fetch가 목차 경로로 연다 — 일반 주소로 잰다
    A = "공시 원문 — " + Q_A + " · 그 밖의 문단"
    B = "정정 공시 원문 — 3분기 연결 영업이익은 12조 1,000억원으로 정정합니다 · 그 밖의 문단"
    pages = {url: (A, "")}
    saved = _fake_fetch(pages)
    try:
        quiet(fe.fetch, url)
        fid = add(earn(url=url))["id"]
        pages[url] = (B, "")
        quiet(fe.fetch, url)
        vs = db.q("SELECT * FROM ev_source_version ORDER BY id")
        f = db.q1("SELECT source_version_id, source_version_link FROM ev_fact WHERE id=?", (fid,))
        files = [x for x in db.raw_dir().glob("*.txt")]
        if len(vs) != 2 or len(files) != 2 or len(db.fetch_rows()) != 2:
            p.append(f"판 {len(vs)} · 파일 {len(files)} · 시도 {len(db.fetch_rows())}(2·2·2)")
        if not f or f["source_version_id"] != vs[0]["id"] or f["source_version_link"] != "recorded":
            p.append(f"사실이 본 판(A · recorded): {f}")
        if db.raw_text(vs[0]["id"]) != A:
            p.append("판 A 본문이 바뀌었다")
        raw, vid, _note = facts._raw_for_row({"fact_id": fid, "_url": url})
        if raw != A or vid != vs[0]["id"]:
            p.append(f"lint 대조 원문이 A가 아니다: v{vid}")
        pages[url] = (A, "")
        quiet(fe.fetch, url)
        n = db.q1("SELECT n_fetch FROM ev_source_version WHERE id=?", (vs[0]["id"],))["n_fetch"]
        if n != 2 or len(list(db.raw_dir().glob("*.txt"))) != 2:
            p.append(f"같은 내용 다시 받기: n_fetch {n} · 파일 {len(list(db.raw_dir().glob('*.txt')))}")
    finally:
        ingest._fetch_text_meta = saved
    report("(13) 원문 판 — 내용이 바뀌면 새 판(옛 판 그대로) · 사실은 본 판(인용 일치 recorded) · lint는 그 판과 대조(리뷰 재현 U01)", p)


def t14_fetch_retention():
    """(14) 리뷰 재현 fetch_purges_body_still_referenced_by_fact의 올바른 동작 — fetch는 파일을 지우지 않는다 · raw_purge는 참조된 판
    (사실·링크)을 남기고 참조 없는 8일 된 판만 파일을 지운다(행은 남김 · purged_at) · 다시 받으면 판이 되살아난다."""
    import fetch as fe
    import ingest
    p = []
    U = "https://ir.example.com/2026q3/release"
    pages = {U: ("원문 — " + Q_A, ""), "https://example.com/other": ("참조 없는 다른 문서 본문입니다", ""),
             "https://example.com/linked": ("링크 한 홉 본문 — 재료가 본 기사", "")}
    saved = _fake_fetch(pages)
    try:
        quiet(fe.fetch, U)
        fid = add(earn(url=U))["id"]
        quiet(fe.fetch, "https://example.com/other")
        db.run_start(ST, MK, session_date=DAY)
        iid = issue()
        db.connect().execute("INSERT INTO mat_link (issue_id, ord, url, run_id) VALUES (?,?,?,?)", (iid, 0, "https://example.com/linked", RID))
        lid = ingest.material_link_record(RID, [iid], "a", "https://example.com/linked", "링크 한 홉 본문 — 재료가 본 기사", "", [])
        old = (datetime.now(KST) - timedelta(days=8)).isoformat(timespec="seconds")
        db.connect().execute("UPDATE ev_source_version SET last_seen=?", (old,))
        quiet(fe.fetch, "https://example.com/new")              # 열기는 아무것도 지우지 않는다
        if len(list(db.raw_dir().glob("*.txt"))) != 3:
            p.append(f"fetch가 파일을 지웠다: {len(list(db.raw_dir().glob('*.txt')))}")
        r = db.raw_purge(7)
        vf = db.q1("SELECT path, purged_at FROM ev_source_version v JOIN ev_fact f ON f.source_version_id=v.id WHERE f.id=?", (fid,))
        vo = db.q1("SELECT v.path, v.purged_at FROM ev_source_version v JOIN ev_source s ON s.id=v.source_id WHERE s.url LIKE '%other'")
        vl = db.q1("SELECT v.path, v.purged_at FROM ev_source_version v JOIN mat_link l ON l.version_id=v.id WHERE l.id=?", (lid,))
        if r["purged"] != 1 or r["kept_referenced"] != 2 or not vf["path"] or not vl["path"] or vo["path"] or not vo["purged_at"]:
            p.append(f"정리: {r} · 사실 판 {vf} · 링크 판 {vl} · 참조 없는 판 {vo}")
        if len(list(db.raw_dir().glob("*.txt"))) != 2:
            p.append(f"남은 파일 {len(list(db.raw_dir().glob('*.txt')))}(2)")
        quiet(fe.fetch, "https://example.com/other")
        vo2 = db.q1("SELECT v.path, v.purged_at FROM ev_source_version v JOIN ev_source s ON s.id=v.source_id WHERE s.url LIKE '%other'")
        if not vo2["path"] or vo2["purged_at"] or not db.raw_abs(vo2["path"]).is_file():
            p.append(f"정리된 판을 다시 받았는데 되살아나지 않았다: {vo2}")
    finally:
        ingest._fetch_text_meta = saved
    report("(14) 보존 — fetch는 안 지움 · raw_purge는 참조된 판(사실·링크) 보존·참조 없는 8일 판만 정리 · 다시 받으면 되살아남(리뷰 재현 U01)", p)


def t15_fetch_section_failure():
    """(15) 절·실패 — 같은 DART 문서의 --section 절과 전체는 다른 판(section) · 인용이 절에만 있으면 사실은 절 판 · 실패는 판 없는 시도 ·
    source_version을 주면 그 판(recorded) · 다른 출처의 판은 거부."""
    import fetch as fe
    import ingest
    p = []
    dart = "https://dart.fss.or.kr/report/viewer.do?rcpNo=20261014000001"
    saved = ingest._fetch_text_meta
    seq = iter([("재무에 관한 사항 — " + Q_A, ""), ("목차 · 회사의 개요 · 사업의 내용", "")])
    try:
        ingest._fetch_text_meta = lambda url: (*next(seq), {"kind": "html"})
        quiet(fe.fetch, dart, 20000, "재무에 관한 사항")
        quiet(fe.fetch, dart)
        vs = db.q("SELECT id, section FROM ev_source_version ORDER BY id")
        fid = add(earn(url=dart))["id"]
        f = db.q1("SELECT source_version_id, source_version_link FROM ev_fact WHERE id=?", (fid,))
        if [v["section"] for v in vs] != ["재무에 관한 사항", ""] or f["source_version_id"] != vs[0]["id"] or f["source_version_link"] != "recorded":
            p.append(f"절 판: {vs} · 사실 {f}")
        ingest._fetch_text_meta = lambda url: ("", "HTTP 403", {"kind": "html"})
        quiet(fe.fetch, "https://blocked.example.com/a")
        fr = db.fetch_rows(url="https://blocked.example.com/a")
        if len(fr) != 1 or fr[0]["status"] != "error" or fr[0]["version_id"] is not None:
            p.append(f"실패 시도: {fr}")
        fid2 = add(analyst(url=dart, house="NH", source_version=vs[1]["id"]))["id"]
        if (db.q1("SELECT source_version_id, source_version_link FROM ev_fact WHERE id=?", (fid2,)) or {}) != \
                {"source_version_id": vs[1]["id"], "source_version_link": "recorded"}:
            p.append("source_version을 준 사실이 그 판에 이어지지 않았다")
        r, _, err = quiet(facts.add_rows, [analyst(house="SK", source_version=vs[0]["id"])], (ST, MK))
        if r == 0:
            p.append("다른 출처의 판을 받았다")
    finally:
        ingest._fetch_text_meta = saved
    report("(15) 절·실패 — 절은 다른 판 · 인용이 든 절 판에 사실 · 실패는 판 없는 시도 · source_version은 recorded · 다른 출처 판 거부", p)


def t16_link_versions():
    """(16) 리뷰 재현 refetch_error_overwrites_article의 올바른 동작 — 링크 성공 → 503 → 바뀐 본문: 503 뒤에도 판·본문 그대로(재료는
    직전 본문 + 실패 한 줄) · 시도 3행 · 바뀐 본문은 새 판 · 이력 2행 · 옛 판 본문 그대로 · body 칸은 비어 있다."""
    import ingest
    p = []
    db.run_start(ST, MK, session_date=DAY)
    iid = issue()
    url = "https://news.example.com/article-1"
    db.connect().execute("INSERT INTO mat_link (issue_id, ord, url, run_id) VALUES (?,?,?,?)", (iid, 0, url, RID))
    lid = ingest.material_link_record(RID, [iid], "a", url, "첫 기사 본문 — HBM 비중 61.5%", "", [("term", "HBM")])
    v1 = db.q1("SELECT version_id FROM mat_link WHERE id=?", (lid,))["version_id"]
    ingest.material_link_record("261020_kr", [iid], "a", url, "", "HTTP 503", [])
    r = db.q1("SELECT version_id, body, fetched FROM mat_link WHERE id=?", (lid,))
    body, err, _ = ingest.material_link_result(lid)
    if r["version_id"] != v1 or r["body"] is not None or r["fetched"] != 1 or "첫 기사 본문" not in body or "503" not in body:
        p.append(f"503 뒤: {r} · 재료 {body[:80]!r}")
    ingest.material_link_record("261021_kr", [iid], "a", url, "바뀐 기사 본문 — HBM 비중 63%", "", [])
    r2 = db.q1("SELECT version_id FROM mat_link WHERE id=?", (lid,))
    hist = db.q("SELECT version_id FROM mat_link_version WHERE link_id=? ORDER BY fetch_id", (lid,))
    tries = [x["status"] for x in db.fetch_rows(url=url)]
    if r2["version_id"] == v1 or [h["version_id"] for h in hist] != [v1, r2["version_id"]] or tries != ["ok", "error", "ok"]:
        p.append(f"바뀐 본문: 판 {r2} · 이력 {hist} · 시도 {tries}")
    if db.raw_text(v1) != "첫 기사 본문 — HBM 비중 61.5%":
        p.append("옛 판 본문이 바뀌었다")
    report("(16) 링크 판 — 재조회 실패가 본문을 덮지 않음 · 직전 본문 + 실패 한 줄 · 바뀐 본문은 새 판 · 이력 · body 칸 비움(리뷰 재현 S01)", p)


def t17_capture_stage_result():
    """(17) 리뷰 재현 capture_ignores_ingest_failure · failed_capture_followed_by_carry_map의 올바른 동작(v4 · U02) —
    ingest 실패 → capture 종료 2 · 단계 failed · capture_at 없음 · 노트 뼈대 없음 · carry·map 종료 2 /
    ingest는 됐는데 새 스냅샷을 안 씀 → 종료 2 / 선택 수집 실패(뉴스레터) → reduced · capture_at · §10 [제한·1단] · 산출물 대조 ·
    스냅샷이 바뀌면 carry 전제 실패."""
    import kis_client
    import sessions as _ss
    p = []
    mk = "kr"
    st = _ss.session_stamp(mk)
    rid = db.run_id(st, mk)
    plan = {}
    saved = {k: getattr(stage, k) for k in ("run", "build_board", "capital_line", "build_universe", "market_window_block",
                                            "watch_loop_block", "tests_block", "news_window_block", "regime_inputs")}
    old_mode = kis_client.account_mode

    def fake_run(cmd, title, *, required=False, ok_rc=(0,)):
        rc = plan.get(cmd[0], 0)
        if cmd[0] == "ingest.py" and plan.get("write"):
            snap = stage.PROJ / "data" / f"snapshot_{st}_{mk}.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps({"generated_at": datetime.now(KST).isoformat(), "balance": {},
                                        "news_status": plan.get("news_status") or {}}), encoding="utf-8")
        return stage.StepResult(f"### {title}\n\n(대역 rc={rc})\n", name=title, cmd=cmd, rc=rc, required=required,
                                status="ok" if rc in ok_rc else ("failed" if required else "reduced"))
    try:
        kis_client.account_mode = lambda cfg=None: "paper"
        stage.run = fake_run
        stage.build_board = lambda market, quick, record=False: ("보드", "보드 표", 3)
        stage.capital_line = lambda market, st_: "자본 기준 — 테스트 1,000만"
        stage.build_universe = lambda market, st_: ("유니버스 표", 4)
        for k in ("market_window_block", "watch_loop_block", "news_window_block"):
            setattr(stage, k, lambda *a_, **k_: "(대역 블록)")
        stage.tests_block = lambda: "(대역 테스트)"
        stage.regime_inputs = lambda *a_, **k_: "(대역 국면)"
        args = types.SimpleNamespace(market=mk, stamp=st, days=1, quick=True, no_record=True, rev=1, force=True)
        with stage_dirs() as sd:
            snap = sd / "data" / f"snapshot_{st}_{mk}.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps({"generated_at": (datetime.now(KST) - timedelta(days=1)).isoformat()}), encoding="utf-8")
            plan.update({"ingest.py": 2, "write": False})
            rc, out, err = quiet(stage.cmd_capture, args)
            last = db.q1("SELECT status, rc FROM ex_stage WHERE run_id=? AND stage='capture' ORDER BY id DESC LIMIT 1", (rid,))
            tools = (sd / "data" / "run_evidence" / f"tools_{st}_{mk}.md").read_text(encoding="utf-8")
            if rc != 2 or (last or {}).get("status") != "failed" or (db.run_get(st, mk) or {}).get("capture_at") \
                    or list((sd / "analysis").glob(f"분석노트_{st}_{mk}*")) or "1단 실패" not in tools:
                p.append(f"ingest 실패: rc={rc} {last} 노트 {list((sd / 'analysis').glob('*'))}")
            ca = types.SimpleNamespace(market=mk, stamp=st, refresh=False)
            if quiet(stage.cmd_carry, ca)[0] != 2 or quiet(stage.cmd_map, ca)[0] != 2:
                p.append("실패한 capture 뒤 carry·map이 돌았다")
            plan.update({"ingest.py": 0, "write": False})
            rc2, _, _ = quiet(stage.cmd_capture, args)
            if rc2 != 2:
                p.append(f"새 스냅샷을 안 썼는데 통과: rc={rc2}")
            plan.update({"ingest.py": 0, "write": True, "news_status": {"attempted": True, "error": "종료코드 1: 메일 서버 오류"}})
            rc3, out3, _ = quiet(stage.cmd_capture, args)
            last3 = db.q1("SELECT status FROM ex_stage WHERE run_id=? AND stage='capture' ORDER BY id DESC LIMIT 1", (rid,))
            note = next(iter(sorted((sd / "analysis").glob(f"분석노트_{st}_{mk}*"))), None)
            if rc3 != 0 or (last3 or {}).get("status") != "reduced" or not (db.run_get(st, mk) or {}).get("capture_at") or not note \
                    or "[제한·1단] 뉴스레터 수집" not in note.read_text(encoding="utf-8"):
                p.append(f"선택 실패 reduced: rc={rc3} {last3} note={note}")
            if stage.capture_ok(st, mk) is not None:
                p.append(f"성공한 capture 뒤 전제 실패: {stage.capture_ok(st, mk)}")
            snap.write_text(snap.read_text(encoding="utf-8") + " ", encoding="utf-8")
            if stage.capture_ok(st, mk) is None:
                p.append("스냅샷이 바뀌었는데 전제가 통과했다")
    finally:
        kis_client.account_mode = old_mode
        for k, v in saved.items():
            setattr(stage, k, v)
    report("(17) capture 단계 결과 — ingest 실패·옛 스냅샷이면 종료 2·carry/map 2 · 선택 실패는 reduced + §10 제한 · 산출물 해시(리뷰 재현 U02)", p)


def t18_calendar_weekday_resolution():
    """(18) 리뷰 재현 calendar_year_rollover의 올바른 동작(v5 · S02) — 발행일 주(±1주)에서 요일·일자가 둘 다 맞는 날짜 하나:
    12/28 발행 '금 1' → 2027-01-01 · 11/02 발행 '금 30' → 10-30 · 09/28 발행 '목 1' → 10-01 · 윤년 2028-02-29 · 불일치·없는 날짜 → None ·
    재료 표에서: 미확정 줄은 저장하지 않고 남긴다 · 저장 행에 발행일·원 줄·요일·confirmed."""
    import ingest
    from datetime import date as _d
    p = []
    r = ingest.resolve_weekday_date
    cases = [((_d(2026, 12, 28), "금", 1), _d(2027, 1, 1)), ((_d(2026, 11, 2), "금", 30), _d(2026, 10, 30)),
             ((_d(2026, 9, 28), "목", 1), _d(2026, 10, 1)), ((_d(2028, 2, 28), "화", 29), _d(2028, 2, 29)),
             ((_d(2026, 10, 19), "수", 22), None), ((_d(2026, 2, 25), "월", 30), None)]
    for args, want in cases:
        if r(*args) != want:
            p.append(f"resolve{args} = {r(*args)} (기대 {want})")
    tmp = _tmp("notes_cal_")
    try:
        md = tmp / "material_261228_kr.md"
        md.write_text("## [경제·금융] 머니레터 — 2026/12/28\n**주간 일정**\n\n"
                      "금 1 (실적발표) 삼성전자 실적\n수 31 (실적발표) SK하이닉스 실적\n", encoding="utf-8")
        wl = {"KR": [{"ticker": "005930", "name": "삼성전자"}, {"ticker": "000660", "name": "SK하이닉스"}], "US": []}
        n = ingest.calendar_from_material(md, wl, "261228")
        ev = {e["key"]: e for e in db.calendar_all()}
        e1 = ev.get("005930_er_270101") or {}
        if n != 1 or e1.get("date") != "2027-01-01" or e1.get("issue_date") != "2026-12-28" or e1.get("weekday_token") != "금" \
                or e1.get("confirmed") != 1 or "삼성전자" not in str(e1.get("source_text")):
            p.append(f"재료 표 일정: {n}건 · {e1}")
        if any(k.startswith("000660") for k in ev) or not any("SK하이닉스" in x and "미확정" in x for x in ingest.CAL_UNRESOLVED):
            p.append(f"요일·일자 불일치(12-31은 목요일) 줄을 저장했거나 남기지 않았다: {ingest.CAL_UNRESOLVED}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report("(18) 일정 날짜 — 발행일 주에서 요일·일자로 · 연말·월초·윤년 · 불일치는 저장 안 함·미확정 줄 · 발행일·원 줄·요일(리뷰 재현 S02)", p)


def _mk_issue(c, runs, subject, day, sha, sender="머니레터"):
    iid = c.execute("INSERT INTO mat_issue (run_id, sender, source, topic, day, subject, body, content_sha, first_run) VALUES (?,?,?,?,?,?,?,?,?)",
                    (runs[0], sender, sender, "경제", day, subject, "본문", sha, runs[0])).lastrowid
    for r in runs:
        c.execute("INSERT INTO mat_issue_run (issue_id, run_id, ord) VALUES (?,?,?)", (iid, r, iid))
    return iid


def t19_link_selection():
    """(19) ← link_selection_first_fifteen(Codex G01) — 링크를 수집 순서대로 15개 잘라 최신 호의 중요한 링크가 밀렸고, 이미 읽은 호·이미 연 주소가
    자리를 다퉜으며 잘린 링크는 기록이 없었다. 올바른 동작: 이번 run의 새 호만 · 층 5/5/5(남는 칸은 다음 층→앞 층) · 최신 호 먼저 ·
    이미 성공 조회한 주소 건너뜀 · 탐색 = 호마다 머리 링크 · 모든 후보에 pick_status·사유·순위(이미 읽은 호의 링크는 비움)."""
    import ingest
    p = []
    db.run_start(ST, MK, session_date=DAY)
    c = db.connect()
    prev = "261018_kr"
    i0 = _mk_issue(c, [prev, RID], "옛 호", "2026/10/16", "sha-i0")
    i1 = _mk_issue(c, [RID], "중간 호", "2026/10/17", "sha-i1")
    i2 = _mk_issue(c, [RID], "최신 호", "2026/10/19", "sha-i2")
    raw = []

    def link(iid, o, anchor, url, fetched=None, run=None):
        raw.append(f"- [{anchor}] {url}")
        return c.execute("INSERT INTO mat_link (issue_id, ord, url, title, fetched, run_id) VALUES (?,?,?,?,?,?)",
                         (iid, o, url, anchor, fetched, run)).lastrowid
    l0 = link(i0, 0, "삼성전자 옛 기사", "https://n.example.com/old0")
    ids1 = [link(i1, k, f"삼성전자 기사 {k}", f"https://n.example.com/i1-{k}") for k in range(16)]
    seen = link(i1, 16, "삼성전자 이미 본 기사", "https://n.example.com/seen")
    c.execute("INSERT INTO mat_link (issue_id, ord, url, title, fetched, run_id) VALUES (?,?,?,?,?,?)",
              (i0, 9, "https://n.example.com/seen", "x", 1, prev))
    lead = link(i2, 0, "반도체 수출 사상 최대", "https://n.example.com/i2-lead")
    hyn = link(i2, 1, "SK하이닉스 신고가", "https://n.example.com/i2-hynix")
    st = {}
    picked = ingest._pick_links(RID, db.material_issues_for_run(RID), "\n".join(raw), ["삼성전자", "SK하이닉스"], ingest.RX_WRAP_LINK, st)
    want = {"candidates": 19, "picked": 15, "wrap": 0, "term": 14, "explore": 1, "cut": 3, "skipped": 1}
    if {k: st.get(k) for k in want} != want:
        p.append(f"상태: {st}")
    g = lambda lid: db.q1("SELECT pick_status, pick_reason, pick_rank FROM mat_link WHERE id=?", (lid,))
    if (g(hyn)["pick_rank"], picked[:1]) != (1, [("SK하이닉스 신고가", "https://n.example.com/i2-hynix", "SK하이닉스")]):
        p.append(f"최신 호 매칭이 1순위가 아니다: {g(hyn)} · {picked[:1]}")
    if (g(lead)["pick_status"], g(lead)["pick_reason"]) != ("picked", "picked:lead"):
        p.append(f"탐색(머리 링크): {g(lead)}")
    if (g(seen)["pick_status"], g(seen)["pick_reason"]) != ("skipped", f"skipped:already_fetched:{prev}"):
        p.append(f"이미 연 주소: {g(seen)}")
    if [g(x)["pick_reason"] for x in ids1[13:]] != ["cut:cap"] * 3 or g(l0)["pick_status"] is not None:
        p.append(f"잘린 링크·이미 읽은 호: {[g(x)['pick_reason'] for x in ids1[13:]]} · {g(l0)}")
    report("(19) 링크 선택 — 새 호만 · 5/5/5 남는 칸 이월 · 최신 먼저 · 이미 연 주소 건너뜀 · 머리 링크 탐색 · 전 후보 기록(리뷰 재현 G01)", p)


def t20_material_status_block():
    """(20) ← old_single_source_material_passes(Codex G01) — 출처 하나·사흘 전 호뿐인데 '재료 결손 없음'만 나왔다. 올바른 동작: 결손 생성줄은
    그대로(게이트 계약) · 그 뒤 기계 줄이 요청 6 · 수신 1 · 빈 5 · 최신 발행 · 장기 미발행(그 출처 발행 간격의 2배 침묵)을 낸다."""
    import ingest
    p = []
    db.run_start(ST, MK, session_date=DAY)
    c = db.connect()
    saved = ingest._roster
    ingest._roster = lambda: [{"name": f"S{k}", "enabled": True, "use_for": ["study", "trade"]} for k in range(6)] + \
        [{"name": "공부만", "enabled": True, "use_for": ["study"]}, {"name": "꺼짐", "enabled": False, "use_for": ["trade"]}]
    tmp = Path(tempfile.mkdtemp(prefix="matst_"))
    try:
        iid = _mk_issue(c, [RID], "사흘 전 호", "2026/10/16", "sha-s0", sender="S0")
        for k, d in enumerate(("2026/09/01", "2026/09/03", "2026/09/05")):
            _mk_issue(c, ["260901_kr"], f"S1 {k}", d, f"sha-s1-{k}", sender="S1")
        stats = ingest.material_source_stats(RID, {"attempted": True}, [iid], DAY)
        line = ingest.material_status_line(stats, {"candidates": 0}, [], 0)
        for need in ("출처 요청 6 · 수신 1 · 빈 5 · 실패 0", "장기 미발행 [S1]", "최신 발행 2026-10-16"):
            if need not in line:
                p.append(f"상태 줄에 '{need}'가 없다: {line}")
        if db.q1("SELECT COUNT(*) AS n FROM mat_source_stat WHERE run_id=?", (RID,))["n"] != 6:
            p.append("출처 통계 행 수(요청 6)")
        md = tmp / "material_261019_kr.md"
        md.write_text("# 재료\n\n## [경제] S0 — 2026/10/16\n본문\n## [경제] S0 — 2026/10/16\n본문\n## [경제] S0 — 2026/10/16\n본문\n", encoding="utf-8")
        if ingest.material_deficit(md, {"attempted": True}, True) != "재료 결손 없음":
            p.append("결손 생성줄(게이트 계약)이 바뀌었다")
    finally:
        ingest._roster = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(20) 재료 상태 기계 줄 — 결손 생성줄 그대로 · 요청 6·수신 1·빈 5·최신 발행·장기 미발행(리뷰 재현 G01)", p)


def t21_no_mail_day_and_coverage():
    """(21) 메일 없는 날은 오류가 아니라 빈 날(예전엔 '경로 없음' 오류로 결손) · 필요 재료 확보 — 보유·대기 논지 종목을 이번 run 재료가
    다뤘나(다룸 1 · 안 다룸 0)."""
    import ingest
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="nomail_"))
    fake = tmp / "digest.py"
    fake.write_text('import sys\nprint("2026-10-19 기준 최근 3일간 도착한 뉴스레터가 없습니다.", file=sys.stderr)\n', encoding="utf-8")
    saved = ingest.DIGEST_SCRIPT
    ingest.DIGEST_SCRIPT = fake
    try:
        st = {}
        path = quiet(ingest.fetch_news, 3, st)[0]
        if path or st.get("error") or not st.get("empty"):
            p.append(f"메일 없는 날: {path!r} {st}")
    finally:
        ingest.DIGEST_SCRIPT = saved
        shutil.rmtree(tmp, ignore_errors=True)
    db.run_start(ST, MK, session_date=DAY)
    seed(theses=[{"id": "kr-005930-t", "market": "KR", "ticker": "005930", "status": "held", "entry_triggers": [], "exit_triggers": []},
                 {"id": "kr-000660-t", "market": "KR", "ticker": "000660", "status": "armed", "entry_triggers": [], "exit_triggers": []},
                 {"id": "us-x-t", "market": "US", "ticker": "AAPL", "status": "held", "entry_triggers": [], "exit_triggers": []}])
    db.connect().execute("INSERT INTO mat_mention (run_id, subject_type, subject_id) VALUES (?,?,?)", (RID, "company", "KR:000660"))
    cov = {r["ticker"]: (r["covered"], r["via"]) for r in ingest.material_coverage(RID, "kr")}
    if cov != {"005930": (0, None), "000660": (1, "mention")}:
        p.append(f"필요 재료 확보: {cov}")
    report("(21) 메일 없는 날 = 빈 날(오류 아님) · 필요 재료 확보(보유·대기 논지 · 이 시장만 · 다룸/안 다룸)", p)


for fn in (t1_render_note_values_and_rerender, t2_old_s12_placeholder_gets_markers, t3_board_render_matches_capture,
           t4_note_lint_reads_store, t5_crosscheck_from_store, t6_verify_numbers_db_corpus, t7_fetch_upserts_source_and_corpus,
           t8_close_counts_and_refs, t9_carry_blocks, t10_dart_fetch_offline, t11_crosscheck_dart_read,
           t12_crosscheck_factor_judged, t13_fetch_versions, t14_fetch_retention, t15_fetch_section_failure, t16_link_versions,
           t17_capture_stage_result, t18_calendar_weekday_resolution, t19_link_selection, t20_material_status_block,
           t21_no_mail_day_and_coverage):
    _safe(fn)

_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_notes.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
