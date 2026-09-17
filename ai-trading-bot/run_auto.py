#!/usr/bin/env python3
"""
무인 실행 지휘자 — 사람 없이 하루 한 바퀴를 돌린다.

★ 이 파일에는 매매 판단이 없다. 판단은 `claude -p`(제안)와 `risk_guard.py`(집행 여부)가
하고, 여기는 순서·중복·실패를 관리한다. 그리고 **집행은 LLM 세션 밖에서 일어난다** —
`execute.py`는 이 스크립트가 부르지 LLM이 부르지 않는다. 무인이 되면 "LLM은 제안만"을
지켜볼 사람이 없으므로, 그 원칙을 문서가 아니라 프로세스 경계로 만든다.

순서(각 단계가 왜 그 자리에 있는지):
  0 락·KILL·자가정지·시장창·일일락  — LLM을 부르기 전에 걸러낸다(비용·사고 둘 다 절약)
  1 ingest                          — 재료·스냅샷
  2 claude -p                       — 분석노트 + 시그널 + 게이트. 여기서 LLM은 끝난다
  3 산출물 검증                      — 스키마·신선도·게이트 원장. 못 미더우면 안 넘긴다
  4 risk_guard → 5 execute          — 결정론. 여기가 유일한 주문 경로
  6 journal (finally)               — 실패해도 반드시. position_peaks가 여기서 갱신된다
  7 dashboard → 8 게시              — 현황 페이지
  9 run_log·알림·락 해제

사용:
    python3 run_auto.py --self-test              # 주문·LLM 없이 환경만 점검
    python3 run_auto.py --market kr --no-send    # 전 과정, 주문만 안 보냄
    python3 run_auto.py --market kr              # 실제 주문까지
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:                                   # 3.9 미만 방어
    ZoneInfo = None

HERE = Path(__file__).parent
CONFIG_DIR = HERE / "config"
JOURNAL_DIR = HERE / "journal"
SIGNALS_DIR = HERE / "signals"
DATA_DIR = HERE / "data"
ANALYSIS_DIR = HERE / "analysis"
LOGS_DIR = HERE / "logs"

KILL_PATH = CONFIG_DIR / "KILL"
LOCK_PATH = CONFIG_DIR / ".run_auto.lock"
RUN_LOG = JOURNAL_DIR / "run_log.jsonl"
DASHBOARD_META = CONFIG_DIR / ".dashboard.json"

KST = timezone(timedelta(hours=9))
CLAUDE = shutil.which("claude") or "claude"
PY = sys.executable

# 단계 명세는 스킬이 갖고 있다. 드라이버가 단계를 위임할 때 **명세 경로를 들려보낸다** —
# 이 경로가 죽으면 위임받은 세션이 규칙 없이 일한다.
SKILL_REF = (HERE.parent / ".claude" / "skills" / "trade-run" / "references")
# 게이트 엔진 — 저장소 루트의 .claude/skills/_stepgate/ (프로젝트 폴더의 형제).
STEPGATE_PY = (HERE.resolve().parent / ".claude" / "skills" / "_stepgate" / "stepgate.py")

LOCK_STALE_SEC = 2 * 3600
LLM_TIMEOUT_SEC = 1800
# 6단은 체결 확인·§11·게이트만 하므로 1~5단보다 훨씬 짧다.
EXECUTE_CP_TIMEOUT_SEC = 900
EXECUTE_CP_BUDGET_USD = "2"
PUBLISH_TIMEOUT_SEC = 300
SIGNAL_MAX_AGE_MIN = 60
SELF_STOP_AFTER = 3            # 연속 실패 이 횟수면 스스로 KILL을 만든다
MAX_ATTEMPTS_PER_DAY = 2

# 시장창 — 이 밖에서는 LLM을 부르지 않는다(모의투자는 정규장 밖 주문을 거부한다).
MARKET_WINDOW = {
    "KR": {"tz": "Asia/Seoul", "open": (9, 0), "close": (15, 20)},
    "US": {"tz": "America/New_York", "open": (9, 30), "close": (15, 40)},
}

# 브로커가 "장이 아니다"라고 답한 경우 — 우리 잘못이 아니므로 실패로 세지 않는다.
MARKET_CLOSED_CODES = ("40580000", "40100000")


class Outcome:
    OK = "ok"; NO_TRADE = "no_trade"
    SKIP_KILL = "skipped_kill"; SKIP_WEEKEND = "skipped_weekend"
    SKIP_CLOSED = "skipped_closed"; SKIP_RAN = "skipped_already_ran"
    MARKET_CLOSED = "market_closed"
    FAIL_INGEST = "failed_ingest"; FAIL_LLM = "failed_llm"
    FAIL_TIMEOUT = "failed_timeout"; FAIL_GATE = "failed_gate"
    FAIL_RISK = "failed_riskguard"; FAIL_EXEC = "failed_execute"
    FAIL_RECORD = "failed_record"
    HALTED = "halted_selfstop"; SELF_TEST = "self_test"


# 자가정지 카운터에 넣지 않는 것 — 환경 사유와 fail-closed의 정상 동작.
# ★ `FAIL_RECORD`는 여기 들어가지 않는다. 주문이 이미 나간 뒤 기록이 안 남은 것은
#   게이트가 제 일을 한 것이 아니라 **사고**다 — 이 프로젝트의 성공 지표가
#   ③기록의 완전성이므로, 미기록 집행은 사람이 볼 때까지 반복하면 안 된다.
NOT_AN_INCIDENT = {Outcome.MARKET_CLOSED, Outcome.FAIL_GATE}


# ---------------------------------------------------------------- 로그·유틸

def log(msg: str) -> None:
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def append_run_log(row: dict) -> None:
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_cmd(args: list, timeout: int, cwd: Path = HERE) -> tuple:
    """(returncode, stdout, stderr). 타임아웃은 예외가 아니라 코드 124로 돌려준다."""
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except OSError as ex:
        return 127, "", str(ex)


# ---------------------------------------------------------------- 시장·세션

def session_date(market: str, now_utc: datetime = None) -> str:
    """세션 날짜 = **시장 현지 날짜**. 미국장은 KST 자정을 넘기므로 KST 날짜를 쓰면
    한 run의 산출물이 두 날짜로 갈라진다."""
    now = now_utc or datetime.now(timezone.utc)
    tzname = MARKET_WINDOW[market]["tz"]
    if ZoneInfo:
        return now.astimezone(ZoneInfo(tzname)).strftime("%Y-%m-%d")
    return now.astimezone(KST).strftime("%Y-%m-%d")      # 폴백(국내만 정확)


def market_state(market: str, now_utc: datetime = None) -> tuple:
    """(상태, 설명). 상태는 'open' | 'weekend' | 'closed'."""
    w = MARKET_WINDOW[market]
    now = now_utc or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(w["tz"])) if ZoneInfo else now.astimezone(KST)
    if local.weekday() >= 5:
        return "weekend", f"{local:%Y-%m-%d %a} 현지 주말"
    o = local.replace(hour=w["open"][0], minute=w["open"][1], second=0, microsecond=0)
    c = local.replace(hour=w["close"][0], minute=w["close"][1], second=0, microsecond=0)
    if o <= local <= c:
        return "open", f"현지 {local:%H:%M} (장중)"
    return "closed", f"현지 {local:%H:%M} — 창 {w['open'][0]:02d}:{w['open'][1]:02d}~{w['close'][0]:02d}:{w['close'][1]:02d} 밖"


# ---------------------------------------------------------------- 락·중복 방지

def acquire_lock() -> bool:
    CONFIG_DIR.mkdir(exist_ok=True)
    if LOCK_PATH.exists():
        try:
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age < LOCK_STALE_SEC:
                log(f"다른 실행이 진행 중이다 ({age/60:.0f}분 전 시작) — 종료")
                return False
            log(f"스테일 락 회수 ({age/3600:.1f}시간 경과)")
            LOCK_PATH.unlink()
        except OSError:
            return False
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f'{{"pid":{os.getpid()},"ts":"{datetime.now(KST).isoformat()}"}}'.encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def day_status(market: str, sdate: str) -> dict:
    """오늘 이 시장에서 이미 뭘 했는지. 일일 락의 근거."""
    rows = [r for r in read_jsonl(RUN_LOG)
            if r.get("market") == market and r.get("session_date") == sdate]
    done = any(r.get("outcome") in (Outcome.OK, Outcome.NO_TRADE)
               or (r.get("orders") or {}).get("sent") for r in rows)
    return {"rows": rows, "attempts": len(rows), "done": done}


def consecutive_failures() -> int:
    n = 0
    for r in reversed(read_jsonl(RUN_LOG)):
        oc = r.get("outcome", "")
        if oc.startswith("skipped") or oc == Outcome.SELF_TEST:
            continue
        if oc.startswith("failed_") and oc not in NOT_AN_INCIDENT:
            n += 1
        else:
            break
    return n


# ---------------------------------------------------------------- 알림

def notify_failure(market: str, sdate: str, outcome: str, reason: str) -> None:
    """실패했을 때만 부른다. 성공한 날은 조용한 것이 자동화의 목적이다.
    네 경로 전부 best-effort — 알림이 실패해도 파이프라인 결과를 바꾸지 않는다."""
    title = f"🔴 자동매매 실패 {sdate} {market}"
    body = f"{outcome} — {reason[:160]}"

    try:                                  # ① 파일 마커 (다음 대화형 세션이 본다)
        CONFIG_DIR.mkdir(exist_ok=True)
        (CONFIG_DIR / f"FAIL_{sdate.replace('-', '')}_{market.lower()}").write_text(
            f"{datetime.now(KST).isoformat()}\n{outcome}\n{reason}\n", encoding="utf-8")
    except OSError:
        pass

    # ② Apple 미리 알림 — 폰으로 바로 뜨는 경로(daily-routine의 `할 일` 목록).
    script = ('tell application "Reminders"\n'
              ' set L to (first list whose name is "할 일")\n'
              f' make new reminder at end of L with properties '
              f'{{name:"{title}", body:"{body}"}}\n'
              'end tell')
    run_cmd(["/usr/bin/osascript", "-e", script], timeout=30)

    # ③ 맥 화면 알림
    run_cmd(["/usr/bin/osascript", "-e",
             f'display notification "{body}" with title "{title}"'], timeout=15)


# ---------------------------------------------------------------- 셀프테스트

def self_test() -> int:
    """launchd가 이 스크립트를 제대로 돌릴 수 있는지만 본다 — 주문·LLM 없음.
    launchd는 이 맥에서 처음 쓰는 기능이라, 실매매 job으로 문제를 발견하면 곤란하다."""
    checks = {}
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        checks[name] = {"ok": bool(cond), "detail": detail}
        ok = ok and bool(cond)
        print(f"  [{'OK  ' if cond else 'FAIL'}] {name:22} {detail}")

    print("run_auto 셀프테스트")
    v = sys.version_info
    chk("python", (v.major, v.minor) == (3, 9), f"{sys.executable} {v.major}.{v.minor}.{v.micro}")
    chk("cwd", Path.cwd() == HERE or True, str(Path.cwd()))
    chk("limits 읽기(TCC 관문)", (CONFIG_DIR / "limits.json").exists(),
        str(CONFIG_DIR / "limits.json"))
    chk(".env 존재", (HERE.parent / ".env").exists(), "(내용은 읽지 않음)")

    gate_dir = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP")
                    or Path.home()) / "claude_stepgate"
    try:
        gate_dir.mkdir(parents=True, exist_ok=True)
        probe = gate_dir / ".run_auto_probe"
        probe.write_text("x", encoding="utf-8"); probe.unlink()
        chk("stepgate 경로 쓰기", True, str(gate_dir))
    except OSError as ex:
        chk("stepgate 경로 쓰기", False, str(ex))

    rc, out, _ = run_cmd([CLAUDE, "--version"], timeout=60)
    chk("claude 실행", rc == 0, out.strip()[:40])

    for m in ("KR", "US"):
        state, why = market_state(m)
        chk(f"타임존 {m}", True, f"{state} · {why} · 세션 {session_date(m)}")

    if acquire_lock():
        release_lock(); chk("락 생성·해제", True, str(LOCK_PATH))
    else:
        chk("락 생성·해제", False, "락을 잡지 못했다")

    append_run_log({"ts": datetime.now(KST).isoformat(), "outcome": Outcome.SELF_TEST,
                    "checks": checks})
    print(f"\n{'전 항목 통과' if ok else '실패 항목 있음 — 위 FAIL을 먼저 해결할 것'}")
    return 0 if ok else 1


# ---------------------------------------------------------------- 단계들

def latest_note(market: str) -> Path:
    notes = sorted(ANALYSIS_DIR.glob(f"분석노트_*_{market.lower()}_v*.md"))
    return notes[-1] if notes else None


def compute_days(market: str) -> int:
    """뉴스 수집 창 = 직전 분석노트 이후. Axios Macro는 KST 저녁, Closer는 새벽에
    오므로 하루만 보면 놓친다 → 최소 2일."""
    note = latest_note(market)
    if not note:
        return 3
    import re
    m = re.search(r"분석노트_(\d{6})_", note.name)
    if not m:
        return 2
    try:
        d = datetime.strptime(m.group(1), "%y%m%d").replace(tzinfo=KST)
    except ValueError:
        return 2
    return max(2, min(7, (datetime.now(KST) - d).days + 1))


def build_prompt(market: str, stamp: str, days: int, sdate: str) -> str:
    """새 세션은 맥락이 0이다. 파일을 뒤지게 하지 말고 결정론으로 계산해 넣는다."""
    note = latest_note(market)
    prev = [r for r in read_jsonl(RUN_LOG) if r.get("market") == market]
    prev_txt = "없음"
    if prev:
        p = prev[-1]
        o = p.get("orders") or {}
        prev_txt = (f"{p.get('session_date')} {p.get('outcome')} "
                    f"(제안 {o.get('proposed', 0)} / 승인 {o.get('approved', 0)} / "
                    f"거부 {o.get('rejected', 0)})")
    return f"""/trade-run {market.lower()} --auto

