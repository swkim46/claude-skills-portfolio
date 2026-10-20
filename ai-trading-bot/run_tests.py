#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""테스트를 끝까지 돌리고 **완주했는지**를 기록한다 — 멈춘 테스트를 모르고 지나가지 않게.

    python3 run_tests.py            # 전부 돌린다 → 저장소 op_test_run 한 행씩(db.log_append('test_runs')) · logs/tests_<시각>.txt · 한 줄 요약
    python3 run_tests.py --last     # 돌리지 않고 마지막 기록만 보여 준다(1단 캡처·토요일 피드백이 읽는다)

왜 있는가 (2026-10-11 사용자: "테스트가 멈추지 않도록 세팅해 · 테스트 멈추는 걸 모르지 않도록"):
`test_risk_guard.py`의 (66)-b가 검사 대상 코드의 sys.exit에 걸려 프로세스가 끝났고, 그 뒤 케이스가 10-03부터
일주일 동안 한 번도 돌지 않았다. 출력 끝에 요약 줄이 없었지만 아무도 세지 않았다.

판정:
- **완주** = 출력에 `TEST-SUMMARY`가 있고 `TEST-HALTED`가 없다. 테스트 파일은 케이스마다 예외·SystemExit를 잡아 FAIL로 적고
  다음으로 가므로, 완주하지 못했다는 것은 격리 밖에서 끝났거나(TEST-HALTED) 시간 초과로 죽였다는 뜻이다.
- **건수 감소** = 이번 총 건수가 그 파일의 지난 최대보다 작다 — 케이스가 조용히 빠졌을 수 있다.
- 실패·멈춤·건수 감소 중 하나라도 있으면 종료코드 1.
- **집행 경로 코드 변경**(2026-10-13) = 마지막 완주 기록이 본 `execute.py`·`fill.py`·`stops.py`·`risk_guard.py`·`kis_client.py`의
  해시가 지금과 다르다 — 고친 뒤 테스트를 안 돌렸다는 뜻이다. `--last`가 ★로 알리고 1단 캡처의 `### 테스트` 줄에 그대로 실린다.
  (10-12 20:56 편집이 전송 줄을 지운 사고의 재발 방지 — 그때는 편집 뒤 테스트가 돌았지만 전송 경로를 덮는 테스트가 없었다:
  그 구멍은 `test_execute_path.py`가 막고, 이 해시는 '고치고 안 돌린 채 run이 시작되는' 구멍을 막는다.)
- **테스트마다 빈 임시 저장소**(2026-10-17) = 자식 프로세스에 `AITB_DB`(새 임시 경로)·`AITB_TESTING=1`을 준다 — 테스트는 실제
  `journal/trading.db`를 열 수 없다(`db._guard`). 완주 기록은 이 부모 프로세스(AITB_DB 없음)가 실제 저장소에 쓴다.
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 다른 폴더에서 이 모듈을 불러와도(코드 편집 감사가 완주 기록을 `_rows()`로 읽는다) 같은 폴더의 저장소 모듈을 찾게 한다
sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

HERE = Path(__file__).resolve().parent
LEDGER = "test_runs"                     # 저장소 로그 이름(op_test_run) — 예전 journal/test_runs.jsonl
LOGS = HERE / "logs"
KST = timezone(timedelta(hours=9))
TESTS = ["test_risk_guard.py", "test_regret.py", "test_execute_path.py", "test_track1.py", "test_levels.py", "test_sessions.py", "test_realweek.py", "test_switch.py",
         "test_db.py", "test_facts.py", "test_migrate.py", "test_judgment.py", "test_reports.py", "test_notes.py"]   # 저장소·사실 층·이전·판단·보고(2026-10-17)   # test_track1 = 주문 근거 검증·노트 필수 요소(2026-10-13)
# 주문이 실제로 나가는 경로 — 이 파일들이 마지막 완주 뒤 바뀌었으면 `--last`가 ★로 알린다.
EXEC_FILES = ["execute.py", "fill.py", "stops.py", "risk_guard.py", "kis_client.py",
              "portfolio.py"]    # 2026-10-19 Codex R06 — risk_guard의 통합자산 분모가 여기서 나온다
TIMEOUT_S = 900          # 정상은 1분 안팎 — 이 시간을 넘기면 멈춘 것으로 보고 끊는다
STALE_DAYS = 7           # 마지막 완주 기록이 이보다 오래되면 '안 돌았다'로 알린다

RX_SUM = re.compile(r"^TEST-SUMMARY file=(\S+) total=(\d+) passed=(\d+) failed=(\d+)", re.M)
RX_HALT = re.compile(r"^TEST-HALTED file=(\S+) ran=(\d+) failed=(\d+) last=(.*)$", re.M)


def _rows() -> list:
    """완주 기록 전건(기록 순서)."""
    return db.log_rows(LEDGER)


def _src_hashes() -> dict:
    """집행 경로 파일의 sha256 앞 12자. 없는 파일은 '없음'."""
    out = {}
    for name in EXEC_FILES:
        p = HERE / name
        try:
            out[name] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        except OSError:
            out[name] = "없음"
    return out


