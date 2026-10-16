# -*- coding: utf-8 -*-
"""코드 편집 검사 — 백업(before) 대비 편집본(after)에서 **사라진 줄**을 함수별로 보여 주고, 설명 안 된 줄이 있으면 실패한다.

왜(2026-10-13 사고): 스크립트로 코드 블록을 통째 치환하면서 새 블록에 원래 줄 하나(`res = place(client, o)`)를 안 옮겼다.
문법 검사도 테스트 130건도 통과했고, 그 함수를 아무 테스트도 부르지 않아 예약 run의 주문 전송이 전부 죽었다. 사라진 줄
대조를 돌렸다면 `-[1] L440 res = place(client, o)`가 바로 보였다. skill-maintainer §2(코드 수정)의 필수 단계다.

사용:
    python3 code_edit_check.py <before.py> <after.py> [--ok <부분 문자열>]... [--quiet]
    python3 code_edit_check.py --new <file.py>          # 새로 만든 파일(백업이 없다) — 컴파일만 보고 기록한다

- 사라진 줄마다 `--ok "<그 줄의 일부>"`로 "의도한 삭제·교체"임을 밝힌다. 하나라도 대응이 없으면 종료코드 1.
- `--ok`가 어떤 사라진 줄과도 맞지 않으면 그것도 1(잘못 적은 설명은 설명이 아니다).
- 종료코드: 0 = 사라진 줄 0이거나 전부 설명됨 · 1 = 설명 안 된 줄(또는 안 쓰인 --ok) · 2 = 컴파일 실패·사용법.

한계: 줄 비교는 lossless_assert와 같은 '공백 제거한 비어 있지 않은 줄의 다중집합'이다 — 들여쓰기만 바뀐 줄, 함수 사이로
옮긴 줄은 보이지 않는다. 그래서 이 검사는 테스트를 대신하지 않는다(그 프로젝트의 테스트 러너를 반드시 같이 돌린다).
"""
import ast
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lossless_assert import lines_ms  # noqa: E402  — 같은 줄 비교 규칙을 공유한다

ASSIGN = re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=]+)?=(?!=)")
# ★ 실행마다 기록을 남긴다 — `code_edit_audit.py`(verify 게이트의 디스크 기준)가 "수정된 파일마다 그 수정 뒤 통과한 검사가 있는가"를
#   이 기록과 파일 해시로 대조한다. 자기 보고("돌렸다")가 아니라 디스크 증거로 확인하기 위해서다(2026-10-13).
LOG = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "_stepgate",
                                   "code_edit_log.jsonl"))


def _log(row: dict) -> None:
    import hashlib
    import json
    from datetime import datetime, timedelta, timezone
    import unicodedata
    for k in ("after", "before"):                # 맥 getcwd 경로는 NFD — 감사(code_edit_audit)와 같은 NFC로 남긴다(2026-10-13)
        if row.get(k):
            row[k] = unicodedata.normalize("NFC", row[k])
    try:
        with open(row["after"], "rb") as f:
            row["sha12"] = hashlib.sha256(f.read()).hexdigest()[:12]
        row["ts"] = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"(검사 기록을 못 남겼다: {e} — code_edit_audit가 이 검사를 못 본다)")