무인 실행이다. 사용자가 없으니 다음을 지킨다.

1. **사용자 확인은 `dispatch` 게이트가 대신한다.** 사람에게 묻지 말고 게이트를 통과하라.
2. **네 범위는 1단~5단이다** — 수집·이어받기·지도·노트·시그널 + `risk_guard` + `execute` dry-run
   + `dispatch`(제안 ≥1건) 또는 `no_trade`(0건) 게이트까지. **`execute.py --send`만 하지 마라**
   — 실주문 전송은 호출자가 네 `dispatch` 토큰을 확인한 뒤 결정론으로 한다.
   전송 후 체결 대조·노트 §11·논지 `held` 이전·저널도 호출자가 파일에서 기계로 처리한다.
3. **★ '안 사는 것'은 기본값이 아니다.** 모호함을 기권 사유로 쓰지 마라 —
   방향 견해가 서면 **제안하고 불확실성은 크기로 흡수한다**(`references/stage5_decide.md` 크기 규칙).
   `no_trade`는 **유니버스 어느 종목에도 방향 견해가 서지 않을 때만** 쓴다.
   "위험이 있다·이벤트가 임박했다·상황을 더 봐야 한다"는 사유가 아니다.
   기권하면 `dispatch` 대신 `no_trade` 게이트를 받아야 하고, **면제는 없다.**
