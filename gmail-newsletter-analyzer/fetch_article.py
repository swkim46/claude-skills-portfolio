#!/usr/bin/env python3
"""
기사 본문을 터미널로 가져온다 — `WebFetch`가 거부하는 도메인용.

    python3 fetch_article.py <url> [<url> ...]          # 네이버 뉴스·일반 HTML
    python3 fetch_article.py --wayback 2025 <url> ...   # Cloudflare 봇차단(IEA·Axios 등) → Wayback 스냅샷
    python3 fetch_article.py --max 8000 <url>           # 본문 출력 상한(기본 6000자)
    python3 fetch_article.py <pdf-url>                  # PDF도 그대로 — 자동 감지해 pypdf로 본문 추출
    python3 fetch_article.py <youtube-url>              # 영상은 재생 없이 제목·채널·설명·게시일만
    python3 fetch_article.py --tag pre <url> ...        # 누가 열었나(writer 기본 · pre 사전 감사 · aud 관측 감사 · fc 검증 조달)
    python3 fetch_article.py --mark-opened <url> ... --how browser   # 내장 브라우저·WebFetch로 연 것을 로그에 적기
    python3 fetch_article.py --selftest                 # 오프라인 자체 시험

연 링크는 `_raw_sources/fetch_log_<YYMMDD>.jsonl`에 한 줄씩 남는다(2026-10-14 · `--no-log`로 끔). `check_note.py`가
이 로그로 '노트와 겹치는데 안 연 링크'와 '연 적 없는 매체를 출처로 단 줄'을 후보로 낸다 — 감사가 5 run 연속 잡은
'링크 내용 미반영'과 3 run 반복된 '출처 귀속 오류'를 쓰는 단계에서 막으려는 것이다.

왜 있나 (2026-09-16 실측):
- `WebFetch`는 n.news.naver.com·wsj.com·nytimes.com·newyorker.com을 **도메인째** 거부한다.
  네이버는 urllib + 브라우저 UA로 그냥 열린다(`#dic_area`가 본문). 세상공부 노트가 여는 링크의
  대부분이 네이버 기사라 이 한 줄이 회차마다 필요했다.
- iea.org·axios.com은 Cloudflare 챌린지(403 `cf-mitigated: challenge`) — 브라우저 UA·Googlebot UA로도
  안 열린다. `https://web.archive.org/web/<YYYY>id_/<원URL>`(id_ = 원본 그대로)은 열리는데,
  `archive.org/wayback/available` API는 429가 잦으니 쓰지 않고 스냅샷 URL을 직접 친다.
- 응답이 gzip이면 압축을 풀어야 grep이 된다 — Wayback이 그렇다(`--compressed` 빠뜨려 한 번 헛돌았다).
- 403·404·401은 그대로 ERROR로 보인다. **추측하지 말 것** — 못 열었으면 노트 헤더 '열지 못한 것'에 적는다.
- **PDF는 절대 브라우저로 넘기지 말 것.** 내장 브라우저로 PDF URL을 navigate하면 렌더가 아니라
  *다운로드 대화상자*가 떠서 사용자 화면을 가로챈다(260918·260923·260924 세 회차에서 반복 발생).
  여기서 content-type과 `%PDF-` 매직바이트로 PDF를 감지해 pypdf로 텍스트를 뽑으므로 첫 수단에서 끝난다.
  텍스트가 안 나오면 스캔 이미지 PDF다 — 그때만 '못 연 것'이고, 역시 브라우저로 재시도하지 않는다.
- **영상·오디오 페이지도 브라우저로 넘기지 말 것.** 내장 브라우저는 영상을 소리까지 켠 채 자동 재생한다 —
  2026-10-06 run이 Axios 뉴스레터의 `Watch` 링크(YouTube의 The Axios Show·60 Minutes)와 axios.com 영상 페이지를
  브라우저로 열어 사용자 컴퓨터에서 인터뷰 음성이 흘러나왔다. YouTube는 공개 oEmbed와 페이지 HTML(스크립트로
  받으면 재생되지 않는다)에서 제목·채널·설명·게시일을 뽑고, 그 밖의 영상 페이지는 `--wayback` 사본이나
  뉴스레터 본문으로 대신한다.
"""
import argparse
import gzip
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "_raw_sources")
LAST_FINAL = None          # 마지막 _get의 최종 URL(리다이렉트 뒤) — 로그에 함께 남긴다
# 같은 기사인데 주소만 다른 경우를 한 주소로 — 추적 파라미터를 지우고 네이버 모바일 경로를 맞춘다.
DROP_PARAMS = re.compile(r"^(utm_\w+|stream|axios_adlink|fbclid|si|nd|dlsi|lctg|sid)$", re.I)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# 브라우저를 흉내 낸 UA를 오히려 막고, **용도를 밝힌 UA**를 요구하는 기관 — 도메인 끝부분으로 고른다.
# 실사례(2026-09-28): sec.gov PDF가 위 UA로는 403이었고 아래 UA로는 200. SEC 공정접근 정책이 스크립트에
# 자기 신원을 밝히라고 요구하기 때문이다. 이런 곳이 또 나오면 여기에 도메인만 보탠다.
UA_DECLARED = "personal-research-notes (non-commercial; fetch_article.py)"
# Wayback(archive.org)도 브라우저 흉내 UA에는 429를 낸다 — 2026-10-06 fact-check 하위 작업이 curl로 8번 막혔다.
DECLARED_UA_HOSTS = ("sec.gov", "archive.org")

