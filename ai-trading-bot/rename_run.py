#!/usr/bin/env python3
"""
한 run의 스탬프를 통째로 바꾼다 — 파일 이름 · 원장 참조 · 회고·자산 곡선의 세션 날짜. 기본은 건조 실행(바꿀 목록만).

왜(2026-10-13 밤 사용자 결정): 날짜는 거래하는 세션의 현지 날짜로 통일한다(2026-10-14 전환). 과거 파일은 그대로 두되
전환 직전 미국 두 run만 제 이름으로 바꾼다 — `261013_us`(실제 10-12 미국 세션) → `261012_us`, 그다음
`261014_us`(실제 10-13 세션) → `261013_us`. 그래야 10-14 밤 미국 run이 `261014`로 겹치지 않고 시작한다.

    python3 rename_run.py --from 261013_us --to 261012_us --also 섹터_반도체_에너지_전력_금융_261013_v1_0.md          # 건조 실행
    python3 rename_run.py --from 261013_us --to 261012_us --also 섹터_반도체_에너지_전력_금융_261013_v1_0.md --apply  # 적용

규칙
- 대상: `analysis/` `journal/` `signals/` `data/` 아래 전부(이전버전·_superseded 포함). 이름에 `<from>`이 든 파일과
  `--also`로 지정한 시장 표기 없는 파일(섹터 리서치)을 바꾼다. **목적지가 이미 있으면 `_b`를 붙이고** 그 이름으로 참조를 고친다.
- 내용 치환(문자열): `<st>_<mkt>` · `<mkt>:<st>` · `<st>-<mkt>`(배분 id) · `US <st>` · `<st> · US` · `<st> US` ·
  `"stamp": "<st>", "market": "<mkt>"` · `--also` 파일 이름. 시나리오 id(`SC-<st>-N`)는 **바꾸지 않는다** — 영구 id다.
- 행 단위: 시나리오 `opened`(그 run이 연 행만) · 회고 `session`(그 run의 시그널을 채점한 행) · 자산 곡선 `date`
  (그 run이 돈 시간대의 그 시장 행 — 스탬프 날짜로 적혀 있던 것만).
- 건드리지 않는 것: `fetch_log.jsonl`(스탬프 칸 없음) · `sessions.jsonl`(`sessions.py scan`이 다시 만든다) · `rename_log.jsonl`.
- **감사 원장 `_stepgate/promotion_ledger.jsonl`의 `run` 이름표도 바꾼다**(문자열 치환만) — `fc_track1.audit_rounds`가 그 이름표로
  주문 근거 감사 회차를 세고 `SG findings`가 run 수를 센다. 안 바꾸면 같은 스탬프를 새로 받은 run이 옛 run의 감사 FAIL을 자기 회차로
  센다(2026-10-14 발견 — 계획에서는 '라벨일 뿐'으로 뺐다가 되돌림). `--ledger-only`는 원장만 바꾼다(파일은 이미 바꾼 뒤).
- **파일 시각을 보존한다**(`os.utime`) — `sessions.first_touch`가 mtime으로 run 시작 시각을 잰다.
- 적용 전 손댈 파일을 전부 `_rename_snapshots/<from>_to_<to>/`에 복사(시각 보존)하고 `journal/rename_log.jsonl`에 매핑을 남긴다.
- 적용 뒤 옛 표기가 남았는지 센다 — 남으면 종료코드 1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
DIRS = ("analysis", "journal", "signals", "data")
TEXT_EXT = {".md", ".json", ".jsonl", ".txt", ".log", ".csv", ".html"}
SKIP_NAMES = {"fetch_log.jsonl", "sessions.jsonl", "rename_log.jsonl"}
SNAP_ROOT = "_rename_snapshots"
LEDGER = HERE.parent.parent / ".claude" / "skills" / "_stepgate" / "promotion_ledger.jsonl"   # 감사 원장(run 이름표)
RUN_WINDOW_H = 12          # 자산 곡선 행을 이 run 것으로 볼 시간 창(첫 파일 시각부터)


def _iso(st: str) -> str:
    return f"20{st[:2]}-{st[2:4]}-{st[4:6]}"


def _files(root: Path):
    for d in DIRS:
        base = root / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and SNAP_ROOT not in p.parts:
                yield p


def plan(root: Path, frm: str, to: str, also: list, started: datetime = None, ledger: Path = None,
         ledger_only: bool = False) -> dict:
    m1 = re.fullmatch(r"(\d{6})_(kr|us)", frm)
    m2 = re.fullmatch(r"(\d{6})_(kr|us)", to)
    if not (m1 and m2) or m1.group(2) != m2.group(2):
        raise SystemExit("--from·--to는 같은 시장의 <YYMMDD>_<kr|us>여야 한다")
    F, T, mk = m1.group(1), m2.group(1), m1.group(2)
    MK = mk.upper()
    files = [] if ledger_only else list(_files(root))
    names = {p.name for p in files}

    # 1) 이름 바꾸기
    renames, collide = [], []
    for p in files:
        new = None
        if frm in p.name:
            new = p.name.replace(frm, to)
        elif p.name in also:
            new = p.name.replace(F, T)
        if not new:
            continue
        dest = p.with_name(new)
        if dest.exists():
            alt = p.with_name(f"{dest.stem}_b{dest.suffix}")
            if alt.exists():
                raise SystemExit(f"목적지가 둘 다 있다: {dest.name} · {alt.name} — 중단")
            collide.append((p.stem, alt.stem))
            dest = alt
        renames.append((p, dest))
    for a in also:
        if a not in names and not ledger_only:
            raise SystemExit(f"--also 파일이 없다: {a}")

    # 2) 내용 치환 — 충돌·--also 파일의 이름(구체적인 것)부터, 그다음 일반 표기
    subs = [(re.escape(o), n) for o, n in collide]
    subs += [(re.escape(Path(a).stem), Path(a).stem.replace(F, T)) for a in also]
    subs += [
        (re.escape(frm), to),
        (rf"\b{mk}:{F}\b", f"{mk}:{T}"),
        (rf"\b{F}-{mk}\b", f"{T}-{mk}"),
        (rf"\b{MK} {F}\b", f"{MK} {T}"),
        (rf"\b{F} · {MK}\b", f"{T} · {MK}"),
        (rf"\b{F} {MK}\b", f"{T} {MK}"),
        (re.escape(f'"stamp": "{F}", "market": "{mk}"'), f'"stamp": "{T}", "market": "{mk}"'),
    ]
    rx = [(re.compile(p), n) for p, n in subs]
    old_iso, new_iso = _iso(F), _iso(T)
    if started is None and not ledger_only:
        import sessions as _ss
        started = _ss.first_touch(F, mk)
    lo = (started - timedelta(minutes=10)) if started else None
    hi = (started + timedelta(hours=RUN_WINDOW_H)) if started else None

    edits = {}
    for p in files:
        if p.suffix not in TEXT_EXT or p.name in SKIP_NAMES:
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new, n = txt, 0
        for r, rep in rx:
            new, k = r.subn(rep, new)
            n += k
        rows = 0
        if p.name == "scenarios.json" and p.parent.name == "journal":
            new, rows = _scenario_opened(new, mk, F, T)
        elif p.name == "reviews.jsonl":
            new, rows = _jsonl_rows(new, lambda r: (str(r.get("market", "")).upper() == MK
                                                    and str(r.get("signal", "")).startswith(f"signal_{T}_{mk}")
                                                    and r.get("session") == old_iso),
                                    "session", old_iso, new_iso)
        elif p.name == "equity_curve.jsonl" and lo:
            def _in_run(r):
                try:
                    ts = datetime.fromisoformat(str(r.get("ts")))
                except ValueError:
                    return False
                return str(r.get("market", "")).upper() == MK and r.get("date") == old_iso and lo <= ts <= hi
            new, rows = _jsonl_rows(new, _in_run, "date", old_iso, new_iso)
        if new != txt:
            edits[p] = {"subs": n, "rows": rows, "text": new}
    # 감사 원장 — 이름표 문자열만 바꾼다(행 단위 규칙 없음 · 다른 세션이 덧붙이는 파일이라 원자적으로 쓴다)
    if ledger and ledger.exists():
        txt = ledger.read_text(encoding="utf-8")
        new, n = txt, 0
        for r, rep in rx:
            new, k = r.subn(rep, new)
            n += k
        if new != txt:
            edits[ledger] = {"subs": n, "rows": 0, "text": new, "extra": True}
    return {"from": frm, "to": to, "market": mk, "renames": renames, "collide": collide, "edits": edits,
            "ledger_only": ledger_only, "orig": {p: p.read_text(encoding="utf-8") for p, e in edits.items() if e.get("extra")},
            "started": started.isoformat() if started else None, "patterns": [p for p, _ in subs]}


def _orig_text(pl: dict, p: Path) -> str:
    return (pl.get("orig") or {}).get(p, "")


def _scenario_opened(txt: str, mk: str, F: str, T: str) -> tuple:
    """이 run이 연 시나리오(opened_by = <mk>:<T> — 치환 뒤)의 `opened`를 T로. id는 그대로."""
    try:
        d = json.loads(txt)
    except json.JSONDecodeError:
        return txt, 0
    n = 0
    for r in d.get("scenarios") or []:
        if r.get("opened_by") == f"{mk}:{T}" and str(r.get("opened")) == F:
            r["opened"] = T
            n += 1
    if not n:
        return txt, 0
    out = json.dumps(d, ensure_ascii=False, indent=2) + ("\n" if txt.endswith("\n") else "")
    return out, n


def _jsonl_rows(txt: str, pick, field: str, old: str, new: str) -> tuple:
    lines, n = txt.split("\n"), 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if pick(r):
            lines[i] = line.replace(f'"{field}": "{old}"', f'"{field}": "{new}"', 1)
            n += 1
    return "\n".join(lines), n


def remaining(root: Path, frm: str) -> dict:
    F, mk = frm.split("_")
    pats = [re.compile(re.escape(frm)), re.compile(rf"\b{mk}:{F}\b"), re.compile(rf"\b{F}-{mk}\b"),
            re.compile(rf"\b{mk.upper()} {F}\b")]
    left = {}
    for p in _files(root):
        if frm in p.name:
            left[str(p.relative_to(root))] = "이름"
            continue
        if p.suffix not in TEXT_EXT or p.name in SKIP_NAMES:
            continue
        try:
            t = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        k = sum(len(r.findall(t)) for r in pats)
        if k:
            left[str(p.relative_to(root))] = k
    return left


def _rel(root: Path, p: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)                              # 프로젝트 밖(감사 원장)은 절대 경로


def apply(root: Path, pl: dict) -> Path:
    snap = root / SNAP_ROOT / f"{pl['from']}_to_{pl['to']}" / ("ledger" if pl.get("ledger_only") else "")
    touched = set(pl["edits"]) | {a for a, _ in pl["renames"]}
    for p in sorted(touched):
        rel = _rel(root, p)
        dst = snap / (Path("_extra") / p.name if rel == str(p) else rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)                       # 시각 보존 복사
    for p, e in pl["edits"].items():
        st = p.stat()
        if e.get("extra"):
            # 감사 원장은 다른 세션이 덧붙인다 — 읽은 뒤 바뀌었으면 쓰지 않는다(덧붙인 행을 지우지 않게) · 임시 파일 → 교체
            if p.read_text(encoding="utf-8") != _orig_text(pl, p):
                raise SystemExit(f"{p.name}이 읽은 뒤 바뀌었다 — 다시 돌려라")
            tmp = p.with_name(p.name + ".renaming")
            tmp.write_text(e["text"], encoding="utf-8")
            os.replace(tmp, p)
            continue
        p.write_text(e["text"], encoding="utf-8")
        os.utime(p, (st.st_atime, st.st_mtime))      # first_touch가 mtime으로 run 시작을 잰다
    for a, b in pl["renames"]:
        os.rename(a, b)                            # rename은 mtime을 그대로 둔다
    log = root / "journal" / "rename_log.jsonl"
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(KST).isoformat(timespec="seconds"), "from": pl["from"], "to": pl["to"],
                            "renamed": [[str(a.relative_to(root)), str(b.relative_to(root))] for a, b in pl["renames"]],
                            "edited": {_rel(root, p): {"subs": e["subs"], "rows": e["rows"]}
                                       for p, e in pl["edits"].items()},
                            "ledger_only": bool(pl.get("ledger_only")),
                            "snapshot": str(snap.relative_to(root))}, ensure_ascii=False) + "\n")
    return snap


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="frm", required=True, help="<YYMMDD>_<kr|us>")
    ap.add_argument("--to", required=True, help="<YYMMDD>_<kr|us>")
    ap.add_argument("--also", action="append", default=[], help="시장 표기 없는 이 run의 파일 이름(섹터 리서치) — 여러 번")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ledger-only", action="store_true", help="감사 원장 이름표만 바꾼다(파일은 이미 바꾼 뒤)")
    ap.add_argument("--root", default=str(HERE), help=argparse.SUPPRESS)
    a = ap.parse_args()
    root = Path(a.root)
    pl = plan(root, a.frm, a.to, a.also, ledger=LEDGER, ledger_only=a.ledger_only)
    print(f"# 스탬프 바꾸기 {a.frm} → {a.to} ({'적용' if a.apply else '건조 실행'}) · run 첫 파일 시각 {pl['started']}")
    print(f"\n## 이름 {len(pl['renames'])}개")
    for x, y in pl["renames"]:
        print(f"  {x.relative_to(root)} → {y.name}")
    if pl["collide"]:
        print("\n## 목적지가 이미 있어 _b를 붙인 것")
        for o, n in pl["collide"]:
            print(f"  {o} → {n}")
    tot = sum(e["subs"] for e in pl["edits"].values())
    print(f"\n## 내용 {len(pl['edits'])}개 파일 · 치환 {tot}곳")
    for p, e in sorted(pl["edits"].items(), key=lambda kv: -kv[1]["subs"]):
        print(f"  {e['subs']:>4}" + (f" · 행 {e['rows']}" if e["rows"] else "") + f"  {_rel(root, p)}")
    if not a.apply:
        print("\n(건조 실행 — 바꾼 것 없음. --apply로 적용)")
        return 0
    snap = apply(root, pl)
    left = {} if a.ledger_only else remaining(root, a.frm)
    print(f"\n적용 완료 · 원본 복사 {snap.relative_to(root)} · 기록 journal/rename_log.jsonl")
    if left:
        print(f"★ 옛 표기가 남았다 {len(left)}개 파일:")
        for k, v in list(left.items())[:20]:
            print(f"  {v}  {k}")
        return 1
    print(f"옛 표기 0 — {a.frm} 흔적 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
