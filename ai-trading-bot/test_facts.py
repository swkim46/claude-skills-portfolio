#!/usr/bin/env python3
"""
사실 저장소(`facts.py`) 테스트 — 임시 저장소에서 등록·재사용·큐·판정·교정·렌더·결정을 끝까지 돈다.

왜(2026-10-17 · Phase 3): 리서치 사실이 문장으로 복사되며 run마다 같은 사실을 다시 조사·검증했다(재사용 0 · 판정 495행).
새 구조에서는 이 모듈이 '한 번만 산다'(중복이면 재사용)·'검증 상태는 사실에 붙는다'(검증된 static은 큐에서 빠진다)·
'교정은 UPDATE 한 번'(판 기록 트리거)을 맡는다. 규칙이 틀리면 같은 사실을 또 조사하거나, 검증 안 된 근거로 주문이 나가거나,
게이트가 글자를 세는 렌더본(§12·세 시점 대조·판정표)의 형식이 깨진다 — 그래서 경계마다 돈다.

규칙: 실제 `journal/trading.db`를 열지 않는다(케이스마다 새 임시 저장소 `AITB_DB` + `db.reset()`) · 네트워크 없음 ·
실제 `analysis/`·`journal/`에 쓰지 않는다(렌더는 --out 임시 경로 · srcledger 비교는 원장 경로를 임시 폴더로 돌린다).

    python3 test_facts.py          # run_tests.py가 부른다
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
TMP = Path(tempfile.mkdtemp(prefix="facts_test_"))
os.environ["AITB_DB"] = str(TMP / "facts_boot.db")      # db를 부르기 전에 — 테스트가 실제 저장소를 열지 않게

import db      # noqa: E402
import facts   # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []

import atexit  # noqa: E402
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n★★ 테스트가 중간에 멈췄다 — 마지막으로 시작한 케이스: {_STATE['current']} · 그때까지 {n}건(실패 {nf})",
          file=sys.stderr)
    print(f"TEST-HALTED file=test_facts.py ran={n} failed={nf} last={_STATE['current']}")
    sys.stdout.flush()


atexit.register(_halt_banner)


def _safe(fn, *a, **k):
    name = getattr(fn, "__name__", str(fn))
    _STATE["current"] = name
    try:
        return fn(*a, **k)
    except KeyboardInterrupt:
        raise
    except BaseException as e:               # noqa: BLE001
        import traceback
        tb = traceback.format_exc().strip().splitlines()[-3:]
        results.append((FAIL, f"{name} — 중단", f"{type(e).__name__}: {e}"))
        print(f"[{FAIL}] {name} — 중단({type(e).__name__}: {str(e)[:160]}) {' | '.join(tb)[:300]}")


def report(label: str, problems: list):
    status = FAIL if problems else PASS
    results.append((status, label, "; ".join(problems)))
    print(f"[{status}] {label}" + (f" — {'; '.join(problems)}" if problems else ""))


ST, MK = "261016", "us"
RID = db.run_id(ST, MK)
NO_LEDGER = TMP / "facts_no_ledger.jsonl"     # 없는 파일 — 실제 게이트 감사 원장을 읽어 오지 않는다
URL_BAC = "https://www.sec.gov/Archives/edgar/data/70858/000007085826000481/bac09302026ex992.htm"
Q_BAC = "NII of $16.4B ($16.6B FTE) increased $1.2B, or 8%; net interest yield of 2.16% increased 15 bps"


def fresh(name: str) -> Path:
    """케이스마다 새 임시 저장소 — 앞 케이스의 행이 섞이지 않게."""
    db.reset()
    for x in TMP.glob(f"facts_{name}.db*"):
        x.unlink()
    p = TMP / f"facts_{name}.db"
    os.environ["AITB_DB"] = str(p)
    facts._COLS.clear()
    db.connect()
    return p


def cli(args: list) -> tuple:
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        try:
            rc = facts.main(args)
        except SystemExit as x:
            rc = x.code
    return rc, o.getvalue(), e.getvalue()


def quiet(fn, *a, **k):
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        r = fn(*a, **k)
    return r, o.getvalue(), e.getvalue()


def earn(**kw):
    r = {"kind": "earnings", "ticker": "BAC", "period": "2026Q3", "metric": "NII", "actual": 16.4, "unit": "B USD",
         "as_of": "2026-10-14", "url": URL_BAC, "quote": Q_BAC, "tier": 1, "source": "BAC 8-K Ex.99.2"}
    r.update(kw)
    return r


def analyst(**kw):
    r = {"kind": "analyst", "ticker": "BAC", "house": "JPM", "date": "2026-10-15", "action": "raise", "target_old": 55,
         "target_new": 60, "currency": "USD", "url": "https://example.com/research/jpm-bac-1015",
         "quote": "We raise our price target on BAC to $60 from $55 on NII momentum", "tier": 2, "source": "JPM 노트 요약"}
    r.update(kw)
    return r


def add(row, run=(ST, MK)):
    r, _, _ = quiet(facts.add_fact, row, run)
    return r


def n_rows(table: str, where: str = "", args=()) -> int:
    return db.q1(f"SELECT COUNT(*) AS n FROM {table} {('WHERE ' + where) if where else ''}", args)["n"]


def proposal(idx, ticker, action, fids, run=RID, body=None):
    st, mk = run.split("_")
    db.run_start(st, mk)
    c = db.connect()
    c.execute("INSERT OR IGNORE INTO ex_signal (run_id, sha) VALUES (?, 'sig-test')", (run,))   # 제안은 시그널 판별(v10) — 지금 판 = 이 해시
    c.execute("INSERT INTO ex_proposal (run_id, sha, idx, ticker, market, action, body) VALUES (?,'sig-test',?,?,?,?,?)",
              (run, idx, ticker, mk, action, json.dumps(body or {"ticker": ticker, "action": action})))
    for f in fids:
        c.execute("INSERT INTO ex_proposal_fact (run_id, sha, idx, fact_id, role) VALUES (?,'sig-test',?,?,'support')", (run, idx, f))


def hold(ticker, fids, run=RID):
    st, mk = run.split("_")
    db.run_start(st, mk)
    c = db.connect()
    c.execute("INSERT INTO ex_hold (run_id, ticker, why) VALUES (?,?,?)", (run, ticker, "보유 판단"))
    for f in fids:
        c.execute("INSERT INTO ex_hold_fact (run_id, ticker, fact_id) VALUES (?,?,?)", (run, ticker, f))


def cite(*fids, slot="§2", run=(ST, MK)):
    """노트가 그 사실을 가리킨다(render-note가 다는 참조와 같은 행) — 검증 큐는 문서가 쓴 사실만 본다."""
    for f in fids:
        facts.add_ref(f, run, "note", f"분석노트_{run[0]}_{run[1]}_v1_0.md", slot, role="note")


def vrow(fid, verdict, tier=1, batch="track1", **kw):
    r = {"fact_id": fid, "verdict": verdict, "tier": tier, "batch": batch, "run_id": kw.pop("run_id", RID),
         "sub": "값·출처", "t1": "T1 자체(공시 원문)", "note": "fetch.py 23:41 · 원문 표의 값과 같다",
         "support": "논지 — BAC thesis의 순이자이익 다리를 받친다"}
    # 검증자는 큐 행의 fact_rev(그때의 지금 판)를 그대로 옮긴다(2026-10-19 · W03) — fact_rev=None을 주면 칸을 비운 행(시험용)
    if "fact_rev" not in kw:
        x = db.q1("SELECT rev FROM ev_fact WHERE id=?", (fid,))
        r["fact_rev"] = x["rev"] if x else None
    r.update(kw)
    if r.get("fact_rev") is None:
        r.pop("fact_rev", None)
    return r


# ───────────────────────────── 케이스 ─────────────────────────────

def t01_reject_missing_required():
    """필수 칸 없음·T1 인용 부족·모르는 칸·없는 표는 거부 — 아무것도 쓰지 않는다(반쪽 행 금지). --minor는 인용 강제에서 빠진다."""
    p = []
    fresh("t01")
    bad = earn()
    del bad["actual"]
    for label, row, want in (("actual 없음", bad, "필수 칸 없음 actual"),
                             ("T1 인용 20자 미만", earn(quote="NII $16.4B"), "원문 인용"),
                             ("T1 URL 없음", earn(url=None, quote=Q_BAC), "url"),
                             ("모르는 칸", earn(acutal=1), "모르는 칸 acutal"),
                             ("상세 kind는 별칭으로만", {"kind": "level", "ticker": "BAC", "as_of": "2026-10-16", "value": 50},
                              "level_kind"),
                             ("모르는 종류", {"kind": "rumor"}, "모르는 종류")):
        try:
            quiet(facts.add_fact, row, (ST, MK))
            p.append(f"{label}: 거부하지 않았다")
        except facts.Reject as e:
            if want not in str(e):
                p.append(f"{label}: 거부 이유 {str(e)[:80]!r}에 {want!r}가 없다")
    if n_rows("ev_fact") or n_rows("co_earnings") or n_rows("ev_fact_run"):
        p.append("거부된 행이 저장소에 남았다")
    r = add(earn(quote=None, minor=True))
    if r["status"] != "신규" or db.q1("SELECT load_bearing FROM ev_fact WHERE id=?", (r["id"],))["load_bearing"] != 0:
        p.append("--minor 행(인용 없음)을 참고 사실로 받지 않았다")
    bf = TMP / "facts_t01_batch.jsonl"
    bad2 = earn(metric="CET1")             # 열쇠가 기존 행과 달라 '새 사실' — 필수 칸(actual)이 없으면 거부
    del bad2["actual"]
    bf.write_text(json.dumps(bad2) + "\n" + json.dumps(analyst()) + "\n", encoding="utf-8")
    rc, out, err = cli(["add", "--stamp", ST, "--market", MK, "--batch", str(bf)])
    if rc != 2 or "등록 거부" not in err or "거부 1" not in out:
        p.append(f"batch에 거부 행이 있으면 rc=2·거부 줄: rc={rc} err={err[:120]!r}")
    if n_rows("ev_fact") != 2 or n_rows("co_analyst") != 1 or n_rows("co_earnings") != 1:
        p.append(f"batch의 정상 행만 들어가야 한다: ev_fact {n_rows('ev_fact')}")
    # 시장 관측(obs)은 도구 사실 — mk_obs가 생긴 뒤로 받는다 · live라 검증 큐 밖
    try:
        ro, _, _ = quiet(facts.add_fact, {"kind": "obs", "subject": "VIX", "field": "last", "value": 15.95,
                                    "ts": "2026-10-16T09:51", "source": "CNBC restQuote"}, (ST, MK))
        vc = db.q1("SELECT valid_class FROM ev_fact WHERE id=?", (ro["id"],))["valid_class"]
        if vc != "live" or n_rows("mk_obs") != 1:
            p.append(f"obs: valid_class {vc} · mk_obs {n_rows('mk_obs')}")
    except facts.Reject as e:
        p.append(f"obs를 거부했다: {e}")
    report("(1) 거부 — 필수 칸·T1 인용·모르는 칸 · 반쪽 행 없음 · --minor · batch rc · obs는 받음(live)", p)


def t02_dedupe_reuse():
    """같은 열쇠 → 기존 사실 재사용(빈 칸만 채움 · 값이 다르면 경고만) · 다음 run에 S 번호 · 재사용 수가 ex_run에."""
    p = []
    fresh("t02")
    r1 = add(earn(), run=("261015", "us"))
    r2 = add({"kind": "earnings", "ticker": "BAC", "period": "2026Q3", "metric": "NII", "as_of": "2026-10-14",
              "consensus": 15.9, "actual": 16.5})
    if r1["status"] != "신규" or r2["status"] != "재사용" or r1["id"] != r2["id"]:
        p.append(f"재사용 못 함: {r1['status']} {r1['id']} / {r2['status']} {r2['id']}")
    d = db.q1("SELECT actual, consensus FROM co_earnings WHERE fact_id=?", (r1["id"],))
    if d["consensus"] != 15.9 or d["actual"] != 16.4:
        p.append(f"빈 칸만 채워야 한다(값은 유지): {d}")
    if not any("값 다름" in w for w in r2["warnings"]):
        p.append("다른 값을 경고하지 않았다")
    if not r2["line"].startswith(f"재사용: {r1['id']} [S1] earnings BAC 2026Q3 NII"):
        p.append(f"재사용 줄 형식: {r2['line'][:80]!r}")
    if n_rows("ev_fact") != 1 or n_rows("ev_fact_run", "fact_id=?", (r1["id"],)) != 2:
        p.append("사실은 하나 · run 연결은 둘이어야 한다")
    run = db.run_get(ST, MK)
    if (run["facts_reused"], run["facts_new"]) != (1, 0):
        p.append(f"ex_run 재사용·신규 수: {run['facts_reused']}·{run['facts_new']}")
    labels = [x["value"] for x in db.q("SELECT value FROM ev_fact_run_label ORDER BY ord")]
    if labels != ["us:261015", "us:261016"]:
        p.append(f"옛 runs 칸(렌더본): {labels}")
    report("(2) 중복 → 재사용 — 빈 칸만 채움 · 다른 값은 경고 · run 연결·S 번호 · ex_run 재사용 수 · runs 칸", p)


def t03_repeat_flag():
    """조사 사실의 URL이 이번 run 재료 링크(mat_link)와 같으면 되풀이(repeat=1) — 추적 쿼리·www·끝 '/'는 같은 URL로 본다."""
    p = []
    fresh("t03")
    c = db.connect()
    iid = c.execute("INSERT INTO mat_issue (run_id, sender, day, content_sha, first_run) VALUES (?,?,?,?,?)",
                    (RID, "Axios Markets", "2026-10-16", "sha-t03", RID)).lastrowid
    lid = c.execute("INSERT INTO mat_link (issue_id, ord, url, run_id) VALUES (?,?,?,?)",
                    (iid, 0, "https://www.example.com/news/power-gen?utm_source=newsletter", RID)).lastrowid
    r = add(analyst(url="https://example.com/news/power-gen/"))
    f = db.q1("SELECT repeat, origin, origin_id FROM ev_fact WHERE id=?", (r["id"],))
    if r["status"] != "되풀이" or f["repeat"] != 1 or f["origin_id"] != f"mat_link:{lid}":
        p.append(f"되풀이 표시: {r['status']} {f}")
    if "되풀이" not in r["line"] or not r["line"].startswith(f"기록: {r['id']} [S1] "):
        p.append(f"줄: {r['line'][:90]!r}")
    r2 = add(earn())
    run = db.run_get(ST, MK)
    if r2["status"] != "신규" or (run["facts_new"], run["facts_repeat"]) != (1, 1):
        p.append(f"ex_run 신규·되풀이: {run['facts_new']}·{run['facts_repeat']}")
    # 재료에서 꺼낸 사실(origin=link 등)은 같은 URL이어도 되풀이가 아니다 — 출처가 원래 재료다
    r3 = add(analyst(url="https://example.com/news/power-gen/", house="KB증권", origin="link", origin_id=str(lid)))
    f3 = db.q1("SELECT repeat, origin FROM ev_fact WHERE id=?", (r3["id"],))
    if r3["status"] == "되풀이" or f3["repeat"] != 0 or f3["origin"] != "link":
        p.append(f"재료 출처 사실이 되풀이로 셌다: {r3['status']} {f3}")
    # 발행분 링크에 뉴스레터 꼬리(stream=top)가 붙고 리서치는 기사 주소만 적었다 → 같은 글(되풀이)
    l2 = c.execute("INSERT INTO mat_link (issue_id, ord, url, run_id) VALUES (?,?,?,?)",
                   (iid, 1, "https://www.cnbc.com/2026/10/14/dell-ai.html?utm_source=newsletter&stream=top", RID)).lastrowid
    r4 = add(analyst(url="https://www.cnbc.com/2026/10/14/dell-ai.html", house="NH투자증권"))
    f4 = db.q1("SELECT repeat, origin_id FROM ev_fact WHERE id=?", (r4["id"],))
    if r4["status"] != "되풀이" or f4["origin_id"] != f"mat_link:{l2}":
        p.append(f"뉴스레터 꼬리 붙은 링크를 못 알아봤다: {r4['status']} {f4}")
    # 쿼리가 내용을 정하는 주소는 쿼리가 다르면 다른 글이다
    c.execute("INSERT INTO mat_link (issue_id, ord, url, run_id) VALUES (?,?,?,?)",
              (iid, 2, "https://kalshi.com/markets/kxfed?series=dec", RID))
    r5 = add(analyst(url="https://kalshi.com/markets/kxfed?series=nov", house="미래에셋증권"))
    if r5["status"] == "되풀이":
        p.append("쿼리가 다른 주소를 되풀이로 셌다")
    report("(3) 되풀이 — 리서치 사실의 URL이 이번 run 재료 링크(정규화)면 repeat=1 · 재료 출처 사실은 아님 · origin_id · ex_run 되풀이 수", p)


def t04_s_no():
    """S 번호 = 이번 run에 붙은 순서. 같은 사실을 다시 넣어도 번호는 그대로 · 다른 run에서는 그 run의 번호."""
    p = []
    fresh("t04")
    a = add(earn())
    b = add(analyst())
    c3 = add(earn(metric="EPS", actual=1.06, unit="USD", quote="Diluted earnings per share of $1.06 rose 22% from 3Q25"))
    if [a["s_no"], b["s_no"], c3["s_no"]] != [1, 2, 3] or "[S2]" not in b["line"]:
        p.append(f"S 번호: {[a['s_no'], b['s_no'], c3['s_no']]}")
    again = add(earn())
    if again["s_no"] != 1 or n_rows("ev_fact_run", "run_id=?", (RID,)) != 3:
        p.append(f"같은 run에 다시 넣은 사실의 S: {again['s_no']}")
    nxt = add(analyst(), run=("261019", "us"))
    if nxt["s_no"] != 1 or nxt["status"] != "재사용":
        p.append(f"다음 run의 S: {nxt['s_no']} {nxt['status']}")
    report("(4) S 번호 — run마다 붙은 순서 · 다시 넣어도 그대로 · 다른 run은 1부터", p)


def t05_dated_expiry():
    """dated 종류는 같은 자리(slot)에 더 새 as-of가 오면 옛 행을 expired로 · 더 옛 행이 늦게 오면 그 행을 expired로 넣는다."""
    p = []
    fresh("t05")
    old = add(analyst(date="2026-10-01", target_old=50, target_new=55,
                      quote="We raise our price target on BAC to $55 from $50 into earnings"))
    new = add(analyst())
    st = {r["id"]: r["verified_status"] for r in db.q("SELECT id, verified_status FROM ev_fact")}
    if st[old["id"]] != "expired" or st[new["id"]] != "none":
        p.append(f"더 새 목표가가 와도 옛 것을 expired로 안 했다: {st}")
    older = add(analyst(date="2026-09-20", target_old=48, target_new=50,
                        quote="We raise our price target on BAC to $50 from $48 on deposits"))
    if db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (older["id"],))["verified_status"] != "expired":
        p.append("늦게 온 옛 행을 expired로 넣지 않았다")
    other = add(analyst(house="MS", quote="Morgan Stanley lifts its BAC target to $61 on capital return"))
    if db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (new["id"],))["verified_status"] != "none" or other["status"] != "신규":
        p.append("다른 증권사(다른 자리)가 옛 행을 expired로 만들었다")
    e1 = add(earn())
    add(earn(period="2026Q2", actual=15.2, quote="NII of $15.2B ($15.4B FTE) increased in the quarter"))
    if db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (e1["id"],))["verified_status"] != "none":
        p.append("static(실적)이 다른 분기 때문에 expired가 됐다")
    report("(5) dated 시효 — 같은 자리 새 as-of → 옛 행 expired · 늦게 온 옛 행 · 다른 자리·static은 그대로", p)


def t06_queue_rules():
    """큐 — none·expired만(live·interp 제외 · 검증된 static 제외) · 주문 근거는 검증됐어도 track1 · B는 A에 없는 것만(번호 이어서)."""
    p = []
    fresh("t06")
    a = add(earn())["id"]
    b = add(earn(metric="EPS", actual=1.06, unit="USD", quote="Diluted earnings per share of $1.06 rose 22% from 3Q25"))["id"]
    c_ = add(earn(metric="NIY", actual=2.16, unit="%", quote="net interest yield of 2.16% increased 15 bps from 3Q25"))["id"]
    d = add({"kind": "level", "ticker": "BAC", "level_kind": "support_1", "value": 50.1, "as_of": "2026-10-16", "method": "pivot"})["id"]
    e = add({"kind": "statement", "speaker": "Warsh", "role": "Fed Governor", "date": "2026-10-15",
             "quote": "the AI investment surge is a near-term demand shock", "url": "https://example.com/speech/warsh",
             "valid_class": "interp", "tier": 2})["id"]
    f = add(analyst())["id"]
    con = db.connect()
    # v3 — 검증된 상태는 그 판(verified_rev)과 함께 선다(값이 뒤에 바뀌면 다시 큐 · S03)
    con.execute("UPDATE ev_fact SET verified_status='verified', verified_tier=1, verified_rev=rev WHERE id IN (?,?)", (b, c_))
    con.execute("UPDATE ev_fact SET verified_status='expired' WHERE id=?", (f,))
    proposal(0, "BAC", "BUY", [c_, d])
    proposal(1, "XOM", "SELL", [a], body={"source": "discipline"})
    cite(a, b, c_, d, e, f)                                   # 노트 §2가 가리킨 사실
    h = add(earn(metric="NIM", actual=2.0, unit="%", quote="net interest margin was 2.0% in the third quarter of 2026"))["id"]
    rows, summ = facts.queue(ST, MK, "A")
    got = [(r["fact_id"], r["n"]) for r in rows]
    if got != [(a, 1), (f, 2)]:
        p.append(f"묶음 A: {got} (기대 {[(a, 1), (f, 2)]}) — {summ}")
    if h in {r["fact_id"] for r in rows} or "문서가 안 쓴 사실 1" not in summ:
        p.append(f"노트가 안 쓴 사실이 큐 A에 들어갔거나 제외 수가 없다: {summ}")
    t1, _ = facts.queue(ST, MK, "track1")
    if [(r["fact_id"], r["n"]) for r in t1] != [(c_, 1)] or not t1[0]["order_basis"] or "BUY BAC E1" not in t1[0]["uses"]:
        p.append(f"track1(검증된 static도 주문 근거면 · live 제외 · 규율 매도 제외): {[(r['fact_id'], r['n'], r['uses']) for r in t1]}")
    g = add(earn(metric="CET1", actual=11.6, unit="%", quote="Common equity tier 1 capital ratio of 11.6% under the standardized approach"))["id"]
    cite(g, slot="§6")                                        # §3 이후 = 묶음 B
    bq, _ = facts.queue(ST, MK, "B")
    if [(r["fact_id"], r["n"]) for r in bq] != [(g, 3)]:
        p.append(f"묶음 B(A에 없는 것만 · 번호 3부터): {[(r['fact_id'], r['n']) for r in bq]}")
    again, _ = facts.queue(ST, MK, "A")
    if [(r["fact_id"], r["n"]) for r in again] != [(a, 1), (f, 2)]:
        p.append("고정된 묶음 A가 다시 뽑혔다")
    rc, out, err = cli(["queue", "--stamp", ST, "--market", MK, "--batch", "A"])
    js = [json.loads(x) for x in out.splitlines() if x.strip()]
    if rc != 0 or [x["fact_id"] for x in js] != [a, f] or "큐 A" not in err:
        p.append(f"CLI queue JSONL: rc={rc} {out[:100]!r}")
    if e in {x["fact_id"] for x in js} or d in {x["fact_id"] for x in js}:
        p.append("interp·live가 큐에 들어갔다")
    report("(6) 큐 — none·expired만 · live·interp 제외 · 검증된 static 제외(주문 근거는 track1) · B는 A 다음 번호 · 고정", p)


def t07_verdict_status_and_trigger():
    """판정 → 검증 상태(MATCH T1/T2 verified · MISMATCH disputed · STALE expired · UNVERIFIABLE·T3 그대로) · MISMATCH는 트리거가 재검토 표시."""
    p = []
    fresh("t07")
    ids = [add(earn(metric=m, quote=f"{m} line: the bank reported this value for the third quarter"))["id"]
           for m in ("M1", "M2", "M3", "M4", "M5", "M6")]
    proposal(0, "BAC", "BUY", [ids[1]])
    rows = [vrow(ids[0], "MATCH", 1), vrow(ids[1], "MISMATCH — 원문 16.5", 1), vrow(ids[2], "STALE", 2),
            vrow(ids[3], "UNVERIFIABLE", 1, note="검색: 'BAC NII' · SEC 전문 검색 · 23:50 — 원문을 못 열었다"),
            vrow(ids[4], "MATCH", 3), vrow(ids[5], "MISMATCH", 1), vrow(ids[5], "MATCH", 1), vrow(ids[0], "MAYBE", 1)]
    rc, out, err = quiet(facts.add_verdicts, rows)
    if rc != 2 or "판정 거부" not in err:
        p.append(f"4값이 아닌 판정을 거부하지 않았다: rc={rc}")
    st = {r["id"]: r for r in db.q("SELECT id, verified_status, verified_tier, verified_run, needs_review FROM ev_fact")}
    want = ["verified", "disputed", "expired", "none", "none", "disputed"]
    got = [st[i]["verified_status"] for i in ids]
    if got != want:
        p.append(f"상태: {got} (기대 {want})")
    if st[ids[0]]["verified_tier"] != 1 or st[ids[0]]["verified_run"] != RID:
        p.append(f"verified 칸: {st[ids[0]]}")
    if st[ids[1]]["needs_review"] != 1 or db.q1("SELECT needs_review FROM ex_proposal_fact")["needs_review"] != 1 \
            or db.q1("SELECT needs_review FROM ex_proposal")["needs_review"] != 1:
        p.append("MISMATCH 트리거가 사실·제안에 재검토 표시를 안 했다")
    if n_rows("ev_verdict") != 7:
        p.append(f"판정 행 수 {n_rows('ev_verdict')}(7이어야)")
    quiet(facts.add_verdicts, [vrow(ids[3], "MATCH", 1)])
    rr = db.q("SELECT round FROM ev_verdict WHERE fact_id=? ORDER BY id", (ids[3],))
    if [r["round"] for r in rr] != [1, 2] or db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (ids[3],))["verified_status"] != "verified":
        p.append(f"재판정 round·상태: {[r['round'] for r in rr]}")
    cite(*ids)
    facts.queue(ST, MK, "A")
    rc, out, _ = quiet(facts.add_verdicts, [{"claim": 1, "verdict": "MATCH", "tier": "T1", "t1": "T1 자체"}], (ST, MK), "A")
    v = db.q1("SELECT fact_id, batch, tier FROM ev_verdict ORDER BY id DESC LIMIT 1")
    if rc != 0 or v["batch"] != "A" or v["tier"] != 1:
        p.append(f"클레임 번호로 판정 잇기: rc={rc} {v}")
    report("(7) 판정 → 상태 · 행 순서와 무관(가장 나쁜 판정) · MISMATCH 트리거 재검토 · 재판정 round · 클레임 번호", p)


def t08_correct_version_rerender():
    """교정 = UPDATE 한 번 — rev+1로 판 기록 트리거(ev_fact_version) · ev_correction(옛·새) · 그 사실을 쓴 열린 run에 rerender."""
    p = []
    fresh("t08")
    fid = add(earn())["id"]
    facts.queue(ST, MK, "A")
    quiet(facts.add_verdicts, [vrow(fid, "MISMATCH", 1, batch="A", note="원문은 16.5")])
    vid = db.q1("SELECT id FROM ev_verdict")["id"]
    rc, out, _ = quiet(facts.correct, fid, {"actual": "16.5"}, "원문 표의 값으로", None, (ST, MK))
    f = db.q1("SELECT * FROM ev_fact WHERE id=?", (fid,))
    ver = db.q("SELECT * FROM ev_fact_version WHERE fact_id=?", (fid,))
    cor = db.q1("SELECT * FROM ev_correction WHERE fact_id=?", (fid,))
    snap = json.loads(ver[0]["snapshot"]) if ver else {}
    # 2026-10-19 R04 — 판 기록은 머리(ev_fact 전부)+세부(종류 표 행) · 값 교정이면 value_rev = 새 판 · 검증 티어·시각·run도 비운다
    if rc != 0 or f["rev"] != 2 or len(ver) != 1 or ver[0]["version"] != 1 or "quote" not in (snap.get("header") or {}) \
            or (snap.get("detail") or {}).get("actual") != 16.4 or ver[0]["correction_id"] != (cor or {}).get("id"):
        p.append(f"판 기록: rc={rc} rev={f['rev']} versions={len(ver)} snap={str(snap)[:120]}")
    if f["value_rev"] != 2 or f["verified_tier"] is not None or f["verified_at"] is not None:
        p.append(f"값 교정 뒤 value_rev 2 · 검증 티어·시각 비움: {f['value_rev']} {f['verified_tier']} {f['verified_at']}")
    if not cor or json.loads(cor["old"]) != {"actual": 16.4} or json.loads(cor["new"]) != {"actual": 16.5} or cor["verdict_id"] != vid:
        p.append(f"교정 행: {cor}")
    if db.q1("SELECT actual FROM co_earnings WHERE fact_id=?", (fid,))["actual"] != 16.5 or "16.5" not in facts.render_line(fid):
        p.append("상세 값·한 줄 렌더가 안 바뀌었다")
    if f["verified_status"] != "none" or f["needs_review"] != 0:
        p.append(f"교정 뒤 상태(none · 재검토 해제): {f['verified_status']} {f['needs_review']}")
    if db.run_get(ST, MK)["rerender"] != 1:
        p.append("ex_run.rerender가 1이 아니다")
    try:
        quiet(facts.correct, fid, {"nonsense": "1"}, "x")
        p.append("없는 칸 교정을 거부하지 않았다")
    except facts.Reject:
        pass
    rc, out, _ = quiet(facts.correct, fid, {"actual": "16.5"}, "같은 값")
    if "바뀐 것 없음" not in out or n_rows("ev_correction") != 1:
        p.append("바뀐 것 없는 교정이 기록됐다")
    rc, out, err = cli(["correct", fid, "--set", "source=BAC 8-K Ex.99.1", "--reason", "출처 이름", "--stamp", ST, "--market", MK])
    if rc != 0 or db.q1("SELECT source_label, verified_status FROM ev_fact WHERE id=?", (fid,)) != \
            {"source_label": "BAC 8-K Ex.99.1", "verified_status": "none"} or len(db.q("SELECT 1 FROM ev_fact_version")) != 2:
        p.append(f"CLI 교정(표기만): rc={rc} {err[:100]!r}")
    f3 = db.q1("SELECT rev, value_rev FROM ev_fact WHERE id=?", (fid,))
    if (f3["rev"], f3["value_rev"]) != (3, 2):
        p.append(f"표기만 교정은 rev만 올린다(value_rev 그대로): {f3}")
    report("(8) 교정 — rev+1 · 판 기록 트리거 · ev_correction 옛·새 · 판정 자동 연결 · rerender · 상태 none", p)


def _track1_fixture(run_st=ST):
    """BUY BAC(근거 둘) · HOLD MET(근거 하나) — 주문 근거 묶음."""
    run = (run_st, MK)
    rid = db.run_id(run_st, MK)
    e1 = add(earn(), run=run)["id"]
    e2 = add(analyst(), run=run)["id"]
    e3 = add({"kind": "release", "indicator_code": "US_CPI_CORE", "period": "2026-09", "actual": 0.4, "unit": "%",
              "consensus": 0.3, "as_of": "2026-10-14", "url": "https://www.bls.gov/news.release/cpi.nr0.htm",
              "quote": "The index for all items less food and energy rose 0.4 percent in September", "tier": 1,
              "source": "BLS CPI"}, run=run)["id"]
    proposal(0, "BAC", "BUY", [e1, e2], run=rid)
    hold("MET", [e3], run=rid)
    return e1, e2, e3


def t09_render_verdict_format():
    """판정표 렌더 — 옛 형식 그대로(머리 집계 · `LINT OK` 줄 · 8칸 판정 표 · 교정 지시서 · TRACK1 줄 · 끝 줄 `REFS OK`) ·
    옛 lint_verdict.py로 돌려도 통과 · 받치는 것 없는 주문 근거·교정 없는 MISMATCH·검색 없는 부재 단정은 LINT FAIL."""
    p = []
    fresh("t09")
    e1, e2, e3 = _track1_fixture()
    out_dir = TMP / "t09"
    claims = out_dir / f"AI클레임_{ST}_{MK}_주문근거.md"
    verdict = out_dir / f"AI클레임_{ST}_{MK}_주문근거_판정.md"
    rc, msg = facts.render_claims(ST, MK, "track1", str(claims))
    ctext = claims.read_text(encoding="utf-8")
    if rc != 0 or f"1. [BUY BAC E1 · {e1}] BAC 2026Q3 NII 16.4 B USD" not in ctext or f"3. [HOLD MET · {e3}]" not in ctext:
        p.append(f"클레임 목록: {msg} | {ctext[:300]!r}")
    if facts.render_claims(ST, MK, "track1", str(claims))[0] != 3:
        p.append("고정된 클레임 목록을 덮어썼다")
    quiet(facts.add_verdicts, [vrow(e1, "MATCH", 1),
                               vrow(e2, "MISMATCH", 2, note="원문은 목표가 $62", support="크기 — BAC 목표가가 기대 상승폭을 정한다"),
                               vrow(e3, "MATCH", 1, support="보유 — MET 보유의 금리 경로 근거")])
    v2 = db.q1("SELECT id FROM ev_verdict WHERE fact_id=?", (e2,))["id"]
    quiet(facts.correct, e2, {"target_new": "62"}, "원문 목표가", v2, (ST, MK))
    rc_d, out_d, err_d = quiet(facts.decide, ST, MK, "pre", ledger=NO_LEDGER)
    if rc_d != 0:
        p.append(f"decide pre rc={rc_d} {err_d[:160]!r}")
    rc, path = quiet(facts.render_verdict, ST, MK, "track1", str(verdict), str(claims))[0]
    text = verdict.read_text(encoding="utf-8")
    lines = [x for x in text.split("\n") if x.strip()]
    if rc != 0 or not re.search(r"^LINT OK \(facts\.py lint ", text, re.M):
        p.append(f"LINT OK 줄 없음(rc={rc}): {text[:600]!r}")
    if not lines[-1].startswith("REFS OK"):
        p.append(f"끝 줄이 REFS OK가 아니다: {lines[-1][:80]!r}")
    if "| 원 문구(위치) | 하위 클레임 | 판정 | 티어 | 원문 인용 | URL | T1 시도 | 비고 |" not in text:
        p.append("판정 표 머리줄이 옛 형식과 다르다")
    if not re.search(r"^\*\*MISMATCH 1건 · STALE 0건 · 🔴\(비가역\) 0건\*\* · UNVERIFIABLE 0건 · MATCH 2건 \(하위 클레임 3건\)$", text, re.M):
        p.append("머리 집계 줄")
    if "TRACK1 RESULT:" not in text or "| 1 | 🟡 |" not in text or not re.search(r"\| MISMATCH \|.*교정 1 ·", text):
        p.append("TRACK1 줄·교정 지시서·교정 연결")
    lv_path = HERE.parent / ".claude" / "skills" / "fact-check" / "scripts"
    if (lv_path / "lint_verdict.py").exists():
        sys.path.insert(0, str(lv_path))
        import lint_verdict as lv
        errs, warns, cnt, total, _ = lv.lint(verdict, claims, [])
        if errs:
            p.append(f"옛 lint_verdict 위반 {len(errs)}건: {errs[:3]}")
        if total != 3:
            p.append(f"옛 lint_verdict가 센 행 {total}(3이어야)")
    # 실패 사례 — 새 저장소에서
    fresh("t09b")
    e1, e2, e3 = _track1_fixture()
    facts.queue(ST, MK, "track1")
    quiet(facts.add_verdicts, [vrow(e1, "MATCH", 1, support=""),
                               vrow(e2, "MISMATCH", 2),
                               vrow(e3, "UNVERIFIABLE", 1, note="발표 자료 없음")])
    errs, warns, cnt, total = facts.lint(ST, MK, "track1")
    blob = " ".join(errs)
    for want in ("받치는 것", "교정이 없다", "부재 단정", "검색 시각"):
        if want not in blob:
            p.append(f"LINT가 '{want}'를 못 잡았다")
    vf = TMP / "t09b" / "v_판정.md"
    rc, _ = quiet(facts.render_verdict, ST, MK, "track1", str(vf))[0]
    if rc == 0 or re.search(r"^LINT OK\b", vf.read_text(encoding="utf-8"), re.M):
        p.append("위반이 있는데 LINT OK 줄을 썼다")
    rc, out, _ = cli(["lint", "--stamp", ST, "--market", MK, "--batch", "track1"])
    if rc != 1 or not any(x.startswith("LINT FAIL") for x in out.split("\n")):
        p.append(f"CLI lint rc={rc}")
    report("(9) 판정표 렌더 — LINT OK·REFS OK 줄 · 옛 형식(머리 집계·8칸·교정·TRACK1) · 옛 lint_verdict 통과 · 위반은 LINT FAIL", p)


def t10_decide_port():
    """fc_track1.decide 이식 — pre: T1/T2 MATCH 근거가 없거나 MISMATCH가 있는 매수는 제외 · 보유 미검증 표시.
    post: 감사 뒤 재채점으로 체결된 매수의 근거가 무너지면 되돌림 매도(db.reversal_append) · 두 번 돌려도 하나."""
    p = []
    fresh("t10")
    st2 = "261013"
    rid = db.run_id(st2, MK)
    run = (st2, MK)
    a1 = add(earn(ticker="AAA", quote="AAA reported net interest income of $16.4B for the quarter"), run=run)["id"]
    a2 = add(analyst(ticker="AAA", quote="We raise our price target on AAA to $60 from $55 on NII"), run=run)["id"]
    b1 = add(earn(ticker="BBB", tier=3, url="https://example.com/blog/bbb-nii",
                  quote="BBB net interest income of $16.4B per a blog summary"), run=run)["id"]
    c1 = add(earn(ticker="CCC", quote="CCC reported net interest income of $16.4B for the quarter"), run=run)["id"]
    h1 = add(earn(ticker="MET", quote="MET reported net investment income of $16.4B in the quarter"), run=run)["id"]
    s1 = add(earn(ticker="XOM", quote="XOM upstream liquids price change of negative 1.3B"), run=run)["id"]
    proposal(0, "AAA", "BUY", [a1, a2], run=rid)
    proposal(1, "BBB", "BUY", [b1], run=rid)
    proposal(2, "CCC", "BUY", [c1], run=rid)
    proposal(3, "XOM", "SELL", [s1], run=rid, body={"source": "discipline"})
    hold("MET", [h1], run=rid)
    rows, _ = facts.queue(st2, MK, "track1")
    if [r["fact_id"] for r in rows] != [a1, a2, b1, c1, h1]:
        p.append(f"주문 근거 큐(규율 매도 제외): {[r['fact_id'] for r in rows]}")
    vr = lambda f, v, t, **k: vrow(f, v, t, run_id=rid, **k)  # noqa: E731
    quiet(facts.add_verdicts, [vr(a1, "MATCH", 1), vr(a2, "MATCH", 2), vr(b1, "MATCH", 3), vr(c1, "MISMATCH", 1),
                               vr(h1, "UNVERIFIABLE", 1, note="검색: 'MET NII' · IR 페이지 · 23:58 — 원문을 열지 못함",
                                  support="보유 — MET 보유 판단의 투자수익 근거")])
    quiet(facts.correct, c1, {"actual": "16.1"}, "원문 값")
    rc, out, err = quiet(facts.decide, st2, MK, "pre", ledger=NO_LEDGER)
    if rc != 0:
        p.append(f"pre rc={rc}: {err[:300]!r}")
        report("(10) decide 이식", p)
        return
    t1 = json.loads(db.q1("SELECT track1 FROM ex_signal WHERE run_id=?", (rid,))["track1"])
    if rc != 0 or t1.get("result") != "PARTIAL" or t1.get("pass") != ["AAA"] or t1.get("drop") != ["BBB", "CCC"] \
            or t1.get("holds_unverified") != ["MET"] or "TRACK1 RESULT:" not in out:
        p.append(f"pre: rc={rc} {t1} {err[:120]!r}")
    rc, out, err = quiet(facts.decide, st2, MK, "post", ledger=NO_LEDGER)
    if rc != 1 or "감사 기록이 없다" not in err:
        p.append(f"감사 없이 post를 돌렸다: rc={rc}")
    c = db.connect()
    oid = c.execute("INSERT INTO ex_order (run_id, ticker, market, side, qty, status, approved_ref, sent_at, trade_key, body)"
                    " VALUES (?,?,?,?,?,?,?,'t','k','{}')",
                    (rid, "AAA", MK, "BUY", 3, "SENT", f"signals/approved_{st2}_{MK}.json")).lastrowid
    c.execute("INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (?,?,?,?,?,?,'{}')",
              (oid, rid, "AAA", MK, 3, "FILLED"))
    c.execute("INSERT INTO op_audit (run_id, skill, status, at, line_sha, body) VALUES (?,?,?,?,?,?)",
              (rid, "fact-check", "FAIL", "2026-10-13T23:00:00", "sha-a1",
               json.dumps({"type": "finding", "run": f"AI클레임_{st2}_{MK}_주문근거_판정.md", "status": "FAIL"})))
    quiet(facts.add_verdicts, [vr(a1, "MISMATCH", 1, note="감사 재채점 — 원문은 16.1")])
    quiet(facts.correct, a1, {"actual": "16.1"}, "감사 재채점")
    facts._market_open = lambda mk: (False, "테스트 — 닫힘")
    rc, out, err = quiet(facts.decide, st2, MK, "post", ledger=NO_LEDGER)
    revs = [r for r in db.reversals_all() if r.get("ticker") == "AAA"]
    if rc != 0 or len(revs) != 1 or revs[0]["qty"] != 3 or revs[0]["status"] != "pending" or revs[0]["id"] != f"REV-{st2}-{MK}-1":
        p.append(f"post 되돌림: rc={rc} {revs} {err[:160]!r}")
    if "TRACK1 POST: audit=FAIL×1 rounds=1/" not in out or "reversals=REV-261013-us-1" not in out:
        p.append(f"TRACK1 POST 줄: {out[:200]!r}")
    if any(r.get("ticker") in ("BBB", "CCC") for r in db.reversals_all()):
        p.append("pre에서 이미 제외된(체결 안 된) 매수를 되돌렸다")
    rc, out, _ = quiet(facts.decide, st2, MK, "post", ledger=NO_LEDGER)
    if len(db.reversals_all()) != 1 or "이미 기록됨" not in out:
        p.append("post를 두 번 돌리자 되돌림이 늘었다")
    t1 = json.loads(db.q1("SELECT track1 FROM ex_signal WHERE run_id=?", (rid,))["track1"])
    if (t1.get("post") or {}).get("reversals") != [f"REV-{st2}-{MK}-1"] or t1.get("pass") != ["AAA"]:
        p.append(f"track1 post 블록(pre 기록은 남긴다): {t1}")
    report("(10) decide 이식 — pre 제외 규칙(T3만·MISMATCH) · 보유 미검증 · 감사 없으면 post 거부 · 재채점 소급 매도 · 멱등", p)


def t11_render_line_each_kind():
    """종류마다 한 줄 렌더 — 판단에 쓰는 칸이 줄에 나온다 · 옛 이전 행은 옛 문장 · db.sources_rows가 같은 줄을 쓴다."""
    p = []
    fresh("t11")
    U = "https://example.com/doc/1"
    Q = "this is the verbatim source sentence for the test row"
    cases = [
        ({"kind": "release", "indicator_code": "US_CPI_CORE", "period": "2026-09", "actual": 0.4, "unit": "%", "consensus": 0.3,
          "as_of": "2026-10-14"}, ["US_CPI_CORE 2026-09 0.4%", "예상 0.3"]),
        ({"kind": "policy", "body": "Commerce", "title": "diesel export license", "status": "검토", "policy_kind": "규제",
          "as_of": "2026-10-15", "deadline": "2026-11-01", "targets": [{"type": "sector", "id": "XLE"}]},
         ["Commerce", "diesel export license — 검토", "기한 2026-11-01"]),
        ({"kind": "metric", "subject_type": "sector", "subject_id": "refining", "metric_code": "crack_321", "value": 57,
          "unit": "USD/bbl", "prior": 64, "as_of": "2026-10-15"}, ["refining crack_321 57 USD/bbl", "이전 64"]),
        (earn(url=U, quote=Q, tier=2), ["BAC 2026Q3 NII 16.4 B USD"]),
        ({"kind": "guidance", "ticker": "NET", "period": "2026Q3", "metric": "revenue", "low": 736.0, "high": 737.0,
          "unit": "M USD", "as_of": "2026-08-01"}, ["NET 2026Q3 revenue 가이던스 736–737 M USD"]),
        ({"kind": "consensus", "ticker": "NET", "period": "2026Q3", "metric": "revenue", "mean": 733.2, "n_analysts": 28,
          "provider": "LSEG", "unit": "M USD", "as_of": "2026-10-10"}, ["컨센서스 733.2 M USD", "28명", "LSEG"]),
        (analyst(url=U, quote=Q), ["BAC JPM 2026-10-15 raise", "목표가 55→60 USD"]),
        ({"kind": "order", "ticker": "267260", "amount": 1.2, "currency": "조원", "counterparty": "Aramco", "product": "변압기",
          "announced_at": "2026-10-10", "market": "KR"}, ["267260 수주 1.2 조원 — Aramco 변압기", "공시 2026-10-10"]),
        ({"kind": "disclosure", "ticker": "005930", "type": "잠정실적", "title": "연결재무제표기준영업(잠정)실적", "filed_at": "2026-10-08",
          "doc_no": "20261008000123", "values": [{"name": "영업이익", "value": 12.1, "unit": "조원"}], "market": "KR"},
         ["005930 공시 잠정실적", "20261008000123", "영업이익 12.1 조원"]),
        ({"kind": "action", "ticker": "AAPL", "action_kind": "buyback", "amount": 100, "currency": "B USD", "status": "승인",
          "as_of": "2026-05-01", "decided_at": "2026-05-01"}, ["AAPL buyback 100 B USD", "승인"]),
        ({"kind": "statement", "speaker": "Warsh", "role": "Fed Governor", "date": "2026-10-15",
          "quote": "the AI investment surge is a near-term demand shock"}, ['Warsh (Fed Governor) 2026-10-15: "the AI investment']),
        ({"kind": "level", "ticker": "BAC", "level_kind": "support_1", "value": 50.1, "lo": 49.8, "hi": 50.4, "method": "pivot",
          "as_of": "2026-10-16"}, ["BAC support_1 50.1 (구간 49.8–50.4) · pivot"]),
        ({"kind": "event", "ticker": "CVX", "event_kind": "earnings_call", "date": "2026-10-30", "time": "11:00", "tz": "ET",
          "confirmed": 1, "as_of": "2026-10-01"}, ["CVX earnings_call 2026-10-30 11:00 ET", "(확정)"]),
    ]
    kinds_seen = set()
    for row, wants in cases:
        row = dict(row)
        if row["kind"] not in ("level",):
            row.setdefault("url", U)
            row.setdefault("quote", Q)
            row.setdefault("tier", 2)
        try:
            r = add(row)
        except facts.Reject as e:
            p.append(f"{row['kind']} 거부: {e}")
            continue
        kinds_seen.add(row["kind"])
        line = facts.render_line(r["id"])
        for w in wants:
            if w not in line:
                p.append(f"{row['kind']}: {w!r} 없음 → {line!r}")
    missing = set(facts.KINDS) - kinds_seen - {"obs"}   # obs 렌더는 (1)에서 본다
    if missing:
        p.append(f"렌더를 안 본 종류: {sorted(missing)}")
    db.connect().execute("INSERT INTO ev_fact (id, kind, legacy_text, as_of, ko) VALUES ('SRC-0001','legacy',?,?,?)",
                         ("BAC NII $16.4B (+8% y/y) per 8-K", "2026-10-14", json.dumps(["id", "fact", "as_of"])))
    if facts.render_line("SRC-0001") != "BAC NII $16.4B (+8% y/y) per 8-K":
        p.append("옛 이전 행은 옛 문장 그대로여야 한다")
    rows = db.sources_rows()
    typed = [r for r in rows if str(r.get("id", "")).startswith("F-")]
    if len(typed) != len(kinds_seen) or not any(r["fact"].startswith("BAC 2026Q3 NII") for r in typed):
        p.append(f"db.sources_rows 렌더 연동: {len(typed)}행 {[r['fact'][:30] for r in typed][:3]}")
    report("(11) 한 줄 렌더 — 종류 13개 · 옛 행은 옛 문장 · sources.json 렌더본(db.sources_rows)이 같은 줄", p)


def t12_srcledger_format_compare():
    """같은 입력 → srcledger와 같은 출력 모양: `기록: <id> [S1] …` 줄 · §12 머리줄·행 칸 · 세 시점 대조(요약 줄·표) —
    게이트 봉인 기준이 이 글자를 센다. srcledger 원장 경로는 임시 폴더로 돌린다(실제 sources.json을 건드리지 않는다)."""
    p = []
    fresh("t12")
    try:
        import srcledger as sl
    except ImportError:                       # 보관 뒤 — 형식 비교 기준은 _archive/의 옛 모듈
        sys.path.insert(0, str(Path(__file__).absolute().parent / "_archive"))
        import srcledger as sl
    d = TMP / "t12"
    saved = (sl.SHARED, sl.EVID, sl.ANALYSIS)
    try:
        sl.SHARED, sl.EVID, sl.ANALYSIS = d / "sources.json", d / "run_evidence", d / "analysis"
        a = types.SimpleNamespace(stamp=ST, market=MK, fact="BAC 3Q26 NII $16.4B, +8% y/y", as_of="2026-10-14",
                                  source="BAC 8-K Ex.99.2", url=URL_BAC, quote=Q_BAC, tier=1, minor=False, batch="")
        _, o_sl, _ = quiet(sl.cmd_add, a)
        r = add(earn())
        rx = r"^기록: (SRC-\d{4}|F-\d{6}) \[S1\] .+ · as-of 2026-10-14 · retrieved \d{4}-\d{2}-\d{2} \d{2}:\d{2} \(이 run 1행\)"
        if not re.search(rx, o_sl, re.M) or not re.search(rx, r["line"], re.M):
            p.append(f"기록 줄 모양: srcledger {o_sl.splitlines()[0][:80]!r} · facts {r['line'][:80]!r}")
        _, s_sl, _ = quiet(sl.cmd_render, types.SimpleNamespace(stamp=ST, market=MK, out=str(d / "s12_sl.md")))
        s_f = facts.render_s12(ST, MK, str(d / "s12_f.md"))
        A = (d / "s12_sl.md").read_text(encoding="utf-8").split("\n")
        B = s_f.split("\n")
        hi = A.index("|---|---|---|---|---|---|---|") + 1
        if A[:hi] != B[:hi]:
            p.append("§12 머리(제목·안내·표 머리줄)가 다르다")
        row_rx = (r"^\| S1 \| .+ \| \".+\" \| 2026-10-14 \| \d{4}-\d{2}-\d{2} \d{2}:\d{2} \| \d{4}-\d{2}-\d{2} \d{2}:\d{2} \| "
                  r"\[BAC 8-K Ex\.99\.2\]\(https://www\.sec\.gov/\S+\) \(T1\) · (SRC-\d{4}|F-\d{6}) \|$")
        ncell = lambda s: len(re.split(r"(?<!\\)\|", s))  # noqa: E731
        if not re.match(row_rx, A[hi]) or not re.match(row_rx, B[hi]) or ncell(A[hi]) != ncell(B[hi]):
            p.append(f"§12 행 모양: {A[hi][:90]!r} / {B[hi][:90]!r}")
        if [x for x in A if x.startswith("<!--")][:1] != [x for x in B if x.startswith("<!--")][:1] or \
                not any(re.match(r"^<!-- written §12 \d{4}-\d{2}-\d{2} \d{2}:\d{2} -->$", x) for x in B):
            p.append("§12 끝 스탬프 두 줄")
        (d / "analysis").mkdir(parents=True, exist_ok=True)
        note = d / "analysis" / f"분석노트_{ST}_{MK}_v1_0.md"
        mask = lambda s: re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "T", s).replace("srcledger.py", "facts.py")  # noqa: E731
        for label, body in (("통과", f"## §2\n\n- BAC NII rose [AI] [S1] ({URL_BAC})\n<!-- ✓ §2 -->\n<!-- written §2 2026-10-16 23:00 -->\n"),
                            ("실패", f"## §2\n\n- BAC NII rose [AI] [S1] ({URL_BAC}) https://example.com/other\n<!-- ✓ §2 -->\n<!-- ✓ §3 -->\n")):
            note.write_text(body, encoding="utf-8")
            rc1, o1, _ = quiet(sl.cmd_check, types.SimpleNamespace(stamp=ST, market=MK, note=str(note), out=str(d / "tp_sl.md")))
            rc2, o2 = facts.check(ST, MK, str(note), str(d / "tp_f.md"))
            t1_ = mask((d / "tp_sl.md").read_text(encoding="utf-8")).replace(
                "스탬프에는 `<!-- ✓ §N · written YYYY-MM-DD HH:MM -->` 형식으로 시각을 붙여라.",
                "완료 스탬프는 `stage.py stamp`로 두 줄(`<!-- ✓ §N -->` + `<!-- written §N YYYY-MM-DD HH:MM -->`)을 찍는다 — "
                "✓ 줄 안에 시각을 끼우지 마라(게이트가 그 줄을 글자 그대로 찾는다).")   # 옛 안내는 문서가 금지한 형식이었다(예행 D7)
            t2_ = mask((d / "tp_f.md").read_text(encoding="utf-8"))
            if rc1 != rc2 or t1_ != t2_:
                p.append(f"세 시점 대조({label}) 다름: rc {rc1}/{rc2}\n--- srcledger\n{t1_}\n--- facts\n{t2_}")
        if "[세시점] 검사 3건 · 실패 0건" in t2_ or "[세시점] 검사 3건 · 실패 2건" not in t2_:
            p.append("실패 사례 요약 줄")
    finally:
        sl.SHARED, sl.EVID, sl.ANALYSIS = saved
    report("(12) srcledger 형식 비교 — 기록 줄 · §12 머리줄·행 칸·스탬프 · 세 시점 대조(통과·실패) 글자 같음", p)


def t13_refs_news_known_mention_reuse():
    """참조 검사(REFS) · 뉴스 전 사실·재료 지연 → ex_run · 재료 언급 · known(검증 상태·언급·옛 문장) · reuse(다른 run 사실)."""
    p = []
    fresh("t13")
    f1 = add(earn())["id"]
    facts.add_ref(f1, (ST, MK), "note", "분석노트", "§2-1", "support")
    ok, line, probs = facts.check_refs(ST, MK)
    if not ok or not line.startswith("REFS OK"):
        p.append(f"정상 참조가 REFS FAIL: {probs}")
    c = db.connect()
    c.execute("INSERT INTO ev_fact (id, kind, legacy_text, as_of, ko, load_bearing) VALUES ('SRC-0001','legacy',?,?,?,1)",
              ("BAC net interest income rose 8% (8-K)", "2026-10-14", json.dumps(["id"])))
    facts.add_ref("SRC-0001", (ST, MK), "note", "분석노트", "§2-2")
    m1 = add(analyst(quote=None, minor=True, tier=3))["id"]
    facts.add_ref(m1, (ST, MK), "note", "분석노트", "§2-3")
    ok, line, probs = facts.check_refs(ST, MK)
    if ok or not any("SRC-0001" in x and "url" in x for x in probs) or any(m1 in x for x in probs):
        p.append(f"옛 행(url 없음)은 FAIL · 참고 사실(quote 없음)은 통과여야: {probs}")
    proposal(0, "BAC", "BUY", [m1])
    ok, line, probs = facts.check_refs(ST, MK)
    if not any(m1 in x and "quote" in x for x in probs):
        p.append("주문 근거가 된 참고 사실은 quote를 물어야 한다")
    rc, out, _ = cli(["check-refs", "--stamp", ST, "--market", MK])
    if rc != 1 or not out.strip().split("\n")[-1].startswith("REFS FAIL"):
        p.append(f"CLI check-refs rc={rc}")
    # 뉴스 전 · 재료 지연
    db.run_set(ST, MK, capture_at="2026-10-16T22:00:00+09:00")
    iid = c.execute("INSERT INTO mat_issue (run_id, sender, day, received_at, content_sha, first_run) VALUES (?,?,?,?,?,?)",
                    (RID, "Axios Macro", "2026-10-16", "2026-10-16T06:00:00+09:00", "sha-t13", RID)).lastrowid
    c.execute("INSERT INTO mat_link (issue_id, ord, url, run_id) VALUES (?,?,?,?)", (iid, 0, "https://example.com/axios/net", RID))
    f_net = add({"kind": "guidance", "ticker": "NET", "period": "2026Q3", "metric": "revenue", "low": 736.0, "high": 737.0,
                 "as_of": "2026-08-01", "url": "https://example.com/net/q2", "quote": "Revenue of $736.0 to $737.0 million for Q3",
                 "tier": 1})["id"]
    mf = TMP / "facts_t13_mention.jsonl"
    mf.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in (
        {"subject_type": "ticker", "subject_id": "BAC", "stance": "긍정", "headline": "BAC NII beat lifts banks",
         "created_at": "2099-01-01T00:00:00+09:00", "fact_ids": [f1]},
        {"subject_type": "ticker", "subject_id": "NET", "stance": "중립", "headline": "Cloudflare guide", "url": "https://example.com/axios/net",
         "created_at": "2026-10-01T00:00:00+09:00"},
        {"subject_id": "X"})) + "\n", encoding="utf-8")
    rc, out, err = cli(["mention", "--stamp", ST, "--market", MK, "--batch", str(mf)])
    lk = db.q1("SELECT link_id FROM mat_mention WHERE subject_id='NET'")["link_id"]
    if rc != 2 or n_rows("mat_mention") != 2 or n_rows("mat_mention_fact") != 1 or not lk:
        p.append(f"mention: rc={rc} 행 {n_rows('mat_mention')} 링크 {lk}")
    r = facts.before_news(ST, MK)
    run = db.run_get(ST, MK)
    if r["before_news_n"] != 2 or f1 not in r["facts"] or f_net in r["facts"] or run["before_news_n"] != 2:
        p.append(f"뉴스 전 사실(BAC·analyst는 먼저 · NET은 재료가 먼저): {r}")
    if run["material_delay_h"] != 16.0:
        p.append(f"재료 지연 {run['material_delay_h']}(16.0이어야)")
    # known
    quiet(facts.add_verdicts, [vrow(f1, "MATCH", 1, batch="A")])
    txt = facts.known(ticker="BAC", stamp=ST, market=MK)
    for want in (f1, "검증됨(T1 · 261016_us)", "BAC NII beat lifts banks", "SRC-0001", "마지막 조사:"):
        if want not in txt:
            p.append(f"known에 {want!r} 없음")
    rc, out, _ = cli(["known", "--axis", "nothing"])
    if rc != 0 or "0건" not in out:
        p.append("빈 축 known")
    # reuse — 다른 run에서 본다
    txt = facts.reuse("261017", MK, days=3650)
    if f1 not in txt or "재사용 후보" not in txt:
        p.append(f"reuse에 앞 run 사실이 없다: {txt[:200]!r}")
    if f1 in facts.reuse(ST, MK, days=3650):
        p.append("reuse가 이번 run 사실을 후보로 냈다")
    report("(13) REFS(옛 행·참고 사실·주문 근거) · 뉴스 전·재료 지연 → ex_run · 재료 언급 · known · reuse", p)


def t14_roles_reverse_unreflected():
    """(2026-10-17 · 2파 ⓑ 요청) decide의 매수 통과 = 받치는 사실(support · 역할이 비면 support)의 주문 근거 MATCH(T1/T2) —
    배경·반대 사실 하나만 MATCH로는 서지 않고, 반대 사실의 MISMATCH는 매수를 빼지 않는다 · `facts.py reverse`(보유 재판정 매도 · 멱등) ·
    unreflected/carry_note(run이 닫힌 뒤 늦게 온 주문 근거 감사 — 엔진 원장을 먼저 옮긴 뒤 post 반영 회차와 비교)."""
    p = []
    fresh("t14")
    st3 = "261014"
    rid = db.run_id(st3, MK)
    run = (st3, MK)
    q = "reported net interest income of $16.4B for the quarter, up 8%"
    s_bad = add(earn(ticker="AAA", quote="AAA " + q), run=run)["id"]
    bg_ok = add(earn(ticker="AAA", metric="EPS", actual=0.9, quote="AAA diluted EPS of $0.90 for the quarter"), run=run)["id"]
    d_sup = add(earn(ticker="DDD", quote="DDD " + q), run=run)["id"]
    d_ag = add(earn(ticker="DDD", metric="EPS", actual=0.5, quote="DDD diluted EPS of $0.50 for the quarter"), run=run)["id"]
    e_sup = add(earn(ticker="EEE", quote="EEE " + q), run=run)["id"]
    h1 = add(earn(ticker="MET", quote="MET reported net investment income of $16.4B in the quarter"), run=run)["id"]
    db.run_start(st3, MK)
    c = db.connect()
    for idx, tk, fr in ((0, "AAA", [(s_bad, "support"), (bg_ok, "background")]), (1, "DDD", [(d_sup, "support"), (d_ag, "against")]),
                        (2, "EEE", [(e_sup, None)])):
        c.execute("INSERT OR IGNORE INTO ex_signal (run_id, sha) VALUES (?, 'sig-test')", (rid,))
        c.execute("INSERT INTO ex_proposal (run_id, sha, idx, ticker, market, action, body) VALUES (?,'sig-test',?,?,?,?,?)",
                  (rid, idx, tk, MK, "BUY", json.dumps({"ticker": tk, "action": "BUY"})))
        for fid, role in fr:
            c.execute("INSERT INTO ex_proposal_fact (run_id, sha, idx, fact_id, role) VALUES (?,'sig-test',?,?,?)", (rid, idx, fid, role))
    hold("MET", [h1], run=rid)
    quiet(facts.queue, st3, MK, "track1")
    vr = lambda f, v, t, **k: vrow(f, v, t, run_id=rid, **k)  # noqa: E731
    quiet(facts.add_verdicts, [vr(s_bad, "UNVERIFIABLE", 1, note="검색: 'AAA NII' · IR · 23:50 — 원문을 열지 못함"),
                               vr(bg_ok, "MATCH", 1), vr(d_sup, "MATCH", 1), vr(d_ag, "MISMATCH", 1, note="원문은 0.55"),
                               vr(e_sup, "MATCH", 1),
                               vr(h1, "UNVERIFIABLE", 1, note="검색: 'MET NII' · IR 페이지 · 23:58 — 원문을 열지 못함",
                                  support="보유 — MET 보유 판단의 투자수익 근거")])
    quiet(facts.correct, d_ag, {"actual": "0.55"}, "원문 값")
    rc, out, err = quiet(facts.decide, st3, MK, "pre", ledger=NO_LEDGER)
    t1 = json.loads((db.q1("SELECT track1 FROM ex_signal WHERE run_id=?", (rid,)) or {}).get("track1") or "{}")
    if rc != 0 or t1.get("pass") != ["DDD", "EEE"] or t1.get("drop") != ["AAA"]:
        p.append(f"역할 규칙(배경 MATCH만으로 안 섬 · 반대 MISMATCH는 안 뺌 · 빈 역할=support): rc={rc} {t1.get('pass')} {t1.get('drop')} "
                 f"{err[:160]!r}")
    # R4 — 보유 재판정 매도
    rc1, o1, _ = cli(["reverse", "--stamp", st3, "--market", MK, "--ticker", "MET", "--qty", "2", "--why", "보유 근거 판정 실패 — 투자수익 미확인"])
    rc2, o2, _ = cli(["reverse", "--stamp", st3, "--market", MK, "--ticker", "MET", "--qty", "2", "--why", "다시"])
    rc3, _, e3 = cli(["reverse", "--stamp", st3, "--market", MK, "--ticker", "MET", "--qty", "0", "--why", "x"])
    revs = [r for r in db.reversals_all() if r.get("ticker") == "MET"]
    if rc1 != 0 or rc2 != 0 or rc3 != 2 or len(revs) != 1 or revs[0].get("kind") != "hold_overturned" \
            or revs[0].get("status") != "pending" or revs[0].get("qty") != 2 or "이미 있음" not in o2:
        p.append(f"reverse: rc {rc1}/{rc2}/{rc3} {revs} {o2!r}")
    # post 안내 줄은 새 명령
    c.execute("INSERT INTO op_audit (run_id, skill, status, at, line_sha, body) VALUES (?,?,?,?,?,?)",
              (rid, "fact-check", "FAIL", "2026-10-14T23:00:00", "sha-t14-1",
               json.dumps({"type": "finding", "run": f"AI클레임_{st3}_{MK}_주문근거_판정.md", "status": "FAIL"})))
    facts._market_open = lambda mk: (False, "테스트 — 닫힘")
    rc, out, err = quiet(facts.decide, st3, MK, "post", ledger=NO_LEDGER)
    if rc != 0 or f"facts.py reverse --stamp {st3} --market {MK} --ticker MET" not in out or "fc_track1" in out:
        p.append(f"post 보유 재판정 안내: rc={rc} {out[-240:]!r}")
    # R5 — 늦게 온 감사: 반영 1회 · op_audit 1행 + 엔진 원장의 늦은 줄 1행 → 미반영
    if facts.unreflected(MK, "261015", ledger=NO_LEDGER):
        p.append(f"반영된 감사를 미반영으로 셌다: {facts.unreflected(MK, '261015', ledger=NO_LEDGER)}")
    db.connect().execute("UPDATE ex_run SET started='2026-10-14T09:00:00+09:00' WHERE run_id=?", (db.run_id(st3, MK),))   # 감사보다 먼저(v4)
    led = TMP / "t14_promotion_ledger.jsonl"
    led.write_text(json.dumps({"skill": "fact-check", "type": "finding", "run": f"AI클레임_{st3}_{MK}_주문근거_판정.md",
                               "verdict": "PASS", "ts": "2026-10-15T01:00:00"}, ensure_ascii=False) + "\n", encoding="utf-8")
    old = os.environ.get("AITB_STEPGATE_LEDGER")
    os.environ["AITB_STEPGATE_LEDGER"] = str(led)
    try:
        ur = facts.unreflected(MK, "261015")
        note = facts.carry_note(MK, "261015")
        none_after = facts.unreflected(MK, st3)            # 그 run 자신(이전 run이 아니다)은 보지 않는다
    finally:
        if old is None:
            os.environ.pop("AITB_STEPGATE_LEDGER", None)
        else:
            os.environ["AITB_STEPGATE_LEDGER"] = old
    if ur != [(st3, 2, 1)] or f"python3 facts.py decide --mode post --stamp {st3} --market {MK}" not in note \
            or "감사 2회 · 반영 1회" not in note or none_after:
        p.append(f"unreflected/carry_note: {ur} {note!r} {none_after}")
    report("(14) 매수 통과는 받치는 사실의 MATCH(T1/T2) · facts.py reverse(멱등) · 늦게 온 감사 → carry_note(엔진 원장 먼저 옮김)", p)


def t15_audit_rounds_skip_superseded():
    """주문 근거 감사 회차 — 대체된(superseded) 결과는 세지 않는다 · 묶음 A·B 판정표 감사는 주문 근거 회차가 아니다
    (게이트 엔진이 같은 판정표를 다시 judge하면 옛 요청의 결과를 superseded로 기록한다 — 2026-10-15)."""
    p = []
    fresh("t15")
    led = TMP / "facts_t15_ledger.jsonl"
    rows = [
        {"type": "finding", "skill": "fact-check", "cp": "verdict", "run": f"AI클레임_{ST}_{MK}_주문근거_판정.md",
         "verdict": "FAIL", "ts": "2026-10-16T01:00:00", "superseded": True},
        {"type": "finding", "skill": "fact-check", "cp": "verdict", "run": f"AI클레임_{ST}_{MK}_주문근거_판정.md",
         "verdict": "PASS", "ts": "2026-10-16T01:20:00"},
        {"type": "finding", "skill": "fact-check", "cp": "verdict", "run": f"AI클레임_{ST}_{MK}_A_판정.md",
         "verdict": "FAIL", "ts": "2026-10-16T01:30:00"},
    ]
    led.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    got = facts.audit_rounds(ST, MK, ledger=led)
    if [s for _, s in got] != ["PASS"]:
        p.append(f"회차(대체·묶음 A 제외 후 PASS 하나): {got}")
    report("(15) 주문 근거 감사 회차 — 대체된 결과·묶음 A·B 감사는 세지 않는다", p)


def t16_rehearsal_fixes():
    """예행(2026-10-17) 결함 — 두 묶음이 같은 사실을 MISMATCH로 잡으면 뒤 판정에 기존 교정을 연결(D12) · 같은 칸을 다른 값으로
    다시 고치면 경고(D14) · 공시는 원문 인용 강제 없음(D13) · known이 종류와 무관하게 옛 문장을 찾는다(D1) · 모르는 칸은 받는 칸을
    알려 주고 kinds가 목록을 낸다(D2)."""
    p = []
    fresh("t16")
    fid = add(earn())["id"]
    quiet(facts.add_verdicts, [vrow(fid, "MISMATCH", 1, batch="track1", note="원문은 16.5")])
    v1 = db.q1("SELECT id FROM ev_verdict ORDER BY id DESC LIMIT 1")["id"]
    quiet(facts.correct, fid, {"actual": "16.5"}, "원문 표의 값으로", v1, (ST, MK))
    quiet(facts.add_verdicts, [vrow(fid, "MISMATCH", 1, batch="A", note="원문은 16.5")])
    v2 = db.q1("SELECT id FROM ev_verdict ORDER BY id DESC LIMIT 1")["id"]
    rc, out, _ = quiet(facts.correct, fid, {"actual": "16.5"}, "묶음 A도 같은 교정", v2, (ST, MK))
    link = db.q1("SELECT * FROM ev_correction WHERE verdict_id=?", (v2,))
    if rc != 0 or not link or json.loads(link["old"]) != json.loads(link["new"]) or "이미 반영됨" not in link["reason"] \
            or "연결만" not in out or db.q1("SELECT rev FROM ev_fact WHERE id=?", (fid,))["rev"] != 2:
        p.append(f"이미 교정된 값에 판정 연결(D12): rc={rc} {link} {out[:120]!r}")
    rc, out, _ = quiet(facts.correct, fid, {"actual": "16.6"}, "다른 판정의 원문", None, (ST, MK))
    if "⚠" not in out or "다시 바꾼다" not in out:
        p.append(f"같은 칸을 다른 값으로 다시 고쳐도 경고가 없다(D14): {out[:160]!r}")
    d = add({"kind": "disclosure", "ticker": "105560", "market": "KR", "type": "자기주식취득결과보고서", "filed_at": "2026-10-15",
             "title": "자기주식취득결과보고서", "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261015000001", "tier": 1})
    if not d or d.get("status") not in ("신규", "되풀이"):
        p.append(f"공시(T1 · 인용 없음)가 거부됐다(D13): {d}")
    con = db.connect()
    con.execute("INSERT INTO ev_fact (id, kind, legacy_text, as_of, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("SRC-9001", "market_state", "우리금융(316140) 10-15 종가 31,150원", "2026-10-15", db.now_iso(), db.now_iso()))
    k = facts.known(ticker="316140", market="KR")
    if "SRC-9001" not in k:
        p.append("known이 종류가 붙은 옛 문장(주체 칸 없음)을 못 찾는다(D1)")
    try:
        quiet(facts.add_fact, analyst(reason="NII 모멘텀"), (ST, MK))
        p.append("모르는 칸 행을 받았다")
    except facts.Reject as e:
        if "받는 상세 칸" not in str(e) or "facts.py kinds" not in str(e):
            p.append(f"모르는 칸 거부 문구에 받는 칸이 없다(D2): {e}")
    kt = facts.kinds_text("analyst")
    if "필수: ticker house date action target_new url quote" not in kt or "선택:" not in kt or "est_op" not in kt:
        p.append(f"kinds 목록(D2): {kt[:200]!r}")
    report("(16) 예행 결함 — 기존 교정 연결 · 같은 칸 재교정 경고 · 공시 인용 면제 · known 옛 문장 · 모르는 칸 안내·kinds", p)


def t17_reverse_tool_only():
    """(FB-261017-9) reverse는 보유 재판정(hold_overturned)뿐 — 이 run의 체결 매수(낡은 근거 아님)·대상 밖 종목은 rc 3 ·
    `--kind buy_unsupported`는 rc 2 · 대상 = 이 run holds_unverified · 이 run stale_support · 직전 run stale_support·holds_unverified ·
    carry_note가 직전 run의 미결 낡은 근거를 〈낡은 근거〉 표로 올린다."""
    p = []
    fresh("t17")
    st_prev, st = "261013", "261014"
    c = db.connect()
    db.run_start(st_prev, MK)
    c.execute("INSERT INTO ex_signal (run_id, track1) VALUES (?,?)",
              (db.run_id(st_prev, MK), json.dumps({"post": {"stale_support": [{"ticker": "PPP", "facts": ["F-000001"]}],
                                                            "holds_unverified": ["QQQ"]}})))
    db.run_start(st, MK)
    rid = db.run_id(st, MK)
    c.execute("INSERT INTO ex_signal (run_id, track1) VALUES (?,?)",
              (rid, json.dumps({"holds_unverified": ["HHH"], "post": {"stale_support": [{"ticker": "SSS", "facts": ["F-000002"]}],
                                                                      "holds_unverified": ["HHH"]}})))
    for tk in ("BBB", "SSS"):
        cur = c.execute("INSERT INTO ex_order (run_id, ticker, market, side, qty, status, approved_ref, sent_at, trade_key, body)"
                    " VALUES (?,?,?,?,?,?,?,'t','k','{}')",
                        (rid, tk, MK, "BUY", 3, "SENT", f"signals/approved_{st}_{MK}.json"))
        c.execute("INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (?,?,?,?,?,?,'{}')",
                  (cur.lastrowid, rid, tk, MK, 3, "FILLED"))

    def rv(tk, *extra):
        return cli(["reverse", "--stamp", st, "--market", MK, "--ticker", tk, "--qty", "1", "--why", "보유 재판정 — 낡은 사실 확인 불가", *extra])
    rc_b, _, e_b = rv("BBB")
    rc_x, _, e_x = rv("XXX")
    rc_k = rv("HHH", "--kind", "buy_unsupported")[0]
    rc_k2 = facts.reverse(st, MK, "HHH", 1, "x", kind="buy_unsupported", out=io.StringIO(), err=io.StringIO())
    if rc_b != 3 or "이 run의 체결 매수다" not in e_b or "판단으로 되팔지 않는다" not in e_b:
        p.append(f"이 run 체결 매수(낡은 근거 아님): rc={rc_b} {e_b[:160]!r}")
    if rc_x != 3 or "보유 재판정 대상이 아니다" not in e_x or "stale_support=SSS" not in e_x:
        p.append(f"대상 밖: rc={rc_x} {e_x[:200]!r}")
    if rc_k != 2 or rc_k2 != 2:
        p.append(f"--kind buy_unsupported 거부: CLI {rc_k} · 함수 {rc_k2}")
    rcs = {tk: rv(tk)[0] for tk in ("HHH", "SSS", "PPP", "QQQ")}
    revs = db.reversals_all()
    if rcs != {"HHH": 0, "SSS": 0, "PPP": 0, "QQQ": 0} or sorted(r["ticker"] for r in revs) != ["HHH", "PPP", "QQQ", "SSS"] \
            or {r.get("kind") for r in revs} != {"hold_overturned"}:
        p.append(f"대상 종목은 hold_overturned로 기록: {rcs} {[(r.get('ticker'), r.get('kind')) for r in revs]}")
    cn = facts.carry_note(MK, "261015", ledger=NO_LEDGER)
    if f"### 낡은 근거(stale_support) — 보유 재판정 (직전 run {st} TRACK1 POST)" not in cn or "| SSS | F-000002 |" not in cn \
            or "facts.py reverse" not in cn:
        p.append(f"carry_note 〈낡은 근거〉: {cn!r}")
    report("(17) reverse는 보유 재판정뿐 — 체결 매수·대상 밖 rc 3 · buy_unsupported rc 2 · 대상 4갈래 rc 0 · carry_note 〈낡은 근거〉", p)


# ───────────────────────────── 2026-10-19 Codex R04·S03 — 사실 판 · 판정 판 ─────────────────────────────

def t18_corrected_fact_voids_old_match():
    """(18) 리뷰 재현 corrected_fact_keeps_old_match의 올바른 동작 — MATCH(T1) 뒤 값 교정 → 옛 MATCH는 안 센다(REVERIFY) ·
    risk_guard.fact_states match12 False · mismatch True · 지금 값을 다시 판정하면 다시 선다."""
    import risk_guard as rg
    p = []
    fresh("t18")
    run = ("261019", "kr")
    fid = add({"kind": "earnings", "ticker": "000660", "period": "2026Q3", "metric": "revenue", "actual": 100, "as_of": "2026-10-19",
               "url": "https://example.com/review-fixture", "tier": 1, "quote": "Reported revenue 100 in the quarter ended"}, run=run)["id"]
    quiet(facts.add_verdicts, [{"fact_id": fid, "fact_rev": 1, "verdict": "MATCH", "tier": 1}], run, "track1")
    before = rg.fact_states([fid], "261019_kr")[fid]
    quiet(facts.correct, fid, {"actual": 200}, "review fixture: corrected actual revenue")
    after = rg.fact_states([fid], "261019_kr")[fid]
    g = facts.grade_verdicts(db.connect(), "261019_kr", [fid])[fid]
    if not before["match12"] or before["mismatch"]:
        p.append(f"교정 전 MATCH가 서야 한다: {before}")
    if after["match12"] or not after["mismatch"] or not after["reverify"] or g["verdict"] != "REVERIFY":
        p.append(f"교정 뒤 옛 MATCH가 셌다: {after} · {g}")
    if (g.get("stale_rev") or {}).get("worst") != "MATCH" or after["value_rev"] != 2:
        p.append(f"REVERIFY 상세(옛 판 MATCH · 값 판 2): {g.get('stale_rev')} · value_rev {after['value_rev']}")
    quiet(facts.add_verdicts, [{"fact_id": fid, "fact_rev": 2, "verdict": "MATCH", "tier": 1}], run, "track1")
    again = rg.fact_states([fid], "261019_kr")[fid]
    if not again["match12"] or again["mismatch"]:
        p.append(f"지금 값을 다시 판정했는데 서지 않는다: {again}")
    report("(18) 교정 뒤 옛 MATCH는 안 센다 — REVERIFY · match12 False · mismatch True · 재판정하면 다시 선다(리뷰 재현 R04)", p)


def t19_explicit_old_rev_verdict():
    """(19) 판정 판을 명시한 늦은 판정 — 옛 판(fact_rev 1)으로 넣은 MATCH는 행은 남되 세지 않는다 · 상태를 verified로 올리지 않는다 ·
    지금 판보다 높은 판은 거부 · 판을 비우고 요청(클레임)도 없으면 거부(W03 — 지금 판으로 추정하지 않는다) · 지금 판을 적으면 선다."""
    p = []
    fresh("t19")
    fid = add(earn())["id"]
    quiet(facts.correct, fid, {"actual": "16.5"}, "원문 값")
    rc, _, err = quiet(facts.add_verdicts, [vrow(fid, "MATCH", 1, fact_rev=1)])
    f = db.q1("SELECT verified_status, value_rev FROM ev_fact WHERE id=?", (fid,))
    v = db.q1("SELECT fact_rev FROM ev_verdict ORDER BY id DESC LIMIT 1")
    if rc != 0 or v["fact_rev"] != 1 or f["verified_status"] == "verified" or "세지 않는다" not in err:
        p.append(f"옛 판 MATCH가 상태를 올렸다: rc={rc} {f} {v} {err[:120]!r}")
    if facts.grade_verdicts(db.connect(), RID, [fid])[fid]["verdict"] != "REVERIFY":
        p.append("옛 판 판정만 있는데 REVERIFY가 아니다")
    rc, _, err = quiet(facts.add_verdicts, [vrow(fid, "MATCH", 1, fact_rev=9)])
    if rc != 2 or "판 범위" not in err:
        p.append(f"지금 판보다 높은 fact_rev를 받았다: rc={rc} {err[:120]!r}")
    rc, _, err = quiet(facts.add_verdicts, [vrow(fid, "MATCH", 1, fact_rev=None)])
    if rc != 2 or "지금 판으로 추정하지 않는다" not in err or db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (fid,))["verified_status"] == "verified":
        p.append(f"판을 비운 판정(요청 없음)을 받았다: rc={rc} {err[:160]!r}")
    quiet(facts.add_verdicts, [vrow(fid, "MATCH", 1)])                  # 큐 행의 지금 판(rev 2)을 옮긴 판정
    f = db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (fid,))
    if f["verified_status"] != "verified" or facts.grade_verdicts(db.connect(), RID, [fid])[fid]["verdict"] != "MATCH":
        p.append(f"지금 판을 적은 판정이 서지 않는다: {f}")
    report("(19) 늦은 판정 — 명시한 옛 판은 행만 남고 안 센다 · 높은 판 거부 · 판을 비우고 요청도 없으면 거부 · 지금 판이면 선다", p)


def t20_history_show_from_rev():
    """(20) 판 이력 — history(판·교정·바뀐 칸) · show --rev(머리+세부) · correct --from-rev(새 교정으로 되돌림 · 이력은 그대로)."""
    p = []
    fresh("t20")
    fid = add(earn())["id"]
    quiet(facts.correct, fid, {"actual": "16.5"}, "원문 값")
    quiet(facts.correct, fid, {"consensus": "15.8"}, "컨센 채움")
    rc, out, _ = cli(["history", fid])
    if rc != 0 or "rev 1 → 2" not in out or "rev 2 → 3" not in out or "actual: 16.4 → 16.5" not in out:
        p.append(f"history: rc={rc} {out[:300]!r}")
    rc, out, _ = cli(["show", fid, "--rev", "1"])
    if rc != 0 or '"actual": 16.4' not in out or "세부:" not in out:
        p.append(f"show --rev 1: rc={rc} {out[:300]!r}")
    rc, out, err = cli(["correct", fid, "--from-rev", "1", "--reason", "첫 판으로"])
    d = db.q1("SELECT actual, consensus FROM co_earnings WHERE fact_id=?", (fid,))
    f = db.q1("SELECT rev, value_rev FROM ev_fact WHERE id=?", (fid,))
    if rc != 0 or d["actual"] != 16.4 or (f["rev"], f["value_rev"]) != (4, 4) or n_rows("ev_fact_version", "fact_id=?", (fid,)) != 3:
        p.append(f"--from-rev 1: rc={rc} {d} {f} {err[:160]!r}")
    if "rev 1의 값으로 되돌림" not in (db.q1("SELECT reason FROM ev_correction ORDER BY id DESC LIMIT 1") or {}).get("reason", ""):
        p.append("되돌림 교정 사유에 판 번호가 없다")
    rc, _, err = cli(["correct", fid, "--set", "actual=1", "--from-rev", "1", "--reason", "x"])
    if rc != 2:
        p.append("--set과 --from-rev를 같이 받았다")
    report("(20) 판 이력 — history · show --rev(머리+세부) · correct --from-rev(새 교정 · 이력 보존)", p)


def t21_reuse_fill_bumps_and_requeues():
    """(21) 리뷰 재현 reuse_adds_value_without_reverification의 올바른 동작 — 검증된 사실을 재사용 등록이 빈 값 칸으로 채우면
    rev·value_rev 2 · 상태 none · 교정 행(재사용 보강) · 판 기록(전부) · 큐에 다시 · 표기 칸만 채우면 value_rev 그대로."""
    p = []
    fresh("t21")
    fid = add(earn(consensus=None))["id"]
    quiet(facts.add_verdicts, [vrow(fid, "MATCH", 1, batch="A")])
    if db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (fid,))["verified_status"] != "verified":
        p.append("준비: verified가 아니다")
    r = add({"kind": "earnings", "ticker": "BAC", "period": "2026Q3", "metric": "NII", "as_of": "2026-10-14", "consensus": 15.9})
    f = db.q1("SELECT rev, value_rev, verified_status, verified_tier FROM ev_fact WHERE id=?", (fid,))
    cor = db.q1("SELECT reason, old, new FROM ev_correction WHERE fact_id=? ORDER BY id DESC LIMIT 1", (fid,))
    ver = db.q1("SELECT snapshot FROM ev_fact_version WHERE fact_id=? ORDER BY id DESC LIMIT 1", (fid,))
    if r["status"] != "재사용" or (f["rev"], f["value_rev"], f["verified_status"], f["verified_tier"]) != (2, 2, "none", None):
        p.append(f"재사용 보강 뒤: {r['status']} {f}")
    if not cor or not cor["reason"].startswith("재사용 보강") or json.loads(cor["new"]) != {"consensus": 15.9}:
        p.append(f"교정 행: {cor}")
    if not ver or "header" not in json.loads(ver["snapshot"]):
        p.append("판 기록이 전부(머리+세부)가 아니다")
    cite(fid)
    rows, _ = facts.queue(ST, MK, None)
    if fid not in [x["fact_id"] for x in rows]:
        p.append("값이 바뀐 재사용 사실이 검증 큐에 다시 들어오지 않았다")
    fid2 = add(analyst(source=None, house="MS", url="https://example.com/ms-bac"))["id"]
    add({"kind": "analyst", "ticker": "BAC", "house": "MS", "date": "2026-10-15", "source": "MS 노트"})
    f2 = db.q1("SELECT rev, value_rev, source_label FROM ev_fact WHERE id=?", (fid2,))
    if (f2["rev"], f2["value_rev"], f2["source_label"]) != (2, 1, "MS 노트"):
        p.append(f"표기 칸(출처 이름)만 채우면 rev만 오른다: {f2}")
    report("(21) 재사용 보강 = 값 변경 — rev·value_rev · 상태 none · 교정 행 · 판 기록 · 큐에 다시 · 표기만이면 value_rev 그대로(리뷰 재현 S03)", p)


def t22_post_value_change_not_sold():
    """(22) 체결 뒤 근거 값이 바뀌었을 뿐(옛 판정 MATCH)이면 소급 매도하지 않고 낡은 근거(재확인·재판정)로 넘긴다 —
    MISMATCH를 고친 경우는 (10)처럼 소급 매도 그대로."""
    p = []
    fresh("t22")
    st2 = "261013"
    rid = db.run_id(st2, MK)
    run = (st2, MK)
    a1 = add(earn(ticker="AAA", quote="AAA reported net interest income of $16.4B for the quarter"), run=run)["id"]
    proposal(0, "AAA", "BUY", [a1], run=rid)
    facts.queue(st2, MK, "track1")
    quiet(facts.add_verdicts, [vrow(a1, "MATCH", 1, run_id=rid)])
    rc, _, err = quiet(facts.decide, st2, MK, "pre", ledger=NO_LEDGER)
    c = db.connect()
    oid = c.execute("INSERT INTO ex_order (run_id, ticker, market, side, qty, status, approved_ref, sent_at, trade_key, body)"
                    " VALUES (?,?,?,?,?,?,?,'t','k','{}')",
                    (rid, "AAA", MK, "BUY", 3, "SENT", f"signals/approved_{st2}_{MK}.json")).lastrowid
    c.execute("INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (?,?,?,?,?,?,'{}')",
              (oid, rid, "AAA", MK, 3, "FILLED"))
    c.execute("INSERT INTO op_audit (run_id, skill, status, at, line_sha, body) VALUES (?,?,?,?,?,?)",
              (rid, "fact-check", "PASS", "2026-10-13T23:00:00", "sha-p1",
               json.dumps({"type": "finding", "run": f"AI클레임_{st2}_{MK}_주문근거_판정.md", "status": "PASS"})))
    quiet(facts.correct, a1, {"as_of": "2026-10-15"}, "기준일 표기 보정(다른 묶음에서)")
    facts._market_open = lambda mk: (False, "테스트 — 닫힘")
    rc, out, err = quiet(facts.decide, st2, MK, "post", ledger=NO_LEDGER)
    if any(r.get("ticker") == "AAA" for r in db.reversals_all()):
        p.append("값만 바뀐(옛 판정 MATCH) 근거로 소급 매도했다")
    if "stale_support=AAA(" not in out or a1 not in out:
        p.append(f"낡은 근거로 넘기지 않았다: {out[:240]!r}")
    report("(22) 체결 뒤 값만 바뀐 근거 — 소급 매도 없음 · 낡은 근거(재확인·재판정)로", p)


def t23_verified_rev_requeue():
    """(23) 검증 판(v3 · S03) — MATCH(T1)로 verified가 되면 verified_rev = 그 판 · 값 교정이면 비운다 · verified인데 verified_rev < value_rev면
    검증 큐(A)가 다시 넣고 doctor가 센다(v_verify_queue와 같은 규칙)."""
    p = []
    fresh("t23")
    fid = add(earn())["id"]
    quiet(facts.add_verdicts, [vrow(fid, "MATCH", 1, batch="A")])
    f = db.q1("SELECT verified_status, verified_rev FROM ev_fact WHERE id=?", (fid,))
    if (f["verified_status"], f["verified_rev"]) != ("verified", 1):
        p.append(f"MATCH 뒤 verified_rev 1: {f}")
    cite(fid)
    if fid in [x["fact_id"] for x in facts.queue(ST, MK, None)[0]]:
        p.append("검증된 사실이 큐에 있다")
    db.connect().execute("UPDATE ev_fact SET value_rev=2, rev=2 WHERE id=?", (fid,))     # 검증 뒤 값이 바뀐 채 verified로 남은 행(옛 경로)
    if fid not in [x["fact_id"] for x in facts.queue(ST, MK, None)[0]]:
        p.append("verified_rev < value_rev인데 큐에 다시 안 들어왔다")
    if fid not in [r["id"] for r in db.q("SELECT id FROM v_verify_queue")]:
        p.append("v_verify_queue가 같은 규칙이 아니다")
    if (db.doctor(verbose=False).get("verify_drift") or 0) < 1:
        p.append("doctor가 검증 뒤 값이 바뀐 verified 사실을 세지 않았다")
    quiet(facts.correct, fid, {"actual": "16.6"}, "원문 값")
    f = db.q1("SELECT verified_status, verified_rev FROM ev_fact WHERE id=?", (fid,))
    if (f["verified_status"], f["verified_rev"]) != ("none", None):
        p.append(f"값 교정 뒤 검증 상태·판 비움: {f}")
    report("(23) 검증 판 — verified_rev = 검증한 판 · 값 교정이면 비움 · verified_rev < value_rev면 큐에 다시 · doctor가 센다(S03)", p)



def t24_verdict_request_contract():
    """(24) ← late_unversioned_verdict_approves_new_value · source_version_change_keeps_match(Codex W03)의 올바른 동작.
    판정은 자기가 본 사실 판을 가진다: 큐 행의 fact_rev를 옮기거나, 클레임 번호면 그 요청이 고정될 때의 판 — 단 요청 뒤 값이 바뀌었으면 거부 ·
    요청도 판도 없으면 거부(지금 판으로 추정하지 않는다) · 클레임과 사실 번호가 어긋나면 거부 · 지금 판을 적은 재검증은 선다 ·
    근거 본문 판을 바꾸면 인용이 옛 판·새 판 모두에 있을 때만 표기, 아니면 값 변경(재검증)."""
    p = []
    fresh("t24")
    fid = add(earn())["id"]
    other = add(earn(metric="EPS", quote="EPS line: the bank reported diluted earnings per share of 0.95 for the quarter"))["id"]
    cite(fid)
    facts.queue(ST, MK, "A")                                     # 클레임 1 = fid · 요청 판 1
    quiet(facts.correct, fid, {"actual": "16.5"}, "검증 도중 교정")    # rev 2 · value_rev 2
    base = {"verdict": "MATCH", "tier": 1, "t1": "T1 자체(공시 원문)", "note": "fetch.py 01:10 · 원문과 같다"}
    rc, _, err = quiet(facts.add_verdicts, [{**base, "claim": 1}], (ST, MK), "A")
    if rc != 2 or "요청" not in err or n_rows("ev_verdict") != 0:
        p.append(f"요청 뒤 값이 바뀐 클레임의 판 없는 판정을 받았다: rc={rc} {err[:160]!r}")
    rc, _, err = quiet(facts.add_verdicts, [{**base, "fact_id": fid}], (ST, MK), "A")
    if rc != 2 or n_rows("ev_verdict") != 0:
        p.append(f"사실 번호 · 판 없음 · 요청 뒤 교정 — 받았다: rc={rc}")
    rc, _, err = quiet(facts.add_verdicts, [{**base, "fact_id": other}], (ST, MK), "A")
    if rc != 2 or "지금 판으로 추정하지 않는다" not in err:
        p.append(f"요청도 판도 없는 판정을 받았다: rc={rc} {err[:160]!r}")
    rc, _, err = quiet(facts.add_verdicts, [{**base, "claim": 1, "fact_id": other}], (ST, MK), "A")
    if rc != 2 or "어긋났다" not in err:
        p.append(f"클레임과 사실 번호가 어긋난 판정을 받았다: rc={rc} {err[:160]!r}")
    if db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (fid,))["verified_status"] == "verified":
        p.append("거부된 판정들이 새 값을 verified로 올렸다")
    rc, _, err = quiet(facts.add_verdicts, [{**base, "claim": 1, "fact_rev": 2}], (ST, MK), "A")
    f = db.q1("SELECT verified_status, verified_rev FROM ev_fact WHERE id=?", (fid,))
    if rc != 0 or f["verified_status"] != "verified" or f["verified_rev"] != 2:
        p.append(f"지금 판을 적은 재검증이 서지 않는다: rc={rc} {dict(f)} {err[:120]!r}")
    third = add(earn(metric="CET1", quote="CET1 line: the bank reported a common equity tier 1 ratio of 11.6 percent"))["id"]
    cite(third)
    facts.queue(ST, MK, "A", append=True)                        # 클레임 2 = third · 요청 판 1 · 그 뒤 값 변경 없음
    slot = db.q1("SELECT slot FROM ev_claim_ref WHERE fact_id=? AND doc_type='claims'", (third,))["slot"]
    rc, _, err = quiet(facts.add_verdicts, [{**base, "claim": int(slot)}], (ST, MK), "A")
    if rc != 0 or db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (third,))["verified_status"] != "verified":
        p.append(f"요청 뒤 안 바뀐 클레임의 판 없는 판정이 서지 않는다: rc={rc} {err[:120]!r}")
    # 근거 본문 판 교체 — 인용이 새 판에 없으면 값 변경(재검증) · 두 판 모두에 있으면 표기
    url = "https://example.com/w03-versioned"
    old = db.evidence_record(url, "Quarter revenue reported was exactly 100 units in total for the period.")
    new = db.evidence_record(url, "Quarter revenue reported was exactly 200 units in total for the period.")
    same = db.evidence_record(url, "Header. Quarter revenue reported was exactly 100 units in total for the period. Footer.")
    fv = add(earn(metric="REV", actual=100, url=url, quote="Quarter revenue reported was exactly 100 units",
                  source_version=old["version_id"]))["id"]
    quiet(facts.add_verdicts, [vrow(fv, "MATCH", 1)])
    quiet(facts.correct, fv, {"source_version": same["version_id"]}, "같은 인용이 든 판으로 연결 정정")
    h = db.q1("SELECT rev, value_rev, verified_status FROM ev_fact WHERE id=?", (fv,))
    if (h["rev"], h["value_rev"], h["verified_status"]) != (2, 1, "verified"):
        p.append(f"같은 인용 판으로 바꾼 것이 값 변경이 됐다: {dict(h)}")
    rc, out, _ = quiet(facts.correct, fv, {"source_version": new["version_id"]}, "다른 내용의 판으로")
    h = db.q1("SELECT rev, value_rev, verified_status FROM ev_fact WHERE id=?", (fv,))
    g = facts.grade_verdicts(db.connect(), RID, [fv])[fv]
    if h["value_rev"] != 3 or h["verified_status"] == "verified" or g["verdict"] != "REVERIFY" or "재검증" not in out:
        p.append(f"인용 없는 판으로 바꿨는데 MATCH가 남았다: {dict(h)} {g['verdict']} {out[:160]!r}")
    report("(24) 판정 요청 계약 — 요청 뒤 교정·요청 없음·어긋난 클레임은 거부 · 지금 판 재검증은 선다 · 본문 판 교체는 인용 대조(리뷰 재현 W03)", p)


def t25_audit_current_content():
    """(25) ← audit_wrong_content_hash_counted(Codex W06)의 올바른 동작 — 감사 회차(이력)는 그대로 세고, '지금 판의 PASS'는 감사가 본 판정표 판이
    지금 파일과 같을 때만: 해시(엔진은 앞 16자)가 다르면 PASS가 아니다 · 같으면 PASS · 해시 없는 옛 줄은 감사 뒤 파일이 안 바뀌었을 때만 ·
    범위 불명은 이력뿐 · 실전 점검표도 같은 규칙 · 렌더 시각만 다른 재렌더는 파일을 다시 쓰지 않는다."""
    import hashlib as _h
    import time
    import real_check
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    KST = timezone(timedelta(hours=9))
    p = []
    fresh("t25")
    root = TMP / "w06" / "ai-trading-bot"
    (root / "analysis").mkdir(parents=True, exist_ok=True)
    (root / "data" / "run_evidence").mkdir(parents=True, exist_ok=True)
    (root / "stage.py").write_text("", encoding="utf-8")
    (root / "db_schema.sql").write_text("", encoding="utf-8")
    name = f"AI클레임_{ST}_{MK}_주문근거_판정.md"
    f = root / "analysis" / name
    f.write_text("판정표 v2(고친 뒤)", encoding="utf-8")
    db.run_start(ST, MK)
    ts0 = (datetime.now(KST) + timedelta(seconds=30)).isoformat(timespec="seconds")
    led = TMP / "facts_t25_ledger.jsonl"

    def row(status, sha, ts, deliv=True, ff=True):
        r_ = {"type": "finding", "skill": "fact-check", "cp": "verdict", "run": name.lower(), "status": status, "ts": ts}
        if deliv:
            r_.update({"dir": str(root), "deliverable": str(f), "content_sha": sha})
        if ff:
            r_["findings_file"] = str(root / "data" / "run_evidence" / "audit_x.md")
        return r_
    old_sha = _h.sha256("판정표 v1(고치기 전)".encode("utf-8")).hexdigest()[:16]
    cur_sha = _h.sha256(f.read_bytes()).hexdigest()[:16]
    with patch.object(db, "project_root", return_value=root):
        led.write_text(json.dumps(row("PASS", old_sha, ts0), ensure_ascii=False) + "\n", encoding="utf-8")
        st_, why = facts.audit_current(ST, MK, ledger=led)
        rounds = facts.audit_rounds(ST, MK, ledger=led)
        flag, line = real_check.c4_audit(ST, MK)
        if st_ is not None or "다른 판" not in why or [s_ for _, s_ in rounds] != ["PASS"] or flag != "★" or "지금 판" not in line:
            p.append(f"옛 판 해시 PASS: 지금 판 {st_} {why!r} · 이력 {rounds} · 점검표 {flag} {line!r}")
        ts1 = (datetime.now(KST) + timedelta(seconds=40)).isoformat(timespec="seconds")
        with led.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row("PASS", cur_sha, ts1), ensure_ascii=False) + "\n")
        st_, _ = facts.audit_current(ST, MK, ledger=led)
        rounds = facts.audit_rounds(ST, MK, ledger=led)
        flag, line = real_check.c4_audit(ST, MK)
        if st_ != "PASS" or len(rounds) != 2 or flag != "✓":
            p.append(f"지금 판 해시 PASS: {st_} · 이력 {len(rounds)} · 점검표 {flag} {line!r}")
    # 해시 없는 옛 엔진 줄 — 감사 뒤 파일이 안 바뀌었으면 지금 판 · 바뀌었으면 아님 · 범위 불명은 이력뿐
    fresh("t25b")
    db.run_start(ST, MK)
    led2 = TMP / "facts_t25b_ledger.jsonl"
    ts2 = (datetime.now(KST) + timedelta(seconds=30)).isoformat(timespec="seconds")
    with patch.object(db, "project_root", return_value=root):
        led2.write_text(json.dumps(row("PASS", "", ts2, deliv=False), ensure_ascii=False) + "\n", encoding="utf-8")
        st_, _ = facts.audit_current(ST, MK, ledger=led2)
        if st_ != "PASS":
            p.append(f"해시 없는 옛 줄 · 파일 그대로: {st_}")
        later = time.time() + 120
        os.utime(f, (later, later))                             # 감사 뒤 파일이 바뀌었다
        st_, why = facts.audit_current(ST, MK, ledger=led2)
        if st_ is not None or "바뀐 판" not in why:
            p.append(f"해시 없는 옛 줄 · 감사 뒤 바뀐 파일: {st_} {why!r}")
        fresh("t25c")
        db.run_start(ST, MK)
        led3 = TMP / "facts_t25c_ledger.jsonl"
        led3.write_text(json.dumps(row("PASS", "", ts2, deliv=False, ff=False), ensure_ascii=False) + "\n", encoding="utf-8")
        st_, why = facts.audit_current(ST, MK, ledger=led3)
        if st_ is not None or len(facts.audit_rounds(ST, MK, ledger=led3)) != 1:
            p.append(f"범위 불명 줄이 지금 판 증거가 됐다: {st_} {why!r}")
    # 렌더 시각만 다른 재렌더는 판정표 파일을 다시 쓰지 않는다(감사가 본 판이 그대로 남는다)
    fresh("t25d")
    fid = add(earn())["id"]
    cite(fid)
    facts.queue(ST, MK, "A")
    outp = TMP / "w06_render.md"
    with patch.object(facts, "_now_min", return_value="2026-10-16 01:00"):
        facts.render_verdict(ST, MK, "A", out_path=str(outp))
    t_a, m_a = outp.read_text(encoding="utf-8"), outp.stat().st_mtime_ns
    with patch.object(facts, "_now_min", return_value="2026-10-16 01:07"):
        facts.render_verdict(ST, MK, "A", out_path=str(outp))
    if outp.read_text(encoding="utf-8") != t_a or outp.stat().st_mtime_ns != m_a:
        p.append("렌더 시각만 다른 재렌더가 판정표를 다시 썼다")
    quiet(facts.add_verdicts, [vrow(fid, "MATCH", 1, batch="A")])
    with patch.object(facts, "_now_min", return_value="2026-10-16 01:09"):
        facts.render_verdict(ST, MK, "A", out_path=str(outp))
    if outp.read_text(encoding="utf-8") == t_a:
        p.append("판정이 더해졌는데 판정표가 그대로다")
    report("(25) 감사 지금 판 — 해시가 다르면 PASS 아님 · 같으면 PASS · 해시 없는 옛 줄은 파일 불변일 때만 · 범위 불명은 이력 · "
           "점검표 같은 규칙 · 시각만 다른 재렌더는 안 씀(리뷰 재현 W06)", p)


for fn in (t01_reject_missing_required, t02_dedupe_reuse, t03_repeat_flag, t04_s_no, t05_dated_expiry, t06_queue_rules,
           t07_verdict_status_and_trigger, t08_correct_version_rerender, t09_render_verdict_format, t10_decide_port,
           t11_render_line_each_kind, t12_srcledger_format_compare, t13_refs_news_known_mention_reuse,
           t14_roles_reverse_unreflected, t15_audit_rounds_skip_superseded, t16_rehearsal_fixes, t17_reverse_tool_only,
           t18_corrected_fact_voids_old_match, t19_explicit_old_rev_verdict, t20_history_show_from_rev,
           t21_reuse_fill_bumps_and_requeues, t22_post_value_change_not_sold, t23_verified_rev_requeue,
           t24_verdict_request_contract, t25_audit_current_content):
    _safe(fn)

db.reset()
shutil.rmtree(TMP, ignore_errors=True)
_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_facts.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
