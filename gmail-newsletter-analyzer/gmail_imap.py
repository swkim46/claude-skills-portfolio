#!/usr/bin/env python3
"""
Gmail transport over IMAP + app password.

Replaces the OAuth/Gmail-API transport in gmail_mcp.py, which was abandoned because
the Cloud Console blocked the Testing -> Production switch (Testing-status refresh
tokens die after 7 days, so no unattended digest was possible).

No third-party dependencies: imaplib and email are both stdlib.

Read-only by discipline: this module issues SEARCH and FETCH with BODY.PEEK only.
It never sends STORE, EXPUNGE, or DELETE. Note that the app password itself does
grant those abilities — the restraint is here in the code, not in the credential.
"""
import base64
import imaplib
import email
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

HERE = Path(__file__).parent
# Project-local .env wins; the shared life/.env is the fallback.
ENV_CANDIDATES = [HERE / ".env", HERE.parent / ".env"]

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------- credentials

def load_env() -> dict:
    """Read GMAIL_ADDRESS / GMAIL_APP_PASSWORD from .env (no dependency on dotenv).

    Merges every candidate file, nearest-first: keys already found are not
    overwritten, so a project-local .env can override the shared life/.env.
    """
    found = [p for p in ENV_CANDIDATES if p.exists()]
    if not found:
        raise FileNotFoundError(
            "No .env found. Looked in:\n  " + "\n  ".join(str(p) for p in ENV_CANDIDATES) +
            "\nCreate one with:\n"
            "  GMAIL_ADDRESS=you@gmail.com\n"
            "  GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx"
        )

    env = {}
    for path in found:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip("'\""))

    missing = [k for k in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD") if not env.get(k)]
    if missing:
        raise KeyError(
            f"{', '.join(missing)} not set in: " + ", ".join(str(p) for p in found)
        )
    return env


# ------------------------------------------------------------------ connection

@contextmanager
def connect(mailbox: str = None):
    """Connect, authenticate, and select a mailbox. Always logs out cleanly.

    mailbox defaults to the All Mail folder, discovered by its \\All special-use
    flag rather than by name — Gmail localises folder names, so a Korean account
    exposes '[Gmail]/전체보관함' instead of '[Gmail]/All Mail'.
    """
    env = load_env()
    # Google displays app passwords in four groups of four; spaces are not part of it.
    password = env["GMAIL_APP_PASSWORD"].replace(" ", "")

    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        imap.login(env["GMAIL_ADDRESS"], password)
        target = mailbox or find_all_mail(imap)
        typ, _ = imap.select(f'"{target}"', readonly=True)   # readonly: no \Seen side effects
        if typ != "OK":
            raise RuntimeError(f"Could not select mailbox {target!r}")
        yield imap
    finally:
        try:
            imap.close()
        except Exception:
            pass
        try:
            imap.logout()
        except Exception:
            pass


def _find_folder_by_flag(imap, flag: str):
    """Return the folder name carrying a special-use flag (\\All, \\Junk, ...), or None.

    Names are matched by flag, never by string: Gmail localises them and IMAP
    encodes non-ASCII names in modified UTF-7, so this account's spam folder
    is literally '[Gmail]/&wqTTONVo-'. Hardcoding a name would work on one
    account and silently return nothing on the next.
    """
    typ, folders = imap.list()
    if typ == "OK":
        for raw in folders:
            line = raw.decode("utf-8", errors="replace")
            if flag in line:
                # Format: (\All \HasNoChildren) "/" "[Gmail]/All Mail"
                match = re.search(r'"([^"]*)"\s*$', line)
                if match:
                    return match.group(1)
    return None


def find_all_mail(imap) -> str:
    """Locate the All Mail folder via its \\All flag, falling back to INBOX."""
    return _find_folder_by_flag(imap, r"\All") or "INBOX"


def find_spam(imap):
    """Locate the spam folder via its \\Junk flag, or None if the account has none.

    All Mail does NOT include spam, so a digest that only reads All Mail never
    sees a newsletter Gmail has decided is junk. 2026-09-16: Axios Macro (9/14·9/15),
    Closer (9/11·9/14·9/15) and Markets (9/10) sat in spam while the roster listed
    them — the pipeline reported nothing because the search simply had no hits.
    Reading spam is still read-only (SEARCH + BODY.PEEK); nothing is un-spammed here.
    """
    return _find_folder_by_flag(imap, r"\Junk")


# ----------------------------------------------------------------- query build

def today_kst() -> str:
    return datetime.now(KST).strftime("%Y/%m/%d")


def build_query(sender: str = None, date: str = None, extra: str = "") -> str:
    """Build a Gmail search query — the same syntax the web UI uses.

    Carried over from gmail_mcp.py: Gmail's after:/before: are date-only, so the
    window is widened by a day on each side to guarantee a KST day is fully
    covered. filter_to_kst_day() then trims the result to the exact day.
    """
    parts = []
    if sender:
        parts.append(f"from:{sender}")
    d = datetime.strptime(date or today_kst(), "%Y/%m/%d")
    parts.append(f"after:{(d - timedelta(days=1)).strftime('%Y/%m/%d')}")
    parts.append(f"before:{(d + timedelta(days=1)).strftime('%Y/%m/%d')}")
    if extra:
        parts.append(extra)
    return " ".join(parts)


# --------------------------------------------------------------------- search

def search(imap, query: str, max_results: int = 50) -> list:
    """Run a Gmail-syntax query via X-GM-RAW and return UIDs, newest first."""
    # imaplib mangles non-ASCII arguments; sending the query as a literal with an
    # explicit charset is the reliable way to support Korean search terms.
    if query.isascii():
        typ, data = imap.uid("SEARCH", None, "X-GM-RAW", f'"{query}"')
    else:
        imap.literal = query.encode("utf-8")
        typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")

    if typ != "OK":
        raise RuntimeError(f"SEARCH failed: {data}")
    uids = data[0].split() if data and data[0] else []
    return [u.decode() for u in reversed(uids)][:max_results]


def _header(value) -> str:
    """Decode a MIME-encoded header (Korean senders and subjects need this)."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def fetch_metadata(imap, uids: list) -> list:
    """Fetch Subject/From/Date for the given UIDs in one round trip."""
    if not uids:
        return []
    typ, data = imap.uid(
        "FETCH", ",".join(uids),
        "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])",   # PEEK: never marks as read
    )
    if typ != "OK":
        raise RuntimeError(f"FETCH failed: {data}")

    out = []
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        uid_match = re.search(rb"UID (\d+)", item[0])
        msg = email.message_from_bytes(item[1])
        out.append({
            "uid": uid_match.group(1).decode() if uid_match else "",
            "subject": _header(msg.get("Subject")),
            "from": _header(msg.get("From")),
            "date": msg.get("Date", ""),
        })
    return out


def filter_to_kst_day(messages: list, date: str = None) -> list:
    """Trim results to a single KST calendar day, using each message's Date header."""
    target = datetime.strptime(date or today_kst(), "%Y/%m/%d").date()
    kept = []
    for m in messages:
        try:
            dt = parsedate_to_datetime(m["date"])
        except (TypeError, ValueError):
            continue
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(KST).date() == target:
            kept.append(m)
    return kept


def message_kst(m: dict):
    """Message Date header as a KST-aware datetime, or None if unparseable."""
    try:
        dt = parsedate_to_datetime(m["date"])
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def filter_since(messages: list, since) -> list:
    """Keep messages whose Date header is strictly after `since` (KST-aware datetime).

    The day-based window (`filter_to_kst_day`) loses whatever arrives after a note's
    collection stamp on the same day — 2026-09-15 collected at 12:37 and the Axios AM
    that landed that evening had to be copied in by hand the next day. A stamp-based
    window has no such seam: the next collection starts exactly where the last ended.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=KST)
    kept = []
    for m in messages:
        dt = message_kst(m)
        if dt is not None and dt > since:
            kept.append(m)
    return kept


# ----------------------------------------------------------------------- body

def extract_plain_text(msg) -> str:
    """Prefer text/plain; fall back to stripped HTML. Ported from gmail_mcp.py."""
    body = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and \
               "attachment" not in str(part.get("Content-Disposition", "")):
                body = part.get_payload(decode=True)
                break
        if body is None:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    body = part.get_payload(decode=True)
                    break
    else:
        body = msg.get_payload(decode=True)

    if body is None:
        return ""
    text = body.decode(msg.get_content_charset() or "utf-8", errors="replace")

    if "<" in text and ">" in text:   # crude, but only reached for HTML-only mail
        text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;?", " ", text)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


# 링크 앵커가 이 말들로 끝나면 본문 키워드가 아니라 뉴스레터 살림살이다.
_NAV_ANCHOR = ("구독", "수신", "문의", "바로가기", "더보기", "피드백", "공유", "신청",
               "보기", "클릭", "다운로드", "이전 레터", "추천하기", "전문읽기",
               # 영어 뉴스레터(Axios 등) — 없으면 링크 섹션이 Unsubscribe·SNS로 찬다.
               "unsubscribe", "update preferences", "manage preferences", "contact us",
               "view in browser", "sign up", "subscribe", "privacy", "advertise",
               "facebook", "instagram", "linkedin", "twitter", "was this email")


def unwrap_tracking(url: str) -> str:
    """클릭 추적 래퍼에서 원본 URL을 복원한다 — 접속하지 않고.

    두 발행 플랫폼이 같은 구조를 쓴다:
      Stibee `event.stibee.com/v2/click/<id>/<base64>`
      Axios  `link.axios.com/click/<id>/<base64>/<sig>`
    가운데 base64 조각을 디코드하면 목적지가 나온다. 리다이렉트를 따라가면 발행사에
    클릭이 집계되고 왕복 시간도 드는데, 그럴 이유가 없다.

    ★ 이 함수가 실패하면 fact-check가 T1 원문에 도달하지 못하고 추적 URL만 인용하게 된다.
    """
    m = re.search(r"/(?:v2/)?click/[^/]+/([A-Za-z0-9_\-]+)", url)
    if not m:
        return url
    s = m.group(1)
    s += "=" * (-len(s) % 4)
    try:
        out = base64.urlsafe_b64decode(s).decode("utf-8", "replace")
        return out if out.startswith("http") else url
    except Exception:
        return url


# 링크가 걸린 도메인 중 내용과 무관한 것. 행사 신청 페이지가 한 발행분에 52건씩 붙는다.
_SKIP_DOMAIN = ("event-us.kr", "stibee.com", "stib.ee")

# 블록 경계 — 앵커 앞 텍스트를 어디까지 거슬러 올릴지 정한다.
_BLOCK = re.compile(r"</?(?:td|tr|p|li|div|span|br|h[1-6])\b[^>]*>", re.I)


def _preceding_text(html: str, pos: int, window: int = 500) -> str:
    """앵커 바로 앞의 같은 블록 텍스트. 헤드라인 끝 단어에만 링크가 걸릴 때 쓴다.

    STARTUP WEEKLY는 `<span>관악아날로그, 3000억원 밸류로 투자 <a>유치</a></span>` 꼴이라
    앵커만 뽑으면 `[유치]`가 되어 무슨 회사인지 알 수 없다. 앞 텍스트를 붙여야 쓸모가 생긴다.
    """
    seg = html[max(0, pos - window):pos]
    tail = _BLOCK.split(seg)[-1]
    tail = re.sub(r"<[^>]+>", "", tail)
    return re.sub(r"\s+", " ", unescape(tail)).strip()


def extract_links(msg, max_anchor: int = 40) -> list:
    """본문 키워드에 걸린 링크만 [(앵커, URL)]로. 네비게이션·구독 링크는 뺀다.

    UPPITY는 용어와 사건에 링크를 건다. 실측(2026-09-01 발행분) 44건 중 네이버 뉴스 원 기사 20건,
    어피티 자체 용어 해설 17건, 정부·FRED 각 1건이었다. 용어의 '왜 그런 규칙인가'와 T1 출처가
    여기 들어 있어서, 태그를 벗기며 통째로 버리면 노트의 깊이를 스스로 깎는 셈이다.
    """
    html = None
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            raw = part.get_payload(decode=True)
            if raw:
                html = raw.decode(part.get_content_charset() or "utf-8", "replace")
                break
    if not html:
        return []

    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.S):
        url, inner = m.group(1), m.group(2)
        anchor = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", inner))).strip()
        if not anchor or len(anchor) > max_anchor:
            continue
        # 영어 앵커는 대문자로 오므로(Unsubscribe·Contact Us) 소문자로 낮춰 비교한다.
        anchor_lc = anchor.lower()
        if any(k in anchor_lc for k in _NAV_ANCHOR) or anchor.strip() in ("X", "𝕏"):
            continue
        real = unwrap_tracking(url)
        if any(d in real for d in _SKIP_DOMAIN):
            continue
        # 앵커가 짧으면(끝 단어에만 링크) 앞 문맥을 붙여 무엇에 대한 링크인지 알아보게 한다
        label = anchor
        if len(anchor) <= 6:
            lead = _preceding_text(html, m.start())
            if lead:
                label = (lead + " " + anchor)[-70:].strip()
        key = (label, real)
        if key in seen:
            continue
        seen.add(key)
        out.append({"anchor": label, "url": real})
    return out


def fetch_body(imap, uid: str, cap: int = None) -> dict:
    """Fetch one message in full and return its decoded plain text.

    Uncapped by default. The old Gmail-API transport capped at 8,000 chars to keep
    MCP responses small, but that silently truncated real newsletters — UPPITY runs
    ~11,000 chars and lost 27% of its content. Pass `cap` only for preview use.
    """
    typ, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
    if typ != "OK" or not data or not isinstance(data[0], tuple):
        raise RuntimeError(f"FETCH body failed for uid {uid}: {data}")

    msg = email.message_from_bytes(data[0][1])
    return {
        "uid": uid,
        "subject": _header(msg.get("Subject")),
        "from": _header(msg.get("From")),
        "date": msg.get("Date", ""),
        "body": extract_plain_text(msg)[:cap] if cap else extract_plain_text(msg),
        # 본문과 분리해 담는다 — 인라인으로 섞으면 기존 코퍼스·문체 측정이 흔들린다
        "links": extract_links(msg),
    }


# ------------------------------------------------------------- high-level API

def fetch(sender: str = None, date: str = None, query: str = None,
          max_results: int = 50, exact_day: bool = True) -> list:
    """Search and return message metadata. The analysis layer's entry point.

    Provide (sender, date) for an auto-built query, or a raw Gmail query string.
    """
    q = query or build_query(sender=sender, date=date)
    with connect() as imap:
        messages = fetch_metadata(imap, search(imap, q, max_results))
    if exact_day and not query:
        messages = filter_to_kst_day(messages, date)
    return messages


def check() -> dict:
    """Verify credentials and connectivity. Run this first after creating .env."""
    with connect() as imap:
        folder = find_all_mail(imap)
        recent = fetch_metadata(imap, search(imap, "newer_than:2d", max_results=3))
    return {"ok": True, "mailbox": folder, "sample_count": len(recent), "sample": recent}


if __name__ == "__main__":
    import json
    try:
        print(json.dumps(check(), ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