4. limits.json·watchlist.json·universe_rules.json은 **읽기만** 한다.
5. **이미 가진 자료부터 뒤진다** — `python3 corpus.py index`로 무엇을 갖고 있는지 보고,
   고리를 `조사불가`로 닫기 전에 `python3 corpus.py search <키워드> --out <로그>`를 반드시 돌려라.
   *실사례(2026-09-10): `_raw_sources/`에 답이 있는데 외부 검색만 하고 조사불가로 닫았다.*

재료:   data/material_{stamp}_{market.lower()}.md   (수집 창 --days {days})
스냅샷: data/snapshot_{stamp}_{market.lower()}.json
시그널을 쓸 경로: signals/signal_{stamp}_{market.lower()}.json

★ 첫 일: `python3 theses.py check --snapshot data/snapshot_{stamp}_{market.lower()}.json --market {market}`
발화한 트리거는 **이미 내린 결정**이다 — 다시 저울질하지 말고 그 비중 그대로 제안하라.
취소는 논지의 invalidation이 재료에서 실제로 확인될 때만 가능하다.
직전 분석노트: {note.name if note else '없음'}
직전 run: {prev_txt}
세션 날짜: {sdate} · KILL 없음 · 연속 실패 {consecutive_failures()}회
"""


# 모델이 1단~5단을 실제로 돌 수 있어야 한다. 예전 목록은 ingest만 허용해서 자기 프롬프트가
# "★ 첫 일"로 지정한 theses.py조차 못 돌았고, 그래서 무인 run이 재료만 남기고 죽었다
# (2026-09-08·09 미국 run이 실제로 그랬다). `execute.py --send`만 호출자 몫으로 남긴다.
ANALYSIS_TOOLS = " ".join([
    "Read", "Write", "Edit", "Glob", "Grep", "Skill", "WebFetch", "WebSearch",
    # 1단 수집·측정
    "Bash(python3 stage.py *)", "Bash(python3 ingest.py *)", "Bash(python3 market_map.py *)",
    # 2단 이어받기
    "Bash(python3 theses.py *)", "Bash(python3 review.py *)",
    # 3단 지도·리서치 — 누적 코퍼스를 뒤지는 경로
    "Bash(python3 corpus.py *)",
    # 4단 종목·노트·검증
    "Bash(python3 universe_review.py *)", "Bash(python3 base_rates.py *)",
    "Bash(python3 price_levels.py *)", "Bash(python3 verify_numbers.py *)",
    "Bash(python3 extract_ai_claims.py *)",
    "Bash(python3 ../gmail-newsletter-analyzer/cache_claims.py *)",
    # 5단 결정 — risk_guard와 dry-run은 브로커에 쓰지 않는다. --send는 목록에 없다.
    "Bash(python3 risk_guard.py *)",
    "Bash(python3 req_audit.py *)",
    # 게이트
    f"Bash(python3 {STEPGATE_PY} *)",
])

DISPATCH_CPS = ("dispatch", "no_trade")


def stepgate_dir() -> Path:
    """게이트 토큰이 실제로 놓이는 폴더 — **엔진에게 물어본다.**

    ★ 복제하지 않는 이유: 처음엔 `TMPDIR or TEMP or ~`로 베껴 썼는데 엔진은
    `TEMP or TMP or ~`만 본다. macOS는 `TMPDIR`만 설정하므로 엔진은 `~/claude_stepgate`에
    쓰고 이쪽은 `$TMPDIR/claude_stepgate`를 뒤졌다 — **토큰을 영원히 못 찾아 모든 무인
    run이 "토콘 없음"으로 막혔을 것이다.** fail-closed라 위험하진 않지만 전부 죽는다.
    경로 규칙을 두 곳에 적으면 이런 식으로 조용히 갈린다.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_sg_dir", STEPGATE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)                 # `__main__` 가드가 있어 CLI는 안 돈다
        return Path(mod.gate_dir())
    except Exception:                                # noqa: BLE001 — 엔진이 없어도 죽지 않는다
        base = os.environ.get("TEMP") or os.environ.get("TMP") or str(Path.home())
        return Path(base) / "claude_stepgate"


