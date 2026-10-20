#!/usr/bin/env python3
"""저장소 모듈 — 매매 프로그램의 모든 사실·데이터는 `journal/trading.db`(SQLite) 한 파일에 있고,
그 파일을 여는 코드는 이 모듈 하나다.

왜 한 파일·한 모듈인가
- 논리 DB 열 개(기준 정보·재료·거시·시장 상태·세부시장·종목·일정·출처·검증·판단·집행·운영)는
  표 이름 앞 글자로 나뉜다(`dim_` `mat_` `ma_` `mk_` `sc_` `co_` `cal_` `ev_` `jd_` `ex_` `op_`).
  파일을 쪼개면 SQLite가 외래키를 강제하지 못한다 — 사실이 판단을, 판단이 주문을 id로 묶어야 한다.
- 원장 파일을 스크립트마다 통째로 읽고-고치고-쓰던 구조(덮어쓰기·유실)를 행 단위 갱신으로 바꾼다.

이 모듈이 주는 것
- `connect()`·`tx()`·`backup()`·`restore_tables()`·`doctor()` — 저장소 자체
- 원장 모양 접근 함수 — 지금 코드가 받던 dict 그대로(`theses_all()`·`thesis_update(id, fn)`…)
  내부적으로는 판단에 쓰는 칸은 컬럼, 옛 행에만 있는 칸은 `extra`, 키 순서는 `ko`에 둔다 —
  그래서 내보내기가 옛 파일과 바이트 단위로 같다(이전이 무손실임을 기계로 증명한다).
- `export()` — 봉인 게이트·포트폴리오 동기화가 파일 경로로 읽는 일곱 개를 읽기 전용 렌더본으로 쓴다.

명령: python3 db.py schema | doctor | show <표> [--where …] [--limit N] | sql "<SELECT …>" | export [이름…] | backup <label>
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HERE = Path(__file__).absolute().parent
JOURNAL = HERE / "journal"
CONFIG = HERE / "config"
SCHEMA_PATH = HERE / "db_schema.sql"
BACKUP_DIR = JOURNAL / "_db_backup"


class StaleWrite(RuntimeError):
    """다른 프로세스가 먼저 고친 행을 옛 판으로 덮어쓰려 했다."""


class TestGuard(RuntimeError):
    """테스트가 실제 저장소를 열려 했다."""


# ★ 저장소 판(2026-10-19 · Codex R12) — 코드가 기대하는 표 모양의 번호. 새 저장소는 db_schema.sql이 이 판으로 만들고,
#   옛 저장소는 `python3 db.py migrate`가 한 판씩 올린다. 판이 다르면 connect()가 열지 않는다(옛 표 위에서 새 코드가 조용히
#   틀린 값을 쓰거나 새 칸이 없어 중간에 죽는 것을 막는다). 판을 올리면 이 숫자 · db_schema.sql의 schema_version · MIGRATIONS 셋을 같이.
SCHEMA_VERSION = 10


class SchemaOutdated(RuntimeError):
    """저장소 판이 코드 판보다 낮다 — `python3 db.py migrate`."""


class SchemaNewer(RuntimeError):
    """저장소 판이 코드 판보다 높다 — 코드가 저장소보다 옛것이다(되돌린 코드 위에서 새 저장소를 열지 않는다)."""


class MigrationError(RuntimeError):
    """이전 단계 검증 실패 — 그 단계는 롤백된다."""


class RunModeConflict(RuntimeError):
    """같은 run(날짜_시장)을 다른 계좌 계열로 다시 시작하려 했다(v4 · Codex R09)."""


def acct_family_of(mode) -> str:
    """계좌 모드 → 계열 'paper' | 'real'(real · real_readonly). 표시 없는 옛 행은 모의다."""
    return "paper" if str(mode or "paper") == "paper" else "real"


# ───────────────────────────── 연결 ─────────────────────────────

def db_path() -> Path:
    p = os.environ.get("AITB_DB")
    return Path(p) if p else JOURNAL / "trading.db"


def _guard(path: Path) -> None:
    """`test_`로 시작하는 실행 파일이 AITB_DB 없이 실제 저장소를 열면 거부한다.
    옛 구조에서는 테스트가 원장 경로를 임시 폴더로 돌려 심고, 심기가 조용히 실패하면 실제 데이터로
    통과했다 — 저장소로 바뀌면 그 실패가 실제 원장을 고치는 사고가 된다."""
    if os.environ.get("AITB_DB"):
        return
    main = sys.modules.get("__main__")
    f = Path(getattr(main, "__file__", "") or sys.argv[0] or "").name
    if f.startswith("test_") or os.environ.get("AITB_TESTING") == "1":
        raise TestGuard(f"테스트({f})가 실제 저장소 {path}를 열려 했다 — AITB_DB를 임시 경로로 지정하라")


_CONN: dict = {}


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else db_path()
    key = str(path)
    c = _CONN.get(key)
    if c is not None:
        return c
    _guard(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA busy_timeout = 5000")
    try:
        c.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    if not _has_schema(c):
        init_schema(c)
        try:
            holidays_sync(c)                         # 새 저장소 — 휴장일 사본을 바로(v5)
        except Exception:                            # noqa: BLE001 — 파일이 없거나 깨져도 저장소는 연다(휴장을 모른다 = 예전 동작)
            pass
    elif not _MIGRATING[0]:
        have = schema_version(c)
        if have != SCHEMA_VERSION:
            c.close()
            if have < SCHEMA_VERSION:
                raise SchemaOutdated(f"저장소 판 {have} < 코드 판 {SCHEMA_VERSION} — `python3 db.py migrate`로 올린 뒤 다시 돌려라"
                                     f"(저장소 {path})")
            raise SchemaNewer(f"저장소 판 {have} > 코드 판 {SCHEMA_VERSION} — 코드가 저장소보다 옛것이다(되돌린 코드면 "
                              f"`python3 db.py restore <백업>`으로 저장소도 되돌린다 · 저장소 {path})")
    _CONN[key] = c
    return c


_MIGRATING = [0]


@contextlib.contextmanager
def _gate_open():
    """판 검사 없이 연다 — migrate·restore·doctor만(판이 다른 저장소를 열어 올리거나 되돌리거나 진단해야 한다)."""
    _MIGRATING[0] += 1
    try:
        yield
    finally:
        _MIGRATING[0] -= 1


def schema_version(c=None) -> int:
    c = c or connect()
    r = c.execute("SELECT v FROM sys_meta WHERE k='schema_version'").fetchone()
    try:
        return int(r[0]) if r else 1
    except (TypeError, ValueError):
        return 1


def reset() -> None:
    """열린 연결을 닫는다(테스트가 AITB_DB를 바꾼 뒤 부른다)."""
    for c in list(_CONN.values()):
        with contextlib.suppress(Exception):
            c.close()
    _CONN.clear()


def _has_schema(c) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sys_meta'").fetchone() is not None


def _schema_statements(text: str) -> list:
    """스키마 파일 → 문장 목록. 트리거 본문(BEGIN … END)의 `;`는 sqlite3.complete_statement가 문장 끝으로 보지 않는다."""
    out, buf = [], []
    for line in text.splitlines(keepends=True):
        buf.append(line)
        chunk = "".join(buf)
        if sqlite3.complete_statement(chunk):
            if chunk.strip():
                out.append(chunk)
            buf = []
    rest = "".join(buf).strip()
    if rest and not all(x.strip().startswith("--") or not x.strip() for x in buf):
        out.append(rest)
    return out


def init_schema(c=None) -> None:
    """스키마를 문장 하나씩 실행한다(멱등 — CREATE … IF NOT EXISTS). executescript를 쓰지 않는 이유: 파이썬 3.9의 executescript는
    열린 트랜잭션을 먼저 커밋해 버려, 이전(migrate)이 한 트랜잭션 안에서 표를 바꾸고 뷰·트리거를 다시 만드는 것을 깨뜨린다."""
    c = c or connect()
    for stmt in _schema_statements(SCHEMA_PATH.read_text(encoding="utf-8")):
        try:
            c.execute(stmt)
        except sqlite3.OperationalError as e:
            # 이전의 중간 판(v1 → v7이면 v2~v6)에서는 뒤 판이 더할 칸을 가리키는 색인·뷰·트리거를 건너뛴다 — 마지막 판의 재적용이 엄격하게 만든다
            if _SCHEMA_PARTIAL[0] and "no such column" in str(e) and re.match(r"\s*CREATE\s+(UNIQUE\s+)?(INDEX|VIEW|TRIGGER)", stmt, re.I):
                continue
            raise


_SCHEMA_PARTIAL = [False]   # migrate가 마지막이 아닌 판을 적용하는 동안만 참
_TX_DEPTH: dict = {}
_PENDING_EXPORTS: set = set()


def mark_export(name: str) -> None:
    """이 쓰기가 렌더본(theses.json 등)을 바꾼다 — 바깥 트랜잭션이 커밋된 뒤 한 번 내보낸다.
    트랜잭션 밖이면 바로 내보낸다. 커밋 전 값이 파일로 나가거나(롤백되면 거짓 렌더) 쓰기마다 여러 번 쓰는 일을 막는다."""
    if name not in EXPORTS or _HOLD[0]:
        return
    c = _CONN.get(str(db_path()))
    if c is not None and _TX_DEPTH.get(id(c)):
        _PENDING_EXPORTS.add(name)
    else:
        export(name)


_HOLD = [0]


@contextlib.contextmanager
def hold_exports():
    """안에서 쌓인 렌더본 표시를 버린다 — 호출자(이전 도구)가 검사를 통과한 뒤 직접 export한다.
    검사 전에 원본 자리를 덮지 않는다는 이전 계약을 지키는 손잡이."""
    _HOLD[0] += 1
    try:
        yield
    finally:
        _HOLD[0] -= 1
        if not _HOLD[0]:
            _PENDING_EXPORTS.clear()


def _flush_exports() -> None:
    names = sorted(_PENDING_EXPORTS)
    _PENDING_EXPORTS.clear()
    for n in names:
        try:
            export(n)
        except Exception as e:  # 렌더본 실패가 원장 쓰기를 되돌리지 않는다 — doctor가 다름을 잡는다
            print(f"[db.export] {n} 실패: {e}", file=sys.stderr)


@contextlib.contextmanager
def tx(c=None):
    """쓰기 트랜잭션(BEGIN IMMEDIATE). 겹쳐 부르면 바깥 하나로 묶인다."""
    c = c or connect()
    k = id(c)
    if _TX_DEPTH.get(k):
        _TX_DEPTH[k] += 1
        try:
            yield c
        finally:
            _TX_DEPTH[k] -= 1
        return
    c.execute("BEGIN IMMEDIATE")
    _TX_DEPTH[k] = 1
    try:
        yield c
    except BaseException:
        _TX_DEPTH[k] = 0
        _PENDING_EXPORTS.clear()
        c.execute("ROLLBACK")
        raise
    else:
        _TX_DEPTH[k] = 0
        c.execute("COMMIT")
        if _PENDING_EXPORTS:
            _flush_exports()


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def q(sql: str, args=()) -> list:
    return [dict(r) for r in connect().execute(sql, args).fetchall()]


def q1(sql: str, args=()):
    r = connect().execute(sql, args).fetchone()
    return dict(r) if r else None


def run_id(stamp: str, market: str) -> str:
    return f"{stamp}_{str(market).lower()}"


# ───────────────────────────── 원장 모양 ↔ 행 ─────────────────────────────
# 판단 원장(논지·시나리오·축·섹터·일정·배분·자산 곡선·유니버스)은 키가 run마다 덧붙어 있다.
# 판단에 쓰는 칸은 컬럼으로, 그 밖의 칸은 `extra`로, 키 순서는 `ko`로 둔다.
# 값의 타입이 컬럼 타입과 다르면(예: 대부분 숫자인 칸에 문자열) 그 행만 `extra`로 간다 — 그래서 무손실이다.

def _fits(v, t: str) -> bool:
    if v is None:
        return True
    if t == "text":
        return isinstance(v, str)
    if t == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "num":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "bool":
        return isinstance(v, bool)
    if t == "json":  # 사실 id 목록 같은 짧은 목록 — 트리거가 json_each로 본다
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    return False


def _to_db(v, t):
    if v is None:
        return None
    if t == "bool":
        return 1 if v else 0
    if t == "json":
        return json.dumps(v, ensure_ascii=False)
    return v


def _from_db(v, t):
    if v is None:
        return None
    if t == "bool":
        return bool(v)
    if t == "json":
        return json.loads(v)
    return v


class Field:
    __slots__ = ("key", "t", "col")

    def __init__(self, key, t="text", col=None):
        self.key, self.t, self.col = key, t, col or key


def F(spec: str) -> list:
    """'id ticker:text horizon_days:int order>bord:int' → Field 목록."""
    out = []
    for tok in spec.split():
        t = "text"
        if ":" in tok:
            tok, t = tok.split(":", 1)
        col = None
        if ">" in tok:
            tok, col = tok.split(">", 1)
        out.append(Field(tok, t, col))
    return out


class Child:
    """dict 목록 칸 → 자식 표 행들(부모 키 · src · ord · 컬럼 · extra · ko)."""

    def __init__(self, key, table, fields, src=None, derived=None):
        self.key, self.table, self.fields, self.src = key, table, fields, src or key
        self.derived = derived  # fn(item) -> {col: value} 문서 키가 아닌 계산 컬럼(예: 다리의 check_kind·level)


class Scalars:
    """문자열 목록 칸 → 자식 표 행들(부모 키 · src · ord · value)."""

    def __init__(self, key, table, src=None):
        self.key, self.table, self.src = key, table, src or key


class Pattern:
    """이름이 규칙을 따르는 칸들(예: 축의 note_261016) → 자식 표(부모 키 · k · text)."""

    def __init__(self, regex, table):
        self.rx, self.table = re.compile(regex), table


class DocSpec:
    def __init__(self, name, table, pk, fields, children=(), derived=None, fk="pid", export=None):
        self.name, self.table, self.pk = name, table, pk
        self.export = export  # 이 원장이 바뀌면 다시 쓸 렌더본 이름
        self.fields = fields
        self.fmap = {f.key: f for f in fields}
        self.children = [c for c in children if isinstance(c, Child)]
        self.scalars = [c for c in children if isinstance(c, Scalars)]
        self.patterns = [c for c in children if isinstance(c, Pattern)]
        self.ckeys = {c.key: c for c in self.children}
        self.skeys = {c.key: c for c in self.scalars}
        self.derived = derived  # fn(doc) -> {col: value} 문서 키가 아닌 계산 컬럼
        self.fk = fk


def _split_row(d: dict, fields, fmap) -> tuple:
    cols, extra = {}, {}
    for k, v in d.items():
        f = fmap.get(k)
        if f is not None and _fits(v, f.t):
            cols[f.col] = _to_db(v, f.t)
        else:
            extra[k] = v
    return cols, extra


def _decompose(spec: DocSpec, d: dict) -> tuple:
    """dict → (부모 컬럼, 자식 행들{table: [row]}). 부모 컬럼에는 extra·ko가 들어 있다."""
    cols, extra, kids = {}, {}, {}
    ko = list(d.keys())
    for k, v in d.items():
        f = spec.fmap.get(k)
        if f is not None and _fits(v, f.t):
            cols[f.col] = _to_db(v, f.t)
            continue
        ch = spec.ckeys.get(k)
        if ch is not None and isinstance(v, list) and all(isinstance(x, dict) for x in v):
            fm = {f.key: f for f in ch.fields}
            rows = kids.setdefault(ch.table, [])
            for i, item in enumerate(v):
                c2, e2 = _split_row(item, ch.fields, fm)
                c2.update({"src": ch.src, "ord": i, "extra": json.dumps(e2, ensure_ascii=False) if e2 else None,
                           "ko": json.dumps(list(item.keys()), ensure_ascii=False)})
                if ch.derived:
                    c2.update(ch.derived(item))
                rows.append(c2)
            continue
        sc = spec.skeys.get(k)
        if sc is not None and isinstance(v, list) and all(isinstance(x, str) for x in v):
            rows = kids.setdefault(sc.table, [])
            for i, item in enumerate(v):
                rows.append({"src": sc.src, "ord": i, "value": item})
            continue
        pat = next((p for p in spec.patterns if p.rx.match(k)), None)
        if pat is not None and isinstance(v, str):
            kids.setdefault(pat.table, []).append({"k": k, "text": v})
            continue
        extra[k] = v
    cols["extra"] = json.dumps(extra, ensure_ascii=False) if extra else None
    cols["ko"] = json.dumps(ko, ensure_ascii=False)
    if spec.derived:
        cols.update(spec.derived(d))
    return cols, kids


def _child_tables(spec: DocSpec) -> list:
    ts = []
    for c in spec.children + spec.scalars + spec.patterns:
        if c.table not in ts:
            ts.append(c.table)
    return ts


def _compose(spec: DocSpec, row: dict, kids: dict) -> dict:
    extra = json.loads(row["extra"]) if row.get("extra") else {}
    ko = json.loads(row["ko"]) if row.get("ko") else []
    out = {}
    for k in ko:
        if k in extra:
            out[k] = extra[k]
            continue
        f = spec.fmap.get(k)
        if f is not None:
            out[k] = _from_db(row.get(f.col), f.t)
            continue
        ch = spec.ckeys.get(k)
        if ch is not None:
            items = []
            for r in kids.get(ch.table, []):
                if r["src"] != ch.src:
                    continue
                e2 = json.loads(r["extra"]) if r.get("extra") else {}
                k2 = json.loads(r["ko"]) if r.get("ko") else []
                fm = {f.key: f for f in ch.fields}
                item = {}
                for kk in k2:
                    if kk in e2:
                        item[kk] = e2[kk]
                    else:
                        ff = fm.get(kk)
                        item[kk] = _from_db(r.get(ff.col), ff.t) if ff else None
                items.append(item)
            out[k] = items
            continue
        sc = spec.skeys.get(k)
        if sc is not None:
            out[k] = [r["value"] for r in kids.get(sc.table, []) if r["src"] == sc.src]
            continue
        pat = next((p for p in spec.patterns if p.rx.match(k)), None)
        if pat is not None:
            hit = [r for r in kids.get(pat.table, []) if r["k"] == k]
            out[k] = hit[0]["text"] if hit else None
            continue
        out[k] = None
    return out


_REVIEW_CHILD = {"jd_thesis_fact", "jd_scenario_judgment"}


class DocStore:
    """원장 하나(논지·시나리오…)를 dict 모양 그대로 읽고 쓴다.
    - get/put/delete/all : 행 단위
    - update(id, fn)      : 한 트랜잭션 안에서 읽고-고치고-쓴다(덮어쓰기 유실 없음)
    - load()/save(docs)   : 옛 코드의 통째 읽기/쓰기 모양. save는 바뀐 행만 쓰고, load 뒤 다른 프로세스가
                            먼저 고친 행이면 StaleWrite를 낸다.
    """

    def __init__(self, spec: DocSpec):
        self.s = spec
        self._loaded: dict = {}

    # 부모 키 → where
    def _where(self, key) -> tuple:
        pk = self.s.pk
        if isinstance(pk, str):
            return f"{pk}=?", (key,)
        return " AND ".join(f"{p}=?" for p in pk), tuple(key)

    def _key_of(self, d: dict):
        pk = self.s.pk
        if isinstance(pk, str):
            f = self.s.fmap.get(pk)
            return d.get(f.key if f else pk)
        return tuple(d.get(self.s.fmap[p].key if p in self.s.fmap else p) for p in pk)

    def _kids(self, c, pid) -> dict:
        out = {}
        for t in _child_tables(self.s):
            out[t] = [dict(r) for r in c.execute(f"SELECT * FROM {t} WHERE {self.s.fk}=? ORDER BY src, ord" if t not in self._pattern_tables() else f"SELECT * FROM {t} WHERE {self.s.fk}=?", (pid,)).fetchall()]
        return out

    def _pattern_tables(self):
        return {p.table for p in self.s.patterns}

    def _rowid(self, c, key):
        w, a = self._where(key)
        r = c.execute(f"SELECT rowid AS rid, * FROM {self.s.table} WHERE {w}", a).fetchone()
        return dict(r) if r else None

    def get(self, key):
        c = connect()
        r = self._rowid(c, key)
        if not r:
            return None
        return _compose(self.s, r, self._kids(c, r["rid"]))

    def history(self, key) -> list:
        """이 행의 지난 판들(오래된 것부터)."""
        return [{"rev": r["rev"], "at": r["at"], "doc": json.loads(r["body"])} for r in
                q("SELECT rev, at, body FROM sys_doc_hist WHERE doc=? AND k=? ORDER BY id", (self.s.name, json.dumps(key, ensure_ascii=False)))]

    def rev(self, key):
        r = self._rowid(connect(), key)
        return r["rev"] if r else None

    def all(self, where: str = "", args=()) -> list:
        c = connect()
        rows = [dict(r) for r in c.execute(
            f"SELECT rowid AS rid, * FROM {self.s.table} {('WHERE ' + where) if where else ''} ORDER BY ord, rowid", args).fetchall()]
        if not rows:
            return []
        kids_all = {}
        for t in _child_tables(self.s):
            order = "" if t in self._pattern_tables() else " ORDER BY src, ord"
            for r in c.execute(f"SELECT * FROM {t}{order}").fetchall():
                kids_all.setdefault(r[self.s.fk], {}).setdefault(t, []).append(dict(r))
        return [_compose(self.s, r, kids_all.get(r["rid"], {})) for r in rows]

    def put(self, d: dict, ord_: int | None = None, expect_rev: int | None = None) -> int:
        """행 하나를 쓴다(없으면 넣고 있으면 바꾼다). 반환 = 새 rev."""
        c = connect()
        key = self._key_of(d)
        if key is None or (isinstance(key, tuple) and None in key):
            raise ValueError(f"{self.s.name}: 키 {self.s.pk}가 없다")
        cols, kids = _decompose(self.s, d)
        with tx(c):
            old = self._rowid(c, key)
            ts = now_iso()
            if old:
                if expect_rev is not None and old["rev"] != expect_rev:
                    raise StaleWrite(f"{self.s.name} {key}: 읽은 판 {expect_rev} · 저장된 판 {old['rev']}")
                # 직전 판을 이력에 남긴다 — 옛 구조의 "파일 통째 백업"을 행 단위로 대신한다
                prev = _compose(self.s, old, self._kids(c, old["rid"]))
                c.execute("INSERT INTO sys_doc_hist (doc, k, rev, body, at) VALUES (?,?,?,?,?)",
                          (self.s.name, json.dumps(key, ensure_ascii=False), old["rev"], json.dumps(prev, ensure_ascii=False), ts))
                rev = (old["rev"] or 0) + 1
                cols.update({"rev": rev, "updated_at": ts})
                if ord_ is not None:
                    cols["ord"] = ord_
                # 계산 컬럼이 아닌데 이번 dict에 없는 컬럼은 NULL로(키가 빠졌으면 컬럼도 비운다)
                for f in self.s.fields:
                    cols.setdefault(f.col, None)
                sets = ", ".join(f"{k}=?" for k in cols)
                c.execute(f"UPDATE {self.s.table} SET {sets} WHERE rowid=?", (*cols.values(), old["rid"]))
                pid = old["rid"]
                # 재검토 표시(needs_review)는 문서 칸이 아니다 — 자식 행을 다시 넣어도 (src, ord)로 되살린다
                flags = {}
                for t in _child_tables(self.s):
                    if t in _REVIEW_CHILD:
                        flags[t] = [(r[0], r[1]) for r in c.execute(
                            f"SELECT src, ord FROM {t} WHERE {self.s.fk}=? AND needs_review=1", (pid,)).fetchall()]
                    c.execute(f"DELETE FROM {t} WHERE {self.s.fk}=?", (pid,))
            else:
                flags = {}
                rev = 1
                if ord_ is None:
                    ord_ = (c.execute(f"SELECT COALESCE(MAX(ord), -1) + 1 FROM {self.s.table}").fetchone()[0])
                cols.update({"rev": rev, "created_at": ts, "updated_at": ts, "ord": ord_})
                ks = ", ".join(cols)
                cur = c.execute(f"INSERT INTO {self.s.table} ({ks}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
                pid = cur.lastrowid
            for t, rows in kids.items():
                for r in rows:
                    r = {self.s.fk: pid, **r}
                    c.execute(f"INSERT INTO {t} ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", tuple(r.values()))
            for t, keys in flags.items():
                for src, o in keys:
                    c.execute(f"UPDATE {t} SET needs_review=1 WHERE {self.s.fk}=? AND src=? AND ord=?", (pid, src, o))
            if self.s.export:
                mark_export(self.s.export)
        return rev

    def delete(self, key) -> bool:
        c = connect()
        with tx(c):
            old = self._rowid(c, key)
            if not old:
                return False
            prev = _compose(self.s, old, self._kids(c, old["rid"]))
            c.execute("INSERT INTO sys_doc_hist (doc, k, rev, body, at) VALUES (?,?,?,?,?)",
                      (self.s.name, json.dumps(key, ensure_ascii=False), old["rev"], json.dumps(prev, ensure_ascii=False), now_iso() + " 삭제"))
            for t in _child_tables(self.s):
                c.execute(f"DELETE FROM {t} WHERE {self.s.fk}=?", (old["rid"],))
            c.execute(f"DELETE FROM {self.s.table} WHERE rowid=?", (old["rid"],))
            if self.s.export:
                mark_export(self.s.export)
        return True

    def update(self, key, fn, retries: int = 1):
        """한 트랜잭션 안에서 읽고 fn(dict)로 고친 뒤 쓴다. fn이 None을 돌려주면 받은 dict를 그대로 쓴다.
        행이 없으면 KeyError."""
        c = connect()
        for attempt in range(retries + 1):
            try:
                with tx(c):
                    d = self.get(key)
                    if d is None:
                        raise KeyError(f"{self.s.name} {key} 없음")
                    r0 = self.rev(key)
                    nd = fn(d)
                    nd = d if nd is None else nd
                    self.put(nd, expect_rev=r0)
                    return nd
            except StaleWrite:
                if attempt >= retries:
                    raise
        return None

    def load(self) -> list:
        docs = self.all()
        self._loaded = {}
        for d in docs:
            k = self._key_of(d)
            self._loaded[k] = (self.rev(k), json.dumps(d, ensure_ascii=False, sort_keys=False))
        return docs

    def save(self, docs: list, delete_missing: bool = False) -> int:
        """옛 `save(통째)` 모양. 바뀐 행만 쓴다. load() 이후 다른 쓰기가 끼었으면 StaleWrite."""
        c = connect()
        n = 0
        with tx(c):
            seen = set()
            for i, d in enumerate(docs):
                k = self._key_of(d)
                seen.add(k)
                snap = json.dumps(d, ensure_ascii=False, sort_keys=False)
                prev = self._loaded.get(k)
                cur_rev = self.rev(k)
                if prev and prev[1] == snap:
                    continue  # 이 호출자가 고치지 않은 행 — 그 사이 다른 쓰기가 있었어도 덮지 않는다
                if prev and cur_rev is not None and cur_rev != prev[0]:
                    raise StaleWrite(f"{self.s.name} {k}: load 뒤 다른 쓰기가 있었다(판 {prev[0]} → {cur_rev})")
                self.put(d, ord_=i if cur_rev is None else None)
                n += 1
            if delete_missing:
                for k in list(self._loaded):
                    if k not in seen:
                        self.delete(k)
                        n += 1
        self.load()
        return n


# ── 원장별 명세 ──
LEG = F("id>leg_id when>when_ check>check_expr size_pct:num basis stock fired:bool fired_at fired_note reach_prob:num reach_n:int")
_CHECK_RX = [
    (re.compile(r"^\s*price\s*<=\s*([-\d.]+)\s*$"), "price_le"),
    (re.compile(r"^\s*price\s*>=\s*([-\d.]+)\s*$"), "price_ge"),
    (re.compile(r"^\s*from_peak_pct\s*<=\s*([-\d.]+)\s*$"), "from_peak"),
    (re.compile(r"^\s*days_since_entry\s*>=\s*([-\d.]+)\s*$"), "days_since"),
    (re.compile(r"^\s*pnl_pct\s*<=\s*([-\d.]+)\s*$"), "pnl_le"),
]


def parse_check(expr) -> tuple:
    """다리의 `check` 문자열 → (check_kind, level). 판정은 이 두 컬럼으로 한다."""
    if not isinstance(expr, str):
        return None, None
    if expr.strip() == "judge":
        return "judge", None
    for rx, kind in _CHECK_RX:
        m = rx.match(expr)
        if m:
            return kind, float(m.group(1))
    return "other", None


def _leg_derived(item: dict) -> dict:
    kind, level = parse_check(item.get("check"))
    return {"check_kind": kind, "level": level}


SPEC = {}
SPEC["thesis"] = DocSpec(
    "thesis", "jd_thesis", "id",
    F("id market ticker name excd axis axis_id axis_label axis_why forecast_id beneficiary_order:int parent "
      "expires horizon_days:int thesis invalidation status created entered_on entry_price:num price_driver "
      "price_driver_why priced_at_entry:bool expected_upside_pct:num increase_when research"),
    [Child("entry_triggers", "jd_thesis_leg", LEG, derived=_leg_derived),
     Child("exit_triggers", "jd_thesis_leg", LEG, derived=_leg_derived),
     Child("entry_triggers_archived", "jd_thesis_leg", LEG, derived=_leg_derived),
     Child("evidence", "jd_thesis_fact", F("claim source_url tier verdict as_of fact_check added fact source url fact_id role")),
     Child("history", "jd_thesis_event", F("ts at status note")),
     Child("corrections", "jd_thesis_event", F("ts what why"))],
)
SPEC["scenario"] = DocSpec(
    "scenario", "jd_scenario", "id",
    F("id market opened local_id status realized:bool acted:bool condition expected response opened_by axis "
      "next_check price_cond:bool invalidation actions_backfill merged_into merged_at possible_duplicate_of "
      "event_id cond_status expected_reaction"),
    [Child("judgments", "jd_scenario_judgment", F("when>when_ realized:bool acted:bool note auto:bool fact_ids:json")),
     Child("actions", "jd_scenario_response", F("market ticker what done:bool done_at done_note added_by noop:bool")),
     Child("cond", "jd_scenario_cond", F("metric op value:num value_hi:num value_text unit window")),
     Scalars("sources", "jd_scenario_list"), Scalars("aliases", "jd_scenario_list")],
)
SPEC["axis"] = DocSpec(
    "axis", "dim_axis", "id",
    F("id name label thesis driver direction expected horizon_weeks:int confidence:num breaks_if status opened "
      "origin event_key created_before_news:bool created_before_news_why cross_market_reviewed tagged updated added "
      "split_why no_cross_market_why no_secondary_why research judged_at excess_pct:num judge_note judged_run_id judge_asof"),
    [Child("indicators", "jd_axis_indicator", F("name source org freq current threshold next")),
     Child("names", "jd_axis_name", F("ticker name sign why in_universe:bool market added")),
     Child("beneficiaries", "jd_axis_beneficiary",
           F("ticker name order>bord:int why priced:bool market in_universe:bool priced_basis priced_at priced_bars_asof "
             "priced_ret20:num priced_vs_bench:num priced_vs_lead:num measure_status measured_at measure_error measure_run_id "
             "judge_ret_pct:num")),
     Child("chain", "jd_axis_chain", F("step:int claim status current indicator source")),
     Scalars("conflicts_with", "jd_axis_list"),
     Pattern(r"^note_\d{6}[a-z]?$", "jd_axis_note")],
)
SPEC["sector"] = DocSpec(
    "sector", "dim_sector", "code",
    F("code market name grade status next_event resolves_when resolves_by carried_runs:int updated last_seen"),
    [Child("names", "jd_sector_name", F("ticker name why in_universe:bool")),
     Scalars("chains", "dim_sector_list"), Scalars("research", "dim_sector_list")],
)
SPEC["calendar"] = DocSpec(
    "calendar", "cal_event", "id",
    F("date event axis key why added kind note kind_fixed resolved source note_261015 issue_date source_text weekday_token confirmed:int"),
    [Scalars("tickers", "cal_event_list"), Scalars("axes", "cal_event_list")],
)
SPEC["allocation"] = DocSpec(
    "allocation", "jd_allocation", "id",
    F("id stamp market based_on target_invested_pct:num regime cash_reason cash_release_when gap_pct:num "
      "deployable:num recorded_at approved_ref"),
    [Child("reserved", "jd_allocation_reserved", F("thesis_id market amount:num why"))],
)
SPEC["equity"] = DocSpec(
    "equity", "ex_equity_point", "id",
    F("date ts market currency equity:num cash:num n_positions:int shadow_benchmark_equity:num cum_pnl_pct:num "
      "cum_benchmark_pct:num excess_pct:num day_pnl_pct:num costs_today:num turnover_today:num costs_source "
      "cum_costs:num cum_turnover:num cost_drag_pct:num cum_turnover_x:num cash_shared:bool account_mode"),
    [Child("positions", "ex_equity_position", F("ticker name qty:num price:num eval_amt:num pnl_pct:num avg_price:num"))],
)
SPEC["company"] = DocSpec(
    "company", "dim_company", ("market", "ticker"),
    F("market ticker excd name added why axis added_by run_id source axis_set_by"),
    [Scalars("aliases", "dim_company_list"), Scalars("gates", "dim_company_list")],
)
SPEC["fact"] = DocSpec(
    "fact", "ev_fact", "id",
    F("id fact>legacy_text as_of retrieved source>source_label url tier:int load_bearing:bool quote as_of_note "
      "minor note_261015_fc"),
    [Scalars("runs", "ev_fact_run_label")],
)

SPEC["thesis"].export = "theses"
SPEC["axis"].export = "market_map"
SPEC["sector"].export = "market_map"
SPEC["company"].export = "watchlist"
SPEC["fact"].export = "sources"
SPEC["equity"].export = "equity_curve"
STORES = {k: DocStore(v) for k, v in SPEC.items()}
theses = STORES["thesis"]
scenarios = STORES["scenario"]
axes = STORES["axis"]
sectors = STORES["sector"]
companies_store = STORES["company"]
facts_store = STORES["fact"]


# ───────────────────────────── 판단 원장 접근 ─────────────────────────────

def theses_all() -> list:
    return theses.all()


def thesis_get(tid: str):
    return theses.get(tid)


def thesis_put(d: dict) -> int:
    return theses.put(d)


def thesis_update(tid: str, fn):
    return theses.update(tid, fn)


def thesis_set_status(tid: str, status: str, note: str = "", run: str = "", ts: str | None = None):
    def fn(t):
        t["status"] = status
        t.setdefault("history", []).append({"ts": ts or now_iso(), "status": status, "note": note})
        return t
    return thesis_update(tid, fn)


def theses_under_review(include_closed: bool = False) -> list:
    """근거가 틀렸다고 판정된 논지(2026-10-19) — 근거(`evidence`) 가운데 반대(against)가 아닌 사실이 지금 판정 불일치(disputed)인 것.
    MISMATCH 판정이 들어오면 저장소 트리거가 논지에 재검토 표시(needs_review)를 붙이는데, 그 뒤 사실이 교정·재판정으로 풀렸으면
    재검토가 아니다 — 그래서 표시가 아니라 사실의 지금 상태로 판정한다. 반환 [{id, ticker, market, status, facts: [{fact_id, claim, why}]}]."""
    out = []
    where = "" if include_closed else " AND COALESCE(t.status, '') NOT IN ('closed', 'expired')"
    for t in q("SELECT t.rid, t.id, t.ticker, t.market, t.status FROM jd_thesis t WHERE 1=1" + where + " ORDER BY t.id"):
        bad = []
        for e in q("SELECT f.fact_id, f.claim, f.role FROM jd_thesis_fact f JOIN ev_fact x ON x.id=f.fact_id"
                   " WHERE f.pid=? AND f.src='evidence' AND x.verified_status='disputed' AND COALESCE(f.role, 'support') <> 'against'"
                   " ORDER BY f.ord", (t["rid"],)):
            v = q1("SELECT verdict, note FROM ev_verdict WHERE fact_id=? ORDER BY id DESC LIMIT 1", (e["fact_id"],)) or {}
            bad.append({"fact_id": e["fact_id"], "claim": e.get("claim") or "", "why": f"{v.get('verdict') or 'MISMATCH'} "
                        f"{str(v.get('note') or '')[:80]}".strip()})
        if bad:
            out.append({"id": t["id"], "ticker": t["ticker"], "market": t["market"], "status": t["status"], "facts": bad})
    return out


def thesis_review_clear(tid: str | None = None, fact_id: str | None = None) -> int:
    """재검토 표시를 지운다 — 논지 하나(`tid`) 또는 사실 하나를 가리킨 근거 행 전부(`fact_id` · 교정으로 풀렸을 때).
    근거 행 표시가 하나도 안 남은 논지는 머리 표시도 지운다. 지운 근거 행 수."""
    c = connect()
    with tx(c):
        if tid:
            rid = (q1("SELECT rid FROM jd_thesis WHERE id=?", (tid,)) or {}).get("rid")
            if rid is None:
                return 0
            n = _ex(c, "UPDATE jd_thesis_fact SET needs_review=0 WHERE pid=? AND needs_review=1", (rid,)).rowcount
            _ex(c, "UPDATE jd_thesis SET needs_review=0 WHERE rid=?", (rid,))
            return n
        n = _ex(c, "UPDATE jd_thesis_fact SET needs_review=0 WHERE fact_id=? AND needs_review=1", (fact_id,)).rowcount
        _ex(c, "UPDATE jd_thesis SET needs_review=0 WHERE needs_review=1 AND NOT EXISTS"
               " (SELECT 1 FROM jd_thesis_fact f WHERE f.pid=jd_thesis.rid AND f.needs_review=1)")
        return n


def thesis_mark_fired(tid: str, leg_id: str, note: str = "", side: str | None = None, at: str | None = None):
    def fn(t):
        for k in ([side] if side else ["entry_triggers", "exit_triggers"]):
            for leg in t.get(k) or []:
                if leg.get("id") == leg_id:
                    leg["fired"] = True
                    leg["fired_at"] = at or now_iso()
                    leg["fired_note"] = note
        return t
    return thesis_update(tid, fn)


def theses_load() -> list:
    return theses.load()


def theses_save(docs: list) -> int:
    return theses.save(docs)


def scenarios_all() -> list:
    return scenarios.all()


def scenario_get(sid):
    return scenarios.get(sid)


def scenario_put(d) -> int:
    return scenarios.put(d)


def scenario_update(sid, fn):
    return scenarios.update(sid, fn)


# ── 시나리오 사건·발생(v6 · S04·S05·J02) — 원장 행(DocStore) 밖의 추가형 표. 시나리오 명령이 그 행을 고치는 같은 트랜잭션 안에서 부른다.

def event_upsert(subject: str, kind: str, scheduled=None, window_end=None, source=None, opened_by=None, metrics=None) -> str:
    """사건 — 자연 열쇠(subject, kind, scheduled)로 하나. 없으면 EV-<YYMMDD>-<n>. 지표 정의는 빈 자리만 채운다. 반환 사건 id."""
    c = connect()
    with tx(c):
        r = c.execute("SELECT id FROM jd_event WHERE subject=? AND kind=? AND scheduled IS ?", (subject, kind, scheduled)).fetchone()
        if r:
            eid = r[0]
            if window_end:
                c.execute("UPDATE jd_event SET window_end=COALESCE(window_end, ?) WHERE id=?", (window_end, eid))
        else:
            day = datetime.now(KST).strftime("%y%m%d")
            n = c.execute("SELECT COUNT(*) FROM jd_event WHERE id LIKE ?", (f"EV-{day}-%",)).fetchone()[0] + 1
            eid = f"EV-{day}-{n}"
            while c.execute("SELECT 1 FROM jd_event WHERE id=?", (eid,)).fetchone():
                n += 1
                eid = f"EV-{day}-{n}"
            c.execute("INSERT INTO jd_event (id, subject, kind, scheduled, window_end, source, opened_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (eid, subject, kind, scheduled, window_end, source, opened_by, now_iso()))
        for m in metrics or []:
            if isinstance(m, dict) and m.get("name") and m.get("domain") in ("num", "enum"):
                c.execute("INSERT OR IGNORE INTO jd_event_metric (event_id, name, domain, unit, enum_values) VALUES (?,?,?,?,?)",
                          (eid, m["name"], m["domain"], m.get("unit"),
                           json.dumps(m.get("values"), ensure_ascii=False) if m.get("values") else None))
    return eid


def event_get(eid: str):
    e = q1("SELECT * FROM jd_event WHERE id=?", (eid,))
    if e:
        e["metrics"] = []
        for m in q("SELECT * FROM jd_event_metric WHERE event_id=? ORDER BY name", (eid,)):
            e["metrics"].append({"name": m["name"], "domain": m["domain"], "unit": m["unit"],
                                 "values": json.loads(m["enum_values"]) if m.get("enum_values") else None})
    return e


def episode_current(sid: str):
    """그 가지의 열린 발생(해제 안 된 마지막) · 없으면 None."""
    return q1("SELECT * FROM jd_scenario_episode WHERE scenario_id=? AND cleared_at IS NULL ORDER BY n DESC LIMIT 1", (sid,))


def episode_open(sid: str, realized_at=None, run: str = None, observation=None, price_reaction=None, note=None) -> dict:
    c = connect()
    with tx(c):
        n = (c.execute("SELECT COALESCE(MAX(n), 0) FROM jd_scenario_episode WHERE scenario_id=?", (sid,)).fetchone()[0] or 0) + 1
        eid = c.execute("INSERT INTO jd_scenario_episode (scenario_id, n, realized_at, opened_by_run, observation, price_reaction, note)"
                        " VALUES (?,?,?,?,?,?,?)", (sid, n, realized_at or now_iso(), run,
                                                    json.dumps(observation, ensure_ascii=False) if isinstance(observation, (dict, list))
                                                    else observation, price_reaction, note)).lastrowid
    return {"id": eid, "n": n}


def episode_clear(sid: str, run: str = None) -> int:
    return connect().execute("UPDATE jd_scenario_episode SET cleared_at=?, cleared_by_run=? WHERE scenario_id=? AND cleared_at IS NULL",
                             (now_iso(), run, sid)).rowcount


def response_key(market, ticker, what) -> str:
    return hashlib.sha1(f"{str(market or '').lower()}|{ticker or ''}|{what or ''}".encode("utf-8")).hexdigest()[:12]


def response_done(episode_id: int, action: dict, note: str = None, run: str = None, order_ref: str = None) -> None:
    connect().execute("INSERT OR IGNORE INTO jd_scenario_response_done (episode_id, response_key, market, done_at, done_note, done_by_run,"
                      " order_ref) VALUES (?,?,?,?,?,?,?)",
                      (episode_id, response_key(action.get("market"), action.get("ticker"), action.get("what")),
                       str(action.get("market") or "?").lower(), now_iso(), note, run, order_ref))


def scenarios_load() -> list:
    return scenarios.load()


def scenarios_save(docs: list) -> int:
    return scenarios.save(docs)


def market_map_load() -> dict:
    """지금 `journal/market_map.json` 모양 {schema_version, axes, sectors}."""
    meta = docmeta_get("market_map") or {"schema_version": "1.0"}
    out = {}
    for k in meta.get("_ko", ["schema_version", "axes", "sectors"]):
        if k == "axes":
            out[k] = axes.all()
        elif k == "sectors":
            out[k] = sectors.all()
        else:
            out[k] = meta.get(k)
    return out


def market_map_save(mm: dict) -> int:
    """옛 `_write(통째)` 모양 — 축·섹터를 행 단위로 비교해 바뀐 것만 쓴다."""
    n = 0
    with tx():
        meta = {k: v for k, v in mm.items() if k not in ("axes", "sectors")}
        meta["_ko"] = list(mm.keys())
        docmeta_put("market_map", meta)
        n += _sync_rows(axes, mm.get("axes") or [])
        n += _sync_rows(sectors, mm.get("sectors") or [])
        mark_export("market_map")
    return n


def _sync_rows(store: DocStore, docs: list) -> int:
    """목록 전체를 표에 맞춘다 — 같은 키는 바뀐 것만, 없는 키는 지우고, 순서는 목록 순서."""
    n = 0
    have = {store._key_of(d): d for d in store.all()}
    keys = []
    for i, d in enumerate(docs):
        k = store._key_of(d)
        keys.append(k)
        old = have.get(k)
        if old is None or json.dumps(old, ensure_ascii=False) != json.dumps(d, ensure_ascii=False):
            store.put(d, ord_=i)
            n += 1
        else:
            w, a = store._where(k)
            connect().execute(f"UPDATE {store.s.table} SET ord=? WHERE {w} AND ord IS NOT ?", (i, *a, i))
    for k in set(have) - set(keys):
        store.delete(k)
        n += 1
    return n


def axis_get(aid):
    return axes.get(aid)


def axis_put(d) -> int:
    return axes.put(d)


def axis_update(aid, fn):
    return axes.update(aid, fn)


def sector_get(code):
    return sectors.get(code)


def sector_put(d) -> int:
    return sectors.put(d)


def sector_update(code, fn):
    return sectors.update(code, fn)


# 일정 — 정수 키 원장
def calendar_all() -> list:
    rows = q("SELECT rowid AS rid FROM cal_event ORDER BY ord, rowid")
    return [_cal_get(r["rid"]) for r in rows]


def _cal_get(rid):
    c = connect()
    r = dict(c.execute("SELECT rowid AS rid, * FROM cal_event WHERE rowid=?", (rid,)).fetchone())
    kids = {"cal_event_list": [dict(x) for x in c.execute("SELECT * FROM cal_event_list WHERE pid=? ORDER BY src, ord", (rid,))]}
    return _compose(SPEC["calendar"], r, kids)


def calendar_doc() -> dict:
    meta = docmeta_get("calendar") or {"schema_version": "1.0"}
    out = {}
    for k in meta.get("_ko", ["schema_version", "events"]):
        out[k] = calendar_all() if k == "events" else meta.get(k)
    return out


def calendar_save(doc_or_events) -> int:
    """일정 목록 전체를 표에 맞춘다(옛 calendar.json 통째 쓰기 자리). 같은 (key|date+event)는 바뀐 것만."""
    events = doc_or_events.get("events") if isinstance(doc_or_events, dict) else doc_or_events
    with tx():
        if isinstance(doc_or_events, dict):
            meta = {k: v for k, v in doc_or_events.items() if k != "events"}
            meta["_ko"] = list(doc_or_events.keys())
            docmeta_put("calendar", meta)
        c = connect()
        c.execute("DELETE FROM cal_event_list")
        c.execute("DELETE FROM cal_event")
        for i, e in enumerate(events or []):
            _insert_rowdoc(SPEC["calendar"], e, i)
    return len(events or [])


def calendar_put(e: dict) -> None:
    """한 행 추가(같은 key가 있으면 바꾼다)."""
    with tx():
        c = connect()
        if e.get("key"):
            r = c.execute("SELECT rowid FROM cal_event WHERE key=?", (e["key"],)).fetchone()
            if r:
                c.execute("DELETE FROM cal_event_list WHERE pid=?", (r[0],))
                c.execute("DELETE FROM cal_event WHERE rowid=?", (r[0],))
        n = c.execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM cal_event").fetchone()[0]
        _insert_rowdoc(SPEC["calendar"], e, n)
        # 옛 add-event·ingest는 넣을 때마다 날짜로 안정 정렬했다 — 같은 날짜는 넣은 순서를 지킨다
        for i, r in enumerate(c.execute("SELECT rowid FROM cal_event ORDER BY COALESCE(date, ''), ord, rowid").fetchall()):
            c.execute("UPDATE cal_event SET ord=? WHERE rowid=?", (i, r[0]))


def calendar_delete(key: str) -> int:
    with tx():
        c = connect()
        rs = c.execute("SELECT rowid FROM cal_event WHERE key=?", (key,)).fetchall()
        for r in rs:
            c.execute("DELETE FROM cal_event_list WHERE pid=?", (r[0],))
            c.execute("DELETE FROM cal_event WHERE rowid=?", (r[0],))
    return len(rs)


def _insert_rowdoc(spec: DocSpec, d: dict, ord_: int) -> int:
    c = connect()
    cols, kids = _decompose(spec, d)
    ts = now_iso()
    cols.update({"ord": ord_, "rev": 1, "created_at": ts, "updated_at": ts})
    cur = c.execute(f"INSERT INTO {spec.table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
    pid = cur.lastrowid
    for t, rows in kids.items():
        for r in rows:
            r = {spec.fk: pid, **r}
            c.execute(f"INSERT INTO {t} ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", tuple(r.values()))
    return pid


# 배분
def allocation_rows(market: str | None = None) -> list:
    rows = STORES["allocation"].all()
    return [r for r in rows if market is None or str(r.get("market", "")).lower() == market.lower()]


def allocation_last(market: str, before_stamp: str | None = None):
    rows = [r for r in allocation_rows(market) if before_stamp is None or str(r.get("stamp")) < before_stamp]
    return rows[-1] if rows else None


def allocation_record(row: dict) -> int:
    """같은 id면 바꾸고(정상 pass·재투자 pass) 없으면 끝에 붙인다."""
    return STORES["allocation"].put(row)


# 자산 곡선 — 추가만
def equity_rows(market: str | None = None, since: str | None = None, family: str | None = None) -> list:
    """family='paper'|'real' 이면 계좌 계열로 거른다(account_mode가 없는 옛 행은 paper)."""
    c = connect()
    where, args = [], []
    if market:
        where.append("UPPER(market)=?")
        args.append(market.upper())
    if since:
        where.append("date>=?")
        args.append(since)
    if family:
        # 계좌 계열 — paper(옛 행은 account_mode 없음) 대 그 밖 전부(real·real_readonly) · risk_guard._acct_family와 같은 정의
        if family == "real":
            where.append("(account_mode IS NOT NULL AND account_mode<>'paper')")
        else:
            where.append("(account_mode IS NULL OR account_mode='paper')")
    rs = [dict(r) for r in c.execute(
        f"SELECT rowid AS rid, * FROM ex_equity_point {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY rowid", args)]
    kids = {}
    for r in c.execute("SELECT * FROM ex_equity_position ORDER BY src, ord"):
        kids.setdefault(r["pid"], {}).setdefault("ex_equity_position", []).append(dict(r))
    return [_compose(SPEC["equity"], r, kids.get(r["rid"], {})) for r in rs]


def equity_append(row: dict) -> int:
    with tx():
        n = connect().execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM ex_equity_point").fetchone()[0]
        pid = _insert_rowdoc(SPEC["equity"], row, n)
        mark_export("equity_curve")
    return pid


# 유니버스(감시 목록) — dim_company가 원본, config/watchlist.json은 렌더본
def companies(market: str | None = None, universe: bool = True) -> list:
    where, args = [], []
    if market:
        where.append("market=?")
        args.append(market.upper())
    if universe:
        where.append("in_watchlist=1")
    rows = companies_store.all(" AND ".join(where), tuple(args))
    return rows


def company_put(d: dict, in_watchlist: bool = True, keep_market_key: bool = False) -> int:
    """종목 행 하나(감시 목록 편입·갱신). market은 표 키라 렌더본 행에는 나오지 않는다."""
    with tx():
        r = _put_company_row(dict(d), None, has_market_key=keep_market_key, in_watchlist=in_watchlist)
    return r


def watchlist_doc() -> dict:
    """지금 `config/watchlist.json` 모양(KR·US 목록은 dim_company의 감시 목록 행 · market 칸은 표 키라 행에 없다)."""
    meta = docmeta_get("watchlist") or {"_ko": ["_comment", "schema_version", "KR", "US", "benchmarks"]}
    return {k: (companies(k) if k in ("KR", "US") else meta.get(k)) for k in meta["_ko"]}


def watchlist_save(doc: dict) -> int:
    """옛 watchlist.json 통째 쓰기 자리 — KR·US 목록을 dim_company(in_watchlist)에 맞춘다."""
    n = 0
    with tx():
        meta = {k: v for k, v in doc.items() if k not in ("KR", "US")}
        meta["_ko"] = list(doc.keys())
        docmeta_put("watchlist", meta)
        c = connect()
        for m in ("KR", "US"):
            keep = []
            for i, r in enumerate(doc.get(m) or []):
                d = {"market": m, **r} if "market" not in r else dict(r)
                _put_company_row(d, i, has_market_key="market" in r)
                keep.append(d["ticker"])
                n += 1
            if keep:
                c.execute(f"UPDATE dim_company SET in_watchlist=0 WHERE market=? AND ticker NOT IN ({','.join('?' * len(keep))})",
                          (m, *keep))
            else:  # NOT IN (NULL)은 늘 NULL이라 아무것도 안 뺀다 — 빈 목록이면 그 시장을 전부 뺀다
                c.execute("UPDATE dim_company SET in_watchlist=0 WHERE market=?", (m,))
        mark_export("watchlist")
    return n


def _put_company_row(d: dict, ord_, has_market_key: bool, in_watchlist: bool = True) -> int:
    # 판·이력·자식 행은 DocStore.put이 다룬다. market은 표 키지 watchlist 행의 칸이 아니다 — ko에서 뺀다.
    rev = companies_store.put(d, ord_=ord_)
    c = connect()
    r = c.execute("SELECT rid, ko FROM dim_company WHERE market=? AND ticker=?", (d["market"], d["ticker"])).fetchone()
    ko = json.loads(r["ko"]) if r["ko"] else []
    if not has_market_key and "market" in ko:
        ko.remove("market")
    c.execute("UPDATE dim_company SET ko=?, in_watchlist=? WHERE rid=?",
              (json.dumps(ko, ensure_ascii=False), 1 if in_watchlist else 0, r["rid"]))
    return rev


def main_names_refresh(market: str, rows: list, as_of: str, source: str = "") -> int:
    """시총 상위 명단 → dim_company.main_rank(감시 목록 밖 종목도 행을 만든다 — in_watchlist=0)."""
    with tx():
        c = connect()
        c.execute("UPDATE dim_company SET main_rank=NULL, main_as_of=NULL WHERE market=?", (market.upper(),))
        for i, r in enumerate(rows, 1):
            ex = c.execute("SELECT rowid FROM dim_company WHERE market=? AND ticker=?", (market.upper(), r["ticker"])).fetchone()
            if ex:
                # 감시 목록 밖 행은 이번에 응답한 거래소 코드로 고친다(시세·주문 코드가 틀리면 빈 응답) — 감시 목록 행의 excd는 손대지 않는다
                c.execute("UPDATE dim_company SET main_rank=?, main_as_of=?, main_source=?, main_mcap=?,"
                          " excd=CASE WHEN in_watchlist=0 THEN COALESCE(?, excd) ELSE excd END,"
                          " name=CASE WHEN in_watchlist=0 THEN COALESCE(?, name) ELSE name END WHERE rowid=?",
                          (i, as_of, source, r.get("mcap_100m_krw") or r.get("mcap_usd") or r.get("mcap"), r.get("excd"),
                           r.get("name"), ex[0]))
            else:
                ts = now_iso()
                c.execute("INSERT INTO dim_company (market, ticker, name, excd, in_watchlist, main_rank, main_as_of, main_source,"
                          " main_mcap, ko, ord, rev, created_at, updated_at) VALUES (?,?,?,?,0,?,?,?,?,?,?,1,?,?)",
                          (market.upper(), r["ticker"], r.get("name"), r.get("excd"), i, as_of, source,
                           r.get("mcap_100m_krw") or r.get("mcap_usd") or r.get("mcap"),
                           json.dumps(["ticker", "name"]), 10_000 + i, ts, ts))
    return len(rows)


def main_names(market: str) -> list:
    return q("SELECT ticker, name, excd, main_rank, main_as_of, main_mcap, in_watchlist FROM dim_company "
             "WHERE market=? AND main_rank IS NOT NULL ORDER BY main_rank", (market.upper(),))


# 섹터 짝(국내 업종 ↔ 미국 섹터 ETF) — 옛 config/sector_bridge.json
def sector_bridge_save(doc: dict) -> int:
    """옛 sector_bridge.json 모양을 dim_sector_link에 맞춘다(이전·통째 갱신 자리).
    links → live · retired_links → retired · kr_without_us/us_without_kr → 한쪽만 있는 gap 행. 그 밖 키는 docmeta."""
    lists = ("links", "retired_links", "kr_without_us", "us_without_kr")
    with tx():
        meta = {k: v for k, v in doc.items() if k not in lists}
        meta["_ko"] = list(doc.keys())
        docmeta_put("sector_bridge", meta)
        c = connect()
        c.execute("DELETE FROM dim_sector_link")
        n = 0
        for lk in lists:
            for r in doc.get(lk) or []:
                if not isinstance(r, dict):
                    r = {"_scalar": r}
                status = "retired" if lk == "retired_links" else ("gap" if lk in ("kr_without_us", "us_without_kr") else "live")
                c.execute("INSERT INTO dim_sector_link (kr_sector, kr_name, us_sector, sign, confidence, why, status, retired_why,"
                          " src, body, ord) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (r.get("kr"), r.get("kr_name"), r.get("us"), r.get("sign"), r.get("confidence"),
                           r.get("note") if lk != "kr_without_us" and lk != "us_without_kr" else (r.get("why") or r.get("note")),
                           status, r.get("why") if status == "retired" else None, lk, json.dumps(r, ensure_ascii=False), n))
                n += 1
    return n


def sector_bridge_doc() -> dict:
    """옛 config/sector_bridge.json 모양 그대로(키 순서 포함)."""
    meta = docmeta_get("sector_bridge") or {"_ko": ["links", "retired_links", "kr_without_us", "us_without_kr"]}
    rows = q("SELECT src, body FROM dim_sector_link ORDER BY ord, id")
    out = {}
    for k in meta["_ko"]:
        if k in ("links", "retired_links", "kr_without_us", "us_without_kr"):
            items = []
            for r in rows:
                if r["src"] == k:
                    b = json.loads(r["body"])
                    items.append(b["_scalar"] if set(b) == {"_scalar"} else b)
            out[k] = items
        else:
            out[k] = meta.get(k)
    return out


def sector_link_retire(us: str, kr: str, why: str, by: str, day: str) -> bool:
    """3회 역행한 짝을 내린다 — live 행을 retired_links 끝으로 옮기고 사유를 남긴다."""
    with tx():
        c = connect()
        r = c.execute("SELECT id, body FROM dim_sector_link WHERE src='links' AND us_sector=? AND kr_sector=?", (us, kr)).fetchone()
        if not r:
            return False
        b = json.loads(r["body"])
        b.update({"retired": day, "by": by, "why": why})
        n = c.execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM dim_sector_link").fetchone()[0]
        c.execute("UPDATE dim_sector_link SET src='retired_links', status='retired', retired_why=?, body=?, ord=? WHERE id=?",
                  (why, json.dumps(b, ensure_ascii=False), n, r["id"]))
    return True


def sector_links(status: str = "live") -> list:
    """판정용 행(컬럼) — kr_sector·us_sector·sign·confidence·why."""
    return q("SELECT kr_sector, kr_name, us_sector, sign, confidence, why, status FROM dim_sector_link WHERE status=? ORDER BY ord",
             (status,))


# 재료 — 발행분이 어느 run의 재료였나(같은 발행분을 국내·미국 run이 함께 받는다)
def material_issue_run_put(run: str, ids: list) -> None:
    with tx():
        c = connect()
        for i, iid in enumerate(ids):
            c.execute("INSERT INTO mat_issue_run (issue_id, run_id, ord) VALUES (?,?,?) ON CONFLICT(issue_id, run_id) DO UPDATE"
                      " SET ord=excluded.ord", (iid, run, i))


def material_issues_for_run(run: str) -> list:
    return [r["issue_id"] for r in q("SELECT issue_id FROM mat_issue_run WHERE run_id=? ORDER BY ord", (run,))]


def material_digest_put(run: str, path: str | None, head: str | None, n_issues: int | None = None,
                        spam_count: int | None = None, collected_at: str | None = None) -> None:
    connect().execute("INSERT INTO mat_digest (run_id, path, head, n_issues, spam_count, collected_at) VALUES (?,?,?,?,?,?)"
                      " ON CONFLICT(run_id) DO UPDATE SET path=excluded.path, head=excluded.head, n_issues=excluded.n_issues,"
                      " spam_count=excluded.spam_count, collected_at=excluded.collected_at",
                      (run, path, head, n_issues, spam_count, collected_at or now_iso()))


def material_digest_get(run: str):
    return q1("SELECT * FROM mat_digest WHERE run_id=?", (run,))


# ───────────────────────────── 파일 단위 메타·작은 문서 ─────────────────────────────

def docmeta_get(name: str):
    r = q1("SELECT body FROM sys_docmeta WHERE doc=?", (name,))
    return json.loads(r["body"]) if r else None


def docmeta_put(name: str, body: dict) -> None:
    connect().execute("INSERT INTO sys_docmeta (doc, body, updated_at) VALUES (?,?,?) "
                      "ON CONFLICT(doc) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
                      (name, json.dumps(body, ensure_ascii=False), now_iso()))


def kv_get(table: str, key: str):
    r = q1(f"SELECT body FROM {table} WHERE k=?", (key,))
    return json.loads(r["body"]) if r else None


def kv_all(table: str) -> dict:
    return {r["k"]: json.loads(r["body"]) for r in q(f"SELECT k, body FROM {table} ORDER BY ord, rowid")}


_KV_EXPORT = {"op_coverage": "universe_coverage"}


def kv_put(table: str, key: str, body) -> None:
    c = connect()
    n = c.execute(f"SELECT COALESCE(MAX(ord), -1) + 1 FROM {table}").fetchone()[0]
    c.execute(f"INSERT INTO {table} (k, body, ord, updated_at) VALUES (?,?,?,?) "
              f"ON CONFLICT(k) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
              (key, json.dumps(body, ensure_ascii=False), n, now_iso()))
    if table in _KV_EXPORT:
        mark_export(_KV_EXPORT[table])


def kv_save(table: str, d: dict) -> None:
    """옛 dict 통째 쓰기 자리 — 순서까지 그대로 맞춘다."""
    with tx():
        c = connect()
        c.execute(f"DELETE FROM {table}")
        for i, (k, v) in enumerate(d.items()):
            c.execute(f"INSERT INTO {table} (k, body, ord, updated_at) VALUES (?,?,?,?)",
                      (k, json.dumps(v, ensure_ascii=False), i, now_iso()))
        if table in _KV_EXPORT:
            mark_export(_KV_EXPORT[table])


def kv_delete(table: str, key: str) -> None:
    connect().execute(f"DELETE FROM {table} WHERE k=?", (key,))
    if table in _KV_EXPORT:
        mark_export(_KV_EXPORT[table])


def coverage_load() -> dict:
    return kv_all("op_coverage")


def coverage_save(d: dict) -> None:
    kv_save("op_coverage", d)


def coverage_put(key: str, body: dict) -> None:
    kv_put("op_coverage", key, body)


def pending_load() -> dict:
    return kv_all("op_pending")


def pending_save(d: dict) -> None:
    kv_save("op_pending", d)


def _bench_key(market: str) -> str:
    # 시장 부분만 대문자 — 실계좌 기준점 키 'KR:real'의 접미사는 그대로 둔다
    head, sep, tail = market.partition(":")
    return head.upper() + sep + tail


def benchmark_get(market: str | None = None):
    d = kv_all("op_benchmark_anchor")
    return d.get(_bench_key(market)) if market else d


def benchmark_set(market: str, body: dict) -> None:
    kv_put("op_benchmark_anchor", _bench_key(market), body)


def current_run_read():
    return kv_get("op_current_run", "current")


def current_run_write(d: dict) -> None:
    kv_put("op_current_run", "current", d)


# 포지션 고점
def peaks_for_market(market: str) -> dict:
    return {r["ticker"]: {"peak_price": r["peak_price"], "peak_at": r["peak_at"]}
            for r in q("SELECT * FROM ex_position_peak WHERE market=? ORDER BY ord, rowid", (market.upper(),))}


def peaks_all() -> dict:
    out = {"_schema": 2}
    for m in ("KR", "US"):
        out[m] = peaks_for_market(m)
    return out


def peaks_update(market: str, positions: list, now: str | None = None) -> dict:
    """보유 종목의 고점을 한 트랜잭션에서 올린다. 보유하지 않은 종목의 고점은 지운다(재진입 시 새로 시작)."""
    now = now or now_iso()
    m = market.upper()
    with tx():
        c = connect()
        held = set()
        for p in positions or []:
            t = str(p.get("ticker") or "")
            px = p.get("price")
            if not t:
                continue
            held.add(t)  # 가격이 비었거나 0 이하여도 보유 중이면 고점은 지키고 갱신만 건너뛴다
            if px is None or float(px) <= 0:
                continue
            r = c.execute("SELECT peak_price FROM ex_position_peak WHERE market=? AND ticker=?", (m, t)).fetchone()
            if r is None:
                n = c.execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM ex_position_peak").fetchone()[0]
                c.execute("INSERT INTO ex_position_peak (market, ticker, peak_price, peak_at, ord) VALUES (?,?,?,?,?)",
                          (m, t, float(px), now, n))
            elif float(px) > float(r[0]):
                c.execute("UPDATE ex_position_peak SET peak_price=?, peak_at=? WHERE market=? AND ticker=?",
                          (float(px), now, m, t))
        if positions is not None:
            for r in c.execute("SELECT ticker FROM ex_position_peak WHERE market=?", (m,)).fetchall():
                if r[0] not in held:
                    c.execute("DELETE FROM ex_position_peak WHERE market=? AND ticker=?", (m, r[0]))
    return peaks_for_market(m)


def peaks_set(market: str, ticker: str, peak_price: float, peak_at: str) -> None:
    c = connect()
    n = c.execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM ex_position_peak").fetchone()[0]
    c.execute("INSERT INTO ex_position_peak (market, ticker, peak_price, peak_at, ord) VALUES (?,?,?,?,?) "
              "ON CONFLICT(market, ticker) DO UPDATE SET peak_price=excluded.peak_price, peak_at=excluded.peak_at",
              (market.upper(), ticker, peak_price, peak_at, n))


def peaks_reset(market: str | None = None) -> None:
    if market:
        connect().execute("DELETE FROM ex_position_peak WHERE market=?", (market.upper(),))
    else:
        connect().execute("DELETE FROM ex_position_peak")


# ───────────────────────────── 로그(추가만) ─────────────────────────────
# jsonl 로그는 행 하나 = 표 행 하나. 원본 dict를 body에 그대로 두고(op_ 로그는 계획상 JSON 칸 허용),
# 자주 거르는 칸(ts·market·stamp)만 컬럼으로 뽑는다.

LOGS = {
    # 이름: (표, 추가만?, 옛 파일)
    "incidents": ("op_incident", True, "incidents.jsonl"),
    "push_log": ("op_push", True, "push_log.jsonl"),
    "test_runs": ("op_test_run", True, "test_runs.jsonl"),
    "fetch_log": ("op_fetch", True, "fetch_log.jsonl"),
    "stops_log": ("op_stops", True, "stops_log.jsonl"),
    "run_log": ("op_run_log", True, "run_log.jsonl"),
    "reviews": ("op_review", True, "reviews.jsonl"),
    "universe_log": ("op_universe", True, "universe_log.jsonl"),
    "bridge_log": ("op_bridge", True, "bridge_log.jsonl"),
    "regret": ("op_regret", True, "regret.jsonl"),
    "rename_log": ("op_rename", True, "rename_log.jsonl"),
    "audit_reversals": ("ex_reversal_event", True, "audit_reversals.jsonl"),
    "sessions": ("op_session", False, "sessions.jsonl"),
    "sector_history": ("op_sector_history", False, "sector_history.jsonl"),
    "sector_watch": ("op_sector_watch", False, "sector_watch.jsonl"),
    "feedback": ("op_feedback", False, "feedback.jsonl"),
    "account_switch": ("op_account_switch", True, "account_switch.jsonl"),
}


def _log_cols(row: dict) -> tuple:
    ts = row.get("ts") or row.get("at") or row.get("recorded_at") or row.get("created_at") or row.get("date")
    mk = row.get("market")
    st = row.get("stamp") or row.get("session") or row.get("week")
    rid = row.get("id")
    return (str(ts) if ts is not None else None, str(mk).lower() if mk else None,
            str(st) if st is not None else None, str(rid) if rid is not None else None)


def log_append(name: str, row: dict) -> int:
    table = LOGS[name][0]
    ts, mk, st, rid = _log_cols(row)
    c = connect()
    cur = c.execute(f"INSERT INTO {table} (ts, market, stamp, rid, body) VALUES (?,?,?,?,?)",
                    (ts, mk, st, rid, json.dumps(row, ensure_ascii=False)))
    if name == "feedback":
        mark_export("feedback")
    return cur.lastrowid


def log_rows(name: str, **where) -> list:
    table = LOGS[name][0]
    w, a = [], []
    for k, v in where.items():
        if k in ("ts_from",):
            w.append("ts>=?")
        elif k in ("ts_to",):
            w.append("ts<?")
        else:
            w.append(f"{k}=?")
            if k == "market" and isinstance(v, str):
                v = v.lower()
        a.append(v)
    return [json.loads(r["body"]) for r in q(
        f"SELECT body FROM {table} {('WHERE ' + ' AND '.join(w)) if w else ''} ORDER BY seq", tuple(a))]


def log_replace(name: str, rows: list) -> None:
    """통째 다시 쓰던 로그(sessions·sector_history·sector_watch·feedback)만 — 추가만 표는 거부."""
    table, append_only, _ = LOGS[name]
    if append_only:
        raise RuntimeError(f"{name}은 추가만 하는 기록이다 — 고쳐 쓰지 않는다")
    with tx():
        c = connect()
        c.execute(f"DELETE FROM {table}")
        for r in rows:
            ts, mk, st, rid = _log_cols(r)
            c.execute(f"INSERT INTO {table} (ts, market, stamp, rid, body) VALUES (?,?,?,?,?)",
                      (ts, mk, st, rid, json.dumps(r, ensure_ascii=False)))
        if name == "feedback":
            mark_export("feedback")


def log_upsert(name: str, row: dict, key: str = "id") -> None:
    """id가 같은 행을 바꾼다(feedback 상태 변경 등) — 추가만 표는 거부."""
    table, append_only, _ = LOGS[name]
    if append_only:
        raise RuntimeError(f"{name}은 추가만 하는 기록이다")
    ts, mk, st, rid = _log_cols(row)
    c = connect()
    with tx(c):
        r = c.execute(f"SELECT seq FROM {table} WHERE rid=?", (str(row.get(key)),)).fetchone()
        if r:
            c.execute(f"UPDATE {table} SET ts=?, market=?, stamp=?, body=? WHERE seq=?",
                      (ts, mk, st, json.dumps(row, ensure_ascii=False), r[0]))
        else:
            c.execute(f"INSERT INTO {table} (ts, market, stamp, rid, body) VALUES (?,?,?,?,?)",
                      (ts, mk, st, rid, json.dumps(row, ensure_ascii=False)))
        if name == "feedback":
            mark_export("feedback")


# 소급(감사 뒤 되파는 것) — 이벤트를 접어 현재 상태를 낸다
def reversals_all() -> list:
    """이벤트 접기: 같은 id의 뒤 행이 앞 행의 칸을 덮는다(옛 reversals.load와 같은 결과)."""
    folded = {}
    order = []
    for r in log_rows("audit_reversals"):
        i = r.get("id")
        if not i:
            continue  # id 없는 행은 어느 되돌림에도 붙지 않는다(옛 reversals.load와 같다)
        if i not in folded:
            folded[i] = {}
            order.append(i)
        folded[i].update(r)
    return [folded[i] for i in order]


def reversals_pending(market: str | None = None) -> list:
    return [r for r in reversals_all() if r.get("status") == "pending"
            and (market is None or str(r.get("market", "")).lower() == market.lower())]


def reversal_append(row: dict) -> int:
    row = dict(row)
    row.setdefault("ts", now_iso())
    return log_append("audit_reversals", row)


def reversal_mark(rid: str, status: str, **kw) -> int:
    return reversal_append({"id": rid, "status": status, "ts": now_iso(), **kw})


# 규율 검사 회차 기억(2026-10-19) — risk_guard가 회차마다 제안별 지문·결과를 남기고 다음 회차가 읽는다(추가만)
def screen_rows(run_id: str) -> list:
    """이 run의 회차 기록 — 회차·기록 순. `canon`·`ctx`는 dict로 되돌린다."""
    out = []
    for r in q("SELECT * FROM ex_screen WHERE run_id=? ORDER BY pass_no, id", (run_id,)):
        for k in ("canon", "ctx"):
            try:
                r[k] = json.loads(r[k]) if r.get(k) else {}
            except ValueError:
                r[k] = {}
        out.append(r)
    return out


def screen_put(rows: list) -> int:
    """한 회차의 행을 한 트랜잭션으로 넣는다."""
    c = connect()
    with tx(c):
        for r in rows:
            _ex(c, "INSERT INTO ex_screen (run_id, pass_no, ts, mode, ticker, action, fingerprint, canon, ctx, outcome, why_class, why,"
                   " limits_sha, approved_path, signal_sha) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["run_id"], int(r["pass_no"]), r.get("ts") or now_iso(), r.get("mode"), str(r.get("ticker")), r.get("action"),
                 r.get("fingerprint"), json.dumps(r.get("canon") or {}, ensure_ascii=False, sort_keys=True),
                 json.dumps(r.get("ctx") or {}, ensure_ascii=False, sort_keys=True), r.get("outcome"), r.get("why_class"),
                 r.get("why"), r.get("limits_sha"), r.get("approved_path"), r.get("signal_sha")))
    return len(rows)


# 요인 상태 · 요인 판정 (FB-261017-7 · 2026-10-17) — 상태는 1단 market_map.exposure_by_driver(stamp)가 쓰고 risk_guard가 run id로 읽는다 ·
# 판정은 시그널 factor_judgments[]를 import_run_artifacts가 옮긴다
FACTOR_STATES = ("intact", "broken", "unknown")
FACTOR_DECISIONS = ("저점매수", "유지", "정리")


def factor_state_put(rid: str, rows: list) -> int:
    """이 run의 요인 상태 — 행을 지우고 다시 쓴다(멱등). rows = exposure_by_driver 결과 행(`state` 칸이 있는 것만)."""
    c = connect()
    n = 0
    with tx(c):
        _ex(c, "DELETE FROM mk_factor_state_member WHERE run_id=?", (rid,))
        _ex(c, "DELETE FROM mk_factor_state WHERE run_id=?", (rid,))
        for r in rows:
            st = r.get("state") if isinstance(r.get("state"), dict) else None
            if not st:
                continue
            _ex(c, "INSERT INTO mk_factor_state (run_id, driver, computed_at, snapshot, n_members, n_with_bars, index_last, index_peak,"
                   " peak_date, drawdown_pct, band_med_pct, band3_pct, fired_dd, sells_window, sells_n, sells_tickers, fired_sell, fired,"
                   " state, body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, r["driver"], st.get("computed_at") or now_iso(), st.get("snapshot"), st.get("n_members"), st.get("n_with_bars"),
                 st.get("index_last"), st.get("index_peak"), st.get("peak_date"), st.get("drawdown_pct"), st.get("band_med_pct"),
                 st.get("band3_pct"), 1 if st.get("fired_dd") else 0, _j(st.get("sells_window")), st.get("sells_n"),
                 _j(st.get("sells_tickers")), 1 if st.get("fired_sell") else 0, 1 if st.get("fired") else 0, st.get("state"),
                 json.dumps({k: v for k, v in st.items() if k != "members"}, ensure_ascii=False)))
            for m in st.get("members") or []:
                _ex(c, "INSERT OR REPLACE INTO mk_factor_state_member (run_id, driver, ticker, market, held, sign, weight_pct, last_close,"
                       " has_bars) VALUES (?,?,?,?,?,?,?,?,?)",
                    (rid, r["driver"], str(m.get("ticker")), m.get("market"), 1 if m.get("held") else 0, m.get("sign"),
                     m.get("weight_pct"), m.get("last_close"), 1 if m.get("has_bars") else 0))
            n += 1
    return n


def factor_state(rid: str) -> list:
    """이 run의 요인 상태 행(멤버 포함) — 없으면 [](1단이 안 쟀다)."""
    out = []
    for r in q("SELECT * FROM mk_factor_state WHERE run_id=? ORDER BY driver", (rid,)):
        d = dict(r)
        for k in ("sells_window", "sells_tickers"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else []
            except ValueError:
                d[k] = []
        d["fired"], d["fired_dd"], d["fired_sell"] = bool(d["fired"]), bool(d["fired_dd"]), bool(d["fired_sell"])
        d["members"] = [dict(m, held=bool(m["held"]), has_bars=bool(m["has_bars"]))
                        for m in q("SELECT * FROM mk_factor_state_member WHERE run_id=? AND driver=? ORDER BY ticker",
                                   (rid, d["driver"]))]
        out.append(d)
    return out


def factor_judgments(rid: str) -> list:
    """이 run의 요인 판정(결정 포함) — 시그널 factor_judgments[]를 import_run_artifacts가 옮긴 것."""
    out = []
    for r in q("SELECT * FROM jd_factor_judgment WHERE run_id=? ORDER BY driver", (rid,)):
        d = dict(r)
        for k in ("fired_by", "sources", "extra"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else ([] if k != "extra" else {})
            except ValueError:
                pass
        d["decisions"] = [dict(x) for x in q("SELECT * FROM jd_factor_decision WHERE run_id=? AND driver=? ORDER BY ticker",
                                             (rid, d["driver"]))]
        out.append(d)
    return out


# 재투자 대기
def refill_get(stamp: str, market: str):
    return kv_get("ex_refill", run_id(stamp, market))


def refill_put(stamp: str, market: str, d: dict) -> None:
    kv_put("ex_refill", run_id(stamp, market), d)


def refill_open(market: str | None = None, with_key: bool = False) -> list:
    """열린 재투자 대기. with_key면 [(stamp, market, body)] — 본문에 stamp 칸이 없는 행도 키에서 읽는다."""
    out = []
    for k, v in kv_all("ex_refill").items():
        st, _, mk = k.partition("_")
        mk = (v.get("market") or mk or "").lower()
        if v.get("status") in ("open", "pending", "deferred") and (market is None or mk == market.lower()):
            out.append((st, mk, v) if with_key else v)
    return out


# ───────────────────────────── run · 집행 기록 ─────────────────────────────

RUN_STAGES = ("capture", "carry", "map", "note", "dispatch", "sent", "filled", "closed")


def run_start(stamp: str, market: str, account_mode: str | None = None, session_date: str | None = None) -> str:
    """run 행을 연다(있으면 빈 칸만). ★ 계좌 계열은 시작 때 정해져 바뀌지 않는다(v4 · Codex R09) — 같은 날짜_시장을 다른 계열로
    다시 시작하면 RunModeConflict(트리거 tg_run_mode_fixed가 뒤를 받친다). 같은 계열 안의 라벨 변화(real_readonly → real)는 받는다."""
    rid = run_id(stamp, market)
    ts = now_iso()
    if account_mode:
        old = connect().execute("SELECT account_mode FROM ex_run WHERE run_id=?", (rid,)).fetchone()
        if old and old[0] and acct_family_of(old[0]) != acct_family_of(account_mode):
            raise RunModeConflict(f"run {rid}는 {old[0]}로 시작했다 — 같은 날 {account_mode}로 다시 시작할 수 없다"
                                  f"(계좌 전환은 account_switch · 날이 바뀐 뒤)")
    connect().execute(
        "INSERT INTO ex_run (run_id, stamp, market, account_mode, session_date, started) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(run_id) DO UPDATE SET account_mode=COALESCE(excluded.account_mode, ex_run.account_mode), "
        "session_date=COALESCE(excluded.session_date, ex_run.session_date)",
        (rid, stamp, market.lower(), account_mode, session_date, ts))
    return rid


def stage_mark(stamp: str, market: str, stage: str, at: str | None = None) -> None:
    if stage not in RUN_STAGES:
        raise ValueError(f"단계 이름 {stage} — {RUN_STAGES}")
    rid = run_id(stamp, market)
    run_start(stamp, market)
    connect().execute(f"UPDATE ex_run SET {stage}_at=COALESCE({stage}_at, ?) WHERE run_id=?", (at or now_iso(), rid))


# ── 단계 결과(v4 · Codex U02) — 단계마다 시도 행(ex_stage) · 단계 안 명령(ex_stage_step) · 산출물 해시(ex_stage_artifact).
#    ex_run.<단계>_at은 "성공한(ok·reduced) 첫 시도"의 끝 시각(execute는 시작 시각 = 첫 전송)만 stage_finish가 쓴다.
STAGES = ("capture", "carry", "map", "research", "note", "verify", "audit", "dispatch", "no_trade", "execute", "fill", "close")
_STAGE_COL = {"capture": "capture_at", "carry": "carry_at", "map": "map_at", "note": "note_at", "dispatch": "dispatch_at",
              "no_trade": "dispatch_at", "execute": "sent_at", "fill": "filled_at", "close": "closed_at"}
STAGE_LABEL = {"capture": "1단", "carry": "2단", "map": "3단", "research": "3단 리서치", "note": "4단", "verify": "검증",
               "audit": "감사", "dispatch": "5단", "no_trade": "5단", "execute": "6단 전송", "fill": "6단 체결", "close": "마감"}


def stage_status(steps) -> str:
    """단계 안 명령들 → 단계 상태: 필수 실패가 있으면 failed · 선택 실패·축소가 있으면 reduced · 아니면 ok."""
    if any(x.get("required") and x.get("status") == "failed" for x in steps):
        return "failed"
    if any(x.get("status") in ("failed", "reduced") for x in steps):
        return "reduced"
    return "ok"


def stage_start(stamp: str, market: str, stage: str, argv=None) -> int:
    if stage not in STAGES:
        raise ValueError(f"단계 이름 {stage} — {STAGES}")
    run_start(stamp, market)
    rid = run_id(stamp, market)
    with tx() as c:
        n = c.execute("SELECT COALESCE(MAX(attempt), 0) + 1 FROM ex_stage WHERE run_id=? AND stage=?", (rid, stage)).fetchone()[0]
        return c.execute("INSERT INTO ex_stage (run_id, stage, attempt, status, started_at, pid, argv) VALUES (?,?,?,'running',?,?,?)",
                         (rid, stage, n, now_iso(), os.getpid(), json.dumps(list(argv or sys.argv), ensure_ascii=False))).lastrowid


def _file_meta(path):
    f = Path(path)
    if not f.is_file():
        return None
    b = f.read_bytes()
    return hashlib.sha256(b).hexdigest(), len(b), datetime.fromtimestamp(f.stat().st_mtime, KST).isoformat(timespec="seconds")


def _rel_path(path) -> str:
    try:
        return str(Path(path).absolute().relative_to(HERE))
    except ValueError:
        return str(Path(path).absolute())


def stage_finish(stage_id: int, status: str, rc=None, steps=(), artifacts=None, note: str = "") -> dict:
    """단계를 닫는다 — 명령 행·산출물 해시(지금 파일 바이트)·끝 시각. ok·reduced면 ex_run.<단계>_at을 처음 한 번만 채운다."""
    ended = now_iso()
    with tx() as c:
        row = dict(c.execute("SELECT * FROM ex_stage WHERE id=?", (stage_id,)).fetchone())
        for i, x in enumerate(steps or []):
            c.execute("INSERT OR REPLACE INTO ex_stage_step (stage_id, ord, name, cmd, required, rc, status, observed_at, note)"
                      " VALUES (?,?,?,?,?,?,?,?,?)",
                      (stage_id, i, str(x.get("name") or f"step{i}")[:200], json.dumps(x.get("cmd"), ensure_ascii=False) if x.get("cmd") else None,
                       1 if x.get("required") else 0, x.get("rc"), x.get("status") or "ok", x.get("observed_at") or ended,
                       (str(x.get("note"))[:500] if x.get("note") else None)))
        for role, path in (artifacts or {}).items():
            m = _file_meta(path) if path else None
            if m:
                c.execute("INSERT OR REPLACE INTO ex_stage_artifact (stage_id, role, path, sha256, bytes, mtime) VALUES (?,?,?,?,?,?)",
                          (stage_id, role, _rel_path(path), *m))
        c.execute("UPDATE ex_stage SET status=?, rc=?, ended_at=?, note=COALESCE(?, note) WHERE id=?",
                  (status, rc, ended, note or None, stage_id))
        col = _STAGE_COL.get(row["stage"])
        if col and status in ("ok", "reduced"):
            at = row["started_at"] if row["stage"] == "execute" else ended
            c.execute(f"UPDATE ex_run SET {col}=COALESCE({col}, ?) WHERE run_id=?", (at, row["run_id"]))
    return {**row, "status": status, "rc": rc, "ended_at": ended}


def stage_record(stamp: str, market: str, stage: str, status: str = "ok", rc=None, steps=(), artifacts=None, note: str = "") -> dict:
    """시작과 끝을 한 번에(도구 밖에서 끝난 단계 — 노트·결정 스탬프 등)."""
    return stage_finish(stage_start(stamp, market, stage), status, rc, steps, artifacts, note)


def stage_last_ok(rid: str, stage: str):
    r = q1("SELECT * FROM v_stage_last_ok WHERE run_id=? AND stage=?", (rid, stage))
    if r:
        r["artifacts"] = {a["role"]: a for a in q("SELECT * FROM ex_stage_artifact WHERE stage_id=?", (r["id"],))}
    return r


def stage_artifact_matches(rid: str, stage: str, role: str, path) -> bool:
    """마지막 성공 시도가 그 산출물을 기록했고, 지금 그 파일의 해시가 같은가(바뀌었거나 다른 파일이면 False)."""
    r = stage_last_ok(rid, stage)
    a = (r or {}).get("artifacts", {}).get(role)
    m = _file_meta(path) if a else None
    return bool(a and m and a["sha256"] == m[0] and a["path"] == _rel_path(path))


def stage_limitations(rid: str) -> list:
    """성공(ok·reduced)한 단계들의 마지막 시도에서 실패·축소된 선택 명령 → `[제한·1단] <이름> — <비고>` 줄."""
    out = []
    for s_ in q("SELECT * FROM v_stage_last_ok WHERE run_id=? ORDER BY id", (rid,)):
        for x in q("SELECT * FROM ex_stage_step WHERE stage_id=? AND required=0 AND status IN ('failed','reduced') ORDER BY ord",
                   (s_["id"],)):
            out.append(f"[제한·{STAGE_LABEL.get(s_['stage'], s_['stage'])}] {x['name']} — "
                       + (x["note"] or (f"종료코드 {x['rc']}" if x.get("rc") is not None else x["status"])))
    return out


def run_set(stamp: str, market: str, **cols) -> None:
    rid = run_id(stamp, market)
    run_start(stamp, market)
    if cols:
        connect().execute(f"UPDATE ex_run SET {', '.join(k + '=?' for k in cols)} WHERE run_id=?", (*cols.values(), rid))


def run_get(stamp: str, market: str):
    return q1("SELECT * FROM ex_run WHERE run_id=?", (run_id(stamp, market),))


def run_close(stamp: str, market: str, **cols) -> None:
    stage_mark(stamp, market, "closed")
    if cols:
        run_set(stamp, market, **cols)


def _num(v):
    """집행 장부 숫자 칸(v8 STRICT) — 숫자나 숫자 문자열은 float, 빈 문자열·글자는 None(원문은 body에 그대로 남는다)."""
    if v is None or isinstance(v, bool):
        return None if v is None else float(v)
    try:
        return float(str(v).replace(",", "")) if not isinstance(v, (int, float)) else float(v)
    except ValueError:
        return None


def _int(v):
    """정수 칸(수량) — 정수로 떨어지는 값만 int, 아니면 None."""
    f = _num(v)
    return int(f) if f is not None and f == int(f) else None


def _txt(v):
    return None if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))


def balance_put(stamp: str, market: str, phase: str, bal: dict, at: str | None = None) -> int:
    """잔고 스냅샷(캡처·체결 뒤·stops·probe) — 원본 칸 이름이 시장·계좌마다 달라 대표 칸만 뽑는다."""
    def g(*ks):
        for k in ks:
            v = bal.get(k)
            if v is not None:
                return v
        return None
    cash = g("cash", "cash_krw", "deposit", "dnca_tot_amt")
    fx = _num(g("exchange_rate", "exrt"))
    cur_ = bal.get("currency") if bal.get("currency") in ("KRW", "USD") else None
    cur = connect().execute(
        "INSERT INTO ex_balance (run_id, market, phase, cash, cash_field, exchange_rate, exchange_rate_at, deposit_only, "
        "cash_shared, buying_power, orderable, equity, currency, at, body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id(stamp, market), market.lower(), phase, _num(cash), _txt(bal.get("cash_field")), fx if fx and fx > 0 else None,
         _txt(bal.get("exchange_rate_at")), 1 if bal.get("deposit_only") else 0 if "deposit_only" in bal else None,
         1 if bal.get("cash_shared") else 0, _num(g("buying_power", "orderable_cash")), _num(g("orderable", "ord_psbl_amt")),
         _num(g("equity", "total_equity", "tot_evlu_amt")), cur_, at or now_iso(),
         json.dumps(bal, ensure_ascii=False, default=str)))
    return cur.lastrowid


def balance_last(market: str, phase: str | None = None):
    r = q1("SELECT * FROM ex_balance WHERE market=? " + ("AND phase=? " if phase else "") + "ORDER BY id DESC LIMIT 1",
           (market.lower(), phase) if phase else (market.lower(),))
    if r and r.get("body"):
        r["body"] = json.loads(r["body"])
    return r


def positions_put(stamp: str, market: str, phase: str, positions: list, at: str | None = None) -> int:
    at = at or now_iso()
    c = connect()
    with tx(c):
        for p in positions or []:
            c.execute("INSERT INTO ex_position (run_id, market, phase, ticker, name, qty, avg_price, price, eval_amt, pnl_pct, at)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (run_id(stamp, market), market.lower(), phase, str(p.get("ticker")), p.get("name"), p.get("qty"),
                       p.get("avg_price"), p.get("price"), p.get("eval_amt"), p.get("pnl_pct"), at))
    return len(positions or [])


def import_run_artifacts(stamp: str, market: str, here: Path | None = None) -> dict:
    """그 run의 시그널·승인·체결·스냅샷 파일을 집행 표로 옮긴다(멱등 — 같은 파일 해시는 한 번만)."""
    here = here or HERE
    rid = run_id(stamp, market)
    mk = market.lower()
    out = {"signal": 0, "proposals": 0, "approvals": 0, "orders": 0, "fills": 0}
    c = connect()
    sig = here / "signals" / f"signal_{stamp}_{mk}.json"
    run_start(stamp, mk)
    with tx(c):
        if sig.exists():
            raw = sig.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            old = _ex(c, "SELECT sha FROM ex_signal WHERE run_id=?", (rid,)).fetchone()
            if not old or old[0] != sha:
                s = json.loads(raw)
                # ★ 시그널 판본(v10 · Codex J03) — 판마다 한 행(추가만) · 제안은 판별로 남긴다(덮어쓰지 않는다 · 지금 판은 v_proposal)
                _ex(c, "INSERT OR IGNORE INTO ex_signal_version (sha, run_id, generated_at, path, imported_at, body) VALUES (?,?,?,?,?,?)",
                    (sha, rid, s.get("generated_at"), str(sig.relative_to(here)), now_iso(), raw.decode("utf-8", errors="replace")))
                _ex(c, "DELETE FROM ex_hold_fact WHERE run_id=?", (rid,))
                _ex(c, "DELETE FROM ex_hold WHERE run_id=?", (rid,))
                _ex(c, "INSERT INTO ex_signal (run_id, generated_at, no_trade, no_trade_reason, note_lint, track1, path, sha)"
                          " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET generated_at=excluded.generated_at,"
                          " no_trade=excluded.no_trade, no_trade_reason=excluded.no_trade_reason, note_lint=excluded.note_lint,"
                          " track1=excluded.track1, path=excluded.path, sha=excluded.sha",
                          (rid, s.get("generated_at"), 1 if s.get("no_trade") else 0, s.get("no_trade_reason"),
                           json.dumps(s.get("note_lint"), ensure_ascii=False) if s.get("note_lint") is not None else None,
                           json.dumps(s.get("track1"), ensure_ascii=False) if s.get("track1") is not None else None,
                           str(sig.relative_to(here)), sha))
                out["signal"] = 1
                for i, p in enumerate(s.get("proposals") or []):
                    tb = p.get("target_basis") if isinstance(p.get("target_basis"), dict) else {}
                    ts_ = p.get("thesis_structure") if isinstance(p.get("thesis_structure"), dict) else {}
                    _ex(c, "INSERT OR IGNORE INTO ex_proposal (run_id, sha, idx, ticker, market, action, thesis_id, axis_id, weight_target_pct,"
                              " expected_upside_pct, horizon_days, confidence, size_why, concentration_why, increase_when, entry,"
                              " exits, risk_notes, parent, is_refill_candidate, body, target_level_kind, target_level_price, target_level_source,"
                              " target_chosen_reason, target_chosen_at, mechanism, priced_in_vs_new, strongest_counter, kill_observation,"
                              " decision_deadline) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (rid, sha, i, str(p.get("ticker")), mk, p.get("action"), p.get("thesis_id"), p.get("axis_id") or p.get("forecast_id"),
                               p.get("weight_target_pct"), p.get("expected_upside_pct"), p.get("horizon_days"),
                               p.get("confidence"), p.get("size_why"), p.get("concentration_why"), p.get("increase_when"),
                               _j(p.get("entry")), _j(p.get("exits") or p.get("exit_triggers")), _j(p.get("risk_notes")),
                               p.get("parent"), 0, json.dumps(p, ensure_ascii=False), tb.get("level_kind"), _num(tb.get("level_price")),
                               _txt(tb.get("level_source")), _txt(tb.get("chosen_reason")), _txt(tb.get("chosen_at")),
                               _txt(ts_.get("mechanism")), _txt(ts_.get("priced_in_vs_new")), _txt(ts_.get("strongest_counter")),
                               _txt(ts_.get("kill_observation")), _txt(ts_.get("decision_deadline"))))
                    out["proposals"] += 1
                    for f in p.get("facts") or []:
                        if isinstance(f, dict) and f.get("id"):
                            ver = _ex(c, "SELECT rev FROM ev_fact WHERE id=?", (f["id"],)).fetchone()
                            if ver is None:  # 원장에 없는 사실 id — 근거 연결만 빼고 집행 기록은 남긴다
                                out.setdefault("missing_facts", []).append(f["id"])
                                continue
                            _ex(c, "INSERT OR IGNORE INTO ex_proposal_fact (run_id, sha, idx, fact_id, role, support, fact_version)"
                                      " VALUES (?,?,?,?,?,?,?)", (rid, sha, i, f["id"], f.get("role"), f.get("support"), ver[0]))
                # 배분 판단의 외부 사실도 주문 근거다(국면·현금 명분 숫자) — regime_refs의 S 번호·사실 id와 allocation.facts를
                # jd_allocation_fact(배분 id = AL-<stamp>-<시장>)로 옮겨 track1 큐에 올린다
                al = s.get("allocation") if isinstance(s.get("allocation"), dict) else {}
                aid = f"AL-{stamp}-{mk}"
                _ex(c, "DELETE FROM jd_allocation_fact WHERE allocation_id=?", (aid,))
                refs = [(f.get("id"), f.get("role") or "support") for f in (al.get("facts") or []) if isinstance(f, dict)]
                refs += [(r, "support") for r in (al.get("regime_refs") or []) if isinstance(r, str)]
                for ref, role in refs:
                    fid = _ref_fact(c, rid, ref, out)
                    if fid:
                        _ex(c, "INSERT OR IGNORE INTO jd_allocation_fact (allocation_id, fact_id, role) VALUES (?,?,?)", (aid, fid, role))
                for k, p in enumerate(s.get("refill_candidates") or []):
                    i = 1000 + k
                    _ex(c, "INSERT OR IGNORE INTO ex_proposal (run_id, sha, idx, ticker, market, action, thesis_id, is_refill_candidate, body)"
                              " VALUES (?,?,?,?,?,?,?,1,?)", (rid, sha, i, str(p.get("ticker")), mk, p.get("action"), p.get("thesis_id"),
                                                               json.dumps(p, ensure_ascii=False)))
                    # 재투자 후보의 근거도 주문 근거다 — track1 큐에 올라야 재투자 pass가 근거 없이 거부되지 않는다
                    for f in p.get("facts") or []:
                        if isinstance(f, dict) and f.get("id"):
                            ver = _ex(c, "SELECT rev FROM ev_fact WHERE id=?", (f["id"],)).fetchone()
                            if ver is None:
                                out.setdefault("missing_facts", []).append(f["id"])
                                continue
                            _ex(c, "INSERT OR IGNORE INTO ex_proposal_fact (run_id, sha, idx, fact_id, role, support, fact_version)"
                                   " VALUES (?,?,?,?,?,?,?)", (rid, sha, i, f["id"], f.get("role"), f.get("support"), ver[0]))
                for h in s.get("holds") or s.get("hold") or []:
                    if isinstance(h, dict):
                        _ex(c, "INSERT OR IGNORE INTO ex_hold (run_id, ticker, thesis_id, why) VALUES (?,?,?,?)",
                                  (rid, str(h.get("ticker")), h.get("thesis_id"), h.get("why") or h.get("reason")))
                        # 보유 판단의 근거 — facts(사실 id) 또는 sources(S 번호·사실 id · 파일·도구 참조는 사실 행이 아니라 건너뛴다)
                        hrefs = [f.get("id") if isinstance(f, dict) else f for f in (h.get("facts") or [])]
                        hrefs += [x for x in (h.get("sources") or []) if isinstance(x, str)]
                        for ref in hrefs:
                            fid = _ref_fact(c, rid, ref, out)
                            if fid:
                                _ex(c, "INSERT OR IGNORE INTO ex_hold_fact (run_id, ticker, fact_id) VALUES (?,?,?)",
                                    (rid, str(h.get("ticker")), fid))
                # 요인 판정(FB-261017-7) — 꺾인 요인마다 원인·상태·종목별 결정. 머리는 driver·state(intact|broken|unknown)·cause 필수,
                # 결정은 ticker·decision(저점매수|유지|정리) 필수 — 틀린 행은 넣지 않고 factor_skipped에 적는다
                _ex(c, "DELETE FROM jd_factor_decision WHERE run_id=?", (rid,))
                _ex(c, "DELETE FROM jd_factor_judgment WHERE run_id=?", (rid,))
                for fj in s.get("factor_judgments") or []:
                    if not (isinstance(fj, dict) and str(fj.get("driver") or "").strip() and fj.get("state") in FACTOR_STATES
                            and str(fj.get("cause") or "").strip()):
                        out.setdefault("factor_skipped", []).append(f"판정 머리 {fj.get('driver') if isinstance(fj, dict) else fj!r}"
                                                                    " — driver·state(intact|broken|unknown)·cause 필수")
                        continue
                    drv = str(fj["driver"]).strip()
                    known = {"driver", "scenario_id", "fired_by", "cause", "state", "sources", "needs_review", "decisions"}
                    _ex(c, "INSERT OR REPLACE INTO jd_factor_judgment (run_id, driver, scenario_id, fired_by, cause, state, sources,"
                           " needs_review, extra, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (rid, drv, fj.get("scenario_id"), _j(fj.get("fired_by") or []), str(fj["cause"]).strip(), fj["state"],
                         _j(fj.get("sources") or []), 1 if fj.get("needs_review") else 0,
                         _j({k: v for k, v in fj.items() if k not in known}) if set(fj) - known else None, now_iso()))
                    out["factor_judgments"] = out.get("factor_judgments", 0) + 1
                    for dd in fj.get("decisions") or []:
                        if not (isinstance(dd, dict) and str(dd.get("ticker") or "").strip() and dd.get("decision") in FACTOR_DECISIONS):
                            out.setdefault("factor_skipped", []).append(f"{drv} 결정 {dd!r} — ticker·decision(저점매수|유지|정리) 필수")
                            continue
                        dknown = {"ticker", "thesis_id", "held", "decision", "why", "sources"}
                        _ex(c, "INSERT OR REPLACE INTO jd_factor_decision (run_id, driver, ticker, thesis_id, held, decision, why, sources,"
                               " extra) VALUES (?,?,?,?,?,?,?,?,?)",
                            (rid, drv, str(dd["ticker"]).strip(), dd.get("thesis_id"), 1 if dd.get("held") else 0, dd["decision"],
                             dd.get("why"), _j(dd.get("sources") or []),
                             _j({k: v for k, v in dd.items() if k not in dknown}) if set(dd) - dknown else None))
        for ap in sorted(here.glob(f"signals/approved_{stamp}_{mk}*.json")):
            raw = ap.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            if _ex(c, "SELECT 1 FROM ex_approval WHERE approved_sha=? LIMIT 1", (sha,)).fetchone():
                continue
            a = json.loads(raw)
            pas = "stops" if "_stops" in ap.name else "refill" if "_refill" in ap.name else "main"
            hid = tuple(_txt(a.get(k)) for k in ("signal_sha256", "snapshot_sha256", "universe_sha256", "material_sha256", "code_commit", "code_sha"))
            # 이 승인을 낸 심사 회차(W05) — 승인 머리 screen.pass. 결과 표가 심사 행 → 이 승인 행 → 주문 → 체결로 잇는다
            hid = hid + (_int((a.get("screen") or {}).get("pass")) if isinstance(a.get("screen"), dict) else None,)
            for i, o in enumerate(a.get("orders") or []):
                if str(o.get("side") or o.get("action") or "").upper() not in ("BUY", "SELL") or not (_int(o.get("qty")) or 0) > 0:
                    out.setdefault("skipped_rows", []).append(f"승인 {ap.name}#{i} {o.get('ticker')} — 방향·수량이 주문 모양이 아니다")
                    continue                         # v8 제약을 어기는 행은 옮기지 않고 보고한다(한 행이 마감 전체를 막지 않게)
                # idx = 승인 파일 안 순번 — 집행기가 거래 행에 적는 approved_idx(`_idx`)와 같은 뜻(v7 · R10 · 주문↔승인 연결 열쇠)
                _ex(c, "INSERT INTO ex_approval (run_id, pass, idx, ticker, market, action, qty, price, source, reasons,"
                          " rejected_why, needs_sell_fill, rounded_up_to_1, order_capped, signal_ref, approved_path, approved_sha,"
                          " body, signal_sha, snapshot_sha, universe_sha, material_sha, code_commit, code_sha, screen_pass)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (rid, pas, i, str(o.get("ticker")), mk, str(o.get("side") or o.get("action")).upper(), _int(o.get("qty")),
                           _num(o.get("price") or o.get("limit_price")), _txt(o.get("source")), _j(o.get("reasons")), None,
                           1 if o.get("needs_sell_fill") else 0, 1 if o.get("rounded_up_to_1") else 0,
                           1 if o.get("order_capped") else 0, _j(o.get("signal_ref")), str(ap.relative_to(here)), sha,
                           json.dumps(o, ensure_ascii=False)) + hid)
                out["approvals"] += 1
                br_ = o.get("base_rate") if isinstance(o.get("base_rate"), dict) else None
                if (br_ or o.get("upside_to_target_pct") is not None) and o.get("action") == "BUY" and hid[0]:
                    # 승인된 매수의 목표까지 거리·기저율(J01)을 그 시그널 판의 제안 행에 — 심사가 계산한 값 그대로
                    _ex(c, "UPDATE ex_proposal SET upside_to_target_pct=?, base_rate_cond=?, base_rate_n=?, base_rate_median_pct=?,"
                           " base_rate_hit_pct=?, base_rate_horizon_days=?, base_rate_as_of=? WHERE run_id=? AND sha=? AND ticker=? AND action='BUY'",
                        (_num(o.get("upside_to_target_pct")), (br_ or {}).get("cond"), _int((br_ or {}).get("n")), _num((br_ or {}).get("median_pct")),
                         _num((br_ or {}).get("hit_pct")), _int((br_ or {}).get("horizon_days")), _txt((br_ or {}).get("as_of")),
                         rid, hid[0], str(o.get("ticker"))))
            for o in a.get("rejected") or []:
                if str(o.get("side") or o.get("action") or "").upper() not in ("BUY", "SELL"):
                    out.setdefault("skipped_rows", []).append(f"거부 {ap.name} {o.get('ticker')} — 방향이 없다")
                    continue
                _ex(c, "INSERT INTO ex_approval (run_id, pass, ticker, market, action, rejected_why, approved_path, approved_sha, body,"
                          " signal_sha, snapshot_sha, universe_sha, material_sha, code_commit, code_sha, screen_pass)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (rid, pas, str(o.get("ticker")), mk, str(o.get("side") or o.get("action")).upper(),
                           _j(o.get("reasons") or o.get("why") or "(사유 칸 없음)"), str(ap.relative_to(here)), sha,
                           json.dumps(o, ensure_ascii=False)) + hid)
        tp = here / "journal" / f"trades_{stamp}_{mk}.json"
        if tp.exists():
            # trades 파일은 체결 확인·손절 pass가 다시 쓴다 — 파일 해시가 아니라 거래 하나하나로 거른다(추가만 표라 겹치면 못 지운다)
            tr = json.loads(tp.read_bytes())
            sha = hashlib.sha256(tp.read_bytes()).hexdigest()
            # 그날 기록의 집행 호출마다 승인 스냅샷(runs[].approved_orders) — 승인 파일이 덮여 저장소에 그 판이 없을 때 되살린다(R10)
            runs_by_sha = {}
            for i_run, rr in enumerate(tr.get("runs") or []):
                if isinstance(rr, dict) and rr.get("approved_sha256") and rr["approved_sha256"] not in runs_by_sha:
                    runs_by_sha[rr["approved_sha256"]] = (i_run, rr)
            for t in tr.get("trades") or []:
                res = t.get("result") if isinstance(t.get("result"), dict) else {}
                ono = res.get("order_no")
                tkey = hashlib.sha256(json.dumps({k: t.get(k) for k in ("ts", "ticker", "action", "qty", "price")},
                                                 ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                ikey = t.get("intent_key") or None
                asha = t.get("approved_sha256") or None
                aidx = t.get("approved_idx")
                # 주문 모양 검사(v8) — 방향·양수 수량이 없으면 주문이 아니다(옮기지 않고 보고). 상태 칸이 비면 체결 판정·주문번호가 있을 때만
                # SENT(보낸 사실이 기록에 있다), 아니면 UNKNOWN(모른다) — 원문은 body에 그대로 남는다.
                side_ = str(t.get("action") or "").upper()
                fv_ = (t.get("fill") or {}).get("verdict") if isinstance(t.get("fill"), dict) else None
                st_ = str(t.get("status") or "").upper() or ("SENT" if (ono or fv_ in ("FILLED", "PARTIAL")) else "UNKNOWN")
                if side_ not in ("BUY", "SELL") or not (_int(t.get("qty")) or 0) > 0 or st_ not in ORDER_STATUSES:
                    out.setdefault("skipped_rows", []).append(f"거래 {t.get('ticker')} {t.get('action')} x{t.get('qty')} 상태 {t.get('status')!r}"
                                                              " — 주문 모양이 아니다")
                    continue
                # 같은 주문 = 같은 의도 키(보내기 전 PENDING 행이 접수 뒤 주문번호를 얻어도 한 행) → 주문번호 → 거래 열쇠(옛 행)
                ex = _ex(c, "SELECT id, approval_id, approval_link FROM ex_order WHERE run_id=? AND intent_key=?", (rid, ikey)).fetchone() if ikey else None
                if ex is None and ono:
                    ex = _ex(c, "SELECT id, approval_id, approval_link FROM ex_order WHERE run_id=? AND order_no=?", (rid, ono)).fetchone()
                if ex is None and not ono:
                    ex = _ex(c, "SELECT id, approval_id, approval_link FROM ex_order WHERE run_id=? AND order_no IS NULL AND trade_key=?",
                             (rid, tkey)).fetchone()
                aid, alink = _approval_link(c, rid, mk, t, asha, aidx, runs_by_sha, tp.name, out)
                if ex:
                    oid = ex[0]
                    if aid and (ex[1] is None or (ex[2] == "inferred" and alink == "recorded")):
                        _ex(c, "UPDATE ex_order SET approval_id=?, approval_link=? WHERE id=?", (aid, alink, oid))
                else:
                    oid = _ex(c, "INSERT INTO ex_order (run_id, ticker, market, side, qty, order_no, sent_at, status, limit_price,"
                                    " approved_ref, thesis_id, trigger_id, trades_sha, trade_key, body, approval_id, approved_sha, approved_idx,"
                                    " intent_key, approval_link) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (rid, str(t.get("ticker")), mk, side_, _int(t.get("qty")), _txt(ono), _txt(t.get("ts")) or now_iso(),
                                     st_, _num(t.get("price")), _txt(t.get("approved_ref")), _txt(t.get("thesis_id")),
                                     _txt(t.get("trigger_id")), sha, tkey, json.dumps(t, ensure_ascii=False), aid, asha, _int(aidx), ikey,
                                     alink)).lastrowid
                    out["orders"] += 1
                fl = t.get("fill") if isinstance(t.get("fill"), dict) else None
                if fl is None or not fl.get("verdict"):
                    continue                         # 판정 없는 fill(보내기 전 PENDING 행의 qty_before만)은 체결 관측이 아니다
                vd_ = str(fl["verdict"]).upper()
                # ★ 체결은 관측이다(v7 · R11) — 수량은 부호 없음(방향은 ex_order.side) · 마지막 관측과 같으면 다시 넣지 않는다
                #   (A→B→A는 이력으로 다시 남는다). 주문의 지금 상태는 v_fill_latest — 행을 합치지 않는다.
                fq = fl.get("filled_qty")
                if fq is None and fl.get("delta") is not None:
                    fq = fl["delta"] if vd_ in ("FILLED", "PARTIAL") else 0
                fq = abs(fq) if isinstance(fq, (int, float)) and not isinstance(fq, bool) else fq
                last = _ex(c, "SELECT verdict, filled_qty, avg_price, qty_after FROM ex_fill WHERE order_id=? ORDER BY id DESC LIMIT 1",
                           (oid,)).fetchone()
                if last and (last[0], abs(last[1]) if isinstance(last[1], (int, float)) else last[1], last[2], last[3]) == \
                        (vd_, fq, fl.get("avg_price"), fl.get("qty_after")):
                    continue
                if vd_ not in FILL_VERDICTS or (vd_ in ("FILLED", "PARTIAL") and fq is not None and not fq):
                    out.setdefault("skipped_rows", []).append(f"체결 {t.get('ticker')} 판정 {fl.get('verdict')!r} 수량 {fq} — 관측 모양이 아니다")
                    continue
                fc = _ex(c, "INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, avg_price, limit_price, verdict,"
                               " qty_before, qty_after, requested, settle_date, at, body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (oid, rid, str(t.get("ticker")), mk, _int(fq), _num(fl.get("avg_price")), _num(fl.get("limit_price")),
                                vd_, _int(fl.get("qty_before")), _int(fl.get("qty_after")), _int(fl.get("requested")),
                                _txt(t.get("settle_date") or fl.get("settle_date")),
                                _txt(fl.get("confirmed_at") or fl.get("checked_at") or t.get("ts")),
                                json.dumps(fl, ensure_ascii=False)))
                out["fills"] += 1
                fx = _num(t.get("settle_fx") if t.get("settle_fx") is not None else fl.get("settle_fx"))
                if fx is not None and fx > 0:
                    _ex(c, "INSERT INTO ex_fill_fx (fill_id, settle_fx, source, at) VALUES (?,?,?,?)",
                              (fc.lastrowid, fx, t.get("settle_fx_source") or fl.get("settle_fx_source"),
                               t.get("settle_fx_at") or fl.get("settle_fx_at")))
    return out


ORDER_STATUSES = ("PENDING", "SENT", "UNKNOWN", "REJECTED", "UNSENT", "FAILED", "FILLED")       # ex_order.status CHECK와 같은 어휘(v8)
FILL_VERDICTS = ("FILLED", "PARTIAL", "CANCELLED", "EXPIRED", "REJECTED", "UNFILLED", "UNKNOWN")  # ex_fill.verdict CHECK와 같은 어휘(v8)


def _approval_link(c, rid: str, mk: str, t: dict, asha, aidx, runs_by_sha: dict, trades_name: str, out: dict) -> tuple:
    """거래 행 → (승인 행 id, 연결 근거). ① 보낸 승인 파일 해시 + 그 안 순번 → recorded ② 그 해시의 승인 행이 저장소에 없으면(파일이 덮여
    이전 전에 사라짐) 그날 기록 runs[]의 승인 스냅샷으로 승인 행을 되살린 뒤 ① ③ 해시가 없는 옛 행은 (run, 파일명, 종목, 방향)이
    수량 있는 승인 행 하나에만 맞을 때 inferred · 아니면 (None, None) — 관계를 지어내지 않는다(v7 · Codex R10)."""
    if asha and aidx is not None:
        q_ = "SELECT id FROM ex_approval WHERE approved_sha=? AND idx=? AND qty IS NOT NULL ORDER BY id LIMIT 1"
        r = c.execute(q_, (asha, int(aidx))).fetchone()
        if r:
            return r[0], "recorded"
        if not c.execute("SELECT 1 FROM ex_approval WHERE approved_sha=? LIMIT 1", (asha,)).fetchone() and asha in runs_by_sha:
            i_run, rr = runs_by_sha[asha]
            ref = Path(str(rr.get("approved_ref") or "")).name
            pas = "stops" if "_stops" in ref else "refill" if "_refill" in ref else "main"
            for k, o in enumerate(rr.get("approved_orders") or []):
                if not isinstance(o, dict):
                    continue
                vals = (rid, pas, _int(o.get("idx", k)), str(o.get("ticker")), mk, o.get("action") or o.get("side"), _int(o.get("qty")) or None,
                        _num(o.get("price")), _txt(o.get("source")), f"journal:{trades_name}#runs[{i_run}]", asha, json.dumps(o, ensure_ascii=False))
                if "screen_pass" in _cols(c, "ex_approval"):     # v10 칸 — 이전 중간 판(v7)에서는 아직 없다
                    _ex(c, "INSERT INTO ex_approval (run_id, pass, idx, ticker, market, action, qty, price, source, approved_path, approved_sha,"
                           " body, screen_pass) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + (_int(rr.get("screen_pass")),))
                else:
                    _ex(c, "INSERT INTO ex_approval (run_id, pass, idx, ticker, market, action, qty, price, source, approved_path, approved_sha,"
                           " body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", vals)
                out["approvals_restored"] = out.get("approvals_restored", 0) + 1
            r = c.execute(q_, (asha, int(aidx))).fetchone()
            if r:
                return r[0], "recorded"
        return None, None
    ref = Path(str(t.get("approved_ref") or "")).name
    if not ref:
        return None, None
    rows = c.execute("SELECT DISTINCT id FROM ex_approval WHERE run_id=? AND ticker=? AND action=? AND qty IS NOT NULL"
                     " AND (approved_path=? OR approved_path LIKE ?)", (rid, str(t.get("ticker")), t.get("action"), ref, "%/" + ref)).fetchall()
    return (rows[0][0], "inferred") if len(rows) == 1 else (None, None)


def order_intents(rid: str, approved_sha: str) -> set:
    """저장소에 이미 들어간 그 승인 파일의 주문 의도 키(v7) — 집행기가 보내기 전에 그날 기록과 대조한다(기록이 덮여 행을 잃었으면 멈춘다)."""
    return {r["intent_key"] for r in q("SELECT intent_key FROM ex_order WHERE run_id=? AND approved_sha=? AND intent_key IS NOT NULL",
                                       (rid, approved_sha))}


def _j(v):
    return None if v is None else json.dumps(v, ensure_ascii=False)


def _ref_fact(c, rid: str, ref, out: dict):
    """시그널의 근거 참조 하나 → 사실 id. `S12`·`[S12]`(이 run의 S 번호)·`F-000731`·`SRC-0012`만 사실이다 —
    파일(`data/…`)·도구(`tools:…`)·일정(`cal:…`)·URL·리서치 행 번호는 사실 행이 아니라 None. 못 찾은 사실은 missing_facts."""
    if not ref:
        return None
    ref = str(ref).strip()
    m = re.fullmatch(r"\[?S(\d+)\]?", ref)
    if m:
        row = c.execute("SELECT fact_id FROM ev_fact_run WHERE run_id=? AND s_no=?", (rid, int(m.group(1)))).fetchone()
        fid = row[0] if row else None
    elif re.fullmatch(r"(F|SRC)-\d+", ref):
        fid = ref if c.execute("SELECT 1 FROM ev_fact WHERE id=?", (ref,)).fetchone() else None
    else:
        return None
    if fid is None:
        out.setdefault("missing_facts", []).append(ref)
    return fid


def _sv(v):
    """스칼라는 그대로, dict·list는 JSON 문자열 — 시그널·승인·거래 파일의 자유 형식 칸(예: increase_when이 dict)."""
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def _ex(c, sql, params=()):
    return c.execute(sql, tuple(_sv(x) for x in params))


def fills_find(run: str, ticker: str, side: str | None = None, sent_at: str | None = None, without_fx: bool = True) -> list:
    """체결 행 id — run_id·종목(·방향·주문 시각)으로 찾는다. without_fx면 결제일 환율이 아직 없는 것만."""
    w, a = ["f.run_id=?", "f.ticker=?"], [run, str(ticker)]
    if side:
        w.append("UPPER(o.side)=?")
        a.append(side.upper())
    if sent_at:
        w.append("o.sent_at=?")
        a.append(sent_at)
    if without_fx:
        # 환율은 주문 단위(v7 · R11) — 그 주문의 어느 관측 행에든 환율이 붙었으면 끝난 것이다(v_tax도 주문 단위로 읽는다)
        w.append("NOT EXISTS (SELECT 1 FROM ex_fill_fx x JOIN ex_fill g ON g.id=x.fill_id WHERE g.order_id=f.order_id)")
    return [r["id"] for r in q(f"SELECT f.id FROM v_fill_latest f LEFT JOIN ex_order o ON o.id=f.order_id WHERE {' AND '.join(w)} ORDER BY f.id",
                                tuple(a))]


def fill_fx_put(fill_id: int, settle_fx: float, source: str = "") -> None:
    connect().execute("INSERT INTO ex_fill_fx (fill_id, settle_fx, source, at) VALUES (?,?,?,?)",
                      (fill_id, settle_fx, source, now_iso()))


# ───────────────────────────── 시장 관측(일봉·업종 보드·시세) ─────────────────────────────

def _subject(market: str, ticker: str) -> str:
    return f"{market.upper()}:{ticker}"


def _iso(d) -> str:
    d = str(d)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d


def _ymd(d) -> str:
    return str(d).replace("-", "")


BAR_TTL_MIN = 10  # 열린 세션이 든 조회(장중 값)는 바뀐다 — 10분 안의 재조회만 표에서 준다(빈 결과도 같은 창 — 잘못된 거래소 탐색의 반복 호출 방지)

# ★ 일봉 관측 계약(2026-10-19 · Codex U04 · 저장소 판 v5) — 확정은 거래량이 아니라 시계로 정한다: 관측 시각 > 그 세션의 마감이면 확정.
#   need_final=True(기본 — 기저율·가격 수준·국면·후보 측정·회고): 끝 날짜를 마지막 닫힌 세션(kis_client.last_session_date)으로 자른 뒤
#   묻고, 그 키가 "받을 때 그 날짜까지 닫혀 있었다"(final_through)면 표에서 준다 — 지난 날짜라고 영원히 믿지 않는다(장중에 받은 행은
#   마감 뒤 다시 본다). need_final=False(손절 감시·보드): 열린 세션 행을 미확정(is_final=0)으로 저장하고 TTL로만 표에서 준다.
#   값이 바뀌거나 확정되면 mk_bar_obs에 관측을 덧붙인다(그때 무엇을 알았나 — bars_asof). 확정 값은 미확정 관측이 덮지 않는다.


def _bar_market(subject: str) -> str:
    return "US" if str(subject).startswith("US:") else "KR"


def last_session_ymd(market: str, now=None) -> str:
    from kis_client import last_session_date
    return last_session_date(market, now or datetime.now(KST)).strftime("%Y%m%d")   # 이 모듈의 시계(시험이 고정한다)


def _is_final(market: str, date_s, observed_at) -> bool:
    """그 세션일의 일봉을 그 시각에 봤다면 확정인가 — 관측 시각 > 그 세션 마감(KST · 미국은 그 날짜의 서머타임)."""
    from kis_client import session_close
    try:
        d = datetime.fromisoformat(_iso(date_s)).date()
        t = datetime.fromisoformat(str(observed_at))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=KST)
    return t > session_close(market, d)


def _key_serves(r, end_ymd: str, open_incl: bool, ttl: float) -> bool:
    if not r:
        return False
    try:
        age = datetime.now(KST) - datetime.fromisoformat(r["fetched_at"])
    except (TypeError, ValueError):
        return False
    if not r.get("n"):
        return age <= timedelta(minutes=BAR_TTL_MIN)
    if open_incl:
        return ttl > 0 and age <= timedelta(minutes=ttl)
    ft = r.get("final_through")
    return bool(ft) and _ymd(ft) >= end_ymd


def _bars_hit(subject: str, key: str, prefix: str, start: str, end_ymd: str, open_incl: bool, ttl: float):
    """그 조회를 표에서 줄 수 있는 조회 키 행(정확히 같은 키 → 더 넓은 구간 키) · 없으면 None."""
    r = q1("SELECT * FROM mk_bar_fetch WHERE subject=? AND k=?", (subject, key))
    if _key_serves(r, end_ymd, open_incl, ttl):
        return r
    if prefix in ("dd", "di"):
        for r2 in q("SELECT * FROM mk_bar_fetch WHERE subject=? AND k LIKE ?", (subject, prefix + ":%")):
            parts = r2["k"].split(":")
            if len(parts) == 3 and parts[1] <= start and parts[2] >= end_ymd and r2.get("n") \
                    and _key_serves(r2, end_ymd, open_incl, ttl):
                return r2
    return None


def _nonfinal_closed(subject: str, lo, hi_ymd: str) -> bool:
    """닫힌 세션인데 미확정 관측만 가진 행이 구간 안에 있나 — 있으면 다시 받는다(장중에 받은 행이 마감 뒤에 확정되도록)."""
    a = [subject, _iso(hi_ymd)]
    w = "subject=? AND is_final=0 AND date<=?"
    if lo:
        w += " AND date>=?"
        a.append(_iso(lo))
    return q1(f"SELECT 1 AS o FROM mk_bar WHERE {w} LIMIT 1", tuple(a)) is not None


def _bars_store(subject: str, key: str, rows: list, source: str = "KIS", exchange: str = None, run_id: str = None) -> None:
    """받은 일봉 저장 — 행마다 확정 여부(관측 시각 vs 그 세션 마감) · 값이나 확정이 바뀌면 판 +1과 관측 이력 한 행 · 같으면 관측 시각만.
    확정 행을 미확정 관측이 덮지 않는다. 조회 키에는 받을 때의 마지막 닫힌 세션일(final_through · 빈 결과면 NULL)."""
    ts = now_iso()
    m = _bar_market(subject)
    with tx():
        c = connect()
        for b in rows:
            d = _iso(b.get("date"))
            vals = (b.get("open"), b.get("high"), b.get("low"), b.get("close"), b.get("volume"))
            fin = 1 if _is_final(m, d, ts) else 0
            cur = c.execute("SELECT open, high, low, close, volume, is_final, version FROM mk_bar WHERE subject=? AND date=?",
                            (subject, d)).fetchone()
            if cur is None:
                c.execute("INSERT INTO mk_bar (subject, date, open, high, low, close, volume, source, fetched_at, exchange, is_final, version)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,1)", (subject, d, *vals, source, ts, exchange, fin))
                c.execute("INSERT INTO mk_bar_obs (subject, date, open, high, low, close, volume, source, exchange, observed_at, is_final,"
                          " version, fetch_key, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                          (subject, d, *vals, source, exchange, ts, fin, key, run_id))
                continue
            if cur["is_final"] and not fin:
                continue                                     # 확정 값은 미확정 관측이 덮지 않는다(시계 어긋남)
            if tuple(cur[i] for i in range(5)) != vals or int(cur["is_final"]) != fin:
                nv = int(cur["version"] or 1) + 1
                c.execute("UPDATE mk_bar SET open=?, high=?, low=?, close=?, volume=?, source=?, fetched_at=?, exchange=COALESCE(?, exchange),"
                          " is_final=?, version=?, final_check_at=NULL, final_check_err=NULL WHERE subject=? AND date=?",
                          (*vals, source, ts, exchange, fin, nv, subject, d))
                c.execute("INSERT INTO mk_bar_obs (subject, date, open, high, low, close, volume, source, exchange, observed_at, is_final,"
                          " version, fetch_key, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (subject, d, *vals, source, exchange, ts, fin, nv, key, run_id))
            else:
                c.execute("UPDATE mk_bar SET fetched_at=? WHERE subject=? AND date=?", (ts, subject, d))
        ft = _iso(last_session_ymd(m)) if rows else None
        c.execute("INSERT INTO mk_bar_fetch (subject, k, fetched_at, n, final_through) VALUES (?,?,?,?,?) ON CONFLICT(subject, k) DO UPDATE"
                  " SET fetched_at=excluded.fetched_at, n=excluded.n, final_through=excluded.final_through",
                  (subject, key, ts, len(rows), ft))


def _bars_read(subject: str, lo: str | None, hi: str | None, limit: int | None, final_only: bool = False) -> list:
    w, a = ["subject=?"], [subject]
    if lo:
        w.append("date>=?")
        a.append(_iso(lo))
    if hi:
        w.append("date<=?")
        a.append(_iso(hi))
    if final_only:
        w.append("is_final=1")
    rows = q(f"SELECT date, close, open, high, low, volume FROM mk_bar WHERE {' AND '.join(w)} ORDER BY date DESC"
             + (f" LIMIT {int(limit)}" if limit else ""), tuple(a))
    for r in rows:
        r["date"] = _ymd(r["date"])
    return rows


def _final_rows(rows: list, last_ymd: str) -> list:
    return [r for r in rows if _ymd(r.get("date")) <= last_ymd]


def domestic_daily(client, ticker: str, start: str, end: str, ttl_min: float | None = None, *, need_final: bool = True) -> list:
    """`client.domestic_daily(ticker, start, end)`와 같은 반환(최신순 · date=YYYYMMDD). 브로커 일봉 호출은 이 세 함수 안에만 있다.
    need_final=True면 마지막 닫힌 세션까지의 확정 행만 준다(끝 날짜를 자른다) · False면 열린 세션 행도(미확정 · TTL)."""
    subj = _subject("KR", ticker)
    last = last_session_ymd("KR")
    end_q = min(_ymd(end), last) if need_final else _ymd(end)
    open_incl = (not need_final) and end_q > last
    ttl = BAR_TTL_MIN if ttl_min is None else ttl_min
    if start and end_q < _ymd(start):
        return []                                    # 확정만 묻는데 구간이 아직 닫히지 않은 세션뿐이다(그 날 장중 등)
    key = f"dd:{start}:{end_q}"
    hit = None if (open_incl and ttl <= 0) else _bars_hit(subj, key, "dd", start, end_q, open_incl, ttl)
    if hit and not open_incl and _nonfinal_closed(subj, start, end_q):
        hit = None
    if hit:
        return _bars_read(subj, start, end_q, None, final_only=need_final) if hit["n"] else []
    rows = client.domestic_daily(ticker, start, end_q)
    _bars_store(subj, key, rows, exchange="KRX")
    return _final_rows(rows, last) if need_final else rows


def domestic_index_daily(client, code: str, start: str, end: str, ttl_min: float | None = None, *, need_final: bool = True) -> list:
    subj = _subject("IDX", code)
    last = last_session_ymd("KR")
    end_q = min(_ymd(end), last) if need_final else _ymd(end)
    open_incl = (not need_final) and end_q > last
    ttl = BAR_TTL_MIN if ttl_min is None else ttl_min
    if start and end_q < _ymd(start):
        return []                                    # 확정만 묻는데 구간이 아직 닫히지 않은 세션뿐이다(그 날 장중 등)
    key = f"di:{start}:{end_q}"
    hit = None if (open_incl and ttl <= 0) else _bars_hit(subj, key, "di", start, end_q, open_incl, ttl)
    if hit and not open_incl and _nonfinal_closed(subj, start, end_q):
        hit = None
    if hit:
        return _bars_read(subj, start, end_q, None, final_only=need_final) if hit["n"] else []
    rows = client.domestic_index_daily(code, start, end_q)
    _bars_store(subj, key, rows, exchange="KRX")
    return _final_rows(rows, last) if need_final else rows


def overseas_daily(client, ticker: str, excd: str = "NAS", bymd: str = "", ttl_min: float | None = None, *,
                   need_final: bool = True) -> list:
    """`client.overseas_daily(ticker, excd, bymd)`와 같은 반환(최신순 · 최대 100행). need_final=True면 기준일이 마지막 닫힌 세션
    이후면 최신 조회(기준일 없음)로 바꾸고, 그 조회가 마지막 마감 뒤에 받은 것일 때만 표에서 준다(동부 날짜 기준 — 한국 자정 무관)."""
    subj = _subject("US", ticker)
    last = last_session_ymd("US")
    b = _ymd(bymd) if bymd else ""
    if need_final and (not b or b >= last):
        b = ""
    open_incl = (not need_final) and (not b or b > last)
    end_q = (b or last) if need_final else (b or "99999999")
    ttl = BAR_TTL_MIN if ttl_min is None else ttl_min
    key = f"od:{excd}:{b}"
    hit = None if (open_incl and ttl <= 0) else _bars_hit(subj, key, "od", "", end_q, open_incl, ttl)
    if not hit and b and open_incl and ttl > 0:
        hit = _bars_hit(subj, f"od:{excd}:", "od", "", end_q, True, ttl)   # 기준일 없는 최근 조회가 오늘 이후 기준일 조회를 덮는다
    if hit and not open_incl and _nonfinal_closed(subj, None, end_q):
        hit = None
    if hit:
        if not hit["n"]:
            return []
        return _bars_read(subj, None, end_q if need_final else (b or None), hit["n"], final_only=need_final)
    rows = client.overseas_daily(ticker, excd=excd, bymd=b)
    _bars_store(subj, key, rows, exchange=excd)
    return _final_rows(rows, last) if need_final else rows


def bars(market: str, ticker: str, since: str | None = None, until: str | None = None, index: bool = False, *,
         final_only: bool = True) -> list:
    """표에 있는 일봉만(브로커 호출 없음) — 날짜 오름차순 · date=YYYY-MM-DD. 기본은 확정 행만(final_only — 종가 계산용)."""
    subj = _subject("IDX" if index else market, ticker)
    w, a = ["subject=?"], [subj]
    if since:
        w.append("date>=?")
        a.append(_iso(since))
    if until:
        w.append("date<=?")
        a.append(_iso(until))
    if final_only:
        w.append("is_final=1")
    return q(f"SELECT date, open, high, low, close, volume, is_final, fetched_at AS observed_at, version FROM mk_bar"
             f" WHERE {' AND '.join(w)} ORDER BY date", tuple(a))


def bars_asof(subject: str, date_s, at) -> dict | None:
    """그 시각에 우리가 본 그 날 일봉(관측 이력 · J03 — 그때 판단의 재현)."""
    return q1("SELECT * FROM mk_bar_obs WHERE subject=? AND date=? AND julianday(observed_at) <= julianday(?) ORDER BY id DESC LIMIT 1",
              (subject, _iso(date_s), str(at)))


def bars_to_finalize(market: str | None = None) -> list:
    """마감됐는데 미확정인 일봉 행(오늘 아직 재확인 안 한 것) — bars_finalize가 다시 묻는 대상."""
    now = datetime.now(KST)
    today = now.date().isoformat()
    out = []
    for r in q("SELECT subject, date, exchange FROM mk_bar WHERE is_final=0 AND (final_check_at IS NULL OR substr(final_check_at,1,10) <> ?)"
               " ORDER BY subject, date", (today,)):
        m = _bar_market(r["subject"])
        if (not market or m == market.upper()) and _is_final(m, r["date"], now.isoformat(timespec="seconds")):
            out.append(r)
    return out


def bars_finalize(client, market: str | None = None, run_id: str = "") -> dict:
    """마감된 세션의 미확정 행을 다시 관측해 확정한다(매 capture · 장중 손절 감시·보드가 남긴 행). 브로커가 그 날 행을 안 주면
    승격하지 않고 final_check_at·final_check_err만 남긴다(확정 읽기에서 빠진다 · 지우지 않는다). 같은 날 다시 돌려도 한 번만 묻는다."""
    now = datetime.now(KST)
    by = {}
    for r in bars_to_finalize(market):
        by.setdefault(r["subject"], []).append(r)
    out = {"subjects": len(by), "finalized": 0, "missing": 0, "errors": 0}
    if by and callable(client) and not hasattr(client, "domestic_daily"):
        client = client()                            # 만들기 비싼 클라이언트는 할 일이 있을 때만
    for subj, items in by.items():
        tk = subj.split(":", 1)[1]
        dates = sorted(x["date"] for x in items)
        err = ""
        try:
            if subj.startswith("IDX:"):
                domestic_index_daily(client, tk, _ymd(dates[0]), _ymd(dates[-1]))
            elif subj.startswith("KR:"):
                domestic_daily(client, tk, _ymd(dates[0]), _ymd(dates[-1]))
            else:
                overseas_daily(client, tk, excd=items[0].get("exchange") or "NAS")
        except Exception as e:                       # noqa: BLE001 — 한 종목 실패가 나머지를 막지 않는다
            err = f"{type(e).__name__}: {str(e)[:120]}"
            out["errors"] += 1
        for dd in dates:
            r = q1("SELECT is_final FROM mk_bar WHERE subject=? AND date=?", (subj, dd))
            if r and r["is_final"]:
                out["finalized"] += 1
            else:
                out["missing"] += 1
                connect().execute("UPDATE mk_bar SET final_check_at=?, final_check_err=? WHERE subject=? AND date=?",
                                  (now_iso(), err or "마감 뒤 다시 물었으나 브로커가 그 날 행을 주지 않았다", subj, dd))
    return out


def holidays_sync(c=None, force: bool = False) -> int:
    """config/holidays.json(사람이 고치는 원본) → mk_holiday(사본). 표가 비었거나 파일이 바뀌었을 때만(멱등). 반환 넣은 행 수."""
    c = c or connect()
    f = CONFIG / "holidays.json"
    if not f.exists():
        return 0
    mt = str(f.stat().st_mtime)
    cur = c.execute("SELECT v FROM sys_meta WHERE k='holidays_file_mtime'").fetchone()
    n = c.execute("SELECT COUNT(*) FROM mk_holiday").fetchone()[0]
    if not force and n and cur and cur[0] == mt:
        return 0
    doc = json.loads(f.read_text(encoding="utf-8"))
    src = doc.get("source") or {}
    rows = [(m.upper(), str(d), None, (src.get(m) if isinstance(src, dict) else None), doc.get("fetched") or now_iso())
            for m in ("kr", "us") for d in (doc.get(m) or [])]
    with tx(c):
        c.execute("DELETE FROM mk_holiday")
        c.executemany("INSERT OR IGNORE INTO mk_holiday (market, date, name, source, loaded_at) VALUES (?,?,?,?,?)", rows)
        c.execute("INSERT INTO sys_meta (k, v) VALUES ('holidays_file_mtime', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (mt,))
    return len(rows)


HOLIDAY_HORIZON_DAYS = 84      # 최대 전망 시계 12주(market_map.forecast_validate의 horizon_weeks 상한) — 그 안에 표가 끝나면 갱신 필요


def holidays_coverage_line(now=None) -> str:
    """휴장일 표가 어디까지 덮나 — 남은 기간이 최대 전망 시계(12주) 안이면 ★(12월 갱신 알림을 기계가 낸다)."""
    now = now or datetime.now(KST)
    parts, warn = [], False
    for m in ("KR", "US"):
        r = q1("SELECT MAX(date) AS d FROM mk_holiday WHERE market=?", (m,))
        d = (r or {}).get("d")
        parts.append(f"{m} {d or '없음'}")
        if not d or (datetime.fromisoformat(d).date() - now.date()).days <= HOLIDAY_HORIZON_DAYS:
            warn = True
    return (("★ " if warn else "") + f"휴장일 표 커버리지 종료 {' · '.join(parts)}"
            + (" — 세션 +84일 이내라 갱신 필요(config/holidays.json에 다음 해를 채운다)" if warn else ""))


def domestic_sector_board(client, market: str = "K") -> list:
    """`client.domestic_sector_board(market)`과 같은 반환. 받은 보드를 오늘 날짜로 mk_sector_board에 남긴다."""
    rows = client.domestic_sector_board(market)
    try:
        day = datetime.now(KST).date().isoformat()
        sector_board_put("KR", day, [{"code": r.get("code") or r.get("bstp_cls_code") or r.get("key"), "name": r.get("name"),
                                      "idx": r.get("index") or r.get("price") or r.get("close"),
                                      "chg_pct": next((r.get(k) for k in ("chg_pct", "change_pct", "pct") if r.get(k) is not None), None),
                                      "body": r} for r in rows])
    except Exception as e:  # 기록 실패가 보드 사용을 막지 않는다
        print(f"[db.domestic_sector_board] 기록 실패: {e}", file=sys.stderr)
    return rows


def sector_board_put(market: str, date: str, rows: list) -> int:
    ts = now_iso()
    with tx():
        c = connect()
        for r in rows:
            c.execute("INSERT INTO mk_sector_board (market, date, code, name, idx, chg_pct, fetched_at, body) VALUES (?,?,?,?,?,?,?,?)"
                      " ON CONFLICT(market, date, code) DO UPDATE SET name=excluded.name, idx=excluded.idx,"
                      " chg_pct=excluded.chg_pct, fetched_at=excluded.fetched_at, body=excluded.body",
                      (market.upper(), date, str(r.get("code")), r.get("name"), r.get("idx"), r.get("chg_pct"), ts,
                       json.dumps(r.get("body", r), ensure_ascii=False, default=str)))
    return len(rows)


def sector_board(market: str, date: str) -> list:
    return q("SELECT code, name, idx, chg_pct, fetched_at FROM mk_sector_board WHERE market=? AND date=? ORDER BY code",
             (market.upper(), date))


def quote_put(market: str, ticker: str, price, *, ts: str | None = None, chg_pct=None, prev_close=None, high=None, low=None,
              source: str = "KIS", stamp: str | None = None) -> None:
    connect().execute("INSERT OR REPLACE INTO mk_quote (subject, ts, price, chg_pct, prev_close, high, low, source, run_id)"
                      " VALUES (?,?,?,?,?,?,?,?,?)",
                      (_subject(market, ticker), ts or now_iso(), price, chg_pct, prev_close, high, low, source,
                       run_id(stamp, market) if stamp else None))


def obs_put(table: str, **cols) -> None:
    """mk_rate·mk_fx·mk_vol·mk_prob·mk_breadth·mk_flow 관측 한 행(같은 키면 바꾼다)."""
    if table not in ("mk_rate", "mk_fx", "mk_vol", "mk_prob", "mk_breadth", "mk_flow"):
        raise ValueError(table)
    connect().execute(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      tuple(cols.values()))


# ── 계산 결과(가격 수준·기저율) ──
def level_put(market: str, ticker: str, kind: str, value=None, lo=None, hi=None, as_of: str = "", method: str = "",
              stamp: str | None = None) -> None:
    connect().execute("INSERT INTO co_level_calc (market, ticker, kind, value, lo, hi, as_of, method, run_id) VALUES (?,?,?,?,?,?,?,?,?)"
                      " ON CONFLICT(market, ticker, kind, as_of) DO UPDATE SET value=excluded.value, lo=excluded.lo,"
                      " hi=excluded.hi, method=excluded.method, run_id=excluded.run_id",
                      (market.upper(), ticker, kind, value, lo, hi, as_of, method, run_id(stamp, market) if stamp else None))


def treasury_latest(market: str = "KR") -> dict:
    """종목별 가장 최근 자기주식 보유 스냅숏 {ticker: row} — 조회 실패 행(stlm_dt='')도 포함(재조회 판단용)."""
    # 가장 최근에 조회한 행이 '최근'이다(조회 실패 행은 결산일이 '' — 결산일로 고르면 옛 성공 행이 이겨 30일 실패 캐시가 안 걸린다)
    rows = q("SELECT * FROM co_treasury_holding WHERE market=? ORDER BY ticker, COALESCE(scanned, ''), stlm_dt", (market.upper(),))
    out = {}
    for r in rows:
        out[r["ticker"]] = r
    return out


def treasury_put(row: dict) -> None:
    connect().execute("INSERT INTO co_treasury_holding (market, ticker, name, corp_cls, period, stlm_dt, issued, treasury, ratio_pct,"
                      " rcept_no, scanned, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market, ticker, stlm_dt) DO UPDATE SET"
                      " name=excluded.name, corp_cls=excluded.corp_cls, period=excluded.period, issued=excluded.issued,"
                      " treasury=excluded.treasury, ratio_pct=excluded.ratio_pct, rcept_no=excluded.rcept_no,"
                      " scanned=excluded.scanned, error=excluded.error",
                      (str(row.get("market") or "KR").upper(), row["ticker"], row.get("name"), row.get("corp_cls"), row.get("period"),
                       row.get("stlm_dt") or "", row.get("issued"), row.get("treasury"), row.get("ratio_pct"), row.get("rcept_no"),
                       row.get("scanned") or now_iso(), row.get("error")))


def levels_latest(market: str, ticker: str) -> list:
    """그 종목의 가장 최근 계산일 가격 수준 행 전부(co_level_calc · 저장소만) — 목표 기준 검사(J01)가 임의성 낮은 수준을 고른다."""
    mk = str(market).upper()
    r = q1("SELECT MAX(as_of) AS a FROM co_level_calc WHERE UPPER(market)=? AND ticker=?", (mk, str(ticker)))
    if not r or not r["a"]:
        return []
    return q("SELECT kind, value, lo, hi, method, as_of FROM co_level_calc WHERE UPPER(market)=? AND ticker=? AND as_of=?",
             (mk, str(ticker), r["a"]))


def base_rate_codes(market: str, ticker: str, window: int) -> list:
    """그 종목·시계의 마지막 계산일에 저장된 조건 코드들."""
    mk = str(market).upper()
    r = q1("SELECT MAX(as_of) AS a FROM co_base_rate WHERE UPPER(market)=? AND ticker=? AND window=?", (mk, str(ticker), int(window)))
    if not r or not r["a"]:
        return []
    return [x["condition"] for x in q("SELECT condition FROM co_base_rate WHERE UPPER(market)=? AND ticker=? AND window=? AND as_of=?",
                                      (mk, str(ticker), int(window), r["a"]))]


def base_rate_latest(market: str, ticker: str, condition: str, window: int):
    """조건부 기저율 한 행(가장 최근 계산일) · 없으면 None."""
    return q1("SELECT * FROM co_base_rate WHERE UPPER(market)=? AND ticker=? AND condition=? AND window=? ORDER BY as_of DESC LIMIT 1",
              (str(market).upper(), str(ticker), condition, int(window)))


def level_clear(market: str, ticker: str, as_of: str) -> int:
    """그 날 계산 행을 지운다 — 같은 날 다시 계산해 합류 자리가 줄면 옛 행이 남지 않게."""
    return connect().execute("DELETE FROM co_level_calc WHERE market=? AND ticker=? AND as_of=?",
                             (market.upper(), ticker, as_of)).rowcount


def levels(market: str, ticker: str, as_of: str | None = None) -> list:
    if as_of:
        return q("SELECT * FROM co_level_calc WHERE market=? AND ticker=? AND as_of=? ORDER BY kind", (market.upper(), ticker, as_of))
    r = q1("SELECT MAX(as_of) AS a FROM co_level_calc WHERE market=? AND ticker=?", (market.upper(), ticker))
    if not r or not r["a"]:
        return []
    return q("SELECT * FROM co_level_calc WHERE market=? AND ticker=? AND as_of=? ORDER BY kind", (market.upper(), ticker, r["a"]))


def base_rate_put(market: str, ticker: str, condition: str, window: int, n: int, hit_rate, avg_ret=None, median_ret=None,
                  as_of: str = "", stamp: str | None = None, baseline: dict | None = None) -> None:
    b = baseline or {}
    connect().execute("INSERT INTO co_base_rate (market, ticker, condition, window, n, hit_rate, avg_ret, median_ret, base_n,"
                      " base_hit_rate, base_median_ret, as_of, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                      " ON CONFLICT(market, ticker, condition, window, as_of) DO UPDATE SET n=excluded.n, hit_rate=excluded.hit_rate,"
                      " avg_ret=excluded.avg_ret, median_ret=excluded.median_ret, base_n=excluded.base_n,"
                      " base_hit_rate=excluded.base_hit_rate, base_median_ret=excluded.base_median_ret, run_id=excluded.run_id",
                      (market.upper(), ticker, condition, window, n, hit_rate, avg_ret, median_ret, b.get("n"), b.get("hit_rate"),
                       b.get("median_ret"), as_of, run_id(stamp, market) if stamp else None))


def base_rates(market: str, ticker: str) -> list:
    return q("SELECT * FROM co_base_rate WHERE market=? AND ticker=? ORDER BY as_of DESC, condition", (market.upper(), ticker))


# ── 운영 기록(감사·실전 점검·손실 판정·검색) ──
def real_check_put(stamp: str, market: str, items: list) -> None:
    """items: [{item, status, note}]"""
    rid = run_id(stamp, market)
    with tx():
        c = connect()
        for it in items:
            c.execute("INSERT INTO op_real_check (run_id, item, status, note, at) VALUES (?,?,?,?,?)"
                      " ON CONFLICT(run_id, item) DO UPDATE SET status=excluded.status, note=excluded.note, at=excluded.at",
                      (rid, str(it.get("item")), it.get("status"), it.get("note"), now_iso()))


def loss_review_put(week: str, rows: list) -> int:
    """손실 판정 행 — 키는 (주, 논지). 같은 주 한 종목에 논지가 둘이면 행도 둘이다. 논지 id가 없으면 market:ticker로 채운다."""
    n = 0
    with tx():
        c = connect()
        for r in rows:
            mk = str(r.get("market", "")).lower()
            tid = r.get("thesis_id") or f"{mk}:{r.get('ticker')}"
            c.execute("INSERT INTO op_loss_review (week, thesis_id, market, ticker, verdict, basis, net_realized, body)"
                      " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(week, thesis_id) DO UPDATE SET market=excluded.market,"
                      " ticker=excluded.ticker, verdict=excluded.verdict, basis=excluded.basis,"
                      " net_realized=excluded.net_realized, body=excluded.body",
                      (week, tid, mk, str(r.get("ticker")), r.get("verdict"), r.get("basis") or r.get("why"),
                       r.get("net_realized"), json.dumps(r, ensure_ascii=False)))
            n += 1
    return n


def loss_review_replace(week: str, rows: list) -> int:
    """그 주의 손실 판정을 통째로 맞춘다 — 손실판정 파일이 그 주의 원본이라, 파일에서 지운 행은 표에서도 지운다."""
    with tx():
        connect().execute("DELETE FROM op_loss_review WHERE week=?", (week,))
        return loss_review_put(week, rows)


def loss_review_rows(week: str | None = None) -> list:
    """손실 판정 원본 행(body) — 넣은 순서대로."""
    rs = q("SELECT body FROM op_loss_review " + ("WHERE week=? " if week else "") + "ORDER BY rowid", (week,) if week else ())
    return [json.loads(r["body"]) for r in rs]


STEPGATE_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "_stepgate"


def _nfc(p) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", str(p))


def _project_root(path):
    """경로 → 그 경로가 속한 프로젝트 뿌리(stage.py·db_schema.sql이 함께 있는 가장 가까운 상위 폴더). 경로가 지금 없으면
    이름이 이 프로젝트 폴더 이름과 같은 가장 가까운 상위 폴더(지워진 예행 사본). 못 찾으면 None."""
    p = Path(str(path))
    if not p.is_absolute():
        return None
    for d in [p] + list(p.parents):
        try:
            if d.is_dir() and (d / "stage.py").exists() and (d / "db_schema.sql").exists():
                return d
        except OSError:
            continue
    for d in [p] + list(p.parents):
        if _nfc(d.name) == _nfc(project_root().name):
            return d
    return None


def project_root() -> Path:
    """이 프로젝트의 뿌리 — 감사 범위를 가르는 기준. 코드 폴더(HERE)이고, 다른 사본(작업 공간·예행)에서 운영 저장소를 예행 이전할 때만
    AITB_PROJECT_ROOT로 운영 폴더를 준다(그 사본 기준으로 재면 운영 감사가 전부 foreign이 된다)."""
    return Path(os.environ["AITB_PROJECT_ROOT"]) if os.environ.get("AITB_PROJECT_ROOT") else HERE


def _same_root(a, b) -> bool:
    try:
        return _nfc(Path(a).resolve()) == _nfc(Path(b).resolve()) or _nfc(Path(a).absolute()) == _nfc(Path(b).absolute())
    except OSError:
        return _nfc(a) == _nfc(b)


def audit_scope_of(body: dict, run_started=None) -> tuple:
    """감사 원장 줄 → (scope, 뿌리, 근거 칸, 사유). ① dir → deliverable → findings_file의 프로젝트 뿌리가 이 프로젝트면 project ·
    다른 뿌리면 foreign ② 그다음 독립 신호 — 줄 시각(KST)이 run 시작보다 이르면 foreign(이 run의 감사일 수 없다) ③ 그 밖 unknown."""
    scope, root, frm, why = "unknown", None, "none", "경로 없음"
    for key in ("dir", "deliverable", "findings_file"):
        v = body.get(key)
        if not v or not Path(str(v)).is_absolute():
            continue
        r = _project_root(v)
        if r is None:
            continue
        root, frm = str(r), key
        if _same_root(r, project_root()):
            scope, why = "project", f"{key} 뿌리 = 이 프로젝트"
        else:
            scope, why = "foreign", f"{key} 뿌리 {r} ≠ 이 프로젝트"
        break
    ts = str(body.get("ts") or "")
    if run_started and ts and scope != "foreign":
        try:
            t = datetime.fromisoformat(ts.replace(" ", "T"))
            t = t if t.tzinfo else t.replace(tzinfo=KST)
            st = datetime.fromisoformat(str(run_started))
            st = st if st.tzinfo else st.replace(tzinfo=KST)
            if t < st:
                scope, frm, why = "foreign", "ts", f"줄 시각 {ts} < run 시작 {run_started}"
        except ValueError:
            pass
    return scope, root, frm, why


def audit_mirror(stamp: str, market: str, ledger: Path | None = None) -> int:
    """게이트 엔진의 승격 원장에서 이 run의 감사 판정 행을 op_audit로 복사한다(엔진 원장은 그대로 둔다). 이름 대조(날짜_시장)는
    **찾기용**이다 — 넣은 행마다 정체 칸(reqid·dir·deliverable_path·content_sha)을 옮기고 범위(project/foreign/unknown)를
    op_audit_scope에 남긴다(v4 · Codex U03 — 다른 폴더의 예행 감사가 이 run 감사 회차에 섞이던 결함). 세는 쪽은 audit_rows."""
    if ledger is None:
        env = os.environ.get("AITB_STEPGATE_LEDGER")
        if os.environ.get("AITB_DB") and not env:
            return 0  # 시험·예행 저장소에는 실제 엔진 원장을 복사하지 않는다
        ledger = Path(env) if env else (STEPGATE_DIR / "promotion_ledger.jsonl")
    ledger = Path(ledger)
    if not ledger.exists():
        return 0
    rid = run_id(stamp, market)
    pat = re.compile(rf"{stamp}[_-]?{market.lower()}|{market.lower()}[_:-]?{stamp}", re.I)
    n = 0
    c = connect()
    started = (c.execute("SELECT started FROM ex_run WHERE run_id=?", (rid,)).fetchone() or [None])[0]
    with tx(c):
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            blob = " ".join(str(r.get(k, "")) for k in ("run", "deliverable", "wfid", "dir"))
            if not pat.search(blob):
                continue
            sha = hashlib.sha256(line.encode("utf-8")).hexdigest()
            cur = c.execute("INSERT OR IGNORE INTO op_audit (run_id, skill, cp, batch, round, status, lenses, findings_file,"
                            " deliverable, at, line_sha, body, reqid, dir, deliverable_path, content_sha)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (rid, r.get("skill"), r.get("cp"), r.get("batch"), r.get("round"), r.get("verdict") or r.get("status"),
                             ",".join(r.get("lenses") or []) if isinstance(r.get("lenses"), list) else r.get("lenses"),
                             r.get("findings_file"), r.get("deliverable") or r.get("run"), r.get("ts"), sha, line,
                             r.get("reqid"), r.get("dir"), r.get("deliverable") if str(r.get("deliverable") or "").startswith("/") else None,
                             r.get("content_sha")))
            if cur.rowcount:
                scope, root, frm, why = audit_scope_of(r, started)
                c.execute("INSERT INTO op_audit_scope (audit_id, scope, project_root, derived_from, reason, at, by)"
                          " VALUES (?,?,?,?,?,?,'import')", (cur.lastrowid, scope, root, frm, why, now_iso()))
            n += cur.rowcount
    return n


def _audit_content_state(r: dict, cache: dict) -> str:
    """감사 행이 본 산출물 판 vs 지금 파일(W06). match·mismatch = 원장에 본문 해시가 있을 때 지금 파일 해시와 같은가 ·
    no_hash_fresh·no_hash_stale = 해시가 없는 옛 줄(엔진이 아직 안 적던 때) — 감사 줄 시각 뒤에 파일이 바뀌지 않았나 · no_file = 산출물이 없다."""
    b = r.get("body_obj") or {}
    path = r.get("deliverable_path") or (b.get("deliverable") if str(b.get("deliverable") or "").startswith("/") else None)
    sha = r.get("content_sha") or b.get("content_sha")
    if not path and r.get("scope") == "project" and r.get("project_root") and str(b.get("run") or "").lower().endswith(".md"):
        # 산출물 경로가 없는 옛 엔진 줄 — 원장의 산출물 이름(소문자 계열 이름)을 그 프로젝트 analysis/에서 찾는다(이름이 같은 파일 하나)
        hits = [x for x in (Path(r["project_root"]) / "analysis").glob("*.md") if x.name.lower() == str(b["run"]).lower()]
        path = str(hits[0]) if len(hits) == 1 else None
    if not path or not Path(path).is_file():
        return "no_file"
    if sha:
        if path not in cache:
            cache[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        sha = str(sha).lower()
        # 게이트 엔진 큐는 sha256 앞 16자를 적는다(`stepgate._content_sha`) — 앞부분 일치(12자 이상)도 같은 판으로 본다
        return "match" if (cache[path] == sha or (len(sha) >= 12 and cache[path].startswith(sha))) else "mismatch"
    try:
        t = datetime.fromisoformat(str(r.get("at") or b.get("ts") or "").replace(" ", "T"))
        t = t if t.tzinfo else t.replace(tzinfo=KST)
    except ValueError:
        return "no_hash_stale"
    return "no_hash_fresh" if datetime.fromtimestamp(Path(path).stat().st_mtime, KST) <= t else "no_hash_stale"


def audit_rows(rid: str, skill: str = None, scopes=("project", "unknown")) -> list:
    """★ 감사 행의 유일한 읽기(v4) — 마지막 범위 판정이 `scopes` 안인 행만(기본: foreign 제외). 행에 body(dict)·scope가 붙는다.
    ★ 이력과 지금 증거를 가른다(W06): 행마다 `content_state`(위 `_audit_content_state`)와 `current_ok`(= 이 프로젝트 범위 ∧ 본 판이 지금
    산출물과 같다 — 해시가 같거나, 해시 없는 옛 줄이면 감사 뒤 파일이 안 바뀌었다). 회차 세기는 모든 행(이력) · 지금 판의 PASS는 current_ok만."""
    rows = q("SELECT * FROM v_audit WHERE run_id=?" + (" AND skill=?" if skill else "") + " ORDER BY at, id",
             (rid, skill) if skill else (rid,))
    out, cache = [], {}
    for r in rows:
        if (r.get("scope") or "unknown") not in scopes:
            continue
        try:
            r["body_obj"] = json.loads(r["body"]) if r.get("body") else {}
        except ValueError:
            r["body_obj"] = {}
        r["content_state"] = _audit_content_state(r, cache)
        r["current_ok"] = (r.get("scope") == "project" and r["content_state"] in ("match", "no_hash_fresh"))
        out.append(r)
    return out


def audit_rescope(rid: str, by: str = "operator") -> list:
    """그 run 감사 행의 범위를 지금 규칙으로 다시 판정 — 바뀐 행만 판정 행을 덧붙인다(지우지 않는다). 반환 [(id, 옛, 새)]."""
    c = connect()
    started = (c.execute("SELECT started FROM ex_run WHERE run_id=?", (rid,)).fetchone() or [None])[0]
    changed = []
    with tx(c):
        for r in c.execute("SELECT a.id, a.body, (SELECT scope FROM op_audit_scope WHERE audit_id=a.id ORDER BY id DESC LIMIT 1)"
                           " FROM op_audit a WHERE a.run_id=?", (rid,)).fetchall():
            try:
                b = json.loads(r[1]) if r[1] else {}
            except ValueError:
                b = {}
            scope, root, frm, why = audit_scope_of(b, started)
            if scope != r[2]:
                c.execute("INSERT INTO op_audit_scope (audit_id, scope, project_root, derived_from, reason, at, by) VALUES (?,?,?,?,?,?,?)",
                          (r[0], scope, root, frm, why, now_iso(), by))
                changed.append((r[0], r[2], scope))
    return changed


def search_log(stamp: str, market: str, query: str, engine: str, results: list, agent: str = "") -> int:
    c = connect()
    with tx(c):
        sid = c.execute("INSERT INTO mat_search (run_id, query, engine, agent, at) VALUES (?,?,?,?,?)",
                        (run_id(stamp, market), query, engine, agent, now_iso())).lastrowid
        for i, r in enumerate(results or []):
            c.execute("INSERT INTO mat_search_result (search_id, ord, url, title) VALUES (?,?,?,?)",
                      (sid, i, r.get("url"), r.get("title")))
    return sid


# ───────────────────────────── 내보내기(읽기 전용 렌더본) ─────────────────────────────
# 봉인 게이트가 파일 경로로 읽는 셋(자산 곡선·피드백·유니버스 범위)과 포트폴리오 동기화가 복사하는 넷.
# 옛 파일과 같은 형식(들여쓰기·끝 줄바꿈)으로 쓴다 — 이전 직후 바이트 단위로 같아야 한다.

EXPORTS = {
    # 이름: (경로, 형식) — 형식: ("json", indent, 끝 줄바꿈) | ("jsonl",)
    "equity_curve": (JOURNAL / "equity_curve.jsonl", ("jsonl",)),
    "feedback": (JOURNAL / "feedback.jsonl", ("jsonl",)),
    "universe_coverage": (JOURNAL / "universe_coverage.json", ("json", 2, "")),
    "market_map": (JOURNAL / "market_map.json", ("json", 1, "")),
    "theses": (JOURNAL / "theses.json", ("json", 2, "\n")),
    "sources": (JOURNAL / "sources.json", ("json", 2, "")),
    "watchlist": (CONFIG / "watchlist.json", ("json", 2, "")),
}
# 이전 검사에만 쓰는 것(작업 폴더에는 쓰지 않는다)
CHECK_ONLY = {
    "scenarios": (JOURNAL / "scenarios.json", ("json", 1, "")),
    "calendar": (JOURNAL / "calendar.json", ("json", 1, "")),
    "position_peaks": (JOURNAL / "position_peaks.json", ("json", 2, "\n")),
    "universe_pending": (JOURNAL / "universe_pending.json", ("json", 2, "")),
}


def render(name: str):
    """렌더본의 파이썬 값(파일로 쓰기 전)."""
    if name == "equity_curve":
        return equity_rows()
    if name == "feedback":
        return log_rows("feedback")
    if name == "universe_coverage":
        return coverage_load()
    if name == "universe_pending":
        return pending_load()
    if name == "market_map":
        return market_map_load()
    if name == "theses":
        meta = docmeta_get("theses") or {"schema_version": "1.0", "_ko": ["schema_version", "theses"]}
        return {k: (theses_all() if k == "theses" else meta.get(k)) for k in meta.get("_ko", ["schema_version", "theses"])}
    if name == "scenarios":
        meta = docmeta_get("scenarios") or {"_ko": ["scenarios"]}
        return {k: (scenarios_all() if k == "scenarios" else meta.get(k)) for k in meta.get("_ko", ["scenarios"])}
    if name == "calendar":
        return calendar_doc()
    if name == "sources":
        meta = docmeta_get("sources") or {"schema_version": "1.1", "_ko": ["schema_version", "sources"]}
        return {k: (sources_rows() if k == "sources" else meta.get(k)) for k in meta.get("_ko", ["schema_version", "sources"])}
    if name == "watchlist":
        return watchlist_doc()
    if name == "position_peaks":
        return peaks_all()
    raise KeyError(name)


def serialize(value, fmt) -> str:
    if fmt[0] == "jsonl":
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in value)
    _, indent, tail = fmt
    return json.dumps(value, ensure_ascii=False, indent=indent) + tail


def set_root(root: Path) -> None:
    """프로젝트 뿌리를 바꾼다(이전 예행 `--root` · 사본 실행) — 렌더본·백업 경로가 함께 따라간다."""
    global HERE, JOURNAL, CONFIG, BACKUP_DIR
    root = Path(root)
    HERE, JOURNAL, CONFIG, BACKUP_DIR = root, root / "journal", root / "config", root / "journal" / "_db_backup"
    for table in (EXPORTS, CHECK_ONLY):
        for n, (path, fmt) in list(table.items()):
            rel = Path(path.parent.name) / path.name
            table[n] = (root / rel, fmt)


def export_path(name: str) -> Path:
    """내보낼 경로. AITB_DB를 지정한 실행(테스트·이전 예행)은 그 저장소 옆 `exports/`로만 쓴다 —
    테스트 데이터가 실제 journal/config의 렌더본을 덮어쓰는 일을 막는다."""
    path, _ = (EXPORTS.get(name) or CHECK_ONLY[name])
    if os.environ.get("AITB_DB") and not os.environ.get("AITB_EXPORT_REAL"):
        return Path(os.environ["AITB_DB"]).parent / "exports" / path.relative_to(HERE)
    return path


def export(name: str, dest: Path | None = None) -> Path:
    path, fmt = (EXPORTS.get(name) or CHECK_ONLY[name])
    dest = dest or export_path(name)
    text = serialize(render(name), fmt)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        with contextlib.suppress(Exception):
            if dest.read_text(encoding="utf-8") == text:
                return dest
        with contextlib.suppress(Exception):
            os.chmod(dest, 0o644)
    tmp = dest.with_name(f".{dest.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
    with contextlib.suppress(Exception):
        os.chmod(dest, 0o444)
    return dest


def export_all() -> list:
    return [export(n) for n in EXPORTS]


def write_json_atomic(path: Path, obj) -> None:
    """JSON 파일을 **원자적으로** 쓴다 — 임시 파일에 다 쓴 뒤 `os.replace`(2026-10-19 · Codex R02).
    도중에 프로세스가 죽어도 옛 파일이 온전히 남는다(`write_text`는 반쯤 쓴 파일을 남겨 '깨진 기록'이 됐다).
    그날 거래 기록(trades_*.json)을 쓰는 곳(execute·fill·tax_ledger)이 이것을 쓴다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ───────────────────────────── 사실(출처 원장 모양) ─────────────────────────────
