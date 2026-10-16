# -*- coding: utf-8 -*-
"""코드 편집 감사 — '검사했다'는 자기 보고가 아니라 **디스크 기록**으로 확인한다.

왜(2026-10-13): skill-maintainer §2(백업 → code_edit_check → 테스트)를 문서로 정하고 verify 게이트에 문항을 넣었지만,
문항은 워커가 답하는 자기 보고라 검사를 안 돌리고 "돌렸다"고 답해도 막을 수 없다. 10-12 사고도 절차를 건너뛴 데서 났다.
그래서 `code_edit_check.py`가 실행마다 `_stepgate/code_edit_log.jsonl`에 남기는 기록과 파일의 지금 해시를 대조한다.
verify 게이트의 디스크 기준(`check: code_audit`)이 이 스크립트를 부른다 — 위반이 있으면 답과 무관하게 GATE FAIL.

판정 — 기준선(마지막 PASS 때의 파일 해시) 뒤에 바뀐 `.py`마다:
  ① 지금 해시와 같은 파일을 검사한 기록이 있고 종료코드 0인가(검사 뒤 또 고쳤으면 다시 검사해야 한다)
  ② 그 기록의 백업이 **같은 폴더**에 있는가(새 파일이면 `--new` 기록)
  ③ 가장 가까운 상위 폴더에 테스트 러너(`run_tests.py`)가 있으면, 그 러너의 파일별 마지막 기록이 전부 완주·실패 0이고
     파일 수정 시각 **뒤**에 돌았는가(러너가 없으면 경고만)

사용:
    python3 code_edit_audit.py                      # 감사만(기준선은 그대로)
    python3 code_edit_audit.py --commit             # PASS면 기준선을 지금 상태로 옮긴다(게이트가 쓴다)
    python3 code_edit_audit.py --adopt <파일> --why "<사유>"   # 다른 세션이 검사 없이 고친 파일을 확인한 뒤 인수(기록이 남는다)
    python3 code_edit_audit.py --init-baseline --why "<사유>"  # 처음 도입할 때 한 번 — 지금 해시를 기준선으로
종료코드: 0 = PASS · 1 = FAIL
"""
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))          # scripts → skill-maintainer → skills → .claude → repo
STEPGATE = os.path.abspath(os.path.join(HERE, "..", "..", "_stepgate"))
LOG = os.path.join(STEPGATE, "code_edit_log.jsonl")
STATE = os.path.join(STEPGATE, "code_edit_audit_state.json")
# 감사 범위 = §2의 대상: 모든 `.claude/skills` 폴더 + 스킬이 **호출하는** 프로젝트 코드(이 파일에 저장소 상대 경로로 등록).
# 스킬과 무관한 일회성 빌드 스크립트(산출물 생성용)는 대상이 아니다. 스킬이 새 프로젝트 코드를 부르게 되면 여기에 추가한다.
ROOTS_CFG = os.path.join(STEPGATE, "code_audit_roots.json")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "claude-skills-portfolio", "_raw_sources",
             "logs", "이전버전", "rubrics", "audit_rubrics"}
DEFAULT_WINDOW_H = 24


def sha12(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return "없음"


def is_backup(name: str) -> bool:
    return name.startswith("_") and "_backup_" in name


def default_roots() -> list:
    """모든 `.claude/skills`(저장소 최상위·한 단계·두 단계 아래) + `code_audit_roots.json`의 프로젝트 폴더."""
    import glob as _g
    roots = []
    for pat in (".claude/skills", "*/.claude/skills", "*/*/.claude/skills"):
        roots += [p for p in _g.glob(os.path.join(REPO, pat)) if "claude-skills-portfolio" not in p]
    try:
        with open(ROOTS_CFG, encoding="utf-8") as f:
            roots += [os.path.join(REPO, r) for r in (json.load(f).get("projects") or [])]
    except (OSError, json.JSONDecodeError):
        pass
    return [r for r in roots if os.path.isdir(r)]


def scan(roots=None) -> dict:
    """감사 대상 `.py` → (해시, 수정 시각). 백업·캐시·공개 레포 사본·원자료 폴더는 뺀다."""
    out = {}
    for root in roots or default_roots():
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".") or d == ".claude"]
            for fn in fns:
                if fn.endswith(".py") and not is_backup(fn):
                    p = os.path.join(dp, fn)
                    try:
                        out[p] = (sha12(p), os.path.getmtime(p))
                    except OSError:
                        continue
    return out