def gate_token(since: datetime, cps: tuple = DISPATCH_CPS) -> dict:
    """stepgate 원장에서 **이번 run의** trade-run 통과 토큰을 읽어 온다.

    예전 구현은 `glob("trade-run*")` + `"PASS" in text`였다 — **어느 단계의 PASS든**
    통과로 읽었으므로 `capture` PASS만 있어도 실주문이 나갈 수 있었다.
    이제 cp를 파싱해 `cps`에 든 체크포인트만 인정한다(기본 `dispatch`·`no_trade`).
    """
    gate_dir = stepgate_dir()
    best = {}
    for p in sorted(gate_dir.glob("trade-run*.ledger")):
        try:
            if datetime.fromtimestamp(p.stat().st_mtime, KST) < since:
                continue          # 이번 run 이전 토큰 — 재사용 금지
            tok = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(tok.get("verdict")) != "PASS":
            continue
        if str(tok.get("cp")) in cps:
            best = {"cp": str(tok.get("cp")), "verdict": "PASS",
                    "ledger": p.name, "ts": tok.get("ts")}
    return best


def consecutive_no_trade(market: str) -> int:
    """`run_log.jsonl`에서 이 시장의 연속 no_trade 횟수.

    `limits.no_trade_max_consecutive_below_floor`를 **읽는 코드가 없어서** 그 규칙이
    문서에만 있었다. 원장이 이미 `no_trade`를 기록하므로 여기서 센다.
    """
    rows = [r for r in read_jsonl(RUN_LOG) if r.get("market") == market]
    n = 0
    for r in reversed(rows):
        if r.get("no_trade"):
            n += 1
        else:
            break
    return n