# 영상·오디오 — 브라우저로 열면 자동 재생으로 소리가 난다. 스크립트로 정보만 받는다(2026-10-06).
MEDIA_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "podcasts.apple.com", "open.spotify.com",
               "soundcloud.com", "megaphone.fm", "omny.fm", "simplecast.com", "tiktok.com")
MEDIA_PATH = re.compile(r"axios-show|/videos?/|/podcasts?/", re.I)
# 스크립트로는 본문이 비는 곳(JS·iframe 렌더) — 첫 시도부터 내장 브라우저로(2026-10-06 실측).
BROWSER_HOSTS = ("chosun.com", "law.go.kr")


def _host_in(url: str, hosts) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def is_media(url: str) -> bool:
    from urllib.parse import urlparse
    return _host_in(url, MEDIA_HOSTS) or bool(MEDIA_PATH.search(urlparse(url).path or ""))


def _ua_for(url: str) -> str:
    return UA_DECLARED if _host_in(url, DECLARED_UA_HOSTS) else UA


def norm_url(u: str) -> str:
    """대조용 정규 주소 — 호스트 소문자·`www.`·추적 파라미터·끝 `/` 제거, 네이버 `/mnews/article/` → `/article/`."""
    from urllib.parse import urlsplit, parse_qsl, urlencode, unquote
    try:
        p = urlsplit((u or "").strip())
    except ValueError:
        return (u or "").strip().lower()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = unquote(p.path).replace("/mnews/article/", "/article/").rstrip("/")
    q = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not DROP_PARAMS.match(k)])
    return host + path + ("?" + q if q else "")


def log_fetch(url, kind, ok, chars=0, title="", err="", tag="writer", final=None):
    """연 링크 한 줄을 `_raw_sources/fetch_log_<YYMMDD>.jsonl`에 덧붙인다. 기록 실패는 열기를 막지 않는다."""
    rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "url": url, "norm": norm_url(url),
           "final": norm_url(final) if final else "", "kind": kind, "ok": bool(ok), "chars": int(chars or 0),
           "title": (title or "")[:120], "err": (err or "")[:200], "tag": tag}
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "fetch_log_%s.jsonl" % datetime.now().strftime("%y%m%d")), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print("(fetch 로그 기록 실패: %s)" % e, file=sys.stderr)
    return rec


def _get(url: str):
    """(바이트, content-type)을 돌려준다 — PDF 판별에 둘 다 필요하다."""
    global LAST_FINAL
    req = urllib.request.Request(url, headers={
        "User-Agent": _ua_for(url), "Accept-Language": "ko,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        LAST_FINAL = r.geturl()
        raw = r.read()
        if r.headers.get("Content-Encoding", "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    return raw, ctype


def is_pdf(raw: bytes, ctype: str, url: str) -> bool:
    """매직바이트가 1순위 — 서버가 octet-stream이나 text/html로 잘못 내보내는 경우가 있다."""
    return raw[:5] == b"%PDF-" or ctype == "application/pdf" or url.lower().split("?")[0].endswith(".pdf")


def pdf(raw: bytes):
    """PDF 본문 추출. 브라우저로 열면 다운로드 대화상자가 뜨므로 여기서 끝내야 한다."""
    import io as _io
    try:
        import pypdf
    except ImportError:
        return ("(PDF)", "", "ERROR: pypdf 없음 — python3 -m pip install pypdf")
    try:
        r = pypdf.PdfReader(_io.BytesIO(raw))
    except Exception as e:
        return ("(PDF)", "", f"ERROR: PDF 파싱 실패 ({e})")
    if getattr(r, "is_encrypted", False):
        try:
            r.decrypt("")
        except Exception:
            return ("(PDF)", "", "ERROR: 암호화된 PDF")
    meta = getattr(r, "metadata", None) or {}
    title = (meta.get("/Title") or "").strip() or "(PDF · 제목 없음)"
    pages = [(p.extract_text() or "") for p in r.pages]
    body = re.sub(r"\n\s*\n+", "\n", "\n".join(pages)).strip()
    if not body:
        body = ("(본문 비어 있음 — 스캔 이미지 PDF로 보임. 텍스트 층이 없으므로 UNVERIFIABLE로 두고 "
                "**브라우저로 재시도하지 말 것**: 다운로드 대화상자만 뜬다)")
    return (f"{title} [PDF {len(r.pages)}쪽]", "", body)


def _clean(s: str) -> str:
    s = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t　]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def naver(raw: str):
    """네이버 뉴스: 제목·날짜·본문(#dic_area). 언론사 페이지와 달리 구조가 고정이라 확실하다."""
    title = re.search(r'<h2[^>]*id="title_area"[^>]*>(.*?)</h2>', raw, re.S)
    body = re.search(r'<article[^>]*id="dic_area"[^>]*>(.*?)</article>', raw, re.S)
    date = re.search(r'data-date-time="([^"]+)"', raw)
    if not body:
        return None
    return (_clean(title.group(1)) if title else "(제목 없음)",
            date.group(1) if date else "", _clean(body.group(1)))


def generic(raw: str):
    """그 밖의 페이지: 제목 태그 + 80자 넘는 줄만(메뉴·푸터를 걷어내는 가장 값싼 방법)."""
    t = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    text = _clean(raw)
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) >= 80]
    return (html.unescape(t.group(1)).strip() if t else "(제목 없음)", "", "\n".join(lines))