# 사실 등록·판정·교정·렌더는 facts.py가 한다. 여기서는 옛 sources.json 모양으로 돌려주는 것만.

def sources_rows() -> list:
    """옛 `journal/sources.json`의 sources 목록 모양. 이전된 옛 행은 그대로, 새 종류별 사실은 한 줄 렌더로."""
    out = []
    for d in facts_store.all():
        if d.get("fact") is None and d.get("id"):
            # 종류별 사실 — 렌더 문장(facts.py의 RENDER가 있으면 그것)
            line = None
            try:
                import facts  # noqa: 순환 없이 늦게 부른다
                line = facts.render_line(d.get("id"))
            except Exception:
                line = None
            r = q1("SELECT * FROM ev_fact WHERE id=?", (d["id"],)) or {}
            d = {"fact": line or r.get("kind") or "", "as_of": r.get("as_of"), "retrieved": r.get("retrieved"),
                 "source": r.get("source_label"), "url": r.get("url"), **({"quote": r["quote"]} if r.get("quote") else {}),
                 "tier": r.get("tier"), "load_bearing": bool(r.get("load_bearing")), "id": r.get("id"),
                 "runs": [x["value"] for x in q("SELECT value FROM ev_fact_run_label WHERE pid=(SELECT rowid FROM ev_fact WHERE id=?)"
                                                " ORDER BY ord", (r.get("id"),))]}
        out.append(d)
    return out


