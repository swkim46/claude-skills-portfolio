#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""세상공부 스크립트의 자체 시험을 끝까지 돌리고 **완주했는지**를 기록한다.

    python3 run_tests.py            # 전부 돌린다 → test_logs/test_runs.jsonl 한 줄씩 · test_logs/tests_<시각>_<이름>.txt · 한 줄 요약
    python3 run_tests.py --last     # 돌리지 않고 마지막 기록만

왜 있는가 (2026-10-14): `check_note.py`·`fetch_article.py`를 고친 뒤 시험을 돌렸는지 기록이 없었다. skill-maintainer의
코드 편집 감사(`code_edit_audit.py`)가 이 러너의 원장으로 '고친 뒤 시험이 완주했는가'를 확인한다 — ai-trading-bot 러너와 같은 형태
(`TESTS` · `_rows()` · `__main__` 가드)를 지켜야 감사가 읽는다.

판정: **완주** = 시간 초과 없이 끝났고 출력 마지막 줄이 `통과` 또는 `실패`다. 실패 건수는 `✗`로 시작하는 줄 수.
실패·멈춤·건수 감소(지난 최대보다 ✓+✗ 줄이 적음) 중 하나라도 있으면 종료코드 1.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "test_logs"
LEDGER = LOGS / "test_runs.jsonl"
KST = timezone(timedelta(hours=9))
TESTS = ["check_note.py --selftest", "cache_claims.py selftest", "fetch_article.py --selftest"]
TIMEOUT_S = 300          # 셋 다 오프라인 — 정상은 몇 초
STALE_DAYS = 7

RX_CASE = re.compile(r"^\s*([✓✗]) (.+)$", re.M)


def _rows() -> list:
    if not LEDGER.exists():
        return []
    out = []
    for ln in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out


def run_one(name: str, prev_max: int) -> dict:
    t0 = time.time()
    row = {"ts": datetime.now(KST).isoformat(timespec="seconds"), "file": name}
    try:
        p = subprocess.run([sys.executable] + shlex.split(name), cwd=HERE, capture_output=True, text=True,
                           timeout=TIMEOUT_S)
        out, code, timed_out = (p.stdout or "") + "\n" + (p.stderr or ""), p.returncode, False
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode("utf-8", "ignore") if isinstance(e.stdout, bytes) else (e.stdout or "")) + \
              ((e.stderr or b"").decode("utf-8", "ignore") if isinstance(e.stderr, bytes) else (e.stderr or ""))
        code, timed_out = None, True
    row["seconds"] = round(time.time() - t0, 1)
    row["exit"] = code
    cases = RX_CASE.findall(out)
    last = next((l.strip() for l in reversed(out.splitlines()) if l.strip()), "")
    row["total"] = len(cases)
    row["failed"] = sum(1 for m, _n in cases if m == "✗")
    row["completed"] = not timed_out and last in ("통과", "실패")
    why = []
    if timed_out:
        why.append(f"{TIMEOUT_S}초 시간 초과로 끊었다")
    elif not row["completed"]:
        why.append("마지막 줄이 '통과'·'실패'가 아니다 — 중간에 죽었다: " + last[:80])
    if row["completed"] and prev_max and row["total"] < prev_max:
        why.append(f"건수 감소 {prev_max} → {row['total']} — 케이스가 조용히 빠졌을 수 있다")
    if row["completed"] and last == "실패" and not row["failed"]:
        why.append("'실패'로 끝났는데 ✗ 줄이 없다")
    if row["failed"]:
        row["fail_names"] = [n.strip()[:80] for m, n in cases if m == "✗"][:20]
    row["why"] = why
    row["ok"] = row["completed"] and code == 0 and not row["failed"] and not why
    LOGS.mkdir(exist_ok=True)
    log = LOGS / f"tests_{datetime.now(KST).strftime('%y%m%d_%H%M%S')}_{Path(shlex.split(name)[0]).stem}.txt"
    log.write_text(out, encoding="utf-8")
    row["log"] = str(log.relative_to(HERE))
    return row


def line_for(r: dict) -> str:
    if r["ok"]:
        return f"{r['file']} {r['total']}/{r['total']} 완주"
    tags = [] if r["completed"] else ["★멈춤"]
    if r.get("failed"):
        tags.append(f"실패 {r['failed']}")
    return f"{r['file']} {r['total'] - (r['failed'] or 0)}/{r['total']} " + " · ".join(tags + r.get("why", []))


def cmd_run() -> int:
    rows = _rows()
    out, bad = [], 0
    for name in TESTS:
        prev = max([r.get("total") or 0 for r in rows if r.get("file") == name and r.get("completed")] or [0])
        r = run_one(name, prev)
        LOGS.mkdir(exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        out.append(line_for(r))
        for n in r.get("fail_names", [])[:8]:
            out.append("  ✗ " + n)
        bad += not r["ok"]
    print(("[테스트] " if not bad else "★★ [테스트 이상] ") + "\n  ".join(out))
    return 1 if bad else 0


def last_line() -> str:
    rows = _rows()
    now = datetime.now(KST)
    parts, warn = [], False
    for name in TESTS:
        mine = [r for r in rows if r.get("file") == name]
        if not mine:
            parts.append(f"{name} 기록 없음")
            warn = True
            continue
        r = mine[-1]
        try:
            age = (now - datetime.fromisoformat(r["ts"])).days
        except (KeyError, ValueError):
            age = None
        stale = age is None or age > STALE_DAYS
        warn |= stale or not r.get("ok")
        parts.append(line_for(r) + f" ({str(r.get('ts'))[:16]})" + (" — 오래됐다" if stale else ""))
    return ("★ " if warn else "") + "[테스트] " + " · ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--last", action="store_true", help="돌리지 않고 마지막 기록만")
    a = ap.parse_args()
    if a.last:
        print(last_line())
        return 0
    return cmd_run()


if __name__ == "__main__":
    sys.exit(main())