def youtube(url: str):
    """YouTube — 재생하지 않고 공개 정보만. oEmbed(제목·채널) + 페이지 HTML의 설명·게시일."""
    import json
    from urllib.parse import quote
    raw, _ = _get("https://www.youtube.com/oembed?format=json&url=" + quote(url, safe=""))
    meta = json.loads(raw.decode("utf-8", "ignore"))
    title = "%s — %s" % (meta.get("title", "(제목 없음)"), meta.get("author_name", "?"))
    date, desc = "", ""
    try:
        page = _get(url)[0].decode("utf-8", "ignore")
        d = re.search(r'"shortDescription":("(?:[^"\\]|\\.)*")', page)
        p = re.search(r'"publishDate":"([^"]+)"', page)
        desc = json.loads(d.group(1)) if d else ""
        date = p.group(1) if p else ""
    except Exception as e:                       # 설명은 덤 — oEmbed만으로도 확인은 된다
        desc = "(설명 못 받음: %s)" % e
    return title, date, desc


def fetch(url: str, wayback: str = None):
    target = f"https://web.archive.org/web/{wayback}id_/{url}" if wayback else url
    raw, ctype = _get(target)
    if is_pdf(raw, ctype, url):
        return pdf(raw), target
    text = raw.decode("utf-8", "ignore")
    parsed = naver(text) if "news.naver.com" in url else None
    return parsed or generic(text), target