EXECUTE_TOOLS = " ".join([
    "Read", "Write", "Edit", "Glob", "Grep",
    # 6단이 쓰는 것뿐 — `--send`는 이미 끝났고 목록에도 없다.
    "Bash(python3 execute.py *)", "Bash(python3 fill.py *)", "Bash(python3 journal.py *)",
    "Bash(python3 theses.py *)", "Bash(python3 review.py *)",
    f"Bash(python3 {STEPGATE_PY} *)",
])


def execute_checkpoint(market: str, stamp: str, approved: Path, since: datetime) -> dict:
    """6단 — **주문을 보낸 뒤** 체결을 확인하고 노트 §11을 닫는 짧은 세션.

    왜 별도 세션인가: 주문 경로는 결정론 파이썬이 쥐어야 하지만(`--send`는 드라이버 몫),
    **접수와 체결을 가르는 판정은 파일을 열어보는 쪽만 할 수 있다.** 예전 구조는
    보내고 나서 아무도 `execute` 게이트를 돌지 않았고, 그래서 **노트 §11이 영구 공백**이었다
    — 다음 run의 §1-A가 읽을 것이 '제안'뿐이어서 피드백 고리가 닫히지 않았다.
    *실사례(2026-09-10): MSFT 주문이 브로커 거부(`[40580000] 모의투자 장종료`)였는데
    보고는 '미체결'로 나갔다. 거부된 주문은 아무것도 남기지 않는다 — 다른 판정이다.*
    """
    note = latest_note(market)
    spec = (SKILL_REF / "stage6_execute.md")
    prompt = f"""6단(집행·마감)만 수행한다. 주문은 **이미 전송됐다** — 다시 보내지 마라.

먼저 `{spec}`를 읽고 그 명세대로 한다. 프로젝트폴더: {HERE}

1. `journal/trades_{stamp}_{market.lower()}.json`과 `{approved.name}`을 열어
   **주문별로 접수·체결·부분체결·거부를 가른다.** 접수를 체결로 쓰지 마라.
   브로커 거부(`status: FAILED`)는 **미체결이 아니다** — 거부된 주문은 아무것도 남기지 않는다.
2. **접수는 run의 끝이 아니다.** `execute.py --send`가 전송 뒤 `fill`로 주문을 체결·부분체결·
   취소·만료·거부 중 하나로 확정하고 `fill_confirmed`를 그 파일에 써뒀다. `open_orders`가 0이
   아니면 `python3 fill.py journal/trades_{stamp}_{market.lower()}.json --note <노트>`를
   **종료코드 0이 날 때까지 반복 호출**한다(호출당 최대 9분). **재전송은 절대 하지 마라** —
   정정·취소·조회만 한다. `thesis_unsynced`가 비어 있지 않으면 그 논지들을 `theses.py`로 맞춘다.
3. 매수 체결분(FILLED·PARTIAL의 체결 수량)에 `exit_triggers`를 걸고 `theses.py set-status
   --status held`로 옮긴다. **취소·만료·거부분은 `held`로 옮기지 마라** — 다음 run이 없는 보유의
   트리거를 보게 된다. 취소·만료 잔량의 행선지(논지 `armed` 유지 / 다음 run 대기열)를 §11에 적는다.
4. **노트 §11 집행 결과를 채우고 `<!-- ✓ §11-집행 -->`을 찍는다** — 대상 노트: {note or '없음'}
   제안한 것 / 승인된 것 / 실제로 체결된 것을 각각 적는다. 셋은 다른 숫자다.
5. `journal/worklog_{stamp}_{market.lower()}.md`에 6단 블록을 append하고
   `<!-- ✓ 작업기록-execute -->`를 찍는다. `python3 journal.py --daily --market {market.lower()}`도
   돈다(건너뛰면 `position_peaks.json`이 안 갱신돼 트레일링 스톱이 멈춘다).
6. 게이트를 돈다 — 이 단계를 수행한 네가 직접 답한다:
   `SG form trade-run execute dir={HERE}` → 실물을 보고 정직히 답 →
   `SG judge trade-run execute <id>=<값> … deliverable=<journal/trades_{stamp}_{market.lower()}.json 절대경로>`
   ` note=<노트 절대경로> today=<YYYY-MM-DD> dir={HERE}`
   (`SG` = `python3 {STEPGATE_PY}`)
   **GATE PASS 라인을 출력에 남겨라.** 게이트 루브릭은 열지 마라.

`config/`의 어떤 파일도 수정하지 마라. 새 주문을 만들지 마라.
"""
    rc, out, err = run_cmd(
        [CLAUDE, "-p", "--model", "opus", "--max-budget-usd", EXECUTE_CP_BUDGET_USD,
         "--permission-mode", "default", "--allowedTools", EXECUTE_TOOLS,
         "--settings", '{"hooks":{"Stop":[]}}', prompt],
        timeout=EXECUTE_CP_TIMEOUT_SEC)
    tok = gate_token(since, cps=("execute",))
    return {"gate_pass": bool(tok), "cp": tok.get("cp"),
            "ledger": tok.get("ledger"), "exit_code": rc,
            "why": None if tok else (err or out or "출력 없음")[-300:]}


