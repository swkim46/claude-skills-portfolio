# Gmail Newsletter Analyzer

Reads subscribed newsletters from personal Gmail and turns them into a digest worth reading, instead of an inbox worth ignoring.

**Status: end-to-end working. Output format settled on `.md` (2026-08-30).**

> **Portfolio snapshot.** The notes (`세상공부/`), the paired corpus and the raw newsletter captures are excluded —
> the notes are personal and the newsletter bodies are copyrighted. Code, roster schema and the note linter ship in full.

---

## 1. What exists today

| Component | File | State |
|---|---|---|
| IMAP transport | `gmail_imap.py` | ✅ **Verified against the live mailbox** 2026-08-27 |
| App password | `<REPO>/.env` | ✅ Repo-root env, one level up (copy `.env.example`) |
| Sender scanner | `scan_senders.py` | ✅ Working — 90-day scan run 2026-08-27 |
| Newsletter roster | `newsletters.json` | ✅ 3 enabled (UPPITY·BOODING·STARTUP WEEKLY), 2 borderline off |
| Digest collector | `digest.py` | ✅ Working — sponsored sections removed, reader feedback kept |
| Paired corpus | `build_corpus.py` → `_raw_sources/corpus/` | ✅ 21 days, 57 newsletters (backlog windows) |
| Transformation rules | `_raw_sources/전이규칙_도출.md` (private — not in this repo) | ✅ Derived by comparison, not guesswork |
| **The analyzer** | `life/.claude/skills/world-study/SKILL.md` | ✅ Holdout-tuned + step gate (`rubrics/world-study.json`) |
| Holdout eval | `_raw_sources/홀드아웃_평가_260828.md` (private — not in this repo) | 🟡 2 rounds; next needs fresh dates |
| Notes | `세상공부/` (private — not in this repo) | ✅ v2_0 확정 (fact-check + 적대 감사 통과) |

The OAuth artifacts (`gmail_mcp.py`, `client_secret.json`, `token.json`, `requirements.txt`, `.venv/`) were **deleted on 2026-08-27** once IMAP was verified.

### What the analyzer is actually for

Not a generic "digest". It continues the **`세상 공부`** note in Apple Notes — a hand-kept study journal running 260112–260303 (21 entries, ~8.5 lines a day) covering 경제·금융, 부동산, 산업·테크, 지정학. Its topics map almost exactly onto the three newsletters in the roster.

The note **stopped six months ago**. Keeping it by hand did not survive contact with a busy schedule, and that — not inbox volume — is the problem this project exists to solve.

**It is not a summarizer.** A summary makes the source shorter; this note starts from one line
of the source and digs out *why it is so*, leaving an understanding deeper than the original.

The note is **hierarchical** — 62 of 177 items (35%) are sub-items, nesting three deep:

```
- 작년 대비 서울 전세 매물 27% 감소                       ← 사실
    - 토지거래허가구역이 되어 실거주 필요 → 전세 놓을 이유 X   ← 원인
        - 일정 규모 이상 거래 시 지자체 허가가 필요한 제도      ← 개념 정의
        - '토지' 구역이지만 건물이 대지 지분을 가져 주택도 대상   ← 비직관적 디테일
```

⚠️ Apple Notes' `plaintext of note` **destroys this hierarchy.** Always read `body of note`
(HTML) and parse the nested `<ul>` — `build_corpus.py` does this.

The rules live in the skill; they were derived by comparing 57 newsletters against 177 note
items, not invented. Two that cost the most to learn:

- **거시지표는 1%.** Index moves and rate commentary are almost never recorded — the first
  draft led with them and scored 0/1 on holdout.
- **Material is the backlog, not the day.** An entry draws on every issue since the *previous*
  entry. 6 of 21 note days were 2–4 day backlogs. Pairing same-day-only put source coverage at
  82%; pairing by backlog window raised it to **92%**, and the remainder is the operator's own
  synthesis rather than outside fact.

### Why IMAP instead of the Gmail API

The original transport used OAuth against the Gmail API. It was abandoned on 2026-08-27 for a concrete reason:

- The saved refresh token was dead (`invalid_grant`). The consent screen for project `<gcp-project-id>` is in **Testing** status with **External** user type, and Google expires refresh tokens for that combination after **7 days**. A recurring digest can never run unattended under that constraint.
- The obvious fix — publishing to Production — is **blocked in the console**. The publish button is disabled with "앱의 OAuth 구성이 완료되지 않았습니다", pointing at the Branding page, whose required fields are already filled. The same symptom is reported on Google's developer forums.
- Separately, the desktop OAuth client (created 2026-03-13) carries a warning that **unused clients are deleted after 6 months** — meaning around mid-September 2026.

IMAP with an app password has no token expiry and no console dependency.

### What the switch cost, and what it didn't