def selftest():
    """네트워크 없이 — 영상·PDF 판별, 주소 정규화, 로그 기록·읽기 왕복."""
    import tempfile
    global LOG_DIR
    ok = True

    def _t(name, hit):
        nonlocal ok
        ok &= bool(hit)
        print("  %s %s" % ("✓" if hit else "✗", name))

    _t("YouTube는 영상", is_media("https://www.youtube.com/watch?v=abc"))
    _t("axios-show 경로는 영상", is_media("https://www.axios.com/2026/10/06/axios-show-x"))
    _t("일반 기사는 영상 아님", not is_media("https://www.axios.com/2026/10/12/fed-rates"))
    _t("매직바이트로 PDF", is_pdf(b"%PDF-1.7", "text/html", "https://x.org/a"))
    _t("주소 끝 .PDF도 PDF", is_pdf(b"<html>", "", "https://x.org/A.PDF?x=1"))
    _t("HTML은 PDF 아님", not is_pdf(b"<html>", "text/html", "https://x.org/a"))
    _t("네이버 mnews·sid → article", norm_url("https://n.news.naver.com/mnews/article/015/0005340887?sid=101")
       == norm_url("https://n.news.naver.com/article/015/0005340887"))
    _t("www·utm·끝 / 제거", norm_url("https://WWW.Axios.com/2026/10/12/a/?utm_source=nl&utm_medium=email")
       == "axios.com/2026/10/12/a")
    _t("의미 있는 파라미터는 유지", norm_url("https://x.kr/view.do?id=7&utm_source=a") == "x.kr/view.do?id=7")
    _t("%20 풀기", norm_url("https://x.kr/a%20b") == "x.kr/a b")
    old = LOG_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            LOG_DIR = d
            log_fetch("https://n.news.naver.com/mnews/article/001/1?sid=101", "html", True, 1200, "제목", tag="pre")
            log_fetch("https://www.axios.com/a", "manual", True, tag="writer")
            rows = [json.loads(l) for f in os.listdir(d) for l in open(os.path.join(d, f), encoding="utf-8")]
            _t("로그 두 줄 · 정규 주소 · 태그", len(rows) == 2 and rows[0]["norm"] == "n.news.naver.com/article/001/1"
               and rows[0]["tag"] == "pre" and rows[1]["tag"] == "writer")
    finally:
        LOG_DIR = old
    print("\n통과" if ok else "\n실패")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--wayback", metavar="YYYY", default=None,
                    help="Wayback 스냅샷(해당 연도 근처)으로 우회 — Cloudflare 봇차단 사이트용")
    ap.add_argument("--max", type=int, default=6000, help="본문 출력 상한(자)")
    ap.add_argument("--tag", default="writer", choices=("writer", "pre", "aud", "fc"),
                    help="누가 열었나 — 노트 작성자(기본)·사전 감사·관측 감사·검증 조달. check_note는 writer만 센다")
    ap.add_argument("--no-log", action="store_true", help="fetch 로그를 남기지 않음")
    ap.add_argument("--mark-opened", action="store_true",
                    help="열지 않고 로그만 — 내장 브라우저·WebFetch로 연 URL을 적는다(--how 필수)")
    ap.add_argument("--how", choices=("browser", "webfetch"), default=None)
    ap.add_argument("--chars", type=int, default=0, help="--mark-opened 때 읽은 본문 길이(자)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.urls:
        ap.error("URL이 필요하다")
    if args.mark_opened:
        if not args.how:
            ap.error("--mark-opened에는 --how browser|webfetch가 필요하다")
        for u in args.urls:
            rec = log_fetch(u, args.how, True, args.chars, tag=args.tag)
            print("기록:", rec["norm"], "·", args.how, "·", args.tag)
        return 0

    def _log(u, kind, ok, chars=0, title="", err=""):
        if not args.no_log:
            log_fetch(u, kind, ok, chars, title, err, args.tag, LAST_FINAL if ok else None)

    rc = 0
    for u in args.urls:
        print("=" * 100)
        print("URL:", u)
        if is_media(u):
            print("⛔ 영상·오디오 페이지 — 내장 브라우저로 열지 말 것(자동 재생으로 사용자 컴퓨터에서 소리가 난다)")
            if _host_in(u, ("youtube.com", "youtu.be")):
                try:
                    title, date, desc = youtube(u)
                    print("제목:", title, ("| " + date) if date else "")
                    print(desc[:args.max])
                    _log(u, "media", True, len(desc), title)
                except Exception as e:
                    rc = 1
                    print("ERROR:", e, "— 뉴스레터 본문의 설명으로 대신할 것(브라우저 재시도 금지)")
                    _log(u, "media", False, err=str(e))
                continue
            if not args.wayback:
                print("→ `--wayback <연도>`로 HTML 사본을 스크립트로 읽거나, 뉴스레터 본문·같은 주제의 텍스트 기사로 대신할 것")
                _log(u, "media", False, err="영상 — 스크립트 미지원(--wayback 필요)")
                continue
        elif _host_in(u, BROWSER_HOSTS) and not args.wayback:
            print("→ 스크립트로는 본문이 빈다(JS·iframe 렌더) — 내장 브라우저로 열 것(SKILL 링크 표의 읽기 스니펫으로)")
            print("  연 뒤: python3 fetch_article.py --mark-opened --how browser", u)
            continue
        try:
            (title, date, body), target = fetch(u, args.wayback)
            if target != u:
                print("VIA:", target)
            print("제목:", title, ("| " + date) if date else "")
            print(body[:args.max])
            if len(body) > args.max:
                print(f"… (총 {len(body)}자, --max로 늘릴 것)")
            if not body.strip():
                print("(본문 비어 있음 — JS 렌더 페이지일 수 있음: FAQ·규정·소개 페이지로 대신 대조)")
            empty = not body.strip() or body.startswith("(본문 비어 있음")
            _log(u, "pdf" if "[PDF" in title else "html", not empty, 0 if empty else len(body), title,
                 "본문 비어 있음" if empty else "")
        except urllib.error.HTTPError as e:
            rc = 1
            hint = {403: "봇차단일 수 있음 → --wayback", 401: "로그인·페이월",
                    404: "URL 추측 금지 — WebSearch로 실제 URL을 얻을 것",
                    429: "요청 과다 — 잠시 뒤"}.get(e.code, "")
            print(f"ERROR: HTTP {e.code} {hint}")
            _log(u, "html", False, err=f"HTTP {e.code}")
        except Exception as e:
            rc = 1
            print("ERROR:", e)
            _log(u, "html", False, err=str(e))
    return rc


if __name__ == "__main__":
    sys.exit(main())