def publish_dashboard() -> dict:
    """게시 전용 세션 — Read와 Artifact만 준다. 매매 파일을 만질 수 없다."""
    meta = {}
    if DASHBOARD_META.exists():
        try:
            meta = json.loads(DASHBOARD_META.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    url = meta.get("artifact_url")
    prompt = (
        "dashboard/index.html 파일을 Artifact로 올려라.\n"
        + (f"이미 만든 페이지가 있다: {url} — 그 URL로 **재배포**해라(새로 만들지 말 것).\n"
           if url else "처음이면 새로 만들어라.\n")
        + "규칙: 파일 내용을 고치지 마라. **공유 링크를 만들거나 공유를 제안하지 마라** — "
          "이 페이지는 비공개다. 마지막 줄에 `ARTIFACT_URL=<url>` 형식으로만 결과를 출력해라."
    )
    rc, out, err = run_cmd(
        [CLAUDE, "-p", "--model", "sonnet", "--max-budget-usd", "1",
         "--allowedTools", "Read Artifact", prompt],
        timeout=PUBLISH_TIMEOUT_SEC)
    new_url = None
    for line in (out or "").splitlines():
        if line.strip().startswith("ARTIFACT_URL="):
            new_url = line.split("=", 1)[1].strip()
    meta.update({
        "artifact_url": new_url or url,
        "published_at": datetime.now(KST).isoformat() if new_url else meta.get("published_at"),
        "last_publish_error": None if new_url else (err or out or "URL 미출력")[:300],
        "consecutive_publish_failures": 0 if new_url
        else meta.get("consecutive_publish_failures", 0) + 1,
    })
    DASHBOARD_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    return {"published": bool(new_url), "artifact_url": meta.get("artifact_url")}


# ---------------------------------------------------------------- 본 실행

def run(market: str, send: bool) -> int:
    started = datetime.now(KST)
    sdate = session_date(market)
    stamp = sdate[2:].replace("-", "")
    row = {"ts": started.isoformat(), "session_date": sdate, "market": market,
           "outcome": None, "reason": None, "pipeline": {}, "orders": {},
           "gate": {}, "journal": {}, "dashboard": {}, "no_trade": None}
    reason = ""

    def finish(outcome: str, why: str = "") -> int:
        row["outcome"] = outcome
        row["reason"] = why or None
        row["duration_s"] = round((datetime.now(KST) - started).total_seconds(), 1)
        row["consecutive_failures"] = (
            consecutive_failures() + 1
            if outcome.startswith("failed_") and outcome not in NOT_AN_INCIDENT else 0)
        append_run_log(row)
        # 대시보드는 결과와 무관하게 갱신한다 — 실패했다는 사실 자체가 봐야 할 정보다.
        try:
            run_cmd([PY, str(HERE / "dashboard.py")], timeout=120)
            row["dashboard"] = publish_dashboard()
        except Exception:                                        # noqa: BLE001
            pass
        if outcome.startswith("failed_") and outcome not in NOT_AN_INCIDENT:
            notify_failure(market, sdate, outcome, why)
            if row["consecutive_failures"] >= SELF_STOP_AFTER:
                KILL_PATH.write_text(
                    f"{datetime.now(KST).isoformat()}\n연속 실패 "
                    f"{row['consecutive_failures']}회로 자가정지. 원인을 확인한 뒤 "
                    f"이 파일을 지우면 재개된다.\n", encoding="utf-8")
                log(f"연속 실패 {row['consecutive_failures']}회 — KILL 생성(자가정지)")
        log(f"결과: {outcome}" + (f" — {why}" if why else ""))
        return 0 if not outcome.startswith("failed_") else 1

    # 0. 사전 판정
    if KILL_PATH.exists():
        return finish(Outcome.SKIP_KILL, "config/KILL 존재")
    if consecutive_failures() >= SELF_STOP_AFTER:
        return finish(Outcome.HALTED, "연속 실패 임계 도달 — 사람이 확인해야 한다")

    state, why = market_state(market)
    if state == "weekend":
        return finish(Outcome.SKIP_WEEKEND, why)
    if state == "closed":
        return finish(Outcome.SKIP_CLOSED, why)

    ds = day_status(market, sdate)
    if ds["done"]:
        return finish(Outcome.SKIP_RAN, f"오늘 이미 완료됨(시도 {ds['attempts']}회)")
    if ds["attempts"] >= MAX_ATTEMPTS_PER_DAY:
        return finish(Outcome.SKIP_RAN, f"오늘 시도 한도 {MAX_ATTEMPTS_PER_DAY}회 소진")
    row["attempt"] = ds["attempts"] + 1

    # 1. 재료
    days = compute_days(market)
    log(f"ingest --market {market} --days {days} --stamp {stamp}")
    rc, out, err = run_cmd(
        [PY, str(HERE / "ingest.py"), "--market", market.lower(),
         "--with-news", "--days", str(days), "--stamp", stamp], timeout=600)
    if rc != 0:
        return finish(Outcome.FAIL_INGEST, (err or out)[-300:])
    row["pipeline"]["material"] = f"data/material_{stamp}_{market.lower()}.md"
    row["pipeline"]["snapshot"] = f"data/snapshot_{stamp}_{market.lower()}.json"

    # 2. 분석 (LLM은 여기서 끝난다)
    log("claude -p (분석·시그널·게이트)")
    llm_started = datetime.now(KST)
    rc, out, err = run_cmd(
        [CLAUDE, "-p", "--model", "opus", "--output-format", "json",
         "--max-budget-usd", "5", "--permission-mode", "default",
         "--allowedTools", ANALYSIS_TOOLS,
         "--add-dir", str(HERE.parent / "gmail-newsletter-analyzer"),
         "--settings", '{"hooks":{"Stop":[]}}',
         build_prompt(market, stamp, days, sdate)],
        timeout=LLM_TIMEOUT_SEC)
    if rc == 124:
        return finish(Outcome.FAIL_TIMEOUT, f"{LLM_TIMEOUT_SEC}초 초과")
    try:
        meta = json.loads(out) if out.strip().startswith("{") else {}
        row["claude"] = {"exit_code": rc, "cost_usd": meta.get("total_cost_usd"),
                         "num_turns": meta.get("num_turns"), "timed_out": False}
    except json.JSONDecodeError:
        row["claude"] = {"exit_code": rc, "timed_out": False}
    if rc != 0:
        return finish(Outcome.FAIL_LLM, (err or out)[-300:])

    # 3. 산출물 검증 — 못 미더우면 집행으로 넘기지 않는다
    sig_path = SIGNALS_DIR / f"signal_{stamp}_{market.lower()}.json"
    if not sig_path.exists():
        return finish(Outcome.FAIL_GATE, f"시그널 파일이 없다: {sig_path.name}")
    try:
        sig = json.loads(sig_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        return finish(Outcome.FAIL_GATE, f"시그널 JSON 파싱 실패: {ex}")
    if sig.get("schema_version") != "1.0":
        return finish(Outcome.FAIL_GATE, f"schema_version={sig.get('schema_version')!r}")
    try:
        gen = datetime.fromisoformat(str(sig["generated_at"]))
        gen = gen if gen.tzinfo else gen.replace(tzinfo=KST)
        age = (datetime.now(KST) - gen).total_seconds() / 60
    except (KeyError, ValueError) as ex:
        return finish(Outcome.FAIL_GATE, f"generated_at 오류: {ex}")
    if age > SIGNAL_MAX_AGE_MIN:
        return finish(Outcome.FAIL_GATE, f"시그널이 낡았다 ({age:.0f}분)")

    proposals = sig.get("proposals") or []
    row["no_trade"] = bool(sig.get("no_trade")) or not proposals
    row["orders"]["proposed"] = len(proposals)
    row["pipeline"]["signal"] = str(sig_path.relative_to(HERE))

    # ★ 게이트는 제안 유무와 무관하게 **항상** 요구한다. 예전에는 `if proposals and …`라서
    #   기권 run이 게이트를 통째로 우회하고 verdict를 "N/A"로 기록했다 — 이 시스템의 실패
    #   양상은 손실이 아니라 기권인데, 정작 기권 경로가 무검문이었다.
    tok = gate_token(llm_started)
    need = "dispatch" if proposals else "no_trade"
    if not tok:
        return finish(Outcome.FAIL_GATE,
                      f"이번 run의 `{need}` GATE PASS 토큰이 없다 "
                      f"(제안 {len(proposals)}건 — 기권도 검문 대상이다)")
    if tok["cp"] != need:
        return finish(Outcome.FAIL_GATE,
                      f"토큰 cp가 `{tok['cp']}`인데 `{need}`가 필요하다 — "
                      f"단계를 건너뛰었거나 다른 경로를 통과했다")
    row["gate"] = {"required": True, "verdict": "PASS", "cp": tok["cp"],
                   "ledger": tok.get("ledger")}

    # ★ 하한 미달 상태에서 기권이 연속되면 설명으로 끝낼 수 없다 —
    #   `limits.no_trade_max_consecutive_below_floor`를 실제로 읽는 자리가 여기다.
    if row["no_trade"]:
        cap = (_read_json(LIMITS_PATH, {}) or {}).get("no_trade_max_consecutive_below_floor")
        streak = consecutive_no_trade(market) + 1
        row["no_trade_streak"] = streak
        if cap is not None and streak > int(cap):
            return finish(Outcome.FAIL_GATE,
                          f"연속 no_trade {streak}회 > 한도 {cap}회 — "
                          f"기권을 사유로 닫을 수 없다. 방향 견해가 서면 최소 크기로라도 제안하라")

    # 4~5. 결정론 집행 (LLM 세션 밖)
    approved_path = SIGNALS_DIR / f"approved_{stamp}_{market.lower()}.json"
    snap_path = DATA_DIR / f"snapshot_{stamp}_{market.lower()}.json"
    log("risk_guard")
    rc, out, err = run_cmd(
        [PY, str(HERE / "risk_guard.py"), str(sig_path), "--snapshot", str(snap_path)],
        timeout=180)
    if rc not in (0, 2):
        return finish(Outcome.FAIL_RISK, (err or out)[-300:])
    if rc == 2:                                # fail-closed가 정상 동작한 것
        return finish(Outcome.NO_TRADE, f"risk_guard 전체 거부: {(err or '')[-200:]}")

    approved = json.loads(approved_path.read_text(encoding="utf-8")) if approved_path.exists() else {}
    orders = approved.get("orders", [])
    row["orders"].update({"approved": len(orders),
                          "rejected": len(approved.get("rejected", []))})
    row["pipeline"]["approved"] = approved_path.name
    # ★ 미달 강제 — risk_guard가 approved에 찍은 `deficit_short`를 여기서도 읽는다(세션 게이트와
    #   같은 판정). 목표 아래인데 제안 건수·축·e0가 모자란 run은 주문을 내지 않고 실패로 닫는다.
    if approved.get("deficit_short"):
        return finish(Outcome.FAIL_GATE,
                      f"미달 강제 — {(approved.get('deficit') or {}).get('why', 'deficit_short')}")

    sent = failed = 0
    if orders and send:
        # ★ `--send`는 **`dispatch` 토큰에서만** 열린다. 위 블록이 이미 cp를 대조했지만
        #   여기서 다시 못 박는다 — 주문 경로를 추론 사슬("제안이 있으니 need는 dispatch일
        #   것"）에 기대게 두면 안 되는 자리다. 예전 구현은 `capture` PASS만 있어도 보냈다.
        if tok.get("cp") != "dispatch":
            return finish(Outcome.FAIL_GATE,
                          f"`--send`는 `dispatch` 토큰에서만 열린다 (현재 `{tok.get('cp')}`)")
        log(f"execute --send ({len(orders)}건)")
        # ★ `--send`는 전송 뒤 `fill`로 체결·취소·만료까지 확정한다(최대 fill_wait_minutes).
        #   접수 상태로 끝내지 않는다 — 여기서 미확정이 남으면 6단 세션이 `fill.py`를 이어 돌린다.
        rc, out, err = run_cmd(
            [PY, str(HERE / "execute.py"), str(approved_path), "--send",
             "--stamp", stamp, "--max-sec", "1500"], timeout=1700)
        blob = f"{out}\n{err}"
        sent = blob.count("  OK ")
        failed = blob.count("  FAIL ") + blob.count("  UNKNOWN ")
        row["orders"].update({"sent": sent, "failed": failed})
        row["pipeline"]["trades"] = f"journal/trades_{stamp}_{market.lower()}.json"
        if failed and any(c in blob for c in MARKET_CLOSED_CODES):
            reason = "브로커가 장종료·휴장으로 거부"
            return finish(Outcome.MARKET_CLOSED, reason)
        if rc not in (0, 1) or (failed and not sent):
            return finish(Outcome.FAIL_EXEC, blob[-300:])

        # 6단 — 보낸 뒤에 체결을 확인하고 노트 §11을 닫는다. 여기가 무주인이어서
        # §11이 영구 공백이었고, 다음 run의 §1-A가 '제안'만 보고 피드백했다.
        log("execute 체크포인트 (6단 — 체결 확인·§11·게이트)")
        cp = execute_checkpoint(market, stamp, approved_path, llm_started)
        row["execute_cp"] = cp
        if not cp["gate_pass"]:
            return finish(Outcome.FAIL_RECORD,
                          "주문은 나갔으나 6단 `execute` GATE PASS 토큰이 없다 — "
                          f"체결 기록이 미완이다: {cp.get('why') or ''}")
    elif orders:
        log(f"execute dry-run ({len(orders)}건 — --no-send)")
        run_cmd([PY, str(HERE / "execute.py"), str(approved_path)], timeout=120)
        row["orders"]["sent"] = 0

    return finish(Outcome.NO_TRADE if row["no_trade"] and not sent else Outcome.OK, reason)


def main() -> int:
    ap = argparse.ArgumentParser(description="무인 실행 지휘자")
    ap.add_argument("--market", choices=["kr", "us"], help="시장")
    ap.add_argument("--no-send", action="store_true",
                    help="주문만 보내지 않는다(그 외 전 과정 수행)")
    ap.add_argument("--self-test", action="store_true",
                    help="주문·LLM 없이 실행 환경만 점검한다")
    args = ap.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)
    if args.self_test:
        return self_test()
    if not args.market:
        ap.error("--market kr|us 가 필요하다 (또는 --self-test)")

    if not acquire_lock():
        return 0
    try:
        # journal은 무슨 일이 있어도 돈다 — position_peaks가 여기서 갱신된다.
        try:
            return run(args.market.upper(), send=not args.no_send)
        finally:
            m = args.market.lower()
            stamp = session_date(args.market.upper())[2:].replace("-", "")
            log("journal --daily (보장 실행)")
            run_cmd([PY, str(HERE / "journal.py"), "--daily", "--market", m,
                     "--stamp", stamp], timeout=180)
            if datetime.now(KST).weekday() == 4:
                run_cmd([PY, str(HERE / "journal.py"), "--weekly"], timeout=180)

            # 유니버스 갱신 — 편입·제외를 **결정론으로** 반영한다.
            # 무인에서 사람 승인을 기다리면 워치리스트가 굳고, 재료가 다루지 않는 종목만
            # 남아 판단 자체가 불가능해진다. 그렇다고 LLM이 파일을 쓰게 하면 뉴스레터발
            # 인젝션이 곧장 주문 대상이 된다 → universe_apply가 브로커가 주는 사실
            # (지수 편입·시가총액·거래대금·관리종목 여부)로만 판정한다. LLM 호출 없음.
            #
            # journal 다음에 두는 이유: 이번 run의 주문은 이미 끝났고, 유니버스 변경은
            # **다음 run부터** 적용되는 것이 맞다. 같은 run 안에서 유니버스를 넓히고
            # 그걸로 바로 주문하면 risk_guard가 검사한 유니버스와 달라진다.
            sig_for_universe = SIGNALS_DIR / f"signal_{stamp}_{m}.json"
            if sig_for_universe.exists():
                log("universe_apply (편입·제외 자동 반영)")
                run_cmd([PY, str(HERE / "universe_apply.py"),
                         "--signal", str(sig_for_universe), "--apply"], timeout=300)
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
