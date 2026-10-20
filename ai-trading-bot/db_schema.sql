-- 매매 저장소 스키마 — db.py만 이 파일을 실행한다(멱등).
-- 층: sys(저장소) · dim(기준 정보) · mat(재료) · ma(거시) · mk(시장 상태) · sc(세부시장) · co(종목) · cal(일정)
--     · ev(출처·검증) · jd(판단) · ex(집행) · op(운영)
-- 원장 모양 표(DocStore)는 공통으로 rid(정수 키) · extra(옛 행의 나머지 칸) · ko(키 순서) · ord · rev · created_at · updated_at 를 갖는다.
-- 숫자 칸은 타입을 비워(affinity 없음) 정수·실수를 그대로 보존한다(내보내기가 옛 파일과 바이트 단위로 같아야 한다).

PRAGMA foreign_keys = ON;

-- ───────────── sys ─────────────
CREATE TABLE IF NOT EXISTS sys_meta (k TEXT PRIMARY KEY, v TEXT);
INSERT OR IGNORE INTO sys_meta (k, v) VALUES ('schema_version', '10');   -- 새 저장소의 판 = db.SCHEMA_VERSION(옛 저장소는 db.py migrate)
CREATE TABLE IF NOT EXISTS sys_docmeta (doc TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS sys_doc_hist (        -- 판단 원장 행의 지난 판(갱신·삭제 직전 모양)
  id INTEGER PRIMARY KEY, doc TEXT NOT NULL, k TEXT NOT NULL, rev INTEGER, body TEXT NOT NULL, at TEXT
);
CREATE INDEX IF NOT EXISTS ix_doc_hist ON sys_doc_hist(doc, k);
CREATE TRIGGER IF NOT EXISTS tg_doc_hist_noupd BEFORE UPDATE ON sys_doc_hist BEGIN SELECT RAISE(ABORT, 'sys_doc_hist는 추가만 한다'); END;

-- ───────────── ① 기준 정보 dim_ ─────────────
CREATE TABLE IF NOT EXISTS dim_market (
  code TEXT PRIMARY KEY,               -- KR · US
  exchange TEXT, currency TEXT, tz TEXT, open_local TEXT, close_local TEXT,
  session_rule TEXT,                   -- 세션 날짜 규칙(현지 거래일)
  benchmark_ticker TEXT, benchmark_excd TEXT, benchmark_name TEXT
);
INSERT OR IGNORE INTO dim_market (code, exchange, currency, tz, open_local, close_local, session_rule)
  VALUES ('KR', 'KRX', 'KRW', 'Asia/Seoul', '09:00', '15:30', '한국 거래일 · 마감 뒤·주말은 다음 거래일'),
         ('US', 'NYSE/NASDAQ', 'USD', 'America/New_York', '09:30', '16:00', '미국 동부 거래일 · 마감 뒤·주말은 다음 거래일');

CREATE TABLE IF NOT EXISTS dim_sector (
  rid INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE, market TEXT, name TEXT,
  grade TEXT, status TEXT, next_event TEXT, resolves_when TEXT, resolves_by TEXT, carried_runs,
  updated TEXT, last_seen TEXT,
  index_ticker TEXT, etf_ticker TEXT,
  extra TEXT, ko TEXT, ord INTEGER, rev INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS jd_sector_name (
  pid INTEGER NOT NULL REFERENCES dim_sector(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  ticker TEXT, name TEXT, why TEXT, in_universe INTEGER, extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS dim_sector_list (
  pid INTEGER NOT NULL REFERENCES dim_sector(rid) ON DELETE CASCADE, src TEXT, ord INTEGER, value TEXT
);
CREATE TABLE IF NOT EXISTS dim_sector_link (   -- 국내 업종 ↔ 미국 섹터(지금 config/sector_bridge.json)
  id INTEGER PRIMARY KEY,
  kr_sector TEXT, kr_name TEXT, us_sector TEXT, sign TEXT, confidence TEXT, why TEXT,
  status TEXT NOT NULL DEFAULT 'live',  -- live · retired · gap(한쪽 시장만 있는 업종)
  retired_why TEXT, src TEXT, body TEXT, ord INTEGER
);

CREATE TABLE IF NOT EXISTS dim_company (
  rid INTEGER PRIMARY KEY,
  market TEXT NOT NULL, ticker TEXT NOT NULL,
  excd TEXT, name TEXT, sector_code TEXT,
  added TEXT, why TEXT, axis TEXT, added_by TEXT, run_id TEXT, source TEXT, axis_set_by TEXT,
  in_watchlist INTEGER NOT NULL DEFAULT 0,    -- 유니버스(감시 목록) — config/watchlist.json은 이 표의 렌더본
  cooling_until TEXT,                          -- 퇴출 뒤 재편입 금지 기한
  main_rank INTEGER, main_as_of TEXT, main_source TEXT, main_mcap,   -- 시총 상위 순위(지금 config/main_names.json)
  over_1m INTEGER, leveraged INTEGER, inverse INTEGER,              -- 1주 100만 초과 · 레버리지 · 인버스
  extra TEXT, ko TEXT, ord INTEGER, rev INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT,
  UNIQUE (market, ticker)
);
CREATE TABLE IF NOT EXISTS dim_company_list (    -- aliases · gates
  pid INTEGER NOT NULL REFERENCES dim_company(rid) ON DELETE CASCADE, src TEXT, ord INTEGER, value TEXT
);

CREATE TABLE IF NOT EXISTS dim_indicator (
  code TEXT PRIMARY KEY, name TEXT, country TEXT, org TEXT, freq TEXT, unit TEXT, next_release TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS dim_asset (
  code TEXT PRIMARY KEY, name TEXT, kind TEXT, unit TEXT, source TEXT, freq TEXT
);

CREATE TABLE IF NOT EXISTS dim_axis (            -- 전망 축(거시 → 섹터 → 종목 고리의 머리)
  rid INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE, name TEXT, label TEXT, thesis TEXT,
  driver TEXT,                                    -- 요인(ai_capex·rates·oil…) — 요인 노출 뷰가 쓴다
  direction TEXT, expected TEXT, horizon_weeks, confidence, breaks_if TEXT, status TEXT, opened TEXT, origin TEXT,
  event_key TEXT, created_before_news INTEGER, created_before_news_why TEXT, cross_market_reviewed TEXT, tagged TEXT,
  updated TEXT, added TEXT, split_why TEXT, no_cross_market_why TEXT, no_secondary_why TEXT, research TEXT,
  extra TEXT, ko TEXT, ord INTEGER, rev INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT,
  -- 전망 판정(v9 · Codex U06 — 예전 extra.score JSON) — 판정일·초과수익·메모·판정 run·초과수익이 기댄 가격의 세션일
  judged_at TEXT, excess_pct, judge_note TEXT, judged_run_id TEXT, judge_asof TEXT
);
CREATE TABLE IF NOT EXISTS jd_axis_indicator (
  pid INTEGER NOT NULL REFERENCES dim_axis(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  name TEXT, code TEXT, source TEXT, org TEXT, freq TEXT, current TEXT, threshold TEXT, next TEXT,
  current_fact_id TEXT, last_checked TEXT, extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS jd_axis_name (
  pid INTEGER NOT NULL REFERENCES dim_axis(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  ticker TEXT, name TEXT, sign TEXT, why TEXT, in_universe INTEGER, market TEXT, added TEXT, extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS jd_axis_beneficiary (
  pid INTEGER NOT NULL REFERENCES dim_axis(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  ticker TEXT, name TEXT, bord INTEGER, why TEXT, priced INTEGER, market TEXT, in_universe INTEGER,
  priced_basis TEXT, priced_at TEXT, extra TEXT, ko TEXT,
  -- ★ 마지막 성공(priced·근거·측정일 + 숫자·일봉 기준일)과 지금 측정 상태(마지막 시도 valid|unavailable · 시각·오류·run)를 나눈다(v9 · U05)
  --   '오래됨'은 저장하지 않고 읽을 때 계산한다(market_map.measure_state — 일봉 기준일 ≠ 마지막 닫힌 세션)
  priced_bars_asof TEXT, priced_ret20, priced_vs_bench, priced_vs_lead, measure_status TEXT, measured_at TEXT, measure_error TEXT,
  measure_run_id TEXT, judge_ret_pct
);
CREATE TABLE IF NOT EXISTS jd_axis_chain (
  pid INTEGER NOT NULL REFERENCES dim_axis(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  step INTEGER, claim TEXT, status TEXT, current TEXT, indicator TEXT, source TEXT, extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS jd_axis_list (       -- conflicts_with
  pid INTEGER NOT NULL REFERENCES dim_axis(rid) ON DELETE CASCADE, src TEXT, ord INTEGER, value TEXT
);
CREATE TABLE IF NOT EXISTS jd_axis_note (       -- run마다 축 판단(옛 note_<stamp> 칸)
  pid INTEGER NOT NULL REFERENCES dim_axis(rid) ON DELETE CASCADE, k TEXT NOT NULL, text TEXT, run_id TEXT, fact_ids TEXT
);

-- ───────────── ② 재료 mat_ ─────────────
CREATE TABLE IF NOT EXISTS mat_issue (           -- 뉴스레터 발행분(본문은 신뢰할 수 없는 외부 입력)
  id INTEGER PRIMARY KEY,
  run_id TEXT, sender TEXT, source TEXT, topic TEXT, day TEXT, received_at TEXT, subject TEXT,
  body TEXT, chars INTEGER, raw_path TEXT, spam_rescued INTEGER, first_run TEXT,
  content_sha TEXT UNIQUE, legacy INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mat_issue_run (       -- 발행분 ↔ run(같은 발행분을 두 run이 받는다)
  issue_id INTEGER NOT NULL REFERENCES mat_issue(id) ON DELETE CASCADE, run_id TEXT NOT NULL, ord INTEGER,
  PRIMARY KEY (issue_id, run_id)
);
CREATE TABLE IF NOT EXISTS mat_digest (           -- run의 수집 파일 머리(제목·수집 스탬프) — 재료 문서 렌더용
  run_id TEXT PRIMARY KEY, path TEXT, head TEXT, n_issues INTEGER, spam_count INTEGER, collected_at TEXT
);
CREATE TABLE IF NOT EXISTS mat_link (
  id INTEGER PRIMARY KEY,
  issue_id INTEGER REFERENCES mat_issue(id) ON DELETE CASCADE, ord INTEGER,
  url TEXT, title TEXT, fetched INTEGER, raw_path TEXT, chars INTEGER, run_id TEXT, body TEXT,   -- body = 옛 행(v3 전)만 · 새 본문은 판본으로
  version_id INTEGER REFERENCES ev_source_version(id),   -- 마지막으로 성공한 본문 판(v3 · S01 — 재조회 실패가 본문을 덮지 않는다)
  last_fetch_id INTEGER REFERENCES ev_fetch(id),         -- 마지막 시도(성공·실패)
  -- 링크 선택 기록(v9 · G01) — picked|cut|skipped · 사유(picked:wrap|term:<말>|lead · cut:cap|no_match · skipped:already_fetched:<run>|uppity|dup) · 순위
  pick_status TEXT, pick_reason TEXT, pick_rank INTEGER
);
CREATE INDEX IF NOT EXISTS ix_mat_link_url ON mat_link(url);
CREATE TABLE IF NOT EXISTS mat_link_match (      -- 링크 원문이 걸린 유니버스 종목·축
  link_id INTEGER NOT NULL REFERENCES mat_link(id) ON DELETE CASCADE, subject_type TEXT, subject_id TEXT,
  PRIMARY KEY (link_id, subject_type, subject_id)
);
CREATE TABLE IF NOT EXISTS mat_link_version (    -- run마다 그 링크에서 본 본문 판(v3 · 영구 — 노트 숫자 대조가 읽는다)
  link_id INTEGER NOT NULL REFERENCES mat_link(id), run_id TEXT NOT NULL,
  fetch_id INTEGER NOT NULL REFERENCES ev_fetch(id), version_id INTEGER NOT NULL REFERENCES ev_source_version(id),
  at TEXT NOT NULL, PRIMARY KEY (link_id, run_id, fetch_id)
);
CREATE TABLE IF NOT EXISTS mat_ws_event (        -- 세상공부 노트의 사건 줄
  id INTEGER PRIMARY KEY,
  ws_note TEXT, ws_date TEXT, headline TEXT, tag TEXT, evidence_line TEXT, url TEXT,
  subject_type TEXT, subject_id TEXT, run_id TEXT, content_sha TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS mat_dart (            -- DART 공시 피드
  rcp_no TEXT PRIMARY KEY, corp_code TEXT, ticker TEXT, report_nm TEXT, rcept_dt TEXT, url TEXT,
  fetched INTEGER, raw_path TEXT, kind TEXT, run_id TEXT, corp_name TEXT, filer TEXT
);
-- 출처별 수집 통계(v9 · Codex G01) — run마다 출처 하나 한 행 · 발행 간격은 그 출처의 과거 호에서(손으로 쓰던 mat_expect는 지웠다)
CREATE TABLE IF NOT EXISTS mat_source_stat (
  run_id TEXT NOT NULL, source TEXT NOT NULL, requested INTEGER, received INTEGER, empty INTEGER, failed INTEGER,
  newest_issue_day TEXT, last_success_run_id TEXT, last_success_at TEXT, median_gap_days, silent_days INTEGER, note TEXT,
  PRIMARY KEY (run_id, source)
);
-- 필요 재료 확보(v9 · G01) — 열린 논지·임박 일정의 종목을 이번 run 재료가 다뤘나(막지 않고 보인다)
CREATE TABLE IF NOT EXISTS mat_coverage (
  run_id TEXT NOT NULL, need_kind TEXT NOT NULL CHECK (need_kind IN ('thesis', 'event')), need_id TEXT NOT NULL,
  ticker TEXT, covered INTEGER NOT NULL, via TEXT, PRIMARY KEY (run_id, need_kind, need_id)
);
CREATE TABLE IF NOT EXISTS mat_mention (         -- 발행분이 어느 종목·섹터·축을 어떻게 다뤘나(노트 §3 섹터 보드의 원본)
  id INTEGER PRIMARY KEY,
  issue_id INTEGER REFERENCES mat_issue(id), link_id INTEGER REFERENCES mat_link(id), ws_event_id INTEGER REFERENCES mat_ws_event(id),
  run_id TEXT NOT NULL, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
  stance TEXT, headline TEXT, board_verdict TEXT, legacy INTEGER DEFAULT 0, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_mention_subject ON mat_mention(subject_type, subject_id);
CREATE TABLE IF NOT EXISTS mat_mention_fact (
  mention_id INTEGER NOT NULL REFERENCES mat_mention(id) ON DELETE CASCADE, fact_id TEXT NOT NULL REFERENCES ev_fact(id),
  PRIMARY KEY (mention_id, fact_id)
);
CREATE TABLE IF NOT EXISTS mat_search (          -- 리서치·검증 에이전트의 검색 기록(부재 단정의 근거)
  id INTEGER PRIMARY KEY, run_id TEXT, query TEXT, engine TEXT, agent TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS mat_search_result (
  search_id INTEGER NOT NULL REFERENCES mat_search(id) ON DELETE CASCADE, ord INTEGER, url TEXT, title TEXT
);

-- ───────────── ⑧ 출처·검증 ev_ (다른 층이 가리키므로 먼저) ─────────────
CREATE TABLE IF NOT EXISTS ev_source (           -- 출처 = 정규화한 URL 하나(db.norm_url) · retrieved_at·raw_path는 v3 전 기록(새로 안 씀)
  id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, publisher TEXT, tier INTEGER, retrieved_at TEXT, raw_path TEXT, title TEXT,
  first_fetched_at TEXT, last_fetched_at TEXT,       -- 처음 시도 · 마지막 성공(v3)
  kind TEXT CHECK (kind IN ('html', 'pdf', 'api'))
);
CREATE TABLE IF NOT EXISTS ev_source_version (   -- 본문 판(v3 · U01) — 같은 출처·절의 같은 내용은 한 판 · 파일 이름은 내용 해시
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES ev_source(id),
  section TEXT NOT NULL DEFAULT '',                 -- DART --section 등 같은 문서의 일부
  content_sha TEXT NOT NULL, bytes INTEGER NOT NULL,
  path TEXT,                                        -- data/_raw_fc/<sha16>.txt(옛 판은 옛 이름 그대로) · 정리되면 파일만 지우고 행은 남긴다
  kind TEXT NOT NULL CHECK (kind IN ('html', 'pdf', 'api')), pages INTEGER, title TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, n_fetch INTEGER NOT NULL DEFAULT 1,
  provenance TEXT NOT NULL CHECK (provenance IN ('fetched', 'link_hop', 'migrated')),
  purged_at TEXT,
  UNIQUE (source_id, section, content_sha)
);
CREATE INDEX IF NOT EXISTS ix_sv_source ON ev_source_version(source_id, section, last_seen);
CREATE TABLE IF NOT EXISTS ev_fetch (            -- 시도마다 한 행(성공·실패 · 추가만 · v3) — 옛 op_fetch는 읽기만
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES ev_source(id),
  version_id INTEGER REFERENCES ev_source_version(id),   -- 실패·빈 본문·보관 실패면 NULL
  url_raw TEXT NOT NULL, section TEXT NOT NULL DEFAULT '', run_id TEXT, at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'empty', 'error')), error TEXT,
  kind TEXT, pages INTEGER, chars_full INTEGER, chars_returned INTEGER, via TEXT,
  agent TEXT NOT NULL CHECK (agent IN ('fetch.py', 'link_hop', 'migrated')), op_fetch_seq INTEGER UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_fetch_source ON ev_fetch(source_id, at);
CREATE INDEX IF NOT EXISTS ix_fetch_url ON ev_fetch(url_raw);
CREATE TABLE IF NOT EXISTS ev_fact (             -- 사실 머리표 — 모든 조사 행의 id·시효·검증 상태·원문 인용·재료 출처
  rid INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,                        -- 새 사실 F-######, 이전된 옛 행은 SRC-####
  kind TEXT NOT NULL DEFAULT 'legacy',            -- 상세 표 종류(earnings·analyst·release…) · 이전된 옛 행은 legacy
  subject_type TEXT, subject_id TEXT, market TEXT, period TEXT,
  as_of TEXT, valid_class TEXT,                   -- static · dated · live · interp
  quote TEXT, load_bearing INTEGER,
  verified_status TEXT NOT NULL DEFAULT 'none',   -- none · verified · expired · disputed
  verified_at TEXT, verified_tier INTEGER, verified_run TEXT,
  source_id INTEGER REFERENCES ev_source(id), url TEXT, source_label TEXT, tier, retrieved TEXT,
  origin TEXT DEFAULT 'research',                 -- issue · link · ws_event · dart · research · tool
  origin_id TEXT, created_run TEXT, repeat INTEGER DEFAULT 0,
  legacy_text TEXT,                               -- 이전 전 문장(옛 행만) — 새 행은 비어 있어야 한다
  as_of_note TEXT, minor, note_261015_fc TEXT,
  needs_review INTEGER DEFAULT 0,
  extra TEXT, ko TEXT, ord INTEGER, rev INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT,
  value_rev INTEGER NOT NULL DEFAULT 1,            -- 값이 마지막으로 바뀐 판(v2 · R04) — 판정은 fact_rev >= value_rev일 때만 센다 · rev는 표기 편집까지 센다
  source_version_id INTEGER REFERENCES ev_source_version(id),   -- 이 사실이 본 본문 판(v3 · U01)
  source_version_link TEXT CHECK (source_version_link IN ('recorded', 'inferred')),   -- recorded = 판 번호·인용 일치 · inferred = 최신 판 추정
  verified_rev INTEGER                             -- 지금 검증 상태(verified)를 얻은 판(v3 · S03) — value_rev보다 낮으면 다시 검증 큐
);
CREATE INDEX IF NOT EXISTS ix_fact_subject ON ev_fact(subject_type, subject_id, kind);
CREATE INDEX IF NOT EXISTS ix_fact_url ON ev_fact(url);
CREATE TABLE IF NOT EXISTS ev_fact_run_label (   -- 옛 sources.json의 runs 칸("kr:261016")
  pid INTEGER NOT NULL REFERENCES ev_fact(rid) ON DELETE CASCADE, src TEXT, ord INTEGER, value TEXT
);
CREATE TABLE IF NOT EXISTS ev_fact_run (         -- 그 run의 S 번호
  fact_id TEXT NOT NULL REFERENCES ev_fact(id), run_id TEXT NOT NULL, s_no INTEGER, added_at TEXT,
  PRIMARY KEY (fact_id, run_id)
);
CREATE TABLE IF NOT EXISTS ev_fact_version (     -- 승인·체결이 가리키는 "그때 값"
  id INTEGER PRIMARY KEY, fact_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot TEXT, changed_at TEXT, correction_id INTEGER,
  source_version_id INTEGER                       -- 그 판이 본 본문 판(v3)
);
CREATE TABLE IF NOT EXISTS ev_fact_link (        -- 사실이 걸린 축·섹터·종목
  fact_id TEXT NOT NULL REFERENCES ev_fact(id) ON DELETE CASCADE, target_type TEXT NOT NULL, target_id TEXT NOT NULL, role TEXT,
  PRIMARY KEY (fact_id, target_type, target_id)
);
CREATE TABLE IF NOT EXISTS ev_claim_ref (        -- 노트·시그널·논지·시나리오·축·배분이 사실을 가리킨 자리
  id INTEGER PRIMARY KEY, fact_id TEXT NOT NULL REFERENCES ev_fact(id), run_id TEXT, doc_type TEXT, doc_id TEXT, slot TEXT,
  role TEXT, needs_review INTEGER DEFAULT 0, at TEXT,
  fact_rev INTEGER,                               -- 그 자리가 사실을 가리킨 때의 판(v2 · 기록)
  UNIQUE (fact_id, run_id, doc_type, doc_id, slot)
);
CREATE TABLE IF NOT EXISTS ev_verdict (          -- 판정(추가만)
  id INTEGER PRIMARY KEY, fact_id TEXT NOT NULL REFERENCES ev_fact(id), run_id TEXT, batch TEXT, round INTEGER,
  verdict TEXT NOT NULL,                          -- MATCH · MISMATCH · STALE · UNVERIFIABLE
  tier INTEGER, lens TEXT, note TEXT, support TEXT, auditor TEXT, at TEXT, src_file TEXT, row_label TEXT,
  match TEXT,                                     -- 이전 때 사실에 맞춘 방법(url · S번호 · url+수치 …)
  quote TEXT,                                     -- 이 판정 행이 본 원문 인용(하위 클레임이 사실의 quote와 다른 문장을 볼 때)
  url TEXT,                                       -- 이 판정 행의 검증 URL(사실 URL과 다를 때)
  t1 TEXT,                                        -- T1 시도(URL·상태 · 'T1 자체' · '미시도 — 사유')
  fact_rev INTEGER NOT NULL,                      -- 이 판정이 본 사실의 판(v2 · R04) — 값 판(ev_fact.value_rev)보다 낮으면 세지 않는다(REVERIFY)
  source_version_id INTEGER REFERENCES ev_source_version(id)   -- 이 판정이 대조한 본문 판(v3)
);
CREATE TABLE IF NOT EXISTS ev_verdict_orphan (   -- 옛 판정표 행 중 사실 id를 정할 수 없던 것(이전 기록 — 지우지 않는다)
  id INTEGER PRIMARY KEY, run_id TEXT, batch TEXT, verdict TEXT, tier INTEGER, url TEXT, claim_text TEXT, sub TEXT,
  note TEXT, reason TEXT, candidates TEXT, src_file TEXT, line INTEGER
);
CREATE INDEX IF NOT EXISTS ix_verdict_fact ON ev_verdict(fact_id);
CREATE INDEX IF NOT EXISTS ix_verdict_run ON ev_verdict(run_id, batch, fact_id, round);
CREATE TABLE IF NOT EXISTS ev_correction (       -- 교정(추가만 · 전파 없음)
  id INTEGER PRIMARY KEY, fact_id TEXT NOT NULL REFERENCES ev_fact(id), run_id TEXT, old TEXT, new TEXT, reason TEXT,
  verdict_id INTEGER REFERENCES ev_verdict(id), applied_at TEXT
);
CREATE TABLE IF NOT EXISTS ev_research_check (   -- "마지막으로 본 시점"
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, topic TEXT NOT NULL DEFAULT '', last_checked TEXT, run_id TEXT,
  n_facts INTEGER, PRIMARY KEY (subject_type, subject_id, topic)
);

-- ───────────── ③ 거시 ma_ ─────────────
CREATE TABLE IF NOT EXISTS ma_release (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  indicator_code TEXT NOT NULL, period TEXT NOT NULL, actual, unit TEXT, prior, consensus, revised,
  released_at TEXT, next_release TEXT
);
CREATE TABLE IF NOT EXISTS ma_policy (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  jurisdiction TEXT, body TEXT NOT NULL, kind TEXT, title TEXT NOT NULL, decided_at TEXT, deadline TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS ma_policy_target (
  fact_id TEXT NOT NULL REFERENCES ma_policy(fact_id) ON DELETE CASCADE, target_type TEXT, target_id TEXT,
  PRIMARY KEY (fact_id, target_type, target_id)
);

-- ───────────── ④ 시장 상태 mk_ (도구 관측 · live · 검증 큐 밖) ─────────────
CREATE TABLE IF NOT EXISTS mk_bar (               -- 일봉 최신 관측(종목·지수) — (subject, date = 거래소 세션일) 하나
  subject TEXT NOT NULL, date TEXT NOT NULL, open, high, low, close, volume, source TEXT,
  fetched_at TEXT,                                -- 관측 시각(KST ISO) — 이 값을 본 때
  exchange TEXT,                                  -- KRX · NAS/NYS/AMS(v5)
  is_final INTEGER NOT NULL DEFAULT 0 CHECK (is_final IN (0, 1)),   -- 관측 시각 > 그 세션 마감이면 1(v5 · U04 — 거래량이 아니라 시계)
  version INTEGER NOT NULL DEFAULT 1,             -- 값·확정이 바뀐 관측 수(= mk_bar_obs 행 수)
  final_check_at TEXT, final_check_err TEXT,      -- 마감 뒤 재확인했는데 브로커가 그 날 행을 안 준 경우
  PRIMARY KEY (subject, date)
);
CREATE TABLE IF NOT EXISTS mk_bar_obs (           -- 일봉 관측 이력(v5 · 추가만) — "그때 무엇을 알았나"(bars_asof)
  id INTEGER PRIMARY KEY, subject TEXT NOT NULL, date TEXT NOT NULL, open, high, low, close, volume,
  source TEXT, exchange TEXT, observed_at TEXT NOT NULL, is_final INTEGER NOT NULL, version INTEGER NOT NULL,
  fetch_key TEXT, run_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_bar_obs ON mk_bar_obs(subject, date, observed_at);
CREATE TABLE IF NOT EXISTS mk_bar_fetch (         -- 조회 키 — 언제 받았고 그때 어디까지 확정이었나
  subject TEXT NOT NULL, k TEXT NOT NULL, fetched_at TEXT, n INTEGER,
  final_through TEXT,                             -- 받을 때의 마지막 닫힌 세션일(v5 · NULL = 빈 결과 — 다시 묻는다)
  PRIMARY KEY (subject, k)
);
CREATE TABLE IF NOT EXISTS mk_holiday (           -- 거래소 휴장일(v5) — config/holidays.json(사람이 고치는 원본)을 동기화한 사본
  market TEXT NOT NULL, date TEXT NOT NULL, name TEXT, source TEXT, loaded_at TEXT, PRIMARY KEY (market, date)
);
CREATE TABLE IF NOT EXISTS mk_sector_board (
  market TEXT NOT NULL, date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, idx, chg_pct, fetched_at TEXT, body TEXT,
  PRIMARY KEY (market, date, code)
);
CREATE TABLE IF NOT EXISTS mk_breadth (
  index_code TEXT NOT NULL, date TEXT NOT NULL, pct_above_50, pct_above_200, up_sectors INTEGER, down_sectors INTEGER, source TEXT,
  PRIMARY KEY (index_code, date)
);
CREATE TABLE IF NOT EXISTS mk_flow (
  market TEXT NOT NULL, date TEXT NOT NULL, foreign_net, inst_net, retail_net, turnover, short_sale, forced_sale, margin_ratio,
  source TEXT, PRIMARY KEY (market, date)
);
CREATE TABLE IF NOT EXISTS mk_rate (
  tenor TEXT NOT NULL, ts TEXT NOT NULL, yld, chg_bp, spread, source TEXT, PRIMARY KEY (tenor, ts)
);
CREATE TABLE IF NOT EXISTS mk_fx (pair TEXT NOT NULL, ts TEXT NOT NULL, rate, source TEXT, PRIMARY KEY (pair, ts));
CREATE TABLE IF NOT EXISTS mk_vol (code TEXT NOT NULL, date TEXT NOT NULL, value, source TEXT, PRIMARY KEY (code, date));
CREATE TABLE IF NOT EXISTS mk_prob (
  provider TEXT NOT NULL, contract TEXT NOT NULL, as_of TEXT NOT NULL, prob, source TEXT, PRIMARY KEY (provider, contract, as_of)
);
CREATE TABLE IF NOT EXISTS mk_quote (
  subject TEXT NOT NULL, ts TEXT NOT NULL, price, chg_pct, prev_close, high, low, source TEXT, run_id TEXT,
  PRIMARY KEY (subject, ts)
);
CREATE TABLE IF NOT EXISTS mk_obs (               -- 노트가 {{F-id}}로 가리키는 시장 관측(VIX·국채 금리·확률 — 도구 사실 · live)
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  subject TEXT NOT NULL, field TEXT NOT NULL, value, unit TEXT, ts TEXT NOT NULL, source TEXT
);
CREATE VIEW IF NOT EXISTS mk_index_bar AS
  SELECT b.subject, b.date, b.close,
         b.close / (SELECT b5.close FROM mk_bar b5 WHERE b5.subject=b.subject AND b5.date<b.date ORDER BY b5.date DESC LIMIT 1 OFFSET 4) * 100 - 100 AS ret_5,
         b.close / (SELECT b20.close FROM mk_bar b20 WHERE b20.subject=b.subject AND b20.date<b.date ORDER BY b20.date DESC LIMIT 1 OFFSET 19) * 100 - 100 AS ret_20,
         b.close / (SELECT b60.close FROM mk_bar b60 WHERE b60.subject=b.subject AND b60.date<b.date ORDER BY b60.date DESC LIMIT 1 OFFSET 59) * 100 - 100 AS ret_60,
         (SELECT AVG(close) FROM (SELECT close FROM mk_bar m WHERE m.subject=b.subject AND m.date<=b.date ORDER BY m.date DESC LIMIT 20)) AS ma20,
         (SELECT AVG(close) FROM (SELECT close FROM mk_bar m WHERE m.subject=b.subject AND m.date<=b.date ORDER BY m.date DESC LIMIT 60)) AS ma60,
         (SELECT MAX(high) FROM (SELECT high FROM mk_bar m WHERE m.subject=b.subject AND m.date<=b.date ORDER BY m.date DESC LIMIT 60)) AS high_60
  FROM mk_bar b WHERE b.subject LIKE 'IDX:%' OR b.subject IN ('US:SPY', 'KR:069500');

-- ───────────── ⑤ 세부시장 sc_ ─────────────
CREATE TABLE IF NOT EXISTS sc_metric (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, metric_code TEXT NOT NULL, period TEXT, value, unit TEXT, prior, chg, trend TEXT
);
CREATE TABLE IF NOT EXISTS sc_grade (
  sector_code TEXT NOT NULL, run_id TEXT NOT NULL, grade TEXT, reason TEXT, next_event TEXT, resolves_when TEXT, carried_runs INTEGER,
  at TEXT, PRIMARY KEY (sector_code, run_id)
);
CREATE TABLE IF NOT EXISTS sc_grade_fact (
  sector_code TEXT NOT NULL, run_id TEXT NOT NULL, fact_id TEXT NOT NULL REFERENCES ev_fact(id),
  PRIMARY KEY (sector_code, run_id, fact_id)
);

-- ───────────── ⑥ 종목 co_ (사실 상세 — fact_id가 머리표를 가리킨다) ─────────────
CREATE TABLE IF NOT EXISTS co_earnings (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL, market TEXT, period TEXT NOT NULL, basis TEXT, metric TEXT NOT NULL, actual, consensus, prior_year,
  yoy, qoq, surprise_pct, unit TEXT, reported_at TEXT
);
CREATE TABLE IF NOT EXISTS co_guidance (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL, market TEXT, period TEXT NOT NULL, metric TEXT NOT NULL, low, high, mid, prior_low, prior_high,
  direction TEXT, unit TEXT, issued_at TEXT
);
CREATE TABLE IF NOT EXISTS co_consensus (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL, market TEXT, period TEXT NOT NULL, metric TEXT NOT NULL, mean, n_analysts INTEGER, unit TEXT, provider TEXT
);
CREATE TABLE IF NOT EXISTS co_analyst (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL, market TEXT, house TEXT NOT NULL, analyst TEXT, date TEXT NOT NULL, action TEXT, rating TEXT,
  target_old, target_new, currency TEXT, est_period TEXT, est_op, est_rev, thesis_line TEXT, report_url TEXT
);
CREATE TABLE IF NOT EXISTS co_order (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL, market TEXT, counterparty TEXT, product TEXT, amount, currency TEXT, pct_of_sales,
  announced_at TEXT, start_date TEXT, end_date TEXT, backlog_after
);
CREATE TABLE IF NOT EXISTS co_disclosure (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL, market TEXT, doc_no TEXT, type TEXT, filed_at TEXT, title TEXT, url TEXT
);
CREATE TABLE IF NOT EXISTS co_disclosure_value (
  fact_id TEXT NOT NULL REFERENCES co_disclosure(fact_id) ON DELETE CASCADE, ord INTEGER, name TEXT, value, unit TEXT
);
CREATE TABLE IF NOT EXISTS co_action (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL, market TEXT, kind TEXT NOT NULL, amount, currency TEXT, shares, pct, period_start TEXT, period_end TEXT,
  status TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS co_statement (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  speaker TEXT NOT NULL, role TEXT, org TEXT, date TEXT, quote TEXT NOT NULL, topic TEXT, ticker TEXT, market TEXT
);
CREATE TABLE IF NOT EXISTS co_level (            -- 노트가 {{F-id}}로 가리키는 가격 수준(도구 사실)
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL, market TEXT, kind TEXT NOT NULL, value, lo, hi, method TEXT
);
CREATE TABLE IF NOT EXISTS co_level_calc (       -- price_levels.py 계산 결과(mk_bar에서)
  market TEXT NOT NULL, ticker TEXT NOT NULL, kind TEXT NOT NULL, as_of TEXT NOT NULL, value, lo, hi, method TEXT, run_id TEXT,
  bars_asof TEXT, bars_final INTEGER,            -- 계산에 쓴 마지막 확정 세션일 · 전부 확정이었나(v5 · U04)
  PRIMARY KEY (market, ticker, kind, as_of)
);
CREATE TABLE IF NOT EXISTS co_base_rate (        -- base_rates.py 조건부 기저율(mk_bar에서)
  market TEXT NOT NULL, ticker TEXT NOT NULL, condition TEXT NOT NULL, window INTEGER NOT NULL, as_of TEXT NOT NULL,
  n INTEGER, hit_rate, avg_ret, median_ret, base_n INTEGER, base_hit_rate, base_median_ret, run_id TEXT,
  bars_asof TEXT, bars_final INTEGER,            -- 계산에 쓴 마지막 확정 세션일 · 전부 확정이었나(v5 · U04)
  PRIMARY KEY (market, ticker, condition, window, as_of)
);
CREATE TABLE IF NOT EXISTS co_treasury_holding (  -- 자기주식 보유 스냅숏(반기보고서 기준 · 옛 data/treasury_scan.json)
  market TEXT NOT NULL, ticker TEXT NOT NULL, name TEXT, corp_cls TEXT, period TEXT, stlm_dt TEXT NOT NULL,
  issued, treasury, ratio_pct, rcept_no TEXT, scanned TEXT, error TEXT, PRIMARY KEY (market, ticker, stlm_dt)
);
CREATE TABLE IF NOT EXISTS co_event (
  fact_id TEXT PRIMARY KEY REFERENCES ev_fact(id) ON DELETE CASCADE,
  ticker TEXT, market TEXT, kind TEXT NOT NULL, date TEXT NOT NULL, time TEXT, tz TEXT, confirmed INTEGER, summary TEXT
);

-- ───────────── ⑦ 일정 cal_ ─────────────
CREATE TABLE IF NOT EXISTS cal_event (
  rid INTEGER PRIMARY KEY,
  date TEXT, event TEXT, axis TEXT, key TEXT, why TEXT, added TEXT, kind TEXT, note TEXT, kind_fixed, resolved TEXT,
  source TEXT, note_261015 TEXT, fact_id TEXT REFERENCES ev_fact(id),
  extra TEXT, ko TEXT, ord INTEGER, rev INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT,
  issue_date TEXT, source_text TEXT, weekday_token TEXT, confirmed INTEGER   -- 재료 주간표에서 온 일정의 발행일·원 줄·요일(v5 · S02)
);
CREATE INDEX IF NOT EXISTS ix_cal_key ON cal_event(key);
CREATE TABLE IF NOT EXISTS cal_event_list (      -- tickers · axes
  pid INTEGER NOT NULL REFERENCES cal_event(rid) ON DELETE CASCADE, src TEXT, ord INTEGER, value TEXT
);

-- ───────────── ⑨ 판단 jd_ ─────────────
CREATE TABLE IF NOT EXISTS jd_thesis (
  rid INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE, market TEXT, ticker TEXT, name TEXT, excd TEXT,
  axis TEXT, axis_id TEXT, axis_label TEXT, axis_why TEXT, forecast_id TEXT, beneficiary_order INTEGER, parent TEXT,
  expires TEXT, horizon_days INTEGER, thesis TEXT, invalidation TEXT, status TEXT, created TEXT, entered_on TEXT, entry_price,
  price_driver TEXT, price_driver_why TEXT, priced_at_entry INTEGER, expected_upside_pct, increase_when TEXT, research TEXT,
  needs_review INTEGER DEFAULT 0,
  extra TEXT, ko TEXT, ord INTEGER, rev INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS jd_thesis_leg (       -- 진입·청산 다리 — 판정은 check_kind·level 컬럼으로
  pid INTEGER NOT NULL REFERENCES jd_thesis(rid) ON DELETE CASCADE,
  src TEXT NOT NULL,                              -- entry_triggers · exit_triggers · entry_triggers_archived
  ord INTEGER, leg_id TEXT, when_ TEXT, check_expr TEXT, check_kind TEXT, level, size_pct, basis TEXT, stock TEXT,
  fired INTEGER, fired_at TEXT, fired_note TEXT, reach_prob, reach_n INTEGER, extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS jd_thesis_fact (      -- 논지 근거(옛 evidence 칸) · fact_id가 사실을 가리킨다
  pid INTEGER NOT NULL REFERENCES jd_thesis(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  claim TEXT, source_url TEXT, tier TEXT, verdict TEXT, as_of TEXT, fact_check TEXT, added TEXT, fact TEXT, source TEXT, url TEXT,
  fact_id TEXT REFERENCES ev_fact(id), role TEXT, needs_review INTEGER DEFAULT 0, extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS jd_thesis_event (     -- history · corrections
  pid INTEGER NOT NULL REFERENCES jd_thesis(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  ts TEXT, at TEXT, status TEXT, note TEXT, what TEXT, why TEXT, run_id TEXT, extra TEXT, ko TEXT
);

CREATE TABLE IF NOT EXISTS jd_scenario (
  rid INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE, market TEXT, opened TEXT, local_id TEXT, status TEXT, realized INTEGER, acted INTEGER,
  condition TEXT, expected TEXT, response TEXT, opened_by TEXT, axis TEXT, next_check TEXT, price_cond INTEGER,
  invalidation TEXT, actions_backfill TEXT, merged_into TEXT, merged_at TEXT, possible_duplicate_of TEXT,
  needs_review INTEGER DEFAULT 0,
  extra TEXT, ko TEXT, ord INTEGER, rev INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT,
  event_id TEXT REFERENCES jd_event(id),          -- 이 가지가 속한 사건(v6 · S04·J02)
  cond_status TEXT,                               -- structured · text_only · else · delayed
  expected_reaction TEXT                          -- '[추정] 시장 반응'(G03 · 확률 아님)
);
CREATE TABLE IF NOT EXISTS jd_scenario_judgment (
  pid INTEGER NOT NULL REFERENCES jd_scenario(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  when_ TEXT, realized INTEGER, acted INTEGER, note TEXT, auto INTEGER, fact_ids TEXT, needs_review INTEGER DEFAULT 0,
  extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS jd_scenario_response (
  pid INTEGER NOT NULL REFERENCES jd_scenario(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  market TEXT, ticker TEXT, what TEXT, done INTEGER, done_at TEXT, done_note TEXT, added_by TEXT, extra TEXT, ko TEXT,
  noop INTEGER NOT NULL DEFAULT 0                 -- '영향 없음 — …' 대응(대기가 아니다 · v6)
);
CREATE TABLE IF NOT EXISTS jd_scenario_list (    -- sources · aliases
  pid INTEGER NOT NULL REFERENCES jd_scenario(rid) ON DELETE CASCADE, src TEXT, ord INTEGER, value TEXT
);
-- ★ 시나리오 사건·가지·발생·대응(v6 · Codex S04·S05·J02) — 사건 하나에 가지(시나리오 행) 여럿 · 가지 조건은 원자 목록(구조화) ·
--   조건이 다시 일어나면 발생(episode)이 하나 더 · 대응 완료는 발생 단위. 확률은 적지 않는다.
CREATE TABLE IF NOT EXISTS jd_event (
  id TEXT PRIMARY KEY,                            -- EV-<YYMMDD>-<n>
  subject TEXT NOT NULL,                          -- 'KR:010120' · 'US:NVDA' · 'macro:CPI' · 'factor:ai_capex'
  kind TEXT NOT NULL CHECK (kind IN ('earnings', 'macro', 'policy', 'corporate', 'factor', 'price')),
  scheduled TEXT, window_end TEXT,                -- 예정일 · 관측 마감(지연 가지의 next_check)
  source TEXT, opened_by TEXT, status TEXT NOT NULL DEFAULT 'open', closed_at TEXT, created_at TEXT,
  UNIQUE (subject, kind, scheduled)
);
CREATE TABLE IF NOT EXISTS jd_event_metric (
  event_id TEXT NOT NULL REFERENCES jd_event(id) ON DELETE CASCADE, name TEXT NOT NULL,
  domain TEXT NOT NULL CHECK (domain IN ('num', 'enum')), unit TEXT, enum_values TEXT,
  PRIMARY KEY (event_id, name)
);
CREATE TABLE IF NOT EXISTS jd_scenario_cond (      -- 가지 조건 원자(AND) — 시나리오 원장 행의 자식(cond)
  pid INTEGER NOT NULL REFERENCES jd_scenario(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  metric TEXT, op TEXT, value, value_hi, value_text TEXT, unit TEXT, window TEXT, extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS jd_scenario_episode (   -- 가지 조건의 발생 한 번(실현 → 해제)
  id INTEGER PRIMARY KEY, scenario_id TEXT NOT NULL REFERENCES jd_scenario(id) ON DELETE CASCADE, n INTEGER NOT NULL,
  realized_at TEXT NOT NULL, opened_by_run TEXT, observation TEXT, price_reaction TEXT,
  cleared_at TEXT, cleared_by_run TEXT, note TEXT, UNIQUE (scenario_id, n)
);
CREATE TABLE IF NOT EXISTS jd_scenario_response_done (   -- 대응 완료는 발생 단위(같은 조건이 다시 오면 다시 대응한다)
  episode_id INTEGER NOT NULL REFERENCES jd_scenario_episode(id) ON DELETE CASCADE,
  response_key TEXT NOT NULL,                     -- sha1(market|ticker|what)[:12]
  market TEXT NOT NULL, done_at TEXT NOT NULL, done_note TEXT, done_by_run TEXT, order_ref TEXT,
  PRIMARY KEY (episode_id, response_key)
);

-- 요인 상태(1단 `market_map.py exposure --by driver --stamp` — 바스켓 20세션 고점 대비 · 일중폭 중앙값×3 · 같은 요인 규칙 매도)와
-- 요인 판정(시그널 factor_judgments[] — db.py import · 원인·상태·종목별 저점매수/유지/정리) — FB-261017-7(2026-10-17)
CREATE TABLE IF NOT EXISTS mk_factor_state (
  run_id TEXT NOT NULL, driver TEXT NOT NULL, computed_at TEXT, snapshot TEXT, n_members INTEGER, n_with_bars INTEGER,
  index_last, index_peak, peak_date TEXT, drawdown_pct, band_med_pct, band3_pct, fired_dd INTEGER, sells_window TEXT,
  sells_n INTEGER, sells_tickers TEXT, fired_sell INTEGER, fired INTEGER NOT NULL, state TEXT, body TEXT,
  bars_asof TEXT, bars_final INTEGER,            -- 계산에 쓴 마지막 확정 세션일 · 전부 확정이었나(v5 · U04)
  PRIMARY KEY (run_id, driver)
);
CREATE TABLE IF NOT EXISTS mk_factor_state_member (
  run_id TEXT NOT NULL, driver TEXT NOT NULL, ticker TEXT NOT NULL, market TEXT, held INTEGER, sign INTEGER, weight_pct,
  last_close, has_bars INTEGER, PRIMARY KEY (run_id, driver, ticker)
);
CREATE TABLE IF NOT EXISTS jd_factor_judgment (
  run_id TEXT NOT NULL, driver TEXT NOT NULL, scenario_id TEXT, fired_by TEXT, cause TEXT NOT NULL, state TEXT NOT NULL,
  sources TEXT, needs_review INTEGER, extra TEXT, created_at TEXT, PRIMARY KEY (run_id, driver)
);
CREATE TABLE IF NOT EXISTS jd_factor_decision (
  run_id TEXT NOT NULL, driver TEXT NOT NULL, ticker TEXT NOT NULL, thesis_id TEXT, held INTEGER, decision TEXT NOT NULL,
  why TEXT, sources TEXT, extra TEXT, PRIMARY KEY (run_id, driver, ticker),
  FOREIGN KEY (run_id, driver) REFERENCES jd_factor_judgment(run_id, driver) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jd_allocation (
  rid INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE, stamp TEXT, market TEXT, based_on TEXT, target_invested_pct, regime TEXT, cash_reason TEXT,
  cash_release_when TEXT, gap_pct, deployable, recorded_at TEXT, approved_ref TEXT,
  extra TEXT, ko TEXT, ord INTEGER, rev INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS jd_allocation_reserved (
  pid INTEGER NOT NULL REFERENCES jd_allocation(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  thesis_id TEXT, market TEXT, amount, why TEXT, extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS jd_allocation_fact (  -- regime_refs
  allocation_id TEXT NOT NULL, fact_id TEXT NOT NULL REFERENCES ev_fact(id), role TEXT, PRIMARY KEY (allocation_id, fact_id)
);

-- ───────────── ⑩ 집행 ex_ ─────────────
CREATE TABLE IF NOT EXISTS ex_run (
  run_id TEXT PRIMARY KEY, stamp TEXT NOT NULL, market TEXT NOT NULL, account_mode TEXT, session_date TEXT, started TEXT,
  capture_at TEXT, carry_at TEXT, map_at TEXT, note_at TEXT, dispatch_at TEXT, sent_at TEXT, filled_at TEXT, closed_at TEXT,
  facts_new INTEGER, facts_reused INTEGER, facts_repeat INTEGER, verified_rows INTEGER, audit_rounds INTEGER,
  before_news_n INTEGER, material_delay_h, rerender INTEGER DEFAULT 0, status TEXT, note TEXT
);
-- ★ 단계 결과(v4 · Codex U02) — 단계마다 시도 행 · 단계 안 명령(필수/선택 · 종료코드) · 산출물 해시. ex_run.<단계>_at = 성공한 첫 시도의 끝.
CREATE TABLE IF NOT EXISTS ex_stage (
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ex_run(run_id),
  stage TEXT NOT NULL CHECK (stage IN ('capture', 'carry', 'map', 'research', 'note', 'verify', 'audit', 'dispatch', 'no_trade',
                                       'execute', 'fill', 'close')),
  attempt INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'reduced', 'failed', 'unknown')),
  rc INTEGER, started_at TEXT NOT NULL, ended_at TEXT, pid INTEGER, argv TEXT, note TEXT,
  provenance TEXT NOT NULL DEFAULT 'stage' CHECK (provenance IN ('stage', 'migrated')),
  UNIQUE (run_id, stage, attempt)
);
CREATE TABLE IF NOT EXISTS ex_stage_step (
  stage_id INTEGER NOT NULL REFERENCES ex_stage(id) ON DELETE CASCADE, ord INTEGER NOT NULL, name TEXT NOT NULL,
  cmd TEXT, required INTEGER NOT NULL CHECK (required IN (0, 1)), rc INTEGER,
  status TEXT NOT NULL CHECK (status IN ('ok', 'reduced', 'failed', 'skipped')), observed_at TEXT NOT NULL, note TEXT,
  PRIMARY KEY (stage_id, ord)
);
CREATE TABLE IF NOT EXISTS ex_stage_artifact (
  stage_id INTEGER NOT NULL REFERENCES ex_stage(id) ON DELETE CASCADE, role TEXT NOT NULL,
  path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, mtime TEXT NOT NULL, PRIMARY KEY (stage_id, role)
);
CREATE INDEX IF NOT EXISTS ix_stage_run ON ex_stage(run_id, stage, status);
CREATE TABLE IF NOT EXISTS ex_signal (
  run_id TEXT PRIMARY KEY REFERENCES ex_run(run_id), generated_at TEXT, no_trade INTEGER, no_trade_reason TEXT,
  note_lint TEXT, track1 TEXT, path TEXT, sha TEXT
);
-- ★ 시그널 판본(v10 · Codex J03) — 같은 run의 시그널이 다시 쓰여도 판마다 한 행(추가만) · ex_signal은 마지막 판을 가리킨다
CREATE TABLE IF NOT EXISTS ex_signal_version (
  sha TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ex_run(run_id), generated_at TEXT, path TEXT, imported_at TEXT, body TEXT NOT NULL
);
-- 제안 — 시그널 판마다(run, sha, idx) · 덮어쓰지 않는다(v10 · J03). 지금 판은 v_proposal. 목표 기준·기저율(J01)과 논지 구조(G02) 칸.
CREATE TABLE IF NOT EXISTS ex_proposal (
  run_id TEXT NOT NULL REFERENCES ex_run(run_id), sha TEXT NOT NULL, idx INTEGER NOT NULL,
  ticker TEXT, market TEXT, action TEXT, thesis_id TEXT, axis_id TEXT, weight_target_pct, expected_upside_pct, horizon_days,
  confidence, size_why TEXT, concentration_why TEXT, increase_when TEXT, entry TEXT, exits TEXT, risk_notes TEXT, parent TEXT,
  is_refill_candidate INTEGER DEFAULT 0, needs_review INTEGER DEFAULT 0, body TEXT,
  target_level_kind TEXT, target_level_price, target_level_source TEXT, target_chosen_reason TEXT, target_chosen_at TEXT,
  upside_to_target_pct, base_rate_cond TEXT, base_rate_n INTEGER, base_rate_median_pct, base_rate_hit_pct, base_rate_horizon_days INTEGER,
  base_rate_as_of TEXT, mechanism TEXT, priced_in_vs_new TEXT, strongest_counter TEXT, kill_observation TEXT, decision_deadline TEXT,
  PRIMARY KEY (run_id, sha, idx)
);
CREATE TABLE IF NOT EXISTS ex_proposal_fact (
  run_id TEXT NOT NULL, sha TEXT NOT NULL, idx INTEGER NOT NULL, fact_id TEXT NOT NULL REFERENCES ev_fact(id), role TEXT, support TEXT,
  fact_version INTEGER, needs_review INTEGER DEFAULT 0,
  PRIMARY KEY (run_id, sha, idx, fact_id),
  FOREIGN KEY (run_id, sha, idx) REFERENCES ex_proposal(run_id, sha, idx) ON DELETE CASCADE
);
-- 결정 뒤 결과(v10 · J03) — 심사한 매수(승인·거부 모두)를 같은 시계(5·10·20 확정 세션)로 · 확정 봉이 다 있을 때만 기록 · 멱등
CREATE TABLE IF NOT EXISTS ex_outcome (
  screen_id INTEGER NOT NULL REFERENCES ex_screen(id), horizon INTEGER NOT NULL, decision_date TEXT, price_at_decision, bar_date TEXT,
  price_at_horizon, ret_pct, max_up_pct, max_down_pct, bars_final INTEGER, filled INTEGER, filled_qty, avg_fill_price, pnl_net,
  computed_at TEXT,
  -- 실제 체결을 이 결정에 붙인 근거(W05): approval = 심사 회차 → 승인 행 → 주문으로 이었다 · not_accepted = 승인되지 않은 결정(실제 체결 없음 ·
  -- 가상 수익만) · unlinked = 승인됐는데 승인 행·주문을 못 찾았다(filled NULL · 지어내지 않는다)
  fill_link TEXT CHECK (fill_link IS NULL OR fill_link IN ('approval', 'not_accepted', 'unlinked')),
  calc_version INTEGER,                           -- 계산 규칙 판(feedback.OUTCOME_CALC_VERSION) — 낮은 판의 행은 다음 계산이 다시 쓴다
  PRIMARY KEY (screen_id, horizon)
);
CREATE TABLE IF NOT EXISTS ex_hold (
  run_id TEXT NOT NULL REFERENCES ex_run(run_id), ticker TEXT NOT NULL, thesis_id TEXT, why TEXT, PRIMARY KEY (run_id, ticker)
);
CREATE TABLE IF NOT EXISTS ex_hold_fact (
  run_id TEXT NOT NULL, ticker TEXT NOT NULL, fact_id TEXT NOT NULL REFERENCES ev_fact(id), PRIMARY KEY (run_id, ticker, fact_id)
);
-- ★ 집행 장부 다섯 표는 자료형·값 제약을 건다(v8 · Codex R12 — 'not-a-number' 수량·'BAD' 시장·'SIDEWAYS' 방향이 그대로 들어갔다).
--   STRICT = 칸 자료형 강제 · 시장 소문자 · 방향·상태·판정은 쓰는 코드의 어휘만 · 체결 수량 부호 없음 · 승인 행은 수량, 거부 행은 사유.
CREATE TABLE IF NOT EXISTS ex_approval (          -- 추가만
  id INTEGER PRIMARY KEY, run_id TEXT REFERENCES ex_run(run_id),
  pass TEXT NOT NULL CHECK (pass IN ('main', 'stops', 'refill')), idx INTEGER,
  ticker TEXT NOT NULL, market TEXT NOT NULL CHECK (market IN ('kr', 'us')), action TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
  qty INTEGER CHECK (qty IS NULL OR qty > 0), price REAL CHECK (price IS NULL OR price >= 0), source TEXT, reasons TEXT, rejected_why TEXT,
  needs_sell_fill INTEGER NOT NULL DEFAULT 0 CHECK (needs_sell_fill IN (0, 1)),
  rounded_up_to_1 INTEGER NOT NULL DEFAULT 0 CHECK (rounded_up_to_1 IN (0, 1)),
  order_capped INTEGER NOT NULL DEFAULT 0 CHECK (order_capped IN (0, 1)),
  signal_ref TEXT, approved_path TEXT NOT NULL, approved_sha TEXT NOT NULL, body TEXT NOT NULL,
  created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
  -- 판단 재현 식별자(v10 · J03 — 승인 머리에서): 시그널·스냅샷·유니버스·재료 해시 · 코드 커밋·해시
  signal_sha TEXT, snapshot_sha TEXT, universe_sha TEXT, material_sha TEXT, code_commit TEXT, code_sha TEXT,
  screen_pass INTEGER,                            -- 이 승인을 낸 심사 회차(승인 머리 screen.pass · W05 — 심사 행 → 승인 행 → 주문 → 체결의 열쇠)
  CHECK ((rejected_why IS NULL) = (qty IS NOT NULL))
) STRICT;
-- 규율 검사 회차 기억(2026-10-19) — risk_guard가 같은 run 안에서 제안마다 내린 판정. 유효성 사유로 거부된 제안이 고쳐져 다시 오면
-- 그 제안만 '거부 회피 재실행'으로 거부한다(나머지는 그대로 승인·전송). ex_run FK 없음 — 첫 회차 전에는 run 행이 없을 수 있다. 추가만.
CREATE TABLE IF NOT EXISTS ex_screen (
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, pass_no INTEGER NOT NULL, ts TEXT, mode TEXT, ticker TEXT, action TEXT,
  fingerprint TEXT, canon TEXT, ctx TEXT, outcome TEXT, why_class TEXT, why TEXT, limits_sha TEXT, approved_path TEXT,
  signal_sha TEXT                                 -- 그 회차가 읽은 시그널 판(v10 · J03)
);
CREATE INDEX IF NOT EXISTS ix_screen_run ON ex_screen(run_id, pass_no);
CREATE TABLE IF NOT EXISTS ex_order (             -- 추가만(승인 연결 칸 approval_id·approval_link만 나중에 채울 수 있다 — v7)
  id INTEGER PRIMARY KEY, approval_id INTEGER REFERENCES ex_approval(id), run_id TEXT NOT NULL REFERENCES ex_run(run_id),
  ticker TEXT NOT NULL, market TEXT NOT NULL CHECK (market IN ('kr', 'us')), side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
  qty INTEGER NOT NULL CHECK (qty > 0), order_no TEXT, sent_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING', 'SENT', 'UNKNOWN', 'REJECTED', 'UNSENT', 'FAILED', 'FILLED')),
  limit_price REAL CHECK (limit_price IS NULL OR limit_price >= 0), approved_ref TEXT,
  thesis_id TEXT, trigger_id TEXT, fallbacks TEXT, trades_sha TEXT, trade_key TEXT NOT NULL, body TEXT NOT NULL,
  -- ★ 주문↔승인 연결(v7 · Codex R10): 보낸 승인 파일 바이트 해시 · 그 파일 안 순번 · 주문 의도 키(execute.intent_key_of) ·
  --   연결 근거 recorded(해시+순번으로 찾음) | inferred(옛 행 — 파일명·종목·방향이 유일할 때만) | NULL(못 찾음 — 지어내지 않는다)
  approved_sha TEXT, approved_idx INTEGER, intent_key TEXT,
  approval_link TEXT CHECK (approval_link IS NULL OR approval_link IN ('recorded', 'inferred'))
) STRICT;
CREATE INDEX IF NOT EXISTS ix_order_no ON ex_order(run_id, order_no);
CREATE UNIQUE INDEX IF NOT EXISTS ux_order_intent ON ex_order(run_id, intent_key) WHERE intent_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS ex_fill (              -- 추가만 · 관측 이력(주문의 지금 상태 = v_fill_latest) · 수량 부호 없음(방향은 ex_order.side)
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES ex_order(id), run_id TEXT NOT NULL, ticker TEXT NOT NULL,
  market TEXT NOT NULL CHECK (market IN ('kr', 'us')),
  filled_qty INTEGER CHECK (filled_qty IS NULL OR filled_qty >= 0), avg_price REAL CHECK (avg_price IS NULL OR avg_price >= 0),
  limit_price REAL, verdict TEXT NOT NULL CHECK (verdict IN ('FILLED', 'PARTIAL', 'CANCELLED', 'EXPIRED', 'REJECTED', 'UNFILLED', 'UNKNOWN')),
  qty_before INTEGER, qty_after INTEGER, requested INTEGER, settle_date TEXT, at TEXT, body TEXT NOT NULL,
  CHECK (verdict NOT IN ('FILLED', 'PARTIAL') OR filled_qty > 0)
) STRICT;
CREATE TABLE IF NOT EXISTS ex_fill_fx (           -- 결제일 환율(세금 원장) — 추가만
  id INTEGER PRIMARY KEY, fill_id INTEGER NOT NULL REFERENCES ex_fill(id), settle_fx REAL NOT NULL CHECK (settle_fx > 0), source TEXT, at TEXT
) STRICT;
CREATE TABLE IF NOT EXISTS ex_position (          -- 캡처·체결 뒤·stops·probe 보유 스냅샷
  id INTEGER PRIMARY KEY, run_id TEXT, market TEXT, phase TEXT NOT NULL, ticker TEXT, name TEXT, qty, avg_price, price,
  eval_amt, pnl_pct, at TEXT
);
CREATE TABLE IF NOT EXISTS ex_balance (
  id INTEGER PRIMARY KEY, run_id TEXT, market TEXT NOT NULL CHECK (market IN ('kr', 'us')), phase TEXT NOT NULL,
  cash REAL, cash_field TEXT, exchange_rate REAL CHECK (exchange_rate IS NULL OR exchange_rate > 0), exchange_rate_at TEXT,
  deposit_only INTEGER CHECK (deposit_only IS NULL OR deposit_only IN (0, 1)),
  cash_shared INTEGER NOT NULL DEFAULT 0 CHECK (cash_shared IN (0, 1)), buying_power REAL, orderable REAL, equity REAL,
  currency TEXT CHECK (currency IS NULL OR currency IN ('KRW', 'USD')), at TEXT NOT NULL, body TEXT
) STRICT;
CREATE TABLE IF NOT EXISTS ex_equity_point (      -- 자산 곡선(추가만) — journal/equity_curve.jsonl은 렌더본
  rid INTEGER PRIMARY KEY,
  date TEXT, ts TEXT, market TEXT, currency TEXT, equity, cash, n_positions INTEGER, shadow_benchmark_equity,
  cum_pnl_pct, cum_benchmark_pct, excess_pct, day_pnl_pct, costs_today, turnover_today, costs_source TEXT, cum_costs,
  cum_turnover, cost_drag_pct, cum_turnover_x, cash_shared INTEGER, account_mode TEXT,
  extra TEXT, ko TEXT, ord INTEGER, rev INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ex_equity_position (
  pid INTEGER NOT NULL REFERENCES ex_equity_point(rid) ON DELETE CASCADE, src TEXT, ord INTEGER,
  ticker TEXT, name TEXT, qty, price, eval_amt, pnl_pct, avg_price, extra TEXT, ko TEXT
);
CREATE TABLE IF NOT EXISTS ex_position_peak (
  market TEXT NOT NULL, ticker TEXT NOT NULL, peak_price, peak_at TEXT, ord INTEGER, PRIMARY KEY (market, ticker)
);
CREATE TABLE IF NOT EXISTS ex_refill (k TEXT PRIMARY KEY, body TEXT NOT NULL, ord INTEGER, updated_at TEXT);

-- ───────────── 운영 op_ ─────────────
-- jsonl 로그: 행 하나 = 표 행 하나. body는 원본 dict(계획상 op_ 로그는 JSON 칸 허용) · 자주 거르는 칸만 컬럼.
CREATE TABLE IF NOT EXISTS op_incident (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_push (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_test_run (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_fetch (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_stops (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_run_log (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_review (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_universe (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_bridge (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_regret (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_rename (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ex_reversal_event (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_session (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_sector_history (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_sector_watch (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS op_account_switch (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
-- 입출금·환전(계좌 계열 단위 · v7 · Codex R06) — 통합자산 수익률이 이것을 뺀다(portfolio.account_return · Modified Dietz). 계좌로 들어오면 +.
-- fx_convert는 두 행(−KRW, +USD @fx)이 pair_id를 나눈다. 추가만.
CREATE TABLE IF NOT EXISTS op_cash_flow (
  id INTEGER PRIMARY KEY, family TEXT NOT NULL CHECK (family IN ('paper', 'real')), date TEXT NOT NULL, ts TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('deposit', 'withdraw', 'fx_convert')), currency TEXT NOT NULL CHECK (currency IN ('KRW', 'USD')),
  amount REAL NOT NULL CHECK (amount <> 0), fx REAL CHECK (fx IS NULL OR fx > 0), pair_id INTEGER REFERENCES op_cash_flow(id),
  note TEXT, created_at TEXT NOT NULL
) STRICT;
CREATE TRIGGER IF NOT EXISTS tg_switch_noupd BEFORE UPDATE ON op_account_switch BEGIN SELECT RAISE(ABORT, '추가만 한다'); END;
CREATE TABLE IF NOT EXISTS op_feedback (seq INTEGER PRIMARY KEY, ts TEXT, market TEXT, stamp TEXT, rid TEXT, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_feedback_rid ON op_feedback(rid);

CREATE TABLE IF NOT EXISTS op_coverage (k TEXT PRIMARY KEY, body TEXT NOT NULL, ord INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS op_pending (k TEXT PRIMARY KEY, body TEXT NOT NULL, ord INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS op_benchmark_anchor (k TEXT PRIMARY KEY, body TEXT NOT NULL, ord INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS op_current_run (k TEXT PRIMARY KEY, body TEXT NOT NULL, ord INTEGER, updated_at TEXT);

CREATE TABLE IF NOT EXISTS op_audit (             -- 게이트 엔진 승격 원장에서 close 때 복사(엔진 원장은 그대로)
  id INTEGER PRIMARY KEY, run_id TEXT, skill TEXT, cp TEXT, batch TEXT, round INTEGER, status TEXT, lenses TEXT,
  findings_file TEXT, deliverable TEXT, at TEXT, line_sha TEXT UNIQUE, body TEXT,
  reqid TEXT, dir TEXT, deliverable_path TEXT, content_sha TEXT   -- 원장 줄의 정체(v4 · Codex U03 — body에서 옮겨 적는다)
);
CREATE TABLE IF NOT EXISTS op_audit_scope (       -- 감사 행이 이 프로젝트 것인가(v4 · 추가만 — 판정을 고치면 새 행)
  id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL REFERENCES op_audit(id),
  scope TEXT NOT NULL CHECK (scope IN ('project', 'foreign', 'unknown')), project_root TEXT,
  derived_from TEXT NOT NULL CHECK (derived_from IN ('dir', 'deliverable', 'findings_file', 'ts', 'none')),
  reason TEXT NOT NULL, at TEXT NOT NULL, by TEXT NOT NULL CHECK (by IN ('import', 'migration', 'operator'))
);
CREATE TABLE IF NOT EXISTS op_real_check (        -- 실전 점검표 ①~⑫
  run_id TEXT NOT NULL, item TEXT NOT NULL, status TEXT, note TEXT, at TEXT, PRIMARY KEY (run_id, item)
);
CREATE TABLE IF NOT EXISTS op_loss_review (       -- 토요일 손실 판정(일시적·오판·관찰) — 키는 (주, 논지): 한 종목에 논지가 둘일 수 있다
  week TEXT NOT NULL, thesis_id TEXT NOT NULL, market TEXT, ticker TEXT, verdict TEXT, basis TEXT, net_realized, body TEXT,
  PRIMARY KEY (week, thesis_id)
);

-- ───────────── 추가만 표 보호 ─────────────
CREATE TRIGGER IF NOT EXISTS tg_approval_noupd BEFORE UPDATE ON ex_approval BEGIN SELECT RAISE(ABORT, 'ex_approval은 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_approval_nodel BEFORE DELETE ON ex_approval BEGIN SELECT RAISE(ABORT, 'ex_approval은 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_screen_noupd BEFORE UPDATE ON ex_screen BEGIN SELECT RAISE(ABORT, 'ex_screen은 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_screen_nodel BEFORE DELETE ON ex_screen BEGIN SELECT RAISE(ABORT, 'ex_screen은 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_order_noupd BEFORE UPDATE OF id, run_id, ticker, market, side, qty, order_no, sent_at, status, limit_price,
  approved_ref, thesis_id, trigger_id, fallbacks, trades_sha, trade_key, body, approved_sha, approved_idx, intent_key ON ex_order
  BEGIN SELECT RAISE(ABORT, 'ex_order는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_cashflow_noupd BEFORE UPDATE ON op_cash_flow BEGIN SELECT RAISE(ABORT, 'op_cash_flow는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_cashflow_nodel BEFORE DELETE ON op_cash_flow BEGIN SELECT RAISE(ABORT, 'op_cash_flow는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_order_nodel BEFORE DELETE ON ex_order BEGIN SELECT RAISE(ABORT, 'ex_order는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_fill_noupd BEFORE UPDATE ON ex_fill BEGIN SELECT RAISE(ABORT, 'ex_fill은 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_fill_nodel BEFORE DELETE ON ex_fill BEGIN SELECT RAISE(ABORT, 'ex_fill은 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_fillfx_noupd BEFORE UPDATE ON ex_fill_fx BEGIN SELECT RAISE(ABORT, 'ex_fill_fx는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_equity_noupd BEFORE UPDATE ON ex_equity_point BEGIN SELECT RAISE(ABORT, 'ex_equity_point는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_equity_nodel BEFORE DELETE ON ex_equity_point WHEN (SELECT v FROM sys_meta WHERE k='allow_equity_reset') IS NOT '1'
  BEGIN SELECT RAISE(ABORT, 'ex_equity_point는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_fver_noupd BEFORE UPDATE ON ev_fact_version BEGIN SELECT RAISE(ABORT, 'ev_fact_version은 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_verdict_noupd BEFORE UPDATE ON ev_verdict BEGIN SELECT RAISE(ABORT, 'ev_verdict는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_verdict_nodel BEFORE DELETE ON ev_verdict BEGIN SELECT RAISE(ABORT, 'ev_verdict는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_corr_noupd BEFORE UPDATE ON ev_correction BEGIN SELECT RAISE(ABORT, 'ev_correction은 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_audit_noupd BEFORE UPDATE ON op_audit BEGIN SELECT RAISE(ABORT, 'op_audit는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_incident_noupd BEFORE UPDATE ON op_incident BEGIN SELECT RAISE(ABORT, '추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_reversal_noupd BEFORE UPDATE ON ex_reversal_event BEGIN SELECT RAISE(ABORT, '추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_reversal_nodel BEFORE DELETE ON ex_reversal_event BEGIN SELECT RAISE(ABORT, '추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_stops_noupd BEFORE UPDATE ON op_stops BEGIN SELECT RAISE(ABORT, '추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_regret_noupd BEFORE UPDATE ON op_regret BEGIN SELECT RAISE(ABORT, '추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_fetch_noupd BEFORE UPDATE ON ev_fetch BEGIN SELECT RAISE(ABORT, 'ev_fetch는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_bar_obs_noupd BEFORE UPDATE ON mk_bar_obs BEGIN SELECT RAISE(ABORT, 'mk_bar_obs는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_bar_obs_nodel BEFORE DELETE ON mk_bar_obs BEGIN SELECT RAISE(ABORT, 'mk_bar_obs는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_audit_scope_noupd BEFORE UPDATE ON op_audit_scope BEGIN SELECT RAISE(ABORT, 'op_audit_scope는 추가만 한다'); END;
CREATE TRIGGER IF NOT EXISTS tg_audit_scope_nodel BEFORE DELETE ON op_audit_scope BEGIN SELECT RAISE(ABORT, 'op_audit_scope는 추가만 한다'); END;
-- run의 계좌 계열은 시작 때 정해지면 바뀌지 않는다(v4 · Codex R09 — 같은 계열 안의 라벨 변화 real_readonly↔real은 허용)
CREATE TRIGGER IF NOT EXISTS tg_run_mode_fixed BEFORE UPDATE OF account_mode ON ex_run
  WHEN OLD.account_mode IS NOT NULL
   AND (CASE WHEN OLD.account_mode = 'paper' THEN 'paper' ELSE 'real' END)
       <> (CASE WHEN COALESCE(NEW.account_mode, 'paper') = 'paper' THEN 'paper' ELSE 'real' END) BEGIN
  SELECT RAISE(ABORT, 'run의 계좌 계열은 바뀌지 않는다(같은 날 다른 계좌로 다시 시작할 수 없다)');
END;
CREATE TRIGGER IF NOT EXISTS tg_fetch_nodel BEFORE DELETE ON ev_fetch BEGIN SELECT RAISE(ABORT, 'ev_fetch는 추가만 한다'); END;

-- ───────────── 사실이 바뀌면 관련 행이 따라온다 ─────────────
-- 사실 값이 바뀌면 판(rev)과 그때 값을 남긴다 — 승인·체결은 그 판을 가리킨다.
CREATE TRIGGER IF NOT EXISTS tg_fact_version AFTER UPDATE ON ev_fact
  WHEN OLD.rev IS NOT NEW.rev
   AND NOT EXISTS (SELECT 1 FROM ev_fact_version WHERE fact_id = OLD.id AND version = OLD.rev) BEGIN
  INSERT INTO ev_fact_version (fact_id, version, snapshot, changed_at)
  VALUES (OLD.id, OLD.rev, json_object('as_of', OLD.as_of, 'quote', OLD.quote, 'url', OLD.url, 'legacy_text', OLD.legacy_text,
          'verified_status', OLD.verified_status, 'tier', OLD.tier), strftime('%Y-%m-%dT%H:%M:%S', 'now'));
END;
-- 판정이 MISMATCH면 그 사실을 가리킨 논지·시나리오 판정·제안·참조에 재검토 표시
CREATE TRIGGER IF NOT EXISTS tg_verdict_review AFTER INSERT ON ev_verdict
  WHEN NEW.verdict = 'MISMATCH' BEGIN
  UPDATE ev_fact SET needs_review = 1 WHERE id = NEW.fact_id;
  UPDATE ex_proposal_fact SET needs_review = 1 WHERE fact_id = NEW.fact_id;
  UPDATE ex_proposal SET needs_review = 1 WHERE (run_id, sha, idx) IN (SELECT run_id, sha, idx FROM ex_proposal_fact WHERE fact_id = NEW.fact_id);
  UPDATE jd_thesis_fact SET needs_review = 1 WHERE fact_id = NEW.fact_id;
  UPDATE jd_thesis SET needs_review = 1 WHERE rid IN (SELECT pid FROM jd_thesis_fact WHERE fact_id = NEW.fact_id);
  UPDATE ev_claim_ref SET needs_review = 1 WHERE fact_id = NEW.fact_id;
  UPDATE jd_scenario_judgment SET needs_review = 1
    WHERE json_valid(fact_ids) AND EXISTS (SELECT 1 FROM json_each(jd_scenario_judgment.fact_ids) WHERE value = NEW.fact_id);
END;

-- ───────────── 뷰 ─────────────
CREATE VIEW IF NOT EXISTS v_calendar AS
  SELECT 'cal' AS origin, c.date, c.kind, c.event AS title, c.axis, c.key, c.fact_id FROM cal_event c
  UNION ALL SELECT 'co_event', e.date, e.kind, COALESCE(e.ticker || ' ', '') || COALESCE(e.summary, e.kind), NULL, 'F:' || e.fact_id, e.fact_id FROM co_event e
  UNION ALL SELECT 'ma_release', r.next_release, 'macro', r.indicator_code || ' 다음 발표', NULL, 'F:' || r.fact_id, r.fact_id FROM ma_release r WHERE r.next_release IS NOT NULL
  UNION ALL SELECT 'ma_policy', p.deadline, 'policy', p.body || ' ' || p.title, NULL, 'F:' || p.fact_id, p.fact_id FROM ma_policy p WHERE p.deadline IS NOT NULL;

CREATE VIEW IF NOT EXISTS v_run_timing AS
  SELECT run_id, stamp, market, account_mode, capture_at, sent_at, filled_at, closed_at,
         ROUND((julianday(sent_at) - julianday(capture_at)) * 1440, 1) AS capture_to_send_min,
         ROUND((julianday(closed_at) - julianday(filled_at)) * 1440, 1) AS post_fill_min,
         facts_new, facts_reused, facts_repeat, verified_rows, audit_rounds, before_news_n, material_delay_h
  FROM ex_run;

CREATE VIEW IF NOT EXISTS v_exposure AS            -- 보유 × 요인(축의 driver)
  SELECT p.market, p.ticker, p.qty, p.eval_amt, t.id AS thesis_id, a.id AS axis_id, a.driver AS factor
  FROM ex_position p
  LEFT JOIN jd_thesis t ON t.ticker = p.ticker AND t.status IN ('open', 'active', 'entered', 'held')
  LEFT JOIN dim_axis a ON a.id = COALESCE(t.axis_id, t.forecast_id)
  WHERE p.run_id = (SELECT run_id FROM ex_position p2 WHERE p2.market = p.market ORDER BY id DESC LIMIT 1)
    AND p.phase = (SELECT phase FROM ex_position p3 WHERE p3.market = p.market ORDER BY id DESC LIMIT 1)
  GROUP BY p.market, p.ticker;

-- ★ 체결은 사건이 아니라 관측이다(v7 · Codex R11) — 증권사 조회는 주문마다 누적 체결을 준다. ex_fill은 관측 이력(추가만)이고
--   주문의 지금 상태는 마지막 관측이다. 행을 합치지 않는다(PARTIAL 1 → FILLED 2는 2주지 3주가 아니다). filled_qty는 부호 없음(방향은 ex_order.side).
CREATE VIEW IF NOT EXISTS v_fill_latest AS
  SELECT f.* FROM ex_fill f WHERE f.id = (SELECT MAX(g.id) FROM ex_fill g WHERE g.order_id = f.order_id);
CREATE VIEW IF NOT EXISTS v_tax AS                 -- 미국 체결 × 결제일 환율(해외주식 양도세 원화 환산) — 주문마다 마지막 관측 · 환율은 주문 단위
  SELECT f.id AS fill_id, f.order_id, f.run_id, f.ticker, f.filled_qty, f.avg_price, f.settle_date,
         (SELECT x.settle_fx FROM ex_fill_fx x JOIN ex_fill g ON g.id = x.fill_id
           WHERE g.order_id = f.order_id ORDER BY x.id DESC LIMIT 1) AS settle_fx,
         o.side
  FROM v_fill_latest f LEFT JOIN ex_order o ON o.id = f.order_id
  WHERE f.market = 'us' AND f.verdict IN ('FILLED', 'PARTIAL') AND COALESCE(f.filled_qty, 0) > 0;

CREATE VIEW IF NOT EXISTS v_stage_last_ok AS       -- 단계마다 마지막 성공(ok·reduced) 시도
  SELECT s.* FROM ex_stage s
  WHERE s.id = (SELECT MAX(id) FROM ex_stage WHERE run_id = s.run_id AND stage = s.stage AND status IN ('ok', 'reduced'));

CREATE VIEW IF NOT EXISTS v_audit AS               -- 감사 행 + 마지막 범위 판정(foreign은 집계에서 뺀다 — db.audit_rows)
  SELECT a.*, sc.scope, sc.project_root, sc.reason AS scope_reason FROM op_audit a
  LEFT JOIN op_audit_scope sc ON sc.id = (SELECT MAX(id) FROM op_audit_scope WHERE audit_id = a.id);

CREATE VIEW IF NOT EXISTS v_verify_queue AS        -- 검증 큐의 기본 조건(run별 필터는 facts.py가 붙인다) · v3: 검증 뒤 값이 바뀐 사실도
  SELECT f.* FROM ev_fact f
  WHERE COALESCE(f.valid_class, '') NOT IN ('live', 'interp')
    AND (f.verified_status IN ('none', 'expired') OR COALESCE(f.verified_rev, 0) < COALESCE(f.value_rev, 1));

CREATE VIEW IF NOT EXISTS v_proposal AS            -- 그 run의 지금 시그널 판 제안(v10 · ex_signal.sha) · ord_ = 넣은 순서
  SELECT p.rowid AS ord_, p.* FROM ex_proposal p JOIN ex_signal s ON s.run_id = p.run_id AND s.sha = p.sha;
CREATE VIEW IF NOT EXISTS v_proposal_fact AS
  SELECT pf.rowid AS ord_, pf.* FROM ex_proposal_fact pf JOIN ex_signal s ON s.run_id = pf.run_id AND s.sha = pf.sha;

CREATE VIEW IF NOT EXISTS v_forecast_outcome AS    -- 전망 → 수혜 → 논지 → 주문 → 체결(v9 · Codex G04 · 지금 계산 가능한 진단 층)
  SELECT a.id AS axis_id, a.status, a.opened, a.judged_at, a.excess_pct, a.judge_asof, b.ticker, b.bord, b.priced, b.priced_at,
         b.measure_status, b.judge_ret_pct, t.id AS thesis_id,
         (SELECT COUNT(*) FROM ex_order o WHERE o.thesis_id = t.id) AS n_orders,
         (SELECT COALESCE(SUM(f.filled_qty), 0) FROM ex_order o JOIN v_fill_latest f ON f.order_id = o.id
           WHERE o.thesis_id = t.id AND f.verdict IN ('FILLED', 'PARTIAL')) AS filled_qty
  FROM dim_axis a JOIN jd_axis_beneficiary b ON b.pid = a.rid
  LEFT JOIN jd_thesis t ON t.ticker = b.ticker AND (t.axis_id = a.id OR t.forecast_id = a.id)
  WHERE a.direction IS NOT NULL;
