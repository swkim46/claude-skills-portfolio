#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_trace.sh가 부르는 시연 준비 — 빈 임시 저장소에 가상 데이터를 넣고 주문 근거 판정까지 돌린다.

운영에서 AI 세션과 도구가 나눠 하는 일을 같은 함수로 재현한다(네트워크·증권사 키 없음 · 저장소는 AITB_DB가 가리키는 임시 파일).
  ① 감시 목록·시장 지도(가상)를 저장소 표에 넣는다            — testkit.load_ledgers
  ② 조사 단계가 찾은 사실을 한 건씩 등록한다(가상 공시·공고·IR) — facts.add_fact
  ③ 주문 제안 파일(시그널)의 근거 자리를 사실 id로 채워 저장소에 옮긴다 — db.import_run_artifacts
  ④ 주문 근거를 판정 대기열에 올리고 판정을 적는다(시연은 전부 일치)  — facts.queue · facts.add_verdicts · facts.lint
  ⑤ 판정표를 바탕으로 주문 전 결정 기록(track1 블록)을 시그널에 쓴다  — facts.decide --mode pre
그다음 run_trace.sh가 risk_guard.py(주문 규칙 검사)를 실제로 실행한다.

    python3 seed_demo.py <작업 폴더(ai-trading-bot 사본)> <_synthetic 폴더> <YYMMDD>
"""
import contextlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORK, SYN, ST = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3]
MK = "kr"
KST = timezone(timedelta(hours=9))
sys.path.insert(0, str(WORK))

import db        # noqa: E402
import facts     # noqa: E402
import testkit   # noqa: E402

facts._market_open = lambda mk: (False, "시연 — 장 닫힘으로 고정")   # 시세 조회 없이 돈다


def quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        r = fn(*a, **k)
    return r, out.getvalue(), err.getvalue()


# ① 저장소(빈 파일 → db.connect가 표를 만든다) · run 시작 · 감시 목록과 시장 지도
db.run_start(ST, MK)
loaded = testkit.load_ledgers(WORK / "demo_in" / "journal", WORK / "demo_in" / "config")
wl = db.watchlist_doc()
print(f"① 저장소 준비 — 감시 목록 KR {len(wl.get('KR') or [])}종목 · 시장 지도 축 {loaded.get('axes', 0)}개 · 논지 {loaded.get('theses', 0)}건")

# ② 사실 등록 — 종류별 필수 칸이 없으면 facts.add_fact가 거부한다
ids = {}
for row in json.loads((SYN / "facts_kr.json").read_text(encoding="utf-8"))["facts"]:
    ref = row.pop("ref")
    r, _, err = quiet(facts.add_fact, row, (ST, MK))
    if not r or not r.get("id"):
        raise SystemExit(f"사실 등록 실패 {ref}: {err[-300:]}")
    ids[ref] = r["id"]
    print(f"② 사실 등록 — {ref} → {r['id']} ({row['kind']} · T{row.get('tier')})")

# ③ 시그널 — 근거 자리(<ref:…>)를 사실 id로, run_id·생성 시각을 지금으로
tpl = (SYN / "signal_kr.json").read_text(encoding="utf-8")
for ref, fid in ids.items():
    tpl = tpl.replace(f"<ref:{ref}>", fid)
sig = json.loads(tpl)
sig["run_id"] = db.run_id(ST, MK)
sig["generated_at"] = datetime.now(KST).isoformat(timespec="seconds")
left = [f["id"] for p in sig["proposals"] + sig.get("watchlist_candidates", []) for f in p.get("facts") or [] if str(f["id"]).startswith("<ref:")]
if left:
    raise SystemExit(f"치환되지 않은 근거 자리: {left}")
spath = WORK / "signals" / f"signal_{ST}_{MK}.json"
spath.parent.mkdir(parents=True, exist_ok=True)
spath.write_text(json.dumps(sig, ensure_ascii=False, indent=2), encoding="utf-8")
imp, _, err = quiet(db.import_run_artifacts, ST, MK, here=WORK)
print(f"③ 시그널 → 저장소 — 제안 {len(sig['proposals'])}건 · 빠진 사실 {imp.get('missing_facts') or 0}")

# ④ 주문 근거 대기열 → 판정(시연은 전부 일치) → 판정 기록 검사
rows, _ = quiet(facts.queue, ST, MK, "track1")[0]
rid = db.run_id(ST, MK)
vrows = []
for r in rows:
    x = db.q1("SELECT rev FROM ev_fact WHERE id=?", (r["fact_id"],))
    vrows.append({"fact_id": r["fact_id"], "verdict": "MATCH", "tier": 1, "batch": "track1", "run_id": rid,
                  "fact_rev": x["rev"] if x else None,
                  "sub": "값·출처", "t1": "T1 자체(가상 원문)", "note": "시연 — 가상 원문과 등록한 값이 같다",
                  "support": "논지 — 이 종목 제안의 근거 다리를 받친다"})
quiet(facts.add_verdicts, vrows)
errs, *_ = quiet(facts.lint, ST, MK, "track1")[0]
print(f"④ 주문 근거 판정 — 대기열 {len(rows)}건 · 일치 {len(vrows)}건 · 판정 기록 검사 오류 {len(errs)}")
if errs:
    raise SystemExit(f"판정 기록 검사 실패: {errs[:2]}")

# ⑤ 주문 전 결정 — 판정표에서 통과·제외를 가려 시그널에 track1 블록을 쓴다(감사 원장은 비어 있는 경로)
rc, out, err = quiet(facts.decide, ST, MK, "pre", ledger=WORK / "demo_in" / "no_ledger.jsonl", signal_path=str(spath))
t1 = json.loads(spath.read_text(encoding="utf-8")).get("track1") or {}
print(f"⑤ 주문 전 결정 — 통과 {t1.get('pass')} · 제외 {t1.get('drop')} · 감사 {t1.get('audit')} (종료 {rc})")
if rc != 0:
    raise SystemExit(f"decide 실패: {err[-300:]}")