# ───────────────────────────── 원문 보관 · 출처 판(2026-10-19 · Codex U01·S01 · 저장소 판 v3) ─────────────────────────────
# 출처(ev_source · 정규화한 URL) → 시도(ev_fetch · 성공·실패 모두 · 추가만) → 본문 판(ev_source_version · 내용 해시 파일).
# 쓰는 함수는 evidence_record 하나 — fetch.py와 재료 링크 한 홉(ingest.link_hop)이 둘 다 부른다. 같은 내용은 한 판(n_fetch만 오른다),
# 다른 내용은 새 판(옛 판 파일은 그대로) — 예전에는 URL 해시 파일 하나를 덮어써서 사실이 본 본문이 다음 조회에 사라졌다.
# 참조된 판(사실·판 기록·판정·링크)은 지우지 않고, 참조 없는 판만 7일 뒤 파일을 정리한다(raw_purge · 행은 남긴다).

RAW_PREFIX = "data/_raw_fc/"
RAW_KEEP_DAYS = 7              # 참조 없는 판의 캐시 보존(옛 fetch.py 상수 그대로 — 사유: 캐시 · 참조된 판은 영구)


def raw_dir() -> Path:
    """받은 원문 보관 폴더 — AITB_RAW_DIR → AITB_DB를 지정한 실행(테스트·예행)은 그 저장소 옆 `_raw_fc/` → `data/_raw_fc/`."""
    if os.environ.get("AITB_RAW_DIR"):
        return Path(os.environ["AITB_RAW_DIR"])
    if os.environ.get("AITB_DB") and not os.environ.get("AITB_EXPORT_REAL"):
        return Path(os.environ["AITB_DB"]).parent / "_raw_fc"
    return HERE / "data" / "_raw_fc"


