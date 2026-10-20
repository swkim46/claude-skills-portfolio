#!/usr/bin/env python3
"""
게이트 엔진 시험 — 2026-10-13에 더한 두 경로를 임시 루브릭 폴더에서 돌린다(실제 봉인 루브릭은 열지도 쓰지도 않는다).

1. 디스크 기준 `check: code_audit` — code_edit_audit의 판정을 그대로 GATE 기준으로 쓴다(PASS·FAIL 둘 다).
2. `rubric-add`가 spec의 `criteria`를 덧붙인다 — 모르는 검사 종류·같은 기준 중복은 거부, 관찰 항목 덧붙이기는 그대로.
3. `rubric-drop crit=`가 디스크 기준을 뺀다 — 맞는 기준만 · 0개 맞으면 아무것도 안 뺀다 · dry · id와 같이(2026-10-13).
4. 감사 lens `kind: reader`는 감사 패킷·기록 허용 목록에서 빠진다(2026-10-13 — 노트는 매매 필수 요소만 감사) ·
   `kind: order`는 주문 근거 판정표(`…주문근거…`)에만 붙는다(같은 날 17:30 — 매매 근거는 배경보다 빡빡하게).

    python3 test_stepgate_engine.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stepgate as sg  # noqa: E402

results = []


def check(name, cond, why=""):
    results.append((bool(cond), name, why))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — {why}"))


def run_drop(skill, cp, *kv):
    """cmd_rubric_drop을 부르고 (종료코드, 출력)을 돌려준다."""
    import contextlib
    import io
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            sg.cmd_rubric_drop(skill, cp, list(kv))
    except SystemExit as e:
        return int(e.code or 0), buf.getvalue()
    return 0, buf.getvalue()


def run_add(skill, cp, spec):
    """cmd_rubric_add를 부르고 종료코드를 돌려준다(성공 0 · 거부 3)."""
    p = os.path.join(TMP, "spec.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    try:
        sg.cmd_rubric_add(skill, cp, [f"spec={p}"])
    except SystemExit as e:
        return int(e.code or 0)
    return 0


TMP = tempfile.mkdtemp(prefix="sgtest_")
saved_dir = sg.RUBRIC_DIR
try:
    # ── 1. code_audit 기준 ──
    sm = os.path.abspath(os.path.join(HERE, "..", "skill-maintainer", "scripts"))
    sys.path.insert(0, sm)
    import code_edit_audit as cea
    real_audit = cea.audit
    calls = []
    cea.audit = lambda **k: (calls.append(k) or (False, "CODE-AUDIT FAIL — 위반 1\n  ✗ x.py — 검사 기록 없음"))
    ok, msg = sg.run_criterion({"check": "code_audit"}, {})
    check("code_audit — 감사 FAIL이면 기준 FAIL", ok is False and "검사 기록 없음" in msg, msg)
    check("code_audit — 게이트는 commit=True로 부른다(PASS면 기준선 이동)", calls and calls[-1].get("commit") is True, calls)
    cea.audit = lambda **k: (True, "CODE-AUDIT PASS — 위반 0")
    ok, msg = sg.run_criterion({"check": "code_audit"}, {})
    check("code_audit — 감사 PASS면 기준 PASS", ok is True, msg)
    cea.audit = real_audit

    # ── 2. rubric-add criteria ──
    sg.RUBRIC_DIR = os.path.join(TMP, "rubrics")
    os.makedirs(sg.RUBRIC_DIR)
    base = {"verify": {"intent": "시험", "observations": [
        {"id": "a", "variants": [{"ask": "A인가 (Y/N)", "must": "Y"}, {"ask": "A가 아닌가 (Y/N)", "must": "N"}]}]}}
    with open(os.path.join(sg.RUBRIC_DIR, "demo.json"), "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False)
    rc = run_add("demo", "verify", {"criteria": [{"check": "code_audit"}]})
    got = json.load(open(os.path.join(sg.RUBRIC_DIR, "demo.json"), encoding="utf-8"))
    check("rubric-add — 디스크 기준을 덧붙인다", rc == 0 and got["verify"].get("criteria") == [{"check": "code_audit"}],
          f"rc={rc} criteria={got['verify'].get('criteria')}")
    check("rubric-add — 기존 관찰 항목은 그대로", [o["id"] for o in got["verify"]["observations"]] == ["a"])
    check("rubric-add — 같은 디스크 기준 중복은 거부", run_add("demo", "verify", {"criteria": [{"check": "code_audit"}]}) == 3)
    check("rubric-add — 모르는 검사 종류는 거부", run_add("demo", "verify", {"criteria": [{"check": "nope"}]}) == 3)
    check("rubric-add — 모르는 키는 거부", run_add("demo", "verify", {"criteria": [{"check": "files", "zzz": 1}]}) == 3)
    rc = run_add("demo", "verify", {"observations": [
        {"id": "b", "variants": [{"ask": "B인가 (Y/N)", "must": "Y"}, {"ask": "B가 아닌가 (Y/N)", "must": "N"}]}]})
    got = json.load(open(os.path.join(sg.RUBRIC_DIR, "demo.json"), encoding="utf-8"))
    check("rubric-add — 관찰 항목 덧붙이기는 예전처럼", rc == 0 and [o["id"] for o in got["verify"]["observations"]] == ["a", "b"])
    check("rubric-add — 극성 하나뿐인 항목은 예전처럼 거부", run_add("demo", "verify", {"observations": [
        {"id": "c", "variants": [{"ask": "C인가 (Y/N)", "must": "Y"}]}]}) == 3)
    check("rubric-add — 백업은 임시 폴더에만", any(n.startswith("_demo_rubric_backup_") for n in os.listdir(TMP)))

    # ── 3. rubric-drop crit= ──
    crit3 = [{"check": "contains", "file": "{deliverable}", "pat": "심층 · ", "min": 5},
             {"check": "contains", "file": "{deliverable}", "pat": "NOTE-LINT OK", "min": 1},
             {"check": "files", "glob": "analysis/*.md", "min": 1}]
    base3 = {"note": {"intent": "시험", "criteria": crit3, "observations": [
        {"id": "x", "variants": [{"ask": "X인가 (Y/N)", "must": "Y"}, {"ask": "X가 아닌가 (Y/N)", "must": "N"}]},
        {"id": "y", "variants": [{"ask": "Y인가 (Y/N)", "must": "Y"}, {"ask": "Y가 아닌가 (Y/N)", "must": "N"}]}]}}
    rp = os.path.join(sg.RUBRIC_DIR, "demo3.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(base3, f, ensure_ascii=False)
    rd = lambda: json.load(open(rp, encoding="utf-8"))["note"]
    rc, out = run_drop("demo3", "note", "crit=심층 · ", "dry=1")
    check("rubric-drop crit — dry는 개수만 알리고 쓰지 않는다", rc == 0 and "1개" in out and len(rd()["criteria"]) == 3, f"rc={rc} out={out!r}")
    check("rubric-drop crit — 출력에 기준 내용(pat·file)이 없다", "{deliverable}" not in out and "min" not in out, out)
    rc, out = run_drop("demo3", "note", "crit=없는문자열,심층 · ")
    check("rubric-drop crit — 하나라도 0개 맞으면 아무것도 빼지 않고 거부", rc == 3 and len(rd()["criteria"]) == 3, f"rc={rc}")
    rc, out = run_drop("demo3", "note", "crit=심층 · ")
    got3 = rd()
    check("rubric-drop crit — 맞는 기준만 뺀다", rc == 0 and [c.get("pat", c.get("glob")) for c in got3["criteria"]] == ["NOTE-LINT OK", "analysis/*.md"],
          f"rc={rc} criteria={got3['criteria']}")
    check("rubric-drop crit — 관찰 항목은 그대로", [o["id"] for o in got3["observations"]] == ["x", "y"])
    rc, out = run_drop("demo3", "note", "ids=x", "crit=analysis/*.md")
    got3 = rd()
    check("rubric-drop ids+crit — 둘 다 뺀다", rc == 0 and [o["id"] for o in got3["observations"]] == ["y"]
          and [c.get("pat") for c in got3["criteria"]] == ["NOTE-LINT OK"], f"rc={rc} {got3}")
    rc, out = run_drop("demo3", "note", "ids=zz", "crit=NOTE-LINT")
    check("rubric-drop ids+crit — 없는 id면 기준도 빼지 않는다(원자적)", rc == 3 and len(rd()["criteria"]) == 1, f"rc={rc}")

    # ── 4. kind=reader lens ──
    cpr = {"lenses": [{"id": "keep1", "ask": "a"}, {"id": "rd1", "ask": "b", "kind": "reader"}, {"id": "keep2", "ask": "c"}]}
    check("reader lens — 감사 대상에서 빠진다", [L["id"] for L in sg._active_lenses(cpr)] == ["keep1", "keep2"])
    saved_lar = sg.load_audit_rubric
    sg.load_audit_rubric = lambda skill: {"note": cpr}
    try:
        check("reader lens — 기록 허용 목록(audit-judge lenses=)에도 없다", sg._audit_lens_ids("demo", "note") == ["keep1", "keep2"])
    finally:
        sg.load_audit_rubric = saved_lar
    check("reader lens — 전부 reader면 활성 lens 0(감사 정의 오류로 잡힌다)",
          sg._active_lenses({"lenses": [{"id": "x", "kind": "reader"}]}) == [])
    cpo = {"lenses": [{"id": "c1", "ask": "a"}, {"id": "o1", "ask": "b", "kind": "order"}, {"id": "r1", "ask": "c", "kind": "reader"}]}
    check("order lens — 주문 근거 판정표에만 붙는다",
          [L["id"] for L in sg._active_lenses(cpo, "/x/AI클레임_261013_us_주문근거_판정.md")] == ["c1", "o1"]
          and [L["id"] for L in sg._active_lenses(cpo, "/x/AI클레임_261013_us_판정.md")] == ["c1"])
    check("order lens — 산출물을 모르면(큐 유실 복구) 기록 허용 쪽은 넓게", [L["id"] for L in sg._active_lenses(cpo, "")] == ["c1", "o1"])

    # ── 5. 감사 중복·대체·패킷(2026-10-15) — 임시 큐 폴더(TEMP)·임시 승격 원장에서 실제 명령으로 ──
    import subprocess
    env = dict(os.environ, TEMP=os.path.join(TMP, "q"), STEPGATE_PROMO_LEDGER=os.path.join(TMP, "ledger.jsonl"))
    os.makedirs(env["TEMP"])
    proj = os.path.join(TMP, "proj")
    os.makedirs(os.path.join(proj, "analysis"))
    os.makedirs(os.path.join(proj, "data", "run_evidence"))
    dv = os.path.join(proj, "analysis", "AI클레임_261015_kr_A_판정.md")
    with open(os.path.join(proj, "analysis", "AI클레임_261015_kr_A.md"), "w", encoding="utf-8") as f:
        f.write("1. 원 문구\n")
    with open(dv, "w", encoding="utf-8") as f:
        f.write("# 판정표\nPROPAGATE OK (x)\nPROPAGATE SCOPE 훑은 파일 1: a.md\n")

    def sgc(*a):
        r = subprocess.run([sys.executable, os.path.join(HERE, "stepgate.py")] + list(a), env=env,
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout + r.stderr

    def reqid_of(out):
        import re as _re
        m = _re.search(r"req=(\S+)", out)
        return m.group(1) if m else ""
    rc1, o1 = sgc("enqueue", "fact-check", "verdict", "deliverable=" + dv, "dir=" + proj)
    r1 = reqid_of(o1)
    rc2, o2 = sgc("enqueue", "fact-check", "verdict", "deliverable=" + dv, "dir=" + proj)
    check("중복 — 같은 내용 10분 안 두 번째 judge는 새 감사를 띄우지 않는다",
          r1 and reqid_of(o2) == r1 and "FIRE" not in o2 and "이미 떠 있다" in o2, o2[-300:])
    with open(dv, "a", encoding="utf-8") as f:
        f.write("고친 줄\n")
    rc3, o3 = sgc("enqueue", "fact-check", "verdict", "deliverable=" + dv, "dir=" + proj)
    r3 = reqid_of(o3)
    check("내용이 바뀌면 새 감사(옛 요청은 대체)", r3 and r3 != r1 and "FIRE" in o3, o3[-200:])
    rcp, pk = sgc("audit-judge", r3)
    check("패킷 — 매매 판정표엔 매매 도메인 지시문(일상 정보 지시문 대신)", "매매 분석 노트" in pk and "일상·실무 정보" not in pk, pk[:400])
    check("패킷 — 식별자 금지·수정 금지·고정본·전파 범위·결과 전문 경로", all(x in pk for x in (
        "사용자 식별자", "고치지 않는다", "판정 기준 고정본", "PROPAGATE SCOPE", "data/run_evidence/audit_")), pk[-700:])
    ff = os.path.join(proj, "data", "run_evidence", "audit_x.md")
    with open(ff, "w", encoding="utf-8") as f:
        f.write("전문\n")
    rcr, orr = sgc("audit-judge", r1, "--record", "subverdict=FAIL", "findings=옛 내용 위반", "lenses=lint-ok", "findings_file=" + ff)
    led = [json.loads(x) for x in open(env["STEPGATE_PROMO_LEDGER"], encoding="utf-8") if x.strip()]
    check("대체된 요청의 결과 — 기록하되 superseded 표시·종료 0", rcr == 0 and led and led[-1].get("superseded") is True
          and led[-1].get("findings_file", "").endswith("audit_x.md"), f"rc={rcr} {orr[-200:]} {led[-1:] }")
    import hashlib as _hl
    first_sha = _hl.sha256("# 판정표\nPROPAGATE OK (x)\nPROPAGATE SCOPE 훑은 파일 1: a.md\n".encode("utf-8")).hexdigest()[:16]
    check("원장 줄에 감사가 본 판 — 과제 폴더 · 산출물 절대경로 · judge 때 내용 해시(2026-10-19 · Codex W06)",
          led and led[-1].get("dir") == proj and led[-1].get("deliverable") == os.path.abspath(dv)
          and led[-1].get("content_sha") == first_sha, f"{ {k: led[-1].get(k) for k in ('dir', 'deliverable', 'content_sha')} if led else None} vs {first_sha}")
    rcs, os_ = sgc("audit-status", "fact-check", "dir=" + proj)
    check("대체된 FAIL이 활성 감사로 되살아나지 않는다(활성 1 · FAIL 0)", "FAIL=0" in os_ and "PENDING=1" in os_, os_[-300:])
    rcb, ob = sgc("audit-judge", r3, "--record", "subverdict=FAIL", "findings=x", "lenses=lint-ok", "findings_file=/nope/x.md")
    check("findings_file이 없으면 거부", rcb == 3, ob[-200:])
    # 소비자 쪽(대체된 감사를 회차로 세지 않는다)은 매매 저장소 사실 층이 본다 — ai-trading-bot `test_facts.py` (15)
    # (옛 fc_track1.audit_rounds는 2026-10-17 `facts.audit_rounds`로 옮겨 `_archive/`에 있다)

    # ── 6. 모의 감사 미리보기(2026-10-15) — 정식 감사와 같은 패킷 · 큐에 안 남는다 ──
    rcv, pv = sgc("audit-packet-preview", "fact-check", "verdict", "deliverable=" + dv, "dir=" + proj)
    check("미리보기 — 정식 패킷과 같은 lens·지시문(매매 도메인)·모의 표시·preaudit 경로",
          rcv == 0 and "PREVIEW" in pv and "매매 분석 노트" in pv and "lens[" in pv and "preaudit_" in pv, pv[:300])
    rcs2, os2 = sgc("audit-status", "fact-check", "dir=" + proj)
    check("미리보기는 큐에 아무것도 넣지 않는다(활성 수 그대로)", "PENDING=1" in os2 and "FAIL=0" in os2, os2[-200:])
    rcj, pj = sgc("audit-judge", r3)
    check("정식 패킷은 그대로(req 표시·--record 안내)", "AUDIT PACKET [" in pj and "--record" in pj and "PREVIEW" not in pj, pj[:200])

    # ── 7. 통과 기록(2026-10-19 · trade-run R03) — PASS마다 approved= 파일 바이트 해시를 서명해 덧붙인다 · 위조·만료 거부 ·
    #       init/clear가 지운다. 게이트 폴더는 임시 폴더로(TEMP) — 실제 ~/claude_stepgate를 건드리지 않는다.
    old_temp = os.environ.get("TEMP")
    os.environ["TEMP"] = os.path.join(TMP, "gd")
    try:
        apf = os.path.join(TMP, "approved_261013_kr.json")
        with open(apf, "w", encoding="utf-8") as f:
            f.write('{"orders": []}')
        import hashlib as _h
        want = _h.sha256(open(apf, "rb").read()).hexdigest()
        r1 = sg.append_pass_record("trade-run", "dispatch", "d_test", {"approved": apf, "deliverable": apf})
        recs = sg.read_pass_records("trade-run", "dispatch")
        check("통과 기록 — 승인 파일 해시가 서명되어 읽힌다", len(recs) == 1 and recs[0]["approved_sha256"] == want, str(recs)[:200])
        sg.append_pass_record("trade-run", "execute", "d_test", {})
        check("다음 단계 통과가 dispatch 기록을 지우지 않는다(덧붙이기)",
              len(sg.read_pass_records("trade-run", "dispatch")) == 1 and len(sg.read_pass_records("trade-run")) == 2, "")
        path = sg.pass_record_path("trade-run", "d_test")
        lines = open(path, encoding="utf-8").read().splitlines()
        forged = json.loads(lines[0]); forged["approved_sha256"] = "0" * 64
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(forged, ensure_ascii=False) + "\n")
        check("손으로 고친 기록(해시 위조)은 읽지 않는다",
              [r["approved_sha256"] for r in sg.read_pass_records("trade-run", "dispatch")] == [want], "")
        old = json.loads(lines[0]); old["epoch"] = 1; old["sig"] = sg._pass_sig(old)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(old, ensure_ascii=False) + "\n")
        check("만료(12시간 넘은) 기록은 읽지 않는다", len(sg.read_pass_records("trade-run", "dispatch")) == 1, "")
        sg.remove_ledger("trade-run", "d_test")
        check("init/clear(remove_ledger)가 통과 기록을 지운다", sg.read_pass_records("trade-run") == [], "")
        # 같은 체크포인트 재판정은 순서 위반이 아니다 — 토큰이 dispatch여도 dispatch를 다시 받을 수 있다(after=note)
        sg.write_token("trade-run", "dispatch", "d_test")
        tok = sg.read_token("trade-run", "d_test")
        check("재판정 허용 조건 — 지금 토큰이 같은 cp PASS", tok and tok.get("cp") == "dispatch" and not tok.get("_invalid"), str(tok))
        src = open(os.path.join(HERE, "stepgate.py"), encoding="utf-8").read()
        check("cmd_judge가 같은 cp 재판정을 순서 위반으로 막지 않는다", "again = bool(tok) and not bad and str(tok.get(\"cp\")) == str(cp)" in src, "")
        sg.remove_ledger("trade-run", "d_test")
    finally:
        if old_temp is None:
            os.environ.pop("TEMP", None)
        else:
            os.environ["TEMP"] = old_temp
finally:
    sg.RUBRIC_DIR = saved_dir
    shutil.rmtree(TMP, ignore_errors=True)

n_fail = sum(1 for ok, _, _ in results if not ok)
print(f"\nTEST-SUMMARY file=test_stepgate_engine.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