def run_one(name: str, prev_max: int, src: dict = None) -> dict:
    t0 = time.time()
    row = {"ts": datetime.now(KST).isoformat(timespec="seconds"), "file": name}
    if src:
        row["src"] = src
    # 테스트 파일마다 빈 임시 저장소 — 실제 저장소(원장·현재 run 포인터)를 쓰거나 읽지 않게(2026-10-14 포인터 사고 · 2026-10-17 저장소)
    tmp_db = Path(tempfile.mkdtemp(prefix="aitb_test_db_")) / "trading.db"
    env = {k: v for k, v in os.environ.items() if k not in ("AITB_CURRENT_RUN", "AITB_EXPORT_REAL")}
    env.update({"AITB_DB": str(tmp_db), "AITB_TESTING": "1"})
    try:
        p = subprocess.run([sys.executable, name], cwd=HERE, capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
        out, code = (p.stdout or "") + "\n" + (p.stderr or ""), p.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode("utf-8", "ignore") if isinstance(e.stdout, bytes) else (e.stdout or "")) + \
              ((e.stderr or b"").decode("utf-8", "ignore") if isinstance(e.stderr, bytes) else (e.stderr or ""))
        code, timed_out = None, True
    shutil.rmtree(tmp_db.parent, ignore_errors=True)
    row["seconds"] = round(time.time() - t0, 1)
    row["exit"] = code
    s, h = RX_SUM.search(out), RX_HALT.search(out)
    row["total"] = int(s.group(2)) if s else (int(h.group(2)) if h else None)
    row["failed"] = int(s.group(4)) if s else (int(h.group(3)) if h else None)
    row["completed"] = bool(s) and not h and not timed_out
    why = []
    if timed_out:
        why.append(f"{TIMEOUT_S}초 시간 초과로 끊었다 — 어딘가에서 멈췄다(무한 대기·네트워크)")
    if h:
        why.append(f"중간에 멈췄다 — 마지막 케이스 {h.group(4).strip()} · 그때까지 {h.group(2)}건")
    if not s and not h and not timed_out:
        why.append("요약 줄(TEST-SUMMARY)이 없다 — 시작부터 죽었거나 출력이 잘렸다")
    if row["completed"] and prev_max and row["total"] is not None and row["total"] < prev_max:
        why.append(f"건수 감소 {prev_max} → {row['total']} — 케이스가 조용히 빠졌을 수 있다")
    if row.get("failed"):
        fails = re.findall(r"^\s*FAIL (.+)$|^\s*✗ (.+)$", out, re.M)
        row["fail_names"] = [a or b for a, b in fails][:20]
    row["why"] = why
    row["ok"] = row["completed"] and not row.get("failed") and not why
    LOGS.mkdir(exist_ok=True)
    log = LOGS / f"tests_{datetime.now(KST).strftime('%y%m%d_%H%M%S')}_{Path(name).stem}.txt"
    log.write_text(out, encoding="utf-8")
    try:
        row["log"] = str(log.relative_to(HERE))
    except ValueError:
        row["log"] = str(log)
    return row


def line_for(r: dict) -> str:
    if r["ok"]:
        return f"{r['file']} {r['total']}/{r['total']} 완주"
    head = f"{r['file']} " + (f"{(r['total'] or 0) - (r['failed'] or 0)}/{r['total']}" if r.get("total") is not None else "?")
    tags = []
    if not r["completed"]:
        tags.append("★멈춤")
    if r.get("failed"):
        tags.append(f"실패 {r['failed']}")
    return head + " " + " · ".join(tags + r.get("why", []))


def cmd_run() -> int:
    rows = _rows()
    out, bad = [], 0
    src = _src_hashes()                  # 시작 시점에 잰다 — 테스트 도중 고친 파일은 다음 --last가 '바뀜'으로 잡는다
    for name in TESTS:
        prev = max([r.get("total") or 0 for r in rows if r.get("file") == name and r.get("completed")] or [0])
        r = run_one(name, prev, src)
        db.log_append(LEDGER, r)
        out.append(line_for(r))
        bad += not r["ok"]
    print(("[테스트] " if not bad else "★★ [테스트 이상] ") + " · ".join(out))
    return 1 if bad else 0


def last_line() -> str:
    """마지막 기록 한 줄 — 파일별 최신 행 + 경과 일수. 기록이 없거나 오래됐거나 멈췄으면 앞에 ★."""
    rows = _rows()
    now = datetime.now(KST)
    parts, warn = [], False
    for name in TESTS:
        mine = [r for r in rows if r.get("file") == name]
        if not mine:
            parts.append(f"{name} 기록 없음 — `python3 run_tests.py`를 돌린 적이 없다")
            warn = True
            continue
        r = mine[-1]
        try:
            age = (now - datetime.fromisoformat(r["ts"])).days
        except (KeyError, ValueError):
            age = None
        stale = age is None or age > STALE_DAYS
        warn |= stale or not r.get("ok")
        parts.append(line_for(r) + f" ({str(r.get('ts'))[:16]}" + (f" · {age}일 전" if age is not None else "") + ")"
                     + (" — 오래됐다, 다시 돌릴 것" if stale else ""))
    # ★ 마지막 완주(ok) 뒤 집행 경로 코드가 바뀌었는가 — 해시를 남긴 행이 있을 때만 비교한다.
    last_ok = next((r for r in reversed(rows) if r.get("ok") and isinstance(r.get("src"), dict)), None)
    if last_ok:
        cur = _src_hashes()
        changed = [f for f in EXEC_FILES if cur.get(f) != last_ok["src"].get(f)]
        if changed:
            warn = True
            parts.append(f"★ 집행 경로 코드가 마지막 완주 뒤 바뀜: {', '.join(changed)} — python3 run_tests.py")
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