def _read_jsonl(path: str) -> list:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return rows


def _state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(files: dict, why: str):
    os.makedirs(STEPGATE, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"baseline_at": datetime.now(KST).isoformat(timespec="seconds"), "why": why,
                   "shas": {p: s for p, (s, _) in files.items()}}, f, ensure_ascii=False, indent=1)


def _log(row: dict):
    os.makedirs(STEPGATE, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def changed_files(files: dict, st: dict) -> list:
    """기준선 뒤에 바뀐 파일. 기준선이 없으면 최근 24시간에 고친 파일."""
    shas = st.get("shas") or {}
    try:
        base_t = datetime.fromisoformat(st["baseline_at"]).timestamp()
    except (KeyError, ValueError):
        base_t = (datetime.now(KST) - timedelta(hours=DEFAULT_WINDOW_H)).timestamp()
    out = []
    for p, (s, mt) in files.items():
        if p in shas:
            if shas[p] != s:
                out.append(p)
        elif mt > base_t:
            out.append(p)
    return sorted(out)


def _runner_for(path: str):
    """가장 가까운 상위 폴더의 run_tests.py (REPO 밖으로는 안 올라간다)."""
    d = os.path.dirname(path)
    while d.startswith(REPO):
        cand = os.path.join(d, "run_tests.py")
        if os.path.isfile(cand):
            return cand
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return None


def _runner_status(runner: str):
    """(TESTS, 파일별 마지막 행 dict) — 러너 모듈을 실행하지 않고 상수·원장만 읽는다."""
    spec = importlib.util.spec_from_file_location("_rt_audit", runner)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # run_tests.py는 main()이 __main__ 가드 안에 있다
    rows = mod._rows()
    last = {}
    for r in rows:
        last[r.get("file")] = r
    return list(mod.TESTS), last


def _nfc(x) -> str:
    """경로 문자열을 NFC로 — 셸 작업 폴더(getcwd)에서 만든 경로는 맥에서 NFD(한글 자모 분리)로 기록된다(2026-10-13:
    작업 루트 폴더 아래 파일 12개가 검사를 돌렸는데도 '검사 기록 없음'으로 나왔다)."""
    import unicodedata
    return unicodedata.normalize("NFC", str(x or ""))


def audit(roots=None, commit: bool = False, quiet: bool = False) -> tuple:
    """(ok, 요약 문자열). commit=True이고 PASS면 기준선을 지금 상태로 옮긴다."""
    files = scan(roots)
    st = _state()
    todo = changed_files(files, st)
    logs = _read_jsonl(LOG)
    for r in logs:
        r["after"] = _nfc(r.get("after"))
    lines, fails, warns = [], 0, 0
    runners = {}
    for p in todo:
        s, mt = files[p]
        rel = os.path.relpath(p, REPO)
        mine = [r for r in logs if r.get("after") == _nfc(p) and r.get("sha12") == s]
        ok_rows = [r for r in mine if r.get("rc") == 0 and (r.get("new") or r.get("same_dir") or r.get("adopted"))]
        if not mine:
            older = [r for r in logs if r.get("after") == _nfc(p)]
            why = "검사 뒤에 다시 고쳤다 — 다시 검사할 것" if older else "검사 기록 없음 — 백업 → code_edit_check.py"
            lines.append(f"  ✗ {rel} — {why}")
            fails += 1
            continue
        if not ok_rows:
            r = mine[-1]
            why = ("백업이 같은 폴더가 아니다" if r.get("rc") == 0 and not r.get("same_dir")
                   else f"검사 종료코드 {r.get('rc')}(미설명 {r.get('unexplained')} · 안 쓰인 --ok {r.get('unused_ok')})")
            lines.append(f"  ✗ {rel} — {why}")
            fails += 1
            continue
        r = ok_rows[-1]
        tag = "인수(" + str(r.get("why", ""))[:40] + ")" if r.get("adopted") else ("새 파일" if r.get("new") else "검사 0")
        if r.get("star_ok"):
            tag += f" · ★ 대입 경고를 --ok로 넘긴 줄 {r['star_ok']}"
            warns += 1
        runner = _runner_for(p)
        if runner:
            if runner not in runners:
                try:
                    runners[runner] = _runner_status(runner)
                except Exception as e:            # noqa: BLE001
                    runners[runner] = ("err", str(e)[:80])
            got = runners[runner]
            if got[0] == "err":
                lines.append(f"  ✗ {rel} — 테스트 러너를 못 읽었다: {got[1]}")
                fails += 1
                continue
            tests, last = got
            bad = []
            for t in tests:
                lr = last.get(t)
                if not lr or not lr.get("ok"):
                    bad.append(f"{t} {'기록 없음' if not lr else '완주·실패0 아님'}")
                    continue
                try:
                    if datetime.fromisoformat(lr["ts"]).timestamp() < mt:
                        bad.append(f"{t} 수정 전에 돈 기록")
                except (KeyError, ValueError):
                    bad.append(f"{t} 시각 없음")
            if bad:
                lines.append(f"  ✗ {rel} — {tag} · 테스트: " + "; ".join(bad) + f" ({os.path.relpath(runner, REPO)})")
                fails += 1
                continue
            tag += " · 테스트 완주"
        else:
            tag += " · 테스트 러너 없음(경고)"
            warns += 1
        lines.append(f"  ✓ {rel} — {tag}")
    ok = fails == 0
    head = (f"CODE-AUDIT {'PASS' if ok else 'FAIL'} — 기준선 뒤 바뀐 .py {len(todo)}개 · 위반 {fails} · 경고 {warns}"
            + (f" (기준선 {st.get('baseline_at', '없음 — 최근 24시간')})"))
    if ok and commit:
        _write_state(files, "code_edit_audit PASS")
    text = head + ("\n" + "\n".join(lines) if lines else "")
    if not quiet:
        print(text)
    return ok, text


def main() -> int:
    a = sys.argv[1:]
    why = ""
    if "--why" in a:
        i = a.index("--why")
        why = a[i + 1] if i + 1 < len(a) else ""
        del a[i:i + 2]
    if "--init-baseline" in a:
        if not why:
            print("--init-baseline에는 --why \"<사유>\"가 필요하다")
            return 2
        files = scan()
        st = _state()
        uncovered = [os.path.relpath(p, REPO) for p in changed_files(files, st)]
        _write_state(files, why)
        _log({"ts": datetime.now(KST).isoformat(timespec="seconds"), "init_baseline": True, "why": why,
              "uncovered": uncovered})
        print(f"기준선 설정 — .py {len(files)}개 · 검사 기록 없이 흡수한 최근 변경 {len(uncovered)}개(로그에 목록): {why}")
        return 0
    if "--adopt" in a:
        i = a.index("--adopt")
        p = os.path.abspath(a[i + 1]) if i + 1 < len(a) else ""
        if not p or not os.path.isfile(p) or not why:
            print("--adopt <파일> --why \"<무엇을 확인했나>\"")
            return 2
        _log({"ts": datetime.now(KST).isoformat(timespec="seconds"), "after": p, "sha12": sha12(p), "rc": 0,
              "adopted": True, "why": why})
        print(f"인수 기록 — {os.path.relpath(p, REPO)} ({why})")
        return 0
    ok, _ = audit(commit="--commit" in a)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