def _intervals(tree) -> list:
    """(시작, 끝, qualname, 노드) — 함수·클래스 구간. 중첩은 `Class.method`."""
    out = []

    def walk(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = f"{prefix}.{ch.name}" if prefix else ch.name
                out.append((ch.lineno, getattr(ch, "end_lineno", ch.lineno), q, ch))
                walk(ch, q)
            else:
                walk(ch, prefix)
    walk(tree, "")
    return out


def _owner(intervals, lineno) -> tuple:
    """그 줄을 품은 가장 안쪽 함수·클래스 (qualname, 시작, 끝). 없으면 ('<module>', None, None)."""
    best = None
    for s, e, q, _ in intervals:
        if s <= lineno <= e and (best is None or s >= best[1]):
            best = (q, s, e)
    return best or ("<module>", None, None)


def _loaded_names(node) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _parse(path):
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        return src, ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return None, None


def main() -> int:
    argv = sys.argv[1:]
    quiet = "--quiet" in argv
    argv = [a for a in argv if a != "--quiet"]
    oks = []
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--ok":
            if i + 1 >= len(argv):
                print("--ok 뒤에 부분 문자열이 필요하다")
                return 2
            oks.append(argv[i + 1])
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    if rest and rest[0] == "--new":
        if len(rest) != 2 or not os.path.isfile(rest[1]):
            print("--new <새 파일.py>")
            return 2
        new_p = os.path.abspath(rest[1])
        try:
            with open(new_p, encoding="utf-8") as f:
                compile(f.read(), new_p, "exec")
        except SyntaxError as e:
            print(f"[compile] {os.path.basename(new_p)} 실패 — {e}")
            _log({"after": new_p, "before": None, "new": True, "rc": 2})
            return 2
        print(f"[compile] {os.path.basename(new_p)} OK · 새 파일로 기록(사라질 줄 없음)")
        _log({"after": new_p, "before": None, "new": True, "rc": 0, "removed": 0, "unexplained": 0, "unused_ok": 0})
        return 0
    if len(rest) != 2:
        print(__doc__.split("사용:")[1].split("- 사라진")[0].strip())
        return 2
    before_p, after_p = rest
    for p in (before_p, after_p):
        if not os.path.isfile(p):
            print(f"파일 없음: {p}")
            return 2

    # ① 편집본이 컴파일되는가 — pyc를 남기지 않도록 compile()로 본다
    if after_p.endswith(".py"):
        try:
            with open(after_p, encoding="utf-8") as f:
                compile(f.read(), after_p, "exec")
            print(f"[compile] {os.path.basename(after_p)} OK")
        except SyntaxError as e:
            print(f"[compile] {os.path.basename(after_p)} 실패 — {e}")
            return 2

    # ② 사라진 줄·더해진 줄
    before, after = lines_ms(before_p), lines_ms(after_p)
    removed, added = before - after, after - before
    print(f"[lines] before {sum(before.values())} / after {sum(after.values())} · "
          f"removed {sum(removed.values())} · added {sum(added.values())}")

    # ③ 사라진 줄의 위치(before 줄 번호·소속 함수)와 ④ 힌트
    b_src, b_tree = _parse(before_p) if before_p.endswith(".py") else (None, None)
    a_src, a_tree = _parse(after_p) if after_p.endswith(".py") else (None, None)
    b_lines = (b_src or open(before_p, encoding="utf-8").read()).splitlines()
    b_iv = _intervals(b_tree) if b_tree else []
    a_iv = {q: node for _, _, q, node in (_intervals(a_tree) if a_tree else [])}
    a_lines = (a_src or "").splitlines()

    def _added_assigns(qual: str, name: str) -> list:
        """after의 같은 함수 안에서 새로 생긴(added) `name = …` 줄 — 있으면 교체로 본다."""
        node = a_iv.get(qual)
        if node is None:
            return []
        body = a_lines[node.lineno - 1:getattr(node, "end_lineno", node.lineno)]
        out = []
        for ln in body:
            s = ln.strip()
            m2 = ASSIGN.match(s)
            if m2 and m2.group(1) == name and added.get(s):
                out.append(s)
        return out

    groups = {}                                   # qualname → [(줄 번호들, 줄, 개수, 힌트)]
    for text, n in removed.most_common():
        nos = [k + 1 for k, ln in enumerate(b_lines) if ln.strip() == text]
        owner = _owner(b_iv, nos[0]) if nos else ("<module>", None, None)
        hint = ""
        m = ASSIGN.match(text)
        if m and owner[0] != "<module>" and owner[0] in a_iv:
            name = m.group(1)
            if name in _loaded_names(a_iv[owner[0]]):
                repl = _added_assigns(owner[0], name)
                if repl:
                    hint = f"(같은 함수에 '{name}' 대입이 새로 생겼다 — 교체로 보임: {repl[0][:80]})"
                else:
                    hint = (f"★ '{name}' 대입이 사라졌는데 같은 함수에서 아직 읽힌다 — 다른 대입이 모든 경로를 덮지 않으면 "
                            f"UnboundLocalError")
        groups.setdefault(owner, []).append((nos, text, n, hint))

    unexplained, used, star_ok = 0, set(), 0
    if removed:
        print("\nREMOVED — before의 함수별")
        for (q, s, e), rows in sorted(groups.items(), key=lambda kv: (kv[0][1] or 0)):
            print(f"  {q}" + (f" (L{s}–{e})" if s else ""))
            for nos, text, n, hint in rows:
                hit = [o for o in oks if o in text]
                used.update(hit)
                mark = "ok" if hit else "!!"
                if not hit:
                    unexplained += n
                elif hint.startswith("★"):
                    star_ok += n                  # ★ 대입 경고를 --ok로 넘긴 줄 — 감사가 경고로 보고한다
                where = ",".join(f"L{x}" for x in nos[:4]) + ("…" if len(nos) > 4 else "")
                print(f"    {mark} -[{n}] {where}  {text[:150]}")
                if hint:
                    print(f"           {hint}")
    unused = [o for o in oks if o not in used]
    if added and not quiet:
        print(f"\nADDED (상위 40)")
        for text, n in added.most_common(40):
            print(f"  +[{n}] {text[:150]}")
    for o in unused:
        print(f"\n★ --ok {o!r}는 어떤 사라진 줄과도 맞지 않는다 — 설명을 다시 적어라")
    rc = 1 if (unexplained or unused) else 0
    print(f"\nVERDICT: REMOVED {sum(removed.values())} · 미설명 {unexplained}"
          + (f" · 안 쓰인 --ok {len(unused)}" if unused else "")
          + f" → exit {rc}"
          + ("  (의도한 삭제·교체면 --ok \"<그 줄 일부>\"를 붙여 다시)" if unexplained else ""))
    a_abs, b_abs = os.path.abspath(after_p), os.path.abspath(before_p)
    _log({"after": a_abs, "before": b_abs, "same_dir": os.path.dirname(a_abs) == os.path.dirname(b_abs),
          "removed": sum(removed.values()), "unexplained": unexplained, "unused_ok": len(unused),
          "star_ok": star_ok, "rc": rc})
    return rc


if __name__ == "__main__":
    sys.exit(main())