Less than expected. Gmail's IMAP server supports the **`X-GM-RAW`** search extension, which accepts full Gmail search syntax, so `build_query()` and its KST day-boundary handling carried over essentially unchanged. Only the connection and fetch layer was genuinely rewritten.

It also **removed all third-party dependencies** — `imaplib` and `email` are stdlib. There is no venv to maintain and nothing to reinstall after a Python upgrade, which is what broke this project once already.

The one real loss: the Gmail API returned a `snippet` for free. IMAP does not, so previews now require fetching the body.

### Details worth not rediscovering

- **KST day boundaries** (`build_query` + `filter_to_kst_day`) — Gmail's `after:`/`before:` are date-only, so a naive "today" query mis-handles the 9-hour offset. The window is widened by a day on each side, then trimmed precisely using each message's `Date` header.
- **`BODY.PEEK`, never `BODY`** — a plain `BODY[]` fetch sets the `\Seen` flag and would silently mark your newsletters as read. Every fetch here uses `PEEK`, and the mailbox is selected `readonly=True`.
- **Localised folder names** — a Korean account exposes `[Gmail]/전체보관함`, not `[Gmail]/All Mail`. `find_all_mail()` discovers it by the `\All` special-use flag instead of by name.
- **MIME-encoded headers** — Korean subjects and sender names arrive as `=?UTF-8?B?...?=` and need decoding.
- **Non-ASCII search** — `imaplib` mangles non-ASCII arguments; Korean queries are sent as a UTF-8 literal with an explicit charset.

### A note on `workflow.py`

`gmail_mcp.py` referred to *"a Python-callable entry point for workflow.py."* **No such file was ever written** — it was a planned filename referenced before it existed, not lost work. `gmail_imap.fetch()` now fills that role.

---

## 2. Setup

### Step 1 — create an app password

Requires 2-Step Verification on the account. Create one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).

### Step 2 — write `.env`

At the repo root (`<REPO>/.env`, gitignored — copy `.env.example`):

```
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
```

Spaces in the displayed password are cosmetic and stripped automatically.

### Step 3 — verify

```bash
python3 gmail_imap.py
```

Prints the detected All Mail folder and up to three recent messages, or a clear failure. Any `python3` works — no venv required.

---

## 3. Files

```
gmail-newsletter-analyzer/
├── README.md            ← this file
├── gmail_imap.py        ← IMAP transport (search + read), stdlib only
├── scan_senders.py      ← surveys mail, proposes the roster
├── digest.py            ← collects a date window, strips sponsored blocks
├── build_corpus.py      ← pairs note entries with their source issues
├── newsletters.json     ← the roster: which senders feed the digest
├── 세상공부/             ← the notes (private; gitignored here)
│   ├── 세상공부_YYMMDD_vN_M.md
│   └── 이전버전/
└── _raw_sources/        ← runtime material (gitignored)

    credentials live one level up in <REPO>/.env  [SECRET]
```

---

## 4. Output — settled on `.md` (2026-08-30)

Notes are written to **files**, not appended to the Apple Note. The note stays untouched because it is
the corpus and the basis of the spec.

```
세상공부/
├── 세상공부_YYMMDD_vN_M.md      ← current
├── 세상공부_YYMMDD_vN-1_M.md    ← previous major, kept
└── 이전버전/                     ← retired minors
```

Versioning follows `deliverable-versioning`: never overwrite, always issue `vN_M`. `M++` retires the
previous minor into `이전버전/`; `N++` (a verification changed the facts) keeps the previous major in place.

---

## 5. Where this is going

Not yet built. Open questions, in the order they need answering:

1. ~~**Which newsletters?**~~ ✅ **Answered** — `scan_senders.py` surveyed 90 days (594 messages, 72 senders) and `newsletters.json` holds the result. Three genuine editorial newsletters are enabled: UPPITY 머니레터 (경제·금융, near-daily), BOODING (부동산, 1–2×/week), STARTUP WEEKLY (스타트업, weekly). Two borderline sources are off pending review. **Still needs the operator's sign-off.**
2. **What does a digest contain?** Per-newsletter summary, cross-newsletter theme clustering, or filtering to standing interests. These imply meaningfully different pipelines.
3. **What cadence?** Daily and weekly differ in volume, and `build_query` currently assumes a single-day window.
4. **Where does output go?** A dated Markdown file here, a Notion page, or something else.

Deliverables follow the `life/` convention: `<주제>_vN_M.md`.

---

## 5. Security

`.env` holds a Google app password. Unlike the `gmail.readonly` OAuth scope it replaces, **an app password grants full IMAP access — including deleting and modifying mail.** This module never issues a write command and selects mailboxes read-only, but that restraint lives in the code, not in the credential. Treat the password accordingly: keep it in `.env`, never paste it elsewhere, and revoke it at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) if it is ever exposed.

`client_secret.json` and `token.json` are leftovers from the OAuth attempt. The token is dead; the client secret is still live until the client is deleted. Both are gitignored and should be removed once IMAP is verified.
