#!/usr/bin/env python3
"""
기사 본문을 터미널로 가져온다 — `WebFetch`가 거부하는 도메인용.

    python3 fetch_article.py <url> [<url> ...]          # 네이버 뉴스·일반 HTML
    python3 fetch_article.py --wayback 2025 <url> ...   # Cloudflare 봇차단(IEA·Axios 등) → Wayback 스냅샷
    python3 fetch_article.py --max 8000 <url>           # 본문 출력 상한(기본 6000자)

왜 있나 (2026-09-16 실측):
- `WebFetch`는 n.news.naver.com·wsj.com·nytimes.com·newyorker.com을 **도메인째** 거부한다.
  네이버는 urllib + 브라우저 UA로 그냥 열린다(`#dic_area`가 본문). 세상공부 노트가 여는 링크의
  대부분이 네이버 기사라 이 한 줄이 회차마다 필요했다.
- iea.org·axios.com은 Cloudflare 챌린지(403 `cf-mitigated: challenge`) — 브라우저 UA·Googlebot UA로도
  안 열린다. `https://web.archive.org/web/<YYYY>id_/<원URL>`(id_ = 원본 그대로)은 열리는데,
  `archive.org/wayback/available` API는 429가 잦으니 쓰지 않고 스냅샷 URL을 직접 친다.
- 응답이 gzip이면 압축을 풀어야 grep이 된다 — Wayback이 그렇다(`--compressed` 빠뜨려 한 번 헛돌았다).
- 403·404·401은 그대로 ERROR로 보인다. **추측하지 말 것** — 못 열었으면 노트 헤더 '열지 못한 것'에 적는다.
"""
import argparse
import gzip
import html
import re
import sys
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "ko,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding", "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "ignore")


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


def fetch(url: str, wayback: str = None):
    target = f"https://web.archive.org/web/{wayback}id_/{url}" if wayback else url
    raw = _get(target)
    parsed = naver(raw) if "news.naver.com" in url else None
    return parsed or generic(raw), target


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--wayback", metavar="YYYY", default=None,
                    help="Wayback 스냅샷(해당 연도 근처)으로 우회 — Cloudflare 봇차단 사이트용")
    ap.add_argument("--max", type=int, default=6000, help="본문 출력 상한(자)")
    args = ap.parse_args()

    rc = 0
    for u in args.urls:
        print("=" * 100)
        print("URL:", u)
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
        except urllib.error.HTTPError as e:
            rc = 1
            hint = {403: "봇차단일 수 있음 → --wayback", 401: "로그인·페이월",
                    404: "URL 추측 금지 — WebSearch로 실제 URL을 얻을 것",
                    429: "요청 과다 — 잠시 뒤"}.get(e.code, "")
            print(f"ERROR: HTTP {e.code} {hint}")
        except Exception as e:
            rc = 1
            print("ERROR:", e)
    return rc


if __name__ == "__main__":
    sys.exit(main())