def raw_abs(path):
    """판의 path(`data/_raw_fc/<이름>` · 옛 판은 옛 이름 그대로) → 실제 파일 경로(테스트·예행이면 그 보관 폴더)."""
    if not path:
        return None
    s_ = str(path).replace("\\", "/")
    if Path(s_).is_absolute():
        return Path(s_)
    if s_.startswith(RAW_PREFIX):
        return raw_dir() / s_[len(RAW_PREFIX):]
    return HERE / s_


def norm_url(u) -> str:
    """같은 출처를 알아보는 정규화 — 조각(#)·추적 쿼리(utm_*·fbclid·gclid)·끝 '/'·www. 를 지우고 호스트를 소문자로.
    다른 쿼리는 남긴다(Kalshi·재무부 CSV처럼 쿼리가 내용을 정한다). (facts._norm_url이 이것을 부른다 — 출처 열쇠는 한 곳)"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    if not u:
        return ""
    u = str(u).strip().rstrip(".,;)")
    try:
        p = urlsplit(u)
    except ValueError:
        return u
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    qs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
          if not (k.lower().startswith("utm_") or k.lower() in ("fbclid", "gclid", "mc_cid", "mc_eid"))]
    return urlunsplit((p.scheme.lower(), host, p.path.rstrip("/"), urlencode(qs), ""))


def _store_text(text: str) -> tuple:
    """본문을 내용 해시 이름 파일로(tmp → replace · 이미 있으면 그대로). 반환 (sha256, path | None, 오류 | None)."""
    b = text.encode("utf-8")
    sha = hashlib.sha256(b).hexdigest()
    try:
        d = raw_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sha[:16]}.txt"
        if f.exists() and hashlib.sha256(f.read_bytes()).hexdigest() != sha:   # 16자리 충돌 — 더 긴 이름
            f = d / f"{sha[:24]}.txt"
        if not f.exists():
            tmp = d / f".{f.name}.{os.getpid()}.tmp"
            tmp.write_bytes(b)
            os.replace(tmp, f)
        return sha, RAW_PREFIX + f.name, None
    except OSError as e:
        return sha, None, f"store_failed: {type(e).__name__}: {str(e)[:80]}"


def evidence_record(url, text, err="", meta=None, *, run_id=None, section="", agent="fetch.py", at=None) -> dict:
    """★ 원문 보관의 유일한 쓰기 — 시도 한 행 + (본문이 있으면) 판 + 출처. 반환 {source_id, fetch_id, version_id, content_sha, path,
    new_version, at, status}. 실패·빈 본문도 시도로 남는다(그 출처의 판은 그대로 — 재조회 실패가 본문을 덮지 않는다)."""
    meta = meta or {}
    nu = norm_url(url) or str(url or "")
    at = at or now_iso()
    text = text or ""
    kind = meta.get("kind") if meta.get("kind") in ("html", "pdf", "api") else "html"
    status = "ok" if text else ("error" if err else "empty")
    sha = path = store_err = None
    if text:
        sha, path, store_err = _store_text(text)
    title = (str(meta.get("title") or "")[:120]) or None
    prov = {"fetch.py": "fetched", "link_hop": "link_hop"}.get(agent, "migrated")
    new_version = False
    with tx() as c:
        c.execute("INSERT INTO ev_source (url, first_fetched_at, last_fetched_at, kind, title) VALUES (?,?,?,?,?)"
                  " ON CONFLICT(url) DO UPDATE SET first_fetched_at=COALESCE(ev_source.first_fetched_at, excluded.first_fetched_at),"
                  " last_fetched_at=COALESCE(excluded.last_fetched_at, ev_source.last_fetched_at),"
                  " kind=COALESCE(ev_source.kind, excluded.kind), title=COALESCE(ev_source.title, excluded.title)",
                  (nu, at, at if status == "ok" else None, kind, title))
        sid = c.execute("SELECT id FROM ev_source WHERE url=?", (nu,)).fetchone()[0]
        vid = None
        if path:
            cur = c.execute("INSERT OR IGNORE INTO ev_source_version (source_id, section, content_sha, bytes, path, kind, pages, title,"
                            " first_seen, last_seen, n_fetch, provenance) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)",
                            (sid, section or "", sha, len(text.encode("utf-8")), path, kind, meta.get("pages"), title, at, at, prov))
            if cur.rowcount:
                vid, new_version = cur.lastrowid, True
            else:
                vid = c.execute("SELECT id FROM ev_source_version WHERE source_id=? AND section=? AND content_sha=?",
                                (sid, section or "", sha)).fetchone()[0]
                c.execute("UPDATE ev_source_version SET last_seen=?, n_fetch=n_fetch+1, path=COALESCE(path, ?), purged_at=NULL"
                          " WHERE id=?", (at, path, vid))
        fid = c.execute("INSERT INTO ev_fetch (source_id, version_id, url_raw, section, run_id, at, status, error, kind, pages,"
                        " chars_full, chars_returned, via, agent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, vid, str(url or ""), section or "", run_id, at, status, store_err or (err or None), kind,
                         meta.get("pages"), len(text), meta.get("chars_returned"), meta.get("via") or None, agent)).lastrowid
    return {"source_id": sid, "fetch_id": fid, "version_id": vid, "content_sha": sha, "path": path,
            "new_version": new_version, "at": at, "status": status}


def version_get(vid):
    return q1("SELECT * FROM ev_source_version WHERE id=?", (int(vid),)) if vid else None


def versions_for_source(source_id, section=None) -> list:
    """그 출처의 판(최근 본 것 먼저) — section을 주면 그 절만."""
    if section is None:
        return q("SELECT * FROM ev_source_version WHERE source_id=? ORDER BY last_seen DESC, id DESC", (source_id,))
    return q("SELECT * FROM ev_source_version WHERE source_id=? AND section=? ORDER BY last_seen DESC, id DESC", (source_id, section))


def raw_text(vid):
    """판의 본문 — 파일이 정리됐거나 없으면 None."""
    v = version_get(vid)
    f = raw_abs(v.get("path")) if v else None
    if f is None or not f.is_file():
        return None
    return f.read_text(encoding="utf-8", errors="ignore")


def fetch_rows(url=None, source_id=None, kind=None) -> list:
    """시도 기록(ev_fetch) — 옛 접속 기록 행 모양(url·at·kind·pages·chars·err·via)도 함께 준다(crosscheck 등)."""
    where, args = [], []
    if url:
        where.append("(f.url_raw=? OR s.url=?)")
        args += [str(url), norm_url(url)]
    if source_id:
        where.append("f.source_id=?")
        args.append(source_id)
    if kind:
        where.append("f.kind=?")
        args.append(kind)
    rows = q("SELECT f.*, s.url AS source_url FROM ev_fetch f JOIN ev_source s ON s.id=f.source_id"
             + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY f.id", tuple(args))
    for r in rows:
        r.update({"url": r["url_raw"], "chars": r.get("chars_full") or 0, "err": r.get("error") or ""})
    return rows


def link_body(link_id) -> tuple:
    """재료 링크 → (본문 | None, 마지막 시도 오류). 본문은 마지막으로 성공한 판(옛 행은 옛 body) — 재조회가 실패해도 남는다."""
    r = q1("SELECT version_id, last_fetch_id, body, fetched FROM mat_link WHERE id=?", (link_id,))
    if not r:
        return None, ""
    body = raw_text(r["version_id"]) if r.get("version_id") else None
    if body is None and r.get("fetched") and r.get("body"):
        body = r["body"]                                   # v3 전 옛 행 — 본문이 칸에 있다
    err = ""
    if r.get("last_fetch_id"):
        f = q1("SELECT status, error FROM ev_fetch WHERE id=?", (r["last_fetch_id"],)) or {}
        if f.get("status") != "ok" or f.get("error"):
            err = f.get("error") or f.get("status") or ""
    elif not r.get("fetched") and r.get("body"):
        err = r["body"]                                    # 옛 행 — 실패 사유가 body 칸에 있었다
    return body, err


def raw_purge(keep_days: int = RAW_KEEP_DAYS, now=None, dry_run: bool = False) -> dict:
    """참조 없는 판의 파일만 지운다(행은 남기고 path=NULL · purged_at). 참조 = 사실·판 기록·판정·링크(현재·이력)가 가리키는 판.
    같은 파일을 다른 판이 아직 쓰면 지우지 않는다. 옛 fetch.py는 참조와 무관하게 7일 지난 파일을 지웠다."""
    c = connect()
    refs = set()
    for sql in ("SELECT source_version_id FROM ev_fact", "SELECT source_version_id FROM ev_fact_version",
                "SELECT source_version_id FROM ev_verdict", "SELECT version_id FROM mat_link_version",
                "SELECT version_id FROM mat_link"):
        refs |= {r[0] for r in c.execute(sql).fetchall() if r[0] is not None}
    nowd = now or datetime.now(KST)
    cut = (nowd - timedelta(days=keep_days)).isoformat(timespec="seconds")
    live = {}
    for r in c.execute("SELECT id, path FROM ev_source_version WHERE path IS NOT NULL").fetchall():
        live.setdefault(r[1], set()).add(r[0])
    out = {"purged": 0, "kept_referenced": 0, "kept_recent": 0, "kept_shared": 0, "files_removed": 0, "dry_run": dry_run}
    rows = c.execute("SELECT id, path, julianday(last_seen) < julianday(?) AS old FROM ev_source_version"
                     " WHERE path IS NOT NULL AND purged_at IS NULL", (cut,)).fetchall()
    purgeable = {vid for vid, _p, old in rows if vid not in refs and old}
    with tx(c):
        for vid, path, old in rows:
            if vid in refs:
                out["kept_referenced"] += 1
                continue
            if not old:
                out["kept_recent"] += 1
                continue
            if (live.get(path, set()) - {vid}) - purgeable:     # 같은 파일을 쓰는 다른 판이 아직 살아 있다
                out["kept_shared"] += 1
                continue
            out["purged"] += 1
            if dry_run:
                continue
            c.execute("UPDATE ev_source_version SET path=NULL, purged_at=? WHERE id=?", (now_iso(), vid))
            live.get(path, set()).discard(vid)
            if not live.get(path):
                f = raw_abs(path)
                if f is not None and f.is_file():
                    f.unlink()
                    out["files_removed"] += 1
    return out


# ───────────────────────────── 백업·복원·검사 ─────────────────────────────

def backup_dir() -> Path:
    """AITB_DB를 지정한 실행(테스트·예행)은 그 저장소 옆 `_db_backup/`에만 쓴다."""
    if os.environ.get("AITB_DB") and not os.environ.get("AITB_EXPORT_REAL"):
        return Path(os.environ["AITB_DB"]).parent / "_db_backup"
    return BACKUP_DIR


def backup(label: str) -> Path:
    bd = backup_dir()
    bd.mkdir(parents=True, exist_ok=True)
    dest = bd / f"trading_{re.sub(r'[^0-9A-Za-z_.-]', '_', label)}.db"
    if dest.exists():
        i = 2
        while (bd / f"{dest.stem}_{i}.db").exists():
            i += 1
        dest = bd / f"{dest.stem}_{i}.db"
    src = connect()
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    dst.close()
    return dest


_TABLE_EXPORT = (("jd_thesis", "theses"), ("dim_axis", "market_map"), ("jd_axis_", "market_map"), ("dim_sector", "market_map"),
                 ("jd_sector_name", "market_map"), ("dim_company", "watchlist"), ("ev_fact", "sources"),
                 ("ex_equity_", "equity_curve"), ("op_coverage", "universe_coverage"), ("op_feedback", "feedback"),
                 ("sys_docmeta", None))


def _fk_parents(c, table: str) -> set:
    return {r[2] for r in c.execute(f"PRAGMA foreign_key_list({table})").fetchall()}


def restore_tables(path: Path, tables: list) -> dict:
    """백업 파일에서 표 몇 개만 되돌린다(계좌 전환 실패 시). 지우기는 자식→부모, 넣기는 부모→자식 순서 —
    부모를 나중에 지우면 ON DELETE CASCADE가 방금 넣은 자식을 지운다. 되돌린 표의 렌더본은 커밋 뒤 다시 쓴다."""
    c = connect()
    out = {}
    tabs = list(dict.fromkeys(tables))
    # 부모가 먼저 오도록 정렬(받은 표들 사이의 외래키만 본다)
    order, left = [], set(tabs)
    while left:
        ready = [t for t in tabs if t in left and not (_fk_parents(c, t) & (left - {t}))]
        if not ready:  # 순환 — 받은 순서대로
            ready = [t for t in tabs if t in left]
        for t in ready:
            order.append(t)
            left.discard(t)
    c.execute("ATTACH DATABASE ? AS bk", (str(path),))
    try:
        with tx(c):
            for t in reversed(order):
                c.execute(f"DELETE FROM main.{t}")
            for t in order:
                c.execute(f"INSERT INTO main.{t} SELECT * FROM bk.{t}")
                out[t] = c.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
            for t in order:
                for prefix, name in _TABLE_EXPORT:
                    if t.startswith(prefix):
                        if name:
                            mark_export(name)
                        else:
                            for n in EXPORTS:
                                mark_export(n)
    finally:
        c.execute("DETACH DATABASE bk")
    return out


def tables() -> list:
    return [r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


# ───────────────────────────── 저장소 판 · 이전 · 복원(2026-10-19 · Codex R12) ─────────────────────────────
# 판 하나 = 표 모양 하나. 이전 단계는 한 트랜잭션에서 ① 칸을 더하거나 표를 다시 만들고 ② 옛 행의 새 칸을 근거 있는 값으로 채우고
# ③ 검증 질의로 보고서를 만들고(실패면 롤백) ④ 뷰·트리거·색인을 스키마 파일에서 다시 만든 뒤 판 번호를 올린다.
# 근거로 정할 수 없는 값은 지어내지 않는다 — 보고서에 그 행 수를 남긴다.

def _cols(c, table: str) -> list:
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]


def _schema_objects(text: str = None) -> dict:
    """스키마 파일의 CREATE 문 → {(종류, 이름): 문장}."""
    out = {}
    for stmt in _schema_statements(text if text is not None else SCHEMA_PATH.read_text(encoding="utf-8")):
        m = re.search(r"CREATE\s+(TABLE|INDEX|UNIQUE\s+INDEX|TRIGGER|VIEW)\s+IF\s+NOT\s+EXISTS\s+(\w+)", stmt, re.I)
        if m:
            kind = "index" if "INDEX" in m.group(1).upper() else m.group(1).lower()
            out[(kind, m.group(2))] = stmt
    return out


def _drop_views(c) -> int:
    names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall()]
    for n in names:
        c.execute(f"DROP VIEW IF EXISTS {n}")
    return len(names)


def rebuild_table(c, name: str, overrides: dict = None, pre_checks=()) -> dict:
    """표를 스키마 파일의 현재 정의로 다시 만든다(sqlite ALTER TABLE §7 순서) — 칸 제약(NOT NULL·CHECK·STRICT)을 바꿀 때.
    `overrides` = {새 칸: 옛 표에서 그 값을 만드는 SQL 식}(없는 칸은 같은 이름의 옛 칸을 그대로) · `pre_checks` = [(설명, 위반 행 수 SQL)]
    — 하나라도 0이 아니면 아무것도 바꾸지 않고 멈춘다. 이 표의 트리거·뷰는 먼저 지우고(복사 중 트리거가 다른 표를 고치지 않게 ·
    이름 바꾸기가 깨진 뷰를 다시 읽지 않게) 단계 끝의 init_schema가 다시 만든다. id는 그대로 옮긴다(외래키 유지)."""
    overrides = overrides or {}
    ddl = _schema_objects().get(("table", name))
    if not ddl:
        raise MigrationError(f"스키마 파일에 표 {name} 정의가 없다")
    for label, sql in pre_checks:
        n = c.execute(sql).fetchone()[0]
        if n:
            raise MigrationError(f"{name}: {label} {n}행 — 새 제약을 어기는 행이 있어 다시 만들지 않는다")
    before = c.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    _drop_views(c)
    for (tg,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND (tbl_name=? OR sql LIKE ?)",
                           (name, f"%{name}%")).fetchall():
        c.execute(f"DROP TRIGGER IF EXISTS {tg}")
    tmp = f"{name}__new"
    c.execute(f"DROP TABLE IF EXISTS {tmp}")
    c.execute(re.sub(rf"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{name}\s*\(", f"CREATE TABLE {tmp} (", ddl, count=1, flags=re.I))
    old = set(_cols(c, name))
    new_cols = _cols(c, tmp)
    exprs = []
    for col in new_cols:
        if col in overrides:
            exprs.append(f"({overrides[col]})")
        elif col in old:
            exprs.append(col)
        else:
            exprs.append("NULL")
    c.execute(f"INSERT INTO {tmp} ({', '.join(new_cols)}) SELECT {', '.join(exprs)} FROM {name}")
    c.execute(f"DROP TABLE {name}")
    c.execute("PRAGMA legacy_alter_table = ON")
    try:
        c.execute(f"ALTER TABLE {tmp} RENAME TO {name}")
    finally:
        c.execute("PRAGMA legacy_alter_table = OFF")
    after = c.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    if after != before:
        raise MigrationError(f"{name}: 다시 만든 뒤 행 수가 다르다 {before} → {after}")
    return {"table": name, "rows": after}


def _jd(c, s):
    """시각 문자열 → 율리우스일(UTC 기준 · '+09:00'·UTC 무표기 모두). 못 읽으면 None."""
    if s in (None, ""):
        return None
    r = c.execute("SELECT julianday(?)", (str(s),)).fetchone()[0]
    return r


def _m2_fact_rev(c) -> dict:
    """v2(R04) — 사실 판(`ev_fact.value_rev` · 값이 마지막으로 바뀐 판)과 판정 판(`ev_verdict.fact_rev` · 그 판정이 본 사실의 판).
    근거: ev_fact_version(트리거가 판이 오를 때마다 옛 판 번호와 시각을 남겼다) · ev_correction(옛·새 칸 · 적용 시각 · 판정 연결).
    판정 판 = 1 + 판정 시각까지 오른 판 수(그 판정을 낳은 교정보다 앞선다 — 교정이 판정을 가리키면 그 교정 직전 판을 넘지 않는다).
    값 판 = 마지막 '값 교정'(종류 표 세부칸 또는 as_of·quote·url·period·legacy_text)이 만든 판 · 없으면 1."""
    import facts as _facts   # 값 칸 목록(종류 표)은 facts가 원본 — 순환 없이 늦게 부른다
    rep = {}
    if "value_rev" not in _cols(c, "ev_fact"):
        c.execute("ALTER TABLE ev_fact ADD COLUMN value_rev INTEGER NOT NULL DEFAULT 1")
    if "fact_rev" not in _cols(c, "ev_claim_ref"):
        c.execute("ALTER TABLE ev_claim_ref ADD COLUMN fact_rev INTEGER")
    facts_rows = {r[0]: {"rev": int(r[1] or 1), "kind": r[2]} for r in c.execute("SELECT id, rev, kind FROM ev_fact").fetchall()}
    vers = {}
    for vid, fid, ver, jd in c.execute("SELECT id, fact_id, version, julianday(changed_at) FROM ev_fact_version ORDER BY fact_id, version, id"):
        vers.setdefault(fid, []).append({"id": vid, "version": int(ver), "jd": jd})
    anomalies = []
    for fid, f in facts_rows.items():
        vs = [v["version"] for v in vers.get(fid, [])]
        if vs != list(range(1, f["rev"])):
            anomalies.append(f"{fid} rev {f['rev']} · 판 기록 {vs}")
    rep["rev_sum"] = sum(f["rev"] - 1 for f in facts_rows.values())
    rep["version_rows"] = sum(len(v) for v in vers.values())
    rep["rev_anomalies"] = anomalies[:20]
    rep["rev_anomaly_n"] = len(anomalies)

    def rev_at(fid, jd):
        if jd is None:
            return None
        return 1 + sum(1 for v in vers.get(fid, []) if v["jd"] is not None and v["jd"] <= jd)

    # 교정 ↔ 판 기록 짝짓기 — '이미 반영됨'(판을 안 올린 연결 행)은 뺀다. 개수가 같으면 순서대로, 다르면 시각이 가장 가까운 판(10초 안)
    corr = {}
    for cid, fid, new, reason, vdx, jd in c.execute(
            "SELECT id, fact_id, new, reason, verdict_id, julianday(applied_at) FROM ev_correction ORDER BY id"):
        if str(reason or "").startswith("이미 반영됨"):
            continue
        corr.setdefault(fid, []).append({"id": cid, "new": new, "verdict_id": vdx, "jd": jd})
    matched, unmatched = {}, []
    for fid, cs in corr.items():
        vs = vers.get(fid, [])
        if len(cs) == len(vs):
            for x, v in zip(cs, vs):
                matched[x["id"]] = v["version"]
            continue
        used = set()
        for x in cs:
            best = None
            for v in vs:
                if v["id"] in used or v["jd"] is None or x["jd"] is None:
                    continue
                gap = abs(v["jd"] - x["jd"]) * 86400
                if gap <= 10 and (best is None or gap < best[0]):
                    best = (gap, v)
            if best:
                used.add(best[1]["id"])
                matched[x["id"]] = best[1]["version"]
            else:
                unmatched.append(x["id"])
    rep["corrections"] = sum(len(v) for v in corr.values())
    rep["corrections_unmatched"] = unmatched[:20]
    rep["corrections_unmatched_n"] = len(unmatched)

    def value_cols(kind):
        k = _facts.KINDS.get(kind)
        dcols = set(_facts._colinfo(k.table)) - {"fact_id"} if k else set()
        return dcols | set(_facts.VALUE_HEADER)
    vrev = {}
    for fid, cs in corr.items():
        vc = value_cols(facts_rows.get(fid, {}).get("kind"))
        for x in cs:
            try:
                keys = set(json.loads(x["new"] or "{}"))
            except ValueError:
                keys = set()
            if keys & vc and x["id"] in matched:
                vrev[fid] = matched[x["id"]] + 1
    for fid, v in vrev.items():
        c.execute("UPDATE ev_fact SET value_rev=? WHERE id=?", (v, fid))
    rep["facts_value_changed"] = len(vrev)
    rep["facts_rev_gt1"] = sum(1 for f in facts_rows.values() if f["rev"] > 1)

    # 판정 판
    corr_of_verdict = {}
    for fid, cs in corr.items():
        for x in cs:
            if x["verdict_id"] is not None and x["id"] in matched:
                corr_of_verdict.setdefault(int(x["verdict_id"]), []).append(matched[x["id"]])
    c.execute("DROP TABLE IF EXISTS _vrev")
    c.execute("CREATE TEMP TABLE _vrev (id INTEGER PRIMARY KEY, fact_rev INTEGER NOT NULL)")
    n_null_at = 0
    rows = c.execute("SELECT id, fact_id, julianday(at) FROM ev_verdict").fetchall()
    for vid, fid, jd in rows:
        r = rev_at(fid, jd)
        if r is None:
            n_null_at += 1
            r = 1                                    # 시각 없는 옛 판정 — 가장 이른 판으로 본다(값이 바뀐 사실이면 세지 않게 된다)
        if vid in corr_of_verdict:
            r = min(r, min(corr_of_verdict[vid]))   # 이 판정을 고친 교정 직전 판을 넘지 않는다(같은 초 경계)
        r = max(1, min(r, facts_rows.get(fid, {}).get("rev", 1)))
        c.execute("INSERT INTO _vrev (id, fact_rev) VALUES (?,?)", (vid, r))
    rep["verdicts"] = len(rows)
    rep["verdicts_without_time"] = n_null_at
    for rid_, fid, jd in c.execute("SELECT id, fact_id, julianday(at) FROM ev_claim_ref").fetchall():
        r = rev_at(fid, jd)
        if r is not None:
            c.execute("UPDATE ev_claim_ref SET fact_rev=? WHERE id=?", (min(r, facts_rows.get(fid, {}).get("rev", 1)), rid_))
    rep["rebuild"] = rebuild_table(c, "ev_verdict", {"fact_rev": "SELECT m.fact_rev FROM _vrev m WHERE m.id = ev_verdict.id"})
    c.execute("DROP TABLE IF EXISTS _vrev")
    # 검증 — 실패면 MigrationError(롤백)
    chk = {
        "verdict_fact_rev_null": c.execute("SELECT COUNT(*) FROM ev_verdict WHERE fact_rev IS NULL").fetchone()[0],
        "verdict_rev_ahead": c.execute("SELECT COUNT(*) FROM ev_verdict v JOIN ev_fact f ON f.id=v.fact_id"
                                       " WHERE v.fact_rev > f.rev").fetchone()[0],
        "value_rev_ahead": c.execute("SELECT COUNT(*) FROM ev_fact WHERE value_rev > rev").fetchone()[0],
        "verdicts_void": c.execute("SELECT COUNT(*) FROM ev_verdict v JOIN ev_fact f ON f.id=v.fact_id"
                                   " WHERE v.fact_rev < f.value_rev").fetchone()[0],
    }
    rep["check"] = chk
    if chk["verdict_fact_rev_null"] or chk["verdict_rev_ahead"] or chk["value_rev_ahead"]:
        raise MigrationError(f"v2 검증 실패 — {chk}")
    if rep["verdicts"] != rep["rebuild"]["rows"]:
        raise MigrationError(f"v2 판정 행 수 {rep['verdicts']} ≠ {rep['rebuild']['rows']}")
    return rep


def _quote_segments(quote: str) -> list:
    """인용 → 대조 조각(8자 이상 · 공백·강조 표시 제거) — facts.lint (x)와 같은 쪼개기."""
    def ns(x):
        return re.sub(r"\s+", "", (x or "").replace("**", "").replace("`", ""))
    return [x for x in (ns(sg).strip("\"'") for sg in re.split(r"…|\.\.\.", quote or "")) if len(x) >= 8]


def _m3_evidence(c) -> dict:
    """v3(U01·S01·S03) — 원문 보관을 출처·시도·판으로. 근거만 옮긴다:
    ① op_fetch 한 행 = ev_fetch 한 행(agent migrated · op_fetch_seq). 옛 보관 파일은 URL 해시 이름이라 마지막 조회가 덮어썼다 —
       그 파일의 내용은 **그 파일을 마지막으로 쓴 시도**만 본 것이 확실하므로 판은 그 시도에만 잇고(처음·마지막 본 시각 = 그 시각),
       앞 시도들은 판 없이 남긴다(당시 본문 미보존 · 보고서 rows_overwritten).
    ② 사실 → 판: 그 출처의 판 가운데 인용 조각이 전부 든 판, 없으면 최신 판 — **전부 inferred**(등록 시각은 관측 시각이 아니다).
    ③ verified_rev = 그 사실 MATCH 판정의 가장 높은 판(없으면 1) — 값이 바뀐 뒤의 옛 검증이면 다시 검증 큐.
    ④ 옛 링크 본문(mat_link.body · fetched=1)은 내용 해시 파일·판·이력으로, 실패 사유가 든 행은 실패 시도로 — body는 그대로 둔다."""
    rep = {}
    adds = (("ev_source", "first_fetched_at", "TEXT"), ("ev_source", "last_fetched_at", "TEXT"),
            ("ev_source", "kind", "TEXT CHECK (kind IN ('html', 'pdf', 'api'))"),
            ("mat_link", "version_id", "INTEGER REFERENCES ev_source_version(id)"),
            ("mat_link", "last_fetch_id", "INTEGER REFERENCES ev_fetch(id)"),
            ("ev_fact", "source_version_id", "INTEGER REFERENCES ev_source_version(id)"),
            ("ev_fact", "source_version_link", "TEXT CHECK (source_version_link IN ('recorded', 'inferred'))"),
            ("ev_fact", "verified_rev", "INTEGER"),
            ("ev_fact_version", "source_version_id", "INTEGER"),
            ("ev_verdict", "source_version_id", "INTEGER REFERENCES ev_source_version(id)"))
    for t, col, ddl in adds:
        if col not in _cols(c, t):
            c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {ddl}")
    _drop_views(c)
    init_schema(c)                                   # 새 표(ev_source_version·ev_fetch·mat_link_version)·색인·트리거·뷰

    # ① op_fetch → 출처·시도·판
    ops = []
    last_writer = {}
    for seq, ts, rid, body in c.execute("SELECT seq, ts, rid, body FROM op_fetch ORDER BY seq").fetchall():
        try:
            b = json.loads(body) or {}
        except ValueError:
            b = {}
        ops.append((seq, rid, b, b.get("at") or ts))
        if b.get("raw_path"):
            last_writer[b["raw_path"]] = seq
    n_ver = n_missing = n_over = 0
    for seq, rid, b, at in ops:
        url = str(b.get("url") or "")
        nu = norm_url(url) or url
        at = str(at or now_iso())
        kind = b.get("kind") if b.get("kind") in ("html", "pdf", "api") else "html"
        err = str(b.get("err") or "")
        chars = int(b.get("chars") or 0)
        status = "ok" if (chars > 0 and not err) else ("error" if err else "empty")
        c.execute("INSERT INTO ev_source (url, first_fetched_at, last_fetched_at, kind) VALUES (?,?,?,?)"
                  " ON CONFLICT(url) DO UPDATE SET first_fetched_at=COALESCE(ev_source.first_fetched_at, excluded.first_fetched_at),"
                  " last_fetched_at=COALESCE(excluded.last_fetched_at, ev_source.last_fetched_at), kind=COALESCE(ev_source.kind, excluded.kind)",
                  (nu, at, at if status == "ok" else None, kind))
        sid = c.execute("SELECT id FROM ev_source WHERE url=?", (nu,)).fetchone()[0]
        vid = None
        rp = b.get("raw_path") or ""
        if rp:
            f = raw_abs(rp)
            if last_writer.get(rp) != seq:
                n_over += 1
            elif f is None or not f.is_file():
                n_missing += 1
            else:
                data = f.read_bytes()
                sha = hashlib.sha256(data).hexdigest()
                cur = c.execute("INSERT OR IGNORE INTO ev_source_version (source_id, section, content_sha, bytes, path, kind, pages,"
                                " first_seen, last_seen, n_fetch, provenance) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, 1, 'migrated')",
                                (sid, sha, len(data), rp, kind, b.get("pages"), at, at))
                if cur.rowcount:
                    vid = cur.lastrowid
                    n_ver += 1
                else:
                    vid = c.execute("SELECT id FROM ev_source_version WHERE source_id=? AND section='' AND content_sha=?",
                                    (sid, sha)).fetchone()[0]
                    c.execute("UPDATE ev_source_version SET last_seen=MAX(last_seen, ?), first_seen=MIN(first_seen, ?),"
                              " n_fetch=n_fetch+1 WHERE id=?", (at, at, vid))
        c.execute("INSERT OR IGNORE INTO ev_fetch (source_id, version_id, url_raw, section, run_id, at, status, error, kind, pages,"
                  " chars_full, via, agent, op_fetch_seq) VALUES (?,?,?,'',?,?,?,?,?,?,?,?, 'migrated', ?)",
                  (sid, vid, url, rid, at, status, err or None, kind, b.get("pages"), chars, b.get("via") or None, seq))
    rep.update({"op_fetch": len(ops), "versions_from_files": n_ver, "files_missing": n_missing, "rows_overwritten": n_over})

    # ② 사실 → 판(전부 inferred)
    texts = {}
    linked = matched = absent = 0
    absent_ids = []
    for fid, sid, quote in c.execute("SELECT id, source_id, quote FROM ev_fact WHERE source_id IS NOT NULL").fetchall():
        vs = c.execute("SELECT id, path FROM ev_source_version WHERE source_id=? ORDER BY last_seen DESC, id DESC", (sid,)).fetchall()
        if not vs:
            continue
        segs = _quote_segments(quote)
        pick = None
        if segs:
            for vid, path in vs:
                if vid not in texts:
                    f = raw_abs(path)
                    texts[vid] = re.sub(r"\s+", "", (f.read_text(encoding="utf-8", errors="ignore") if f and f.is_file() else "")
                                        .replace("**", "").replace("`", ""))
                if all(sg in texts[vid] for sg in segs):
                    pick = vid
                    break
        if pick:
            matched += 1
        elif segs:
            absent += 1
            absent_ids.append(fid)
        c.execute("UPDATE ev_fact SET source_version_id=?, source_version_link='inferred' WHERE id=?", (pick or vs[0][0], fid))
        linked += 1
    rep.update({"facts_linked": linked, "facts_quote_matched": matched, "facts_quote_absent": absent,
                "facts_quote_absent_ids": absent_ids[:20]})
    # ③ verified_rev
    c.execute("UPDATE ev_fact SET verified_rev=COALESCE((SELECT MAX(v.fact_rev) FROM ev_verdict v WHERE v.fact_id=ev_fact.id"
              " AND v.verdict='MATCH'), 1) WHERE verified_status='verified'")
    rep["verify_drift"] = c.execute("SELECT COUNT(*) FROM ev_fact WHERE verified_status='verified'"
                                    " AND COALESCE(verified_rev,0) < COALESCE(value_rev,1)").fetchone()[0]

    # ④ 옛 링크 본문 → 판 · 실패 사유 → 실패 시도
    n_link = n_link_err = 0
    for lid, url, body, fetched, run_id, iid in c.execute(
            "SELECT id, url, body, fetched, run_id, issue_id FROM mat_link WHERE body IS NOT NULL AND body<>'' AND version_id IS NULL"
            " AND last_fetch_id IS NULL ORDER BY id").fetchall():
        at = (c.execute("SELECT COALESCE(i.received_at, (SELECT r.capture_at FROM ex_run r WHERE r.run_id=?), i.day)"
                        " FROM mat_issue i WHERE i.id=?", (run_id, iid)).fetchone() or [None])[0] or now_iso()
        if fetched:
            rec = evidence_record(url, body, "", {"kind": "html"}, run_id=run_id, agent="migrated", at=str(at))
            if not rec["version_id"]:
                raise MigrationError(f"v3 링크 {lid} 본문 보관 실패 — {rec}")
            c.execute("UPDATE mat_link SET version_id=?, last_fetch_id=? WHERE id=?", (rec["version_id"], rec["fetch_id"], lid))
            c.execute("INSERT OR IGNORE INTO mat_link_version (link_id, run_id, fetch_id, version_id, at) VALUES (?,?,?,?,?)",
                      (lid, run_id or "", rec["fetch_id"], rec["version_id"], str(at)))
            n_link += 1
        else:
            rec = evidence_record(url, "", body, {}, run_id=run_id, agent="migrated", at=str(at))
            c.execute("UPDATE mat_link SET last_fetch_id=? WHERE id=?", (rec["fetch_id"], lid))
            n_link_err += 1
    rep.update({"links_versioned": n_link, "link_errors": n_link_err})

    # 검증 — 실패면 MigrationError(롤백)
    bad_files = []
    for vid, path, sha in c.execute("SELECT id, path, content_sha FROM ev_source_version WHERE path IS NOT NULL").fetchall():
        f = raw_abs(path)
        if f is None or not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != sha:
            bad_files.append(vid)
    chk = {
        "fetch_rows_migrated": c.execute("SELECT COUNT(*) FROM ev_fetch WHERE op_fetch_seq IS NOT NULL").fetchone()[0],
        "version_file_mismatch": len(bad_files),
        "fact_version_other_source": c.execute("SELECT COUNT(*) FROM ev_fact f JOIN ev_source_version v ON v.id=f.source_version_id"
                                               " WHERE v.source_id <> f.source_id").fetchone()[0],
        "facts_recorded": c.execute("SELECT COUNT(*) FROM ev_fact WHERE source_version_link='recorded'").fetchone()[0],
    }
    rep["check"] = chk
    if chk["fetch_rows_migrated"] != len(ops) or bad_files or chk["fact_version_other_source"] or chk["facts_recorded"]:
        raise MigrationError(f"v3 검증 실패 — {chk} · 파일 어긋난 판 {bad_files[:10]}")
    return rep


def _m4_stage_audit(c) -> dict:
    """v4(U02·U03·R09) — 단계 결과 표 + 감사 정체·범위 + run 계좌 계열 고정(트리거). 근거만 옮긴다:
    ① ex_run의 비어 있지 않은 단계 시각마다 ex_stage 한 행(attempt 1 · status unknown · provenance migrated · 끝 = 그 시각) —
       성공 여부는 그때 기록이 없어 지어내지 않는다(unknown). ex_run 시각은 그대로(v_run_timing 불변).
    ② op_audit 정체 칸 = body JSON의 reqid·dir·deliverable(절대 경로만)·content_sha · 범위 판정은 행마다 migration 판정 한 행.
    ③ 닫힌 run의 audit_rounds를 새 읽기(foreign 제외)로 다시 센다 — 옛→새를 보고서에."""
    rep = {}
    for col in ("reqid", "dir", "deliverable_path", "content_sha"):
        if col not in _cols(c, "op_audit"):
            c.execute(f"ALTER TABLE op_audit ADD COLUMN {col} TEXT")
    _drop_views(c)
    c.execute("DROP TRIGGER IF EXISTS tg_audit_noupd")          # 정체 칸을 채우는 동안만 — 단계 끝 init_schema가 되살린다
    init_schema(c)
    c.execute("DROP TRIGGER IF EXISTS tg_audit_noupd")
    _tq = ("SELECT run_id, capture_at, sent_at, filled_at, closed_at, capture_to_send_min, post_fill_min FROM v_run_timing"
           " ORDER BY run_id")                     # 시각 칸만 — audit_rounds는 이 단계가 일부러 다시 센다
    timing_before = [tuple(r) for r in c.execute(_tq).fetchall()]
    n_stage = 0
    stage_cols = (("capture", "capture_at"), ("carry", "carry_at"), ("map", "map_at"), ("note", "note_at"),
                  ("dispatch", "dispatch_at"), ("execute", "sent_at"), ("fill", "filled_at"), ("close", "closed_at"))
    for r in c.execute("SELECT * FROM ex_run ORDER BY run_id").fetchall():
        r = dict(r)
        for stg, col in stage_cols:
            if r.get(col):
                cur = c.execute("INSERT OR IGNORE INTO ex_stage (run_id, stage, attempt, status, started_at, ended_at, note, provenance)"
                                " VALUES (?,?,1,'unknown',?,?,'ex_run에서 옮김 — 성공 여부 기록 없음','migrated')",
                                (r["run_id"], stg, r.get("started") or r[col], r[col]))
                n_stage += cur.rowcount
    rep["stage_rows"] = n_stage
    scopes = {"project": 0, "foreign": 0, "unknown": 0}
    foreign = []
    for aid, rid, body in c.execute("SELECT id, run_id, body FROM op_audit ORDER BY id").fetchall():
        try:
            b = json.loads(body) if body else {}
        except ValueError:
            b = {}
        dlv = str(b.get("deliverable") or "")
        c.execute("UPDATE op_audit SET reqid=?, dir=?, deliverable_path=?, content_sha=? WHERE id=?",
                  (b.get("reqid"), b.get("dir"), dlv if dlv.startswith("/") else None, b.get("content_sha"), aid))
        if c.execute("SELECT 1 FROM op_audit_scope WHERE audit_id=?", (aid,)).fetchone():
            continue
        started = (c.execute("SELECT started FROM ex_run WHERE run_id=?", (rid,)).fetchone() or [None])[0]
        scope, root, frm, why = audit_scope_of(b, started)
        c.execute("INSERT INTO op_audit_scope (audit_id, scope, project_root, derived_from, reason, at, by) VALUES (?,?,?,?,?,?,'migration')",
                  (aid, scope, root, frm, why, now_iso()))
        scopes[scope] += 1
        if scope == "foreign":
            foreign.append({"id": aid, "run": rid, "why": why})
    rep.update({"audit_scope": scopes, "audit_foreign": foreign[:10]})
    rounds = []
    for rid, old in c.execute("SELECT run_id, audit_rounds FROM ex_run WHERE closed_at IS NOT NULL").fetchall():
        new = 0
        for row in c.execute("SELECT a.at, a.status, a.body, a.deliverable FROM op_audit a WHERE a.run_id=? AND a.skill='fact-check'"
                             " AND COALESCE((SELECT scope FROM op_audit_scope WHERE audit_id=a.id ORDER BY id DESC LIMIT 1), 'unknown')"
                             " <> 'foreign'", (rid,)).fetchall():
            try:
                bb = json.loads(row[2]) if row[2] else {}
            except ValueError:
                bb = {}
            series = str(bb.get("run") or row[3] or "").lower()
            if bb.get("type") == "finding" and not bb.get("superseded") and "주문근거" in series:
                new += 1
        if old is not None and int(old) != new:
            c.execute("UPDATE ex_run SET audit_rounds=? WHERE run_id=?", (new, rid))
            rounds.append({"run": rid, "old": old, "new": new})
    rep["audit_rounds_changed"] = rounds
    timing_after = [tuple(r) for r in c.execute(_tq).fetchall()]
    chk = {"run_timing_same": timing_before == timing_after,
           "audit_scope_missing": c.execute("SELECT COUNT(*) FROM op_audit a WHERE NOT EXISTS (SELECT 1 FROM op_audit_scope s"
                                            " WHERE s.audit_id=a.id)").fetchone()[0]}
    rep["check"] = chk
    if not chk["run_timing_same"] or chk["audit_scope_missing"]:
        raise MigrationError(f"v4 검증 실패 — {chk}")
    return rep


def _m5_bars_calendar(c) -> dict:
    """v5(U04·S02) — 일봉 관측 계약 + 휴장일 사본 + 파생 결과 출처 칸 + 일정 발행일 칸. 근거만 옮긴다:
    ① mk_bar 한 행 = 관측 한 번(그때 받은 시각 fetched_at) — 확정 여부는 그 시각 > 그 세션 마감으로 계산(지어내지 않는다) ·
       관측 이력 한 행씩(version 1) · 거래소는 국내 KRX · 미국은 그 종목의 성공한 od 조회 키의 거래소.
    ② mk_bar_fetch.final_through = 받을 때의 마지막 닫힌 세션일(빈 결과면 NULL — 다시 묻는다).
    ③ 휴장일 사본 ← config/holidays.json. ④ 기존 일정은 confirmed=1(그때 규칙으로 정해진 날짜 · 원 줄은 남지 않았다)."""
    rep = {}
    adds = (("mk_bar", "exchange", "TEXT"), ("mk_bar", "is_final", "INTEGER NOT NULL DEFAULT 0 CHECK (is_final IN (0, 1))"),
            ("mk_bar", "version", "INTEGER NOT NULL DEFAULT 1"), ("mk_bar", "final_check_at", "TEXT"), ("mk_bar", "final_check_err", "TEXT"),
            ("mk_bar_fetch", "final_through", "TEXT"),
            ("co_level_calc", "bars_asof", "TEXT"), ("co_level_calc", "bars_final", "INTEGER"),
            ("co_base_rate", "bars_asof", "TEXT"), ("co_base_rate", "bars_final", "INTEGER"),
            ("mk_factor_state", "bars_asof", "TEXT"), ("mk_factor_state", "bars_final", "INTEGER"),
            ("cal_event", "issue_date", "TEXT"), ("cal_event", "source_text", "TEXT"), ("cal_event", "weekday_token", "TEXT"),
            ("cal_event", "confirmed", "INTEGER"))
    for t, col, ddl in adds:
        if col not in _cols(c, t):
            c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {ddl}")
    _drop_views(c)
    init_schema(c)                                   # mk_bar_obs · mk_holiday · 트리거
    exch = {}
    for subj, k in c.execute("SELECT subject, k FROM mk_bar_fetch WHERE k LIKE 'od:%' AND n > 0 ORDER BY fetched_at").fetchall():
        exch[subj] = k.split(":")[1]
    n = n_final0 = 0
    by_day = {}
    for subj, d, o, h, lo, cl, v, src, fa in c.execute(
            "SELECT subject, date, open, high, low, close, volume, source, fetched_at FROM mk_bar ORDER BY subject, date").fetchall():
        m = _bar_market(subj)
        fin = 1 if (fa and _is_final(m, d, fa)) else 0
        ex = exch.get(subj) if m == "US" else "KRX"
        c.execute("UPDATE mk_bar SET is_final=?, exchange=?, version=1 WHERE subject=? AND date=?", (fin, ex, subj, d))
        c.execute("INSERT INTO mk_bar_obs (subject, date, open, high, low, close, volume, source, exchange, observed_at, is_final, version,"
                  " fetch_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'migrated')", (subj, d, o, h, lo, cl, v, src, ex, fa or now_iso(), fin))
        n += 1
        if not fin:
            n_final0 += 1
            by_day[f"{m}:{d}"] = by_day.get(f"{m}:{d}", 0) + 1
    rep.update({"bars": n, "bars_nonfinal": n_final0, "bars_nonfinal_by_day": dict(list(by_day.items())[:10])})
    from kis_client import last_session_date
    for subj, k, fa, nn in c.execute("SELECT subject, k, fetched_at, n FROM mk_bar_fetch").fetchall():
        ft = None
        if nn and fa:
            try:
                ft = last_session_date(_bar_market(subj), datetime.fromisoformat(fa)).isoformat()
            except ValueError:
                ft = None
        c.execute("UPDATE mk_bar_fetch SET final_through=? WHERE subject=? AND k=?", (ft, subj, k))
    rep["holidays"] = holidays_sync(c, force=True)
    rep["calendar_confirmed"] = c.execute("UPDATE cal_event SET confirmed=1 WHERE confirmed IS NULL").rowcount
    chk = {"obs_rows": c.execute("SELECT COUNT(*) FROM mk_bar_obs").fetchone()[0],
           "us_exchange_null": c.execute("SELECT COUNT(*) FROM mk_bar WHERE subject LIKE 'US:%' AND exchange IS NULL").fetchone()[0],
           "kr_exchange_null": c.execute("SELECT COUNT(*) FROM mk_bar WHERE subject NOT LIKE 'US:%' AND exchange IS NULL").fetchone()[0]}
    rep["check"] = chk
    if chk["obs_rows"] < n or chk["kr_exchange_null"]:
        raise MigrationError(f"v5 검증 실패 — {chk}")
    return rep


def _m6_scenarios(c) -> dict:
    """v6(S04·S05·J02) — 시나리오 사건·구조화 조건·발생·대응 완료. 근거만 옮긴다(조건을 지어내지 않는다):
    ① 기존 행은 전부 cond_status=text_only · 사건 없음. ② '영향 없음 — …' 대응은 noop=1.
    ③ 축·대응·예상·무효화가 비었으면 그 행의 출처 태그(signal_<st>_<mkt>.json#<id>)가 가리키는 시그널 행에서만 채운다 — 값이 하나일 때만
       (둘 이상이면 비워 두고 needs_review=1) · 채우면 판정 행에 '(migration v6)' 기록. ④ 실현된 행마다 발생 1(첫 실현 판정 시각) ·
       완료 표시된 대응은 그 발생의 완료 행으로."""
    rep = {}
    for t, col, ddl in (("jd_scenario", "event_id", "TEXT REFERENCES jd_event(id)"), ("jd_scenario", "cond_status", "TEXT"),
                        ("jd_scenario", "expected_reaction", "TEXT"), ("jd_scenario_response", "noop", "INTEGER NOT NULL DEFAULT 0")):
        if col not in _cols(c, t):
            c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {ddl}")
    _drop_views(c)
    init_schema(c)
    sig_dir = project_root() / "signals"
    sig_cache = {}

    def sig_rows(name):
        if name not in sig_cache:
            f = sig_dir / name
            try:
                sig_cache[name] = {str(r.get("id")): r for r in (json.loads(f.read_text(encoding="utf-8")).get("scenarios") or [])
                                   if isinstance(r, dict)} if f.is_file() else {}
            except (OSError, ValueError):
                sig_cache[name] = {}
        return sig_cache[name]
    filled = {"axis": 0, "response": 0, "expected": 0, "invalidation": 0}
    conflicts = []
    store = STORES["scenario"]
    for d in store.all():
        tags = [t for t in (d.get("sources") or []) if "#" in str(t) and str(t).startswith("signal_")]
        vals = {"axis": set(), "response": set(), "expected": set(), "invalidation": set()}
        for t in tags:
            name, lid = str(t).split("#", 1)
            r = sig_rows(name).get(lid)
            if not r:
                continue
            if r.get("axis_id") or r.get("axis"):
                vals["axis"].add(str(r.get("axis_id") or r.get("axis")))
            if r.get("response") or r.get("action"):
                vals["response"].add(str(r.get("response") or r.get("action")))
            for k in ("expected", "invalidation"):
                if r.get(k):
                    vals[k].add(str(r[k]))
        changed, notes = False, []
        for k, vs in vals.items():
            if d.get(k) or not vs:
                continue
            if len(vs) == 1:
                d[k] = next(iter(vs))
                filled[k] += 1
                changed = True
                notes.append(k)
            else:
                conflicts.append(f"{d.get('id')}.{k}")
        if changed:
            d.setdefault("judgments", []).append({"when": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "realized": d.get("realized"),
                                                  "acted": d.get("acted"), "note": f"{'·'.join(notes)} ← 출처 시그널 행 (migration v6)",
                                                  "auto": True})
            store.put(d)
    for cid in conflicts:                            # 축이 갈리면 재검토(요인·축 판단에 쓰인다) · 대응·예상 문구 차이는 비워 두기만 한다
        if cid.endswith(".axis"):
            c.execute("UPDATE jd_scenario SET needs_review=1 WHERE id=?", (cid.split(".")[0],))
    # 채우기(DocStore put — 행 칸·자식을 문서에서 다시 쓴다) 뒤에 칸 값을 정한다(앞에 하면 put이 지운다)
    rep["text_only"] = c.execute("UPDATE jd_scenario SET cond_status='text_only' WHERE cond_status IS NULL").rowcount
    rep["noop"] = c.execute("UPDATE jd_scenario_response SET noop=1 WHERE what LIKE '영향 없음%'").rowcount
    rep.update({"backfilled": filled, "backfill_conflicts": conflicts[:20], "backfill_conflict_n": len(conflicts),
                "axis_conflicts": [x for x in conflicts if x.endswith(".axis")]})
    n_ep = n_done = 0
    for d in store.all():
        if d.get("realized") is not True or c.execute("SELECT 1 FROM jd_scenario_episode WHERE scenario_id=?", (d["id"],)).fetchone():
            continue
        js = [j for j in (d.get("judgments") or []) if j.get("realized") is True]
        at = (str(js[0].get("when")).replace(" ", "T") + ":00+09:00") if js and js[0].get("when") else now_iso()
        eid = c.execute("INSERT INTO jd_scenario_episode (scenario_id, n, realized_at, note) VALUES (?,1,?,'migration v6')",
                        (d["id"], at)).lastrowid
        n_ep += 1
        for x in d.get("actions") or []:
            if x.get("done"):
                c.execute("INSERT OR IGNORE INTO jd_scenario_response_done (episode_id, response_key, market, done_at, done_note)"
                          " VALUES (?,?,?,?,?)", (eid, response_key(x.get("market"), x.get("ticker"), x.get("what")),
                                                  str(x.get("market") or "?").lower(), x.get("done_at") or at, x.get("done_note")))
                n_done += 1
    rep.update({"episodes": n_ep, "responses_done": n_done})
    chk = {"cond_status_null": c.execute("SELECT COUNT(*) FROM jd_scenario WHERE cond_status IS NULL").fetchone()[0],
           "realized_without_episode": c.execute("SELECT COUNT(*) FROM jd_scenario s WHERE s.realized=1 AND NOT EXISTS"
                                                 " (SELECT 1 FROM jd_scenario_episode e WHERE e.scenario_id=s.id)").fetchone()[0]}
    rep["check"] = chk
    if chk["cond_status_null"] or chk["realized_without_episode"]:
        raise MigrationError(f"v6 검증 실패 — {chk}")
    return rep


def _m7_ledger_links(c) -> dict:
    """v7(R10·R11·R06) — 주문↔승인 연결 · 체결 관측 뷰 · 입출금 표. 근거만 옮긴다(관계를 지어내지 않는다):
    ① 승인 행 idx = 그 승인 파일 안 순번(수량 있는 행의 넣은 순서 = 파일 orders[] 순서 · 거부 행은 순번 없음)
    ② 주문 행의 해시·순번·의도 키 = 거래 행 원문(body)에 집행기가 적은 값(2026-10-19 창 ① 뒤 집행분) · 같은 run에 같은 의도 키가 둘이면 뒤 행은 비움
    ③ 연결: 해시+순번 → recorded(그 해시의 승인 행이 없으면 그날 기록 runs[] 스냅샷으로 되살림) · 옛 행은 (run·파일명·종목·방향)이 수량 있는
       승인 행 하나에만 맞으면 inferred · 나머지 NULL ④ 체결 부호 — 주문마다 마지막 관측이 음수면 절댓값 관측 행을 덧붙인다(옛 행 그대로)
    ⑤ op_cash_flow·v_fill_latest·v_tax(주문 단위 환율)·ux_order_intent·tg_order_noupd(연결 칸만 열림)는 스키마 파일에서."""
    rep = {}
    for col, ddl in (("approved_sha", "TEXT"), ("approved_idx", "INTEGER"), ("intent_key", "TEXT"), ("approval_link", "TEXT")):
        if col not in _cols(c, "ex_order"):
            c.execute(f"ALTER TABLE ex_order ADD COLUMN {col} {ddl}")
    for tg in ("tg_order_noupd", "tg_approval_noupd"):           # 연결 칸을 채우는 동안만 — 끝에 init_schema가 새 정의로 다시 만든다
        c.execute(f"DROP TRIGGER IF EXISTS {tg}")
    _drop_views(c)
    rep["approval_idx"] = c.execute(
        "UPDATE ex_approval SET idx = (SELECT COUNT(*) FROM ex_approval b WHERE b.approved_sha = ex_approval.approved_sha"
        " AND b.rejected_why IS NULL AND b.id < ex_approval.id) WHERE idx IS NULL AND rejected_why IS NULL AND approved_sha IS NOT NULL").rowcount
    seen, dup, n_keys = set(), [], 0
    for oid, rid, asha, aidx, ikey in c.execute(
            "SELECT id, run_id, json_extract(body, '$.approved_sha256'), json_extract(body, '$.approved_idx'), json_extract(body, '$.intent_key')"
            " FROM ex_order WHERE approved_sha IS NULL AND intent_key IS NULL ORDER BY id").fetchall():
        if not (asha or ikey):
            continue
        if ikey and (rid, ikey) in seen:
            dup.append(oid)
            ikey = None
        if ikey:
            seen.add((rid, ikey))
        c.execute("UPDATE ex_order SET approved_sha=?, approved_idx=?, intent_key=? WHERE id=?", (asha, aidx, ikey, oid))
        n_keys += 1
    rep.update({"order_keys": n_keys, "order_dup_intent": dup})
    jcache, restored = {}, {}
    n_rec = n_inf = 0
    for oid, rid, mk_, tk, side, aref, asha, aidx in c.execute(
            "SELECT id, run_id, market, ticker, side, approved_ref, approved_sha, approved_idx FROM ex_order WHERE approval_id IS NULL ORDER BY id").fetchall():
        aid = alink = None
        if asha and aidx is not None:
            if rid not in jcache:
                st_, m_ = str(rid).split("_", 1)
                f = project_root() / "journal" / f"trades_{st_}_{m_}.json"
                runs_by_sha = {}
                try:
                    for i_run, rr in enumerate((json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}).get("runs") or []):
                        if isinstance(rr, dict) and rr.get("approved_sha256") and rr["approved_sha256"] not in runs_by_sha:
                            runs_by_sha[rr["approved_sha256"]] = (i_run, rr)
                except (OSError, ValueError):
                    pass
                jcache[rid] = (runs_by_sha, f.name)
            aid, alink = _approval_link(c, rid, str(mk_ or "").lower(), {}, asha, aidx, jcache[rid][0], jcache[rid][1], restored)
        elif aref:
            aid, alink = _approval_link(c, rid, str(mk_ or "").lower(), {"approved_ref": aref, "ticker": tk, "action": side},
                                        None, None, {}, "", restored)
        if aid:
            c.execute("UPDATE ex_order SET approval_id=?, approval_link=? WHERE id=?", (aid, alink, oid))
            n_rec += alink == "recorded"
            n_inf += alink == "inferred"
    rep["approval_link"] = {"recorded": n_rec, "inferred": n_inf,
                            "null": c.execute("SELECT COUNT(*) FROM ex_order WHERE approval_id IS NULL").fetchone()[0],
                            "approvals_restored": restored.get("approvals_restored", 0)}
    rep["fill_sign_rows"] = c.execute(
        "INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, avg_price, limit_price, verdict, qty_before, qty_after, requested,"
        " settle_date, at, body) SELECT order_id, run_id, ticker, market, ABS(filled_qty), avg_price, limit_price, verdict, qty_before, qty_after,"
        " requested, settle_date, at, json_object('migration', 'R11-sign', 'from_fill_id', id) FROM ex_fill f"
        " WHERE f.id = (SELECT MAX(g.id) FROM ex_fill g WHERE g.order_id = f.order_id) AND f.filled_qty < 0").rowcount
    chk = {"latest_negative": c.execute("SELECT COUNT(*) FROM ex_fill f WHERE f.id = (SELECT MAX(g.id) FROM ex_fill g WHERE g.order_id = f.order_id)"
                                        " AND f.filled_qty < 0").fetchone()[0],
           "dup_intent_left": c.execute("SELECT COUNT(*) FROM (SELECT run_id, intent_key FROM ex_order WHERE intent_key IS NOT NULL"
                                        " GROUP BY 1, 2 HAVING COUNT(*) > 1)").fetchone()[0]}
    rep["check"] = chk
    if any(chk.values()):
        raise MigrationError(f"v7 검증 실패 — {chk}")
    return rep


_LEDGER5 = ("ex_approval", "ex_order", "ex_fill", "ex_fill_fx", "ex_balance")


def _ledger_sums(c) -> dict:
    """v8 전후 대조값 — 표마다 행 수 · 주문 방향별 수량 합 · 체결 판정별 |수량| 합 · 시장 값 · 환율 합."""
    one = lambda q_: c.execute(q_).fetchone()[0]
    return {"rows": {t: one(f"SELECT COUNT(*) FROM {t}") for t in _LEDGER5},
            "order_qty": {r[0]: r[1] for r in c.execute("SELECT side, SUM(qty) FROM ex_order GROUP BY 1").fetchall()},
            "fill_abs": {r[0]: r[1] for r in c.execute("SELECT verdict, SUM(ABS(COALESCE(filled_qty, 0))) FROM ex_fill GROUP BY 1").fetchall()},
            "markets": sorted({r[0] for t in ("ex_approval", "ex_order", "ex_fill", "ex_balance")
                               for r in c.execute(f"SELECT DISTINCT LOWER(market) FROM {t}").fetchall()}),
            "fx_sum": one("SELECT ROUND(COALESCE(SUM(settle_fx), 0), 6) FROM ex_fill_fx")}


def _m8_typed_ledger(c) -> dict:
    """v8(R12) — 집행 장부 다섯 표를 STRICT + CHECK로 다시 만든다(스키마 파일 정의 · rebuild_table · id 그대로). 옮기면서 맞추는 것:
    시장 소문자 · 체결 수량 절댓값(방향은 주문 행) · 승인 플래그 빈 값 0 · 거부 행 사유가 비면 '(사유 칸 없음)' · 공유 현금 빈 값 0 ·
    0 이하 환율·통화 밖 값은 비움. 새 제약을 어기는 행이 있으면(사전 검사) 아무것도 바꾸지 않고 멈춘다. 전후 행 수·수량 합·환율 합이 같아야 한다."""
    pre = _ledger_sums(c)
    rep = {"before": pre}
    rep["ex_approval"] = rebuild_table(c, "ex_approval", {
        "market": "LOWER(market)", "needs_sell_fill": "COALESCE(needs_sell_fill, 0)", "rounded_up_to_1": "COALESCE(rounded_up_to_1, 0)",
        "order_capped": "COALESCE(order_capped, 0)",
        "rejected_why": "CASE WHEN qty IS NULL AND rejected_why IS NULL THEN '\"(사유 칸 없음)\"' ELSE rejected_why END"}, pre_checks=[
        ("pass 어휘 밖", "SELECT COUNT(*) FROM ex_approval WHERE pass IS NULL OR pass NOT IN ('main','stops','refill')"),
        ("시장 어휘 밖", "SELECT COUNT(*) FROM ex_approval WHERE market IS NULL OR LOWER(market) NOT IN ('kr','us')"),
        ("방향 어휘 밖", "SELECT COUNT(*) FROM ex_approval WHERE action IS NULL OR action NOT IN ('BUY','SELL')"),
        ("수량 0 이하·정수 아님", "SELECT COUNT(*) FROM ex_approval WHERE qty IS NOT NULL AND (qty <= 0 OR qty <> CAST(qty AS INTEGER))"),
        ("음수 가격", "SELECT COUNT(*) FROM ex_approval WHERE price < 0"),
        ("수량과 거부 사유가 함께 있음", "SELECT COUNT(*) FROM ex_approval WHERE qty IS NOT NULL AND rejected_why IS NOT NULL"),
        ("경로·해시·원문 빈 칸", "SELECT COUNT(*) FROM ex_approval WHERE approved_path IS NULL OR approved_sha IS NULL OR body IS NULL OR ticker IS NULL")])
    rep["ex_order"] = rebuild_table(c, "ex_order", {"market": "LOWER(market)"}, pre_checks=[
        ("run·종목·시각·열쇠·원문 빈 칸", "SELECT COUNT(*) FROM ex_order WHERE run_id IS NULL OR ticker IS NULL OR sent_at IS NULL"
                                         " OR trade_key IS NULL OR body IS NULL"),
        ("시장 어휘 밖", "SELECT COUNT(*) FROM ex_order WHERE market IS NULL OR LOWER(market) NOT IN ('kr','us')"),
        ("방향 어휘 밖", "SELECT COUNT(*) FROM ex_order WHERE side IS NULL OR side NOT IN ('BUY','SELL')"),
        ("수량 0 이하·정수 아님", "SELECT COUNT(*) FROM ex_order WHERE qty IS NULL OR qty <= 0 OR qty <> CAST(qty AS INTEGER)"),
        ("상태 어휘 밖", "SELECT COUNT(*) FROM ex_order WHERE status IS NULL OR status NOT IN"
                        " ('PENDING','SENT','UNKNOWN','REJECTED','UNSENT','FAILED','FILLED')"),
        ("음수 지정가", "SELECT COUNT(*) FROM ex_order WHERE limit_price < 0")])
    rep["ex_fill"] = rebuild_table(c, "ex_fill", {"market": "LOWER(market)", "filled_qty": "ABS(filled_qty)"}, pre_checks=[
        ("주문·run·종목·원문 빈 칸", "SELECT COUNT(*) FROM ex_fill WHERE order_id IS NULL OR run_id IS NULL OR ticker IS NULL OR body IS NULL"),
        ("시장 어휘 밖", "SELECT COUNT(*) FROM ex_fill WHERE market IS NULL OR LOWER(market) NOT IN ('kr','us')"),
        ("판정 어휘 밖", "SELECT COUNT(*) FROM ex_fill WHERE verdict IS NULL OR verdict NOT IN"
                        " ('FILLED','PARTIAL','CANCELLED','EXPIRED','REJECTED','UNFILLED','UNKNOWN')"),
        ("체결인데 수량 0", "SELECT COUNT(*) FROM ex_fill WHERE verdict IN ('FILLED','PARTIAL') AND filled_qty = 0"),
        ("수량 정수 아님", "SELECT COUNT(*) FROM ex_fill WHERE filled_qty IS NOT NULL AND filled_qty <> CAST(filled_qty AS INTEGER)"),
        ("음수 평단", "SELECT COUNT(*) FROM ex_fill WHERE avg_price < 0")])
    rep["ex_fill_fx"] = rebuild_table(c, "ex_fill_fx", None, pre_checks=[
        ("환율 0 이하·빈 칸", "SELECT COUNT(*) FROM ex_fill_fx WHERE settle_fx IS NULL OR settle_fx <= 0")])
    rep["ex_balance"] = rebuild_table(c, "ex_balance", {
        "market": "LOWER(market)", "cash_shared": "COALESCE(cash_shared, 0)",
        "exchange_rate": "CASE WHEN exchange_rate > 0 THEN exchange_rate END",
        "currency": "CASE WHEN currency IN ('KRW','USD') THEN currency END"}, pre_checks=[
        ("시장 어휘 밖", "SELECT COUNT(*) FROM ex_balance WHERE market IS NULL OR LOWER(market) NOT IN ('kr','us')"),
        ("단계·시각 빈 칸", "SELECT COUNT(*) FROM ex_balance WHERE phase IS NULL OR at IS NULL"),
        ("공유 현금 0·1 밖", "SELECT COUNT(*) FROM ex_balance WHERE cash_shared IS NOT NULL AND cash_shared NOT IN (0, 1)")])
    post = _ledger_sums(c)
    rep["after"] = post
    if (pre["rows"], pre["order_qty"], pre["fill_abs"], pre["markets"], pre["fx_sum"]) != \
            (post["rows"], post["order_qty"], post["fill_abs"], post["markets"], post["fx_sum"]):
        raise MigrationError(f"v8 검증 실패 — 전 {pre} · 후 {post}")
    return rep


def _m9_measure_forecast_material(c) -> dict:
    """v9(U05·U06·G04·G01) — 후보 측정 상태 · 전망 판정 타입 칸 · 결과 연결 뷰 · 수집 품질 표. 근거만 옮긴다:
    ① 측정일이 있는 수혜 행 → measure_status valid · measured_at = 측정일(날짜만 안다) · 숫자는 적힌 근거 문장에서 정확한 형식만 읽는다 ·
       일봉 기준일은 모른다(NULL → 읽을 때 '오래됨' — 정직한 상태) · 근거가 '측정 실패'인 행 → unavailable
    ② extra.score → judged_at·excess_pct·judge_note ③ 과거 run의 출처 통계는 받은 호에서만(요청·실패는 모른다 — NULL)
    ④ mat_expect는 0행이면 지운다(발행 간격은 이제 과거 호에서 계산)."""
    rep = {}
    for t, col, ddl in [("jd_axis_beneficiary", x, "TEXT") for x in ("priced_bars_asof", "measure_status", "measured_at", "measure_error",
                                                                     "measure_run_id")] + \
                       [("jd_axis_beneficiary", x, "") for x in ("priced_ret20", "priced_vs_bench", "priced_vs_lead", "judge_ret_pct")] + \
                       [("dim_axis", x, "TEXT") for x in ("judged_at", "judge_note", "judged_run_id", "judge_asof")] + [("dim_axis", "excess_pct", "")] + \
                       [("mat_link", "pick_status", "TEXT"), ("mat_link", "pick_reason", "TEXT"), ("mat_link", "pick_rank", "INTEGER")]:
        if col not in _cols(c, t):
            c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {ddl}".rstrip())
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mat_expect'").fetchone():
        n = c.execute("SELECT COUNT(*) FROM mat_expect").fetchone()[0]
        if n:
            raise MigrationError(f"v9: mat_expect에 {n}행이 있다 — 지우기 전에 옮길 곳을 정한다")
        c.execute("DROP TABLE mat_expect")
    _drop_views(c)
    init_schema(c)
    rx = re.compile(r"20일 ([+-]?\d+\.\d)% · 벤치 대비 ([+-]?\d+\.\d)%p · 선두 대비 ([+-]?\d+\.\d)%p")
    store = STORES["axis"]
    n_valid = n_unav = n_score = n_nums = 0
    for d in store.all():
        changed = False
        sc = d.pop("score", None)
        if isinstance(sc, dict):
            d.setdefault("judged_at", sc.get("judged"))
            if isinstance(sc.get("excess_pct"), (int, float)):
                d.setdefault("excess_pct", float(sc["excess_pct"]))
            if sc.get("note"):
                d.setdefault("judge_note", sc.get("note"))
            n_score += 1
            changed = True
        for b in d.get("beneficiaries") or []:
            if b.get("measure_status"):
                continue
            basis = str(b.get("priced_basis") or "")
            if basis.startswith("측정 실패"):
                m_ = re.match(r"측정 실패 (\d{4}-\d{2}-\d{2})", basis)
                b.update({"measure_status": "unavailable", "measured_at": m_.group(1) if m_ else None, "measure_error": basis})
                n_unav += 1
                changed = True
            elif b.get("priced_at"):
                b.update({"measure_status": "valid", "measured_at": b["priced_at"]})
                m_ = rx.search(basis)
                if m_:
                    b.update({"priced_ret20": float(m_.group(1)), "priced_vs_bench": float(m_.group(2)), "priced_vs_lead": float(m_.group(3))})
                    n_nums += 1
                n_valid += 1
                changed = True
        if changed:
            store.put(d)
    rep.update({"measure_valid": n_valid, "measure_unavailable": n_unav, "measure_numbers": n_nums, "score_moved": n_score})
    rep["source_stat_rows"] = c.execute(
        "INSERT OR IGNORE INTO mat_source_stat (run_id, source, received, newest_issue_day, note)"
        " SELECT r.run_id, COALESCE(NULLIF(i.sender, ''), NULLIF(i.source, ''), '?'), COUNT(*), MAX(REPLACE(i.day, '/', '-')),"
        " 'migration v9 — 받은 호에서만(요청·실패는 모른다)' FROM mat_issue_run r JOIN mat_issue i ON i.id = r.issue_id GROUP BY 1, 2").rowcount
    chk = {"score_left": c.execute("SELECT COUNT(*) FROM dim_axis WHERE extra LIKE '%\"score\"%'").fetchone()[0],
           "received_mismatch": c.execute(
               "SELECT COUNT(*) FROM (SELECT r.run_id, COUNT(*) n FROM mat_issue_run r GROUP BY 1) a"
               " JOIN (SELECT run_id, SUM(received) n FROM mat_source_stat GROUP BY 1) b ON a.run_id = b.run_id WHERE a.n <> b.n").fetchone()[0],
           "pick_nonnull": c.execute("SELECT COUNT(*) FROM mat_link WHERE pick_status IS NOT NULL").fetchone()[0]}
    rep["check"] = chk
    if any(chk.values()):
        raise MigrationError(f"v9 검증 실패 — {chk}")
    return rep


def _m10_decision_versions(c) -> dict:
    """v10(J03·J01·G02) — 시그널 판본 · 제안을 판별로(run, sha, idx) · 승인 머리 식별자 칸 · 심사 회차의 시그널 판 · 결과 표. 근거만 옮긴다:
    ① ex_signal_version = 지금 시그널 파일의 해시가 ex_signal.sha와 같을 때만 그 본문(덮인 옛 판은 재구성하지 않는다)
    ② 기존 제안·근거 행의 sha = 그 run의 ex_signal.sha(없으면 'legacy') ③ 새 칸(목표 기준·기저율·논지 구조·머리 식별자)은 비워 둔다(지어내지 않는다)."""
    rep = {}
    before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("ex_proposal", "ex_proposal_fact")}
    rep["proposals_without_signal"] = c.execute(
        "SELECT COUNT(*) FROM ex_proposal p WHERE NOT EXISTS (SELECT 1 FROM ex_signal s WHERE s.run_id = p.run_id AND s.sha IS NOT NULL)").fetchone()[0]
    sha_of = "COALESCE((SELECT s.sha FROM ex_signal s WHERE s.run_id = {t}.run_id), 'legacy')"
    rep["ex_proposal"] = rebuild_table(c, "ex_proposal", {"sha": sha_of.format(t="ex_proposal")})
    rep["ex_proposal_fact"] = rebuild_table(c, "ex_proposal_fact", {"sha": sha_of.format(t="ex_proposal_fact")})
    for col in ("signal_sha", "snapshot_sha", "universe_sha", "material_sha", "code_commit", "code_sha"):
        if col not in _cols(c, "ex_approval"):
            c.execute(f"ALTER TABLE ex_approval ADD COLUMN {col} TEXT")
    if "signal_sha" not in _cols(c, "ex_screen"):
        c.execute("ALTER TABLE ex_screen ADD COLUMN signal_sha TEXT")
    if "screen_pass" not in _cols(c, "ex_approval"):          # W05 — 옛 승인 행은 비워 둔다(회차를 지어내지 않는다 → 결과는 unlinked)
        c.execute("ALTER TABLE ex_approval ADD COLUMN screen_pass INTEGER")
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ex_outcome'").fetchone():
        for col, typ in (("fill_link", "TEXT"), ("calc_version", "INTEGER")):
            if col not in _cols(c, "ex_outcome"):
                c.execute(f"ALTER TABLE ex_outcome ADD COLUMN {col} {typ}")
    _drop_views(c)
    init_schema(c)
    n_ver = n_skip = 0
    for rid, path_, sha_, gen in c.execute("SELECT run_id, path, sha, generated_at FROM ex_signal WHERE sha IS NOT NULL").fetchall():
        f = project_root() / str(path_ or "")
        try:
            raw = f.read_bytes() if path_ and f.is_file() else None
        except OSError:
            raw = None
        if raw is None or hashlib.sha256(raw).hexdigest() != sha_:
            n_skip += 1
            continue
        n_ver += c.execute("INSERT OR IGNORE INTO ex_signal_version (sha, run_id, generated_at, path, imported_at, body) VALUES (?,?,?,?,?,?)",
                           (sha_, rid, gen, path_, now_iso(), raw.decode("utf-8", errors="replace"))).rowcount
    rep.update({"signal_versions": n_ver, "signal_versions_unmatched": n_skip})
    after = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("ex_proposal", "ex_proposal_fact")}
    chk = {"rows_changed": {t: (before[t], after[t]) for t in before if before[t] != after[t]},
           "fact_orphans": c.execute("SELECT COUNT(*) FROM ex_proposal_fact pf WHERE NOT EXISTS (SELECT 1 FROM ex_proposal p WHERE"
                                     " p.run_id = pf.run_id AND p.sha = pf.sha AND p.idx = pf.idx)").fetchone()[0]}
    rep["check"] = chk
    if chk["rows_changed"] or chk["fact_orphans"]:
        raise MigrationError(f"v10 검증 실패 — {chk}")
    return rep


MIGRATIONS = ((2, _m2_fact_rev), (3, _m3_evidence), (4, _m4_stage_audit), (5, _m5_bars_calendar), (6, _m6_scenarios),
              (7, _m7_ledger_links), (8, _m8_typed_ledger), (9, _m9_measure_forecast_material), (10, _m10_decision_versions))


def migrate(dry_run: bool = False) -> dict:
    """저장소를 코드 판까지 올린다. 판마다 한 트랜잭션(실패하면 그 판만 롤백 · 앞 판은 남는다) · 시작 전 백업 1개
    (`_db_backup/trading_pre_migrate_v<n>.db`) · `--dry-run`은 전 과정을 한 트랜잭션에서 돌리고 롤백한다(보고서만).
    다시 돌리면 올릴 판이 없어 아무것도 하지 않는다(멱등)."""
    with _gate_open():
        c = connect()
        have = schema_version(c)
        rep = {"from": have, "to": have, "code": SCHEMA_VERSION, "dry_run": dry_run, "backup": None, "steps": []}
        if have > SCHEMA_VERSION:
            raise SchemaNewer(f"저장소 판 {have} > 코드 판 {SCHEMA_VERSION}")
        todo = [(n, fn) for n, fn in MIGRATIONS if n > have]
        if (todo[-1][0] if todo else have) != SCHEMA_VERSION:
            raise MigrationError(f"코드 판 {SCHEMA_VERSION}까지 올릴 이전 단계가 없다(저장소 판 {have} · 등록된 단계 "
                                 f"{[n for n, _ in MIGRATIONS]}) — MIGRATIONS를 확인하라")
        if not todo:
            return rep
        k = id(c)
        if _TX_DEPTH.get(k):
            raise MigrationError("트랜잭션 안에서 migrate를 부를 수 없다")
        if not dry_run:
            rep["backup"] = str(backup(f"pre_migrate_v{have}"))
        c.execute("PRAGMA foreign_keys = OFF")              # 트랜잭션 밖에서만 듣는다(표를 다시 만드는 동안 외래키 검사를 미룬다)
        open_tx = False
        try:
            for n, fn in todo:
                if not open_tx:
                    c.execute("BEGIN IMMEDIATE")
                    _TX_DEPTH[k] = 1
                    open_tx = True
                _SCHEMA_PARTIAL[0] = n != todo[-1][0]
                with hold_exports():
                    step = fn(c)
                    init_schema(c)                          # 뷰·트리거·색인을 스키마 파일에서 다시(멱등 · 마지막 판은 엄격)
                    bad = c.execute("PRAGMA foreign_key_check").fetchall()
                    if bad:
                        raise MigrationError(f"v{n}: 외래키 위반 {len(bad)}건 — {[tuple(b) for b in bad[:5]]}")
                    c.execute("UPDATE sys_meta SET v=? WHERE k='schema_version'", (str(n),))
                rep["steps"].append({"to": n, **step})
                rep["to"] = n
                if not dry_run:
                    c.execute("COMMIT")
                    _TX_DEPTH[k] = 0
                    open_tx = False
            if open_tx:                                     # dry-run — 전부 되돌린다
                c.execute("ROLLBACK")
                _TX_DEPTH[k] = 0
                open_tx = False
        except BaseException:
            if open_tx:
                _TX_DEPTH[k] = 0
                _PENDING_EXPORTS.clear()
                c.execute("ROLLBACK")
            raise
        finally:
            _SCHEMA_PARTIAL[0] = False
            c.execute("PRAGMA foreign_keys = ON")
        if not dry_run:
            # 단계마다 hold_exports가 렌더본 표시를 버렸다 — 전 단계가 검사를 통과해 커밋된 지금 한 번 다시 쓴다(hold_exports의 계약).
            #   안 쓰면 새 칸이 든 표(예: v9 지도)의 렌더본이 옛 모양으로 남아 doctor가 '다름'으로 실패한다(2026-10-19 연기 시험 ⑫).
            try:
                rep["exported"] = [p.name for p in export_all()]
            except Exception as e:                     # noqa: BLE001 — 렌더본 실패가 커밋된 이전을 되돌리지 않는다 · doctor가 다름을 잡는다
                rep["export_error"] = f"{type(e).__name__}: {e}"
            d = doctor(verbose=False)
            rep["doctor_ok"], rep["drift"] = d["ok"], d["schema_drift"]
        return rep


def restore(path, tables_: list = None) -> dict:
    """백업 파일로 저장소를 되돌린다 — 전체(백업 API로 통째로) 또는 표 몇 개(`restore_tables`). 전체 복원 뒤 판은 백업의 판이다:
    코드도 같은 판으로 되돌려야(git revert) 다음 run이 열린다. 판이 다른 저장소를 열어야 하므로 판 검사를 건너뛴다."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"백업 없음: {path}")
    with _gate_open():
        if tables_:
            return {"restored_from": str(path), "tables": restore_tables(path, tables_), "schema_version": schema_version()}
        c = connect()
        if _TX_DEPTH.get(id(c)):
            raise MigrationError("트랜잭션 안에서 restore를 부를 수 없다")
        src = sqlite3.connect(str(path))
        try:
            src.backup(c)
        finally:
            src.close()
        return {"restored_from": str(path), "schema_version": schema_version(c)}


def _checks(sql: str) -> list:
    out, i = [], 0
    s = sql or ""
    while True:
        m = re.search(r"\bCHECK\s*\(", s[i:], re.I)
        if not m:
            break
        j = i + m.end()
        depth, k = 1, j
        while k < len(s) and depth:
            depth += {"(": 1, ")": -1}.get(s[k], 0)
            k += 1
        out.append(re.sub(r"\s+", "", s[j:k - 1]).lower())
        i = k
    return sorted(out)


def schema_drift(c=None) -> list:
    """저장소 표 모양 vs 스키마 파일 — 표·칸(자료형·NOT NULL·기본값·기본키)·STRICT·CHECK·색인·트리거·뷰 이름. 다름이 없으면 []."""
    c = c or connect()
    ref = sqlite3.connect(":memory:")
    try:
        for stmt in _schema_statements(SCHEMA_PATH.read_text(encoding="utf-8")):
            ref.execute(stmt)

        def objs(conn, typ):
            return {r[0]: r[1] for r in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'", (typ,)).fetchall()}

        def colmap(conn, t):
            return {r[1]: (str(r[2] or "").upper(), int(r[3]), r[4], int(r[5])) for r in conn.execute(f"PRAGMA table_info({t})")}
        out = []
        rt, lt = objs(ref, "table"), objs(c, "table")
        for t in sorted(rt):
            if t not in lt:
                out.append(f"표 없음: {t}")
                continue
            rc, lc = colmap(ref, t), colmap(c, t)
            for col in rc:
                if col not in lc:
                    out.append(f"{t}.{col}: 칸 없음")
                elif rc[col] != lc[col]:
                    out.append(f"{t}.{col}: (자료형, NOT NULL, 기본값, 키) 저장소 {lc[col]} ≠ 파일 {rc[col]}")
            for col in lc:
                if col not in rc:
                    out.append(f"{t}.{col}: 파일에 없는 칸")
            if bool(re.search(r"\)\s*STRICT\s*;?\s*$", rt[t] or "", re.I)) != bool(re.search(r"\)\s*STRICT\s*;?\s*$", lt[t] or "", re.I)):
                out.append(f"{t}: STRICT 다름")
            if _checks(rt[t]) != _checks(lt[t]):
                out.append(f"{t}: CHECK 다름")
        for t in sorted(set(lt) - set(rt)):
            out.append(f"파일에 없는 표: {t}")
        for typ in ("index", "trigger", "view"):
            ro, lo = set(objs(ref, typ)), set(objs(c, typ))
            for n in sorted(ro - lo):
                out.append(f"{typ} 없음: {n}")
            for n in sorted(lo - ro):
                out.append(f"파일에 없는 {typ}: {n}")
        return out
    finally:
        ref.close()


def doctor(verbose: bool = True) -> dict:
    with _gate_open():
        return _doctor(connect(), verbose)


def _doctor(c, verbose: bool) -> dict:
    res = {"integrity": c.execute("PRAGMA integrity_check").fetchone()[0],
           "fk_violations": [dict(r) for r in c.execute("PRAGMA foreign_key_check").fetchall()],
           "counts": {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables()}}
    res["exports"] = {}
    for n, (_p, fmt) in EXPORTS.items():
        p = export_path(n)
        try:
            want = serialize(render(n), fmt)
            have = p.read_text(encoding="utf-8") if p.exists() else None
            res["exports"][n] = "같음" if have == want else ("파일 없음" if have is None else "다름")
        except Exception as e:
            res["exports"][n] = f"오류 {e}"
    res["schema_version"] = schema_version(c)
    res["schema_drift"] = schema_drift(c)
    try:          # 검증 뒤 값이 바뀌었는데 verified로 남은 사실(v3 · 다시 검증 큐가 잡는다 — 보고만)
        res["verify_drift"] = c.execute("SELECT COUNT(*) FROM ev_fact WHERE verified_status='verified'"
                                        " AND COALESCE(verified_rev,0) < COALESCE(value_rev,1)").fetchone()[0]
    except sqlite3.OperationalError:
        res["verify_drift"] = None
    res["ok"] = (res["integrity"] == "ok" and not res["fk_violations"] and all(v == "같음" for v in res["exports"].values())
                 and res["schema_version"] == SCHEMA_VERSION and not res["schema_drift"])
    if verbose:
        print(f"integrity: {res['integrity']} · FK 위반 {len(res['fk_violations'])}건")
        print(f"  저장소 판 {res['schema_version']} · 코드 판 {SCHEMA_VERSION}"
              + ("" if res["schema_version"] == SCHEMA_VERSION else " — 다르다(`python3 db.py migrate`)"))
        print(f"  스키마 차이 {len(res['schema_drift'])}건" + "".join(f"\n    - {x}" for x in res["schema_drift"][:30]))
        if res.get("verify_drift"):
            print(f"  검증 뒤 값이 바뀐 verified 사실 {res['verify_drift']}건 — 다음 검증 큐가 다시 본다")
        try:
            print("  " + holidays_coverage_line())
        except Exception:                            # noqa: BLE001
            pass
        for n, v in res["exports"].items():
            print(f"  내보내기 {n}: {v}")
        nonzero = {t: n for t, n in res["counts"].items() if n}
        print("  행 수: " + " · ".join(f"{t} {n}" for t, n in nonzero.items()))
        print("DOCTOR " + ("OK" if res["ok"] else "FAIL"))
    return res


# ───────────────────────────── CLI ─────────────────────────────

def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("schema")
    sp.add_parser("doctor")
    s = sp.add_parser("show")
    s.add_argument("table")
    s.add_argument("--where", default="")
    s.add_argument("--limit", type=int, default=30)
    s = sp.add_parser("sql")
    s.add_argument("query")
    s = sp.add_parser("export")
    s.add_argument("names", nargs="*")
    s = sp.add_parser("backup")
    s.add_argument("label")
    s = sp.add_parser("migrate", help="저장소를 코드 판까지 올린다(판마다 한 트랜잭션 · 시작 전 백업 · 검증 보고)")
    s.add_argument("--dry-run", action="store_true", help="전 과정을 돌리고 롤백한다(보고서만)")
    s = sp.add_parser("bars-finalize", help="마감된 세션의 미확정 일봉을 다시 관측해 확정(브로커 조회 · capture가 부른다)")
    s.add_argument("--market", choices=["kr", "us"], default=None)
    s = sp.add_parser("audit-rescope", help="그 run 감사 행의 범위(project/foreign/unknown)를 지금 규칙으로 다시 판정(덧붙임)")
    s.add_argument("run_id")
    s = sp.add_parser("raw-purge", help="참조 없는 원문 판의 파일만 정리(기본 7일 · 참조된 판은 영구)")
    s.add_argument("--keep-days", type=int, default=RAW_KEEP_DAYS)
    s.add_argument("--dry-run", action="store_true")
    s = sp.add_parser("restore", help="백업 파일로 되돌린다(전체 또는 --tables)")
    s.add_argument("backup_path")
    s.add_argument("--tables", default="", help="쉼표로 표 이름 — 주면 그 표만")
    s = sp.add_parser("import", help="그 run의 시그널·승인·체결 파일을 집행 표로(멱등) — 시그널 초안 직후·전송 뒤·마감 때")
    s.add_argument("stamp")
    s.add_argument("market")
    a = ap.parse_args(argv)
    if a.cmd == "schema":
        init_schema()
        print(f"{db_path()} — 표 {len(tables())}개")
        return 0
    if a.cmd == "doctor":
        return 0 if doctor()["ok"] else 1
    if a.cmd == "show":
        if a.table not in tables():
            print(f"표 {a.table} 없음", file=sys.stderr)
            return 2
        rows = q(f"SELECT * FROM {a.table} {('WHERE ' + a.where) if a.where else ''} LIMIT ?", (a.limit,))
        for r in rows:
            print(json.dumps(r, ensure_ascii=False, default=str)[:600])
        print(f"({len(rows)}행)")
        return 0
    if a.cmd == "sql":
        if not re.match(r"^\s*(SELECT|WITH|PRAGMA\s+table_info)\b", a.query, re.I):
            print("읽기 전용 — SELECT만 받는다", file=sys.stderr)
            return 2
        for r in q(a.query):
            print(json.dumps(r, ensure_ascii=False, default=str)[:800])
        return 0
    if a.cmd == "export":
        for n in (a.names or list(EXPORTS)):
            print(export(n))
        return 0
    if a.cmd == "backup":
        print(backup(a.label))
        return 0
    if a.cmd == "migrate":
        try:
            r = migrate(dry_run=a.dry_run)
        except (MigrationError, SchemaNewer) as e:
            print(f"MIGRATE FAIL — {e}", file=sys.stderr)
            return 2
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        if r["from"] == r["to"] and not r["steps"]:
            print(f"MIGRATE 할 것 없음 — 저장소 판 {r['from']} = 코드 판 {SCHEMA_VERSION}")
            return 0
        ok = a.dry_run or r.get("doctor_ok")
        print(("MIGRATE DRY-RUN" if a.dry_run else "MIGRATE") + f" v{r['from']} → v{r['to']}"
              + ("" if a.dry_run else f" · 백업 {r['backup']} · doctor {'OK' if r.get('doctor_ok') else 'FAIL'}"))
        return 0 if ok else 1
    if a.cmd == "bars-finalize":
        from kis_client import KisClient
        print(json.dumps(bars_finalize(KisClient(), a.market), ensure_ascii=False))
        return 0
    if a.cmd == "audit-rescope":
        ch = audit_rescope(a.run_id)
        print(json.dumps({"changed": ch}, ensure_ascii=False))
        return 0
    if a.cmd == "raw-purge":
        print(json.dumps(raw_purge(a.keep_days, dry_run=a.dry_run), ensure_ascii=False))
        return 0
    if a.cmd == "restore":
        tabs = [t.strip() for t in a.tables.split(",") if t.strip()]
        r = restore(a.backup_path, tabs or None)
        print(json.dumps(r, ensure_ascii=False, default=str))
        return 0
    if a.cmd == "import":
        r = import_run_artifacts(a.stamp, a.market)
        miss = r.pop("missing_facts", [])
        print("[저장소] " + " · ".join(f"{k} {v}" for k, v in r.items())
              + (f" · 원장에 없는 근거 사실 {len(miss)}건: {', '.join(miss[:10])}" if miss else ""))
        return 1 if miss else 0
    return 2


if __name__ == "__main__":
    # `python3 db.py …`로 돌 때 다른 모듈(facts 등)의 `import db`가 이 파일을 두 번째 모듈로 다시 읽지 않게 한다 —
    #   두 벌이면 연결 캐시·판 검사 우회(_MIGRATING)·트랜잭션 깊이가 갈라져 migrate가 자기 연결을 못 본다(2026-10-19).
    sys.modules.setdefault("db", sys.modules[__name__])
    sys.exit(_cli())
