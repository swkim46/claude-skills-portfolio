#!/usr/bin/env bash
# examples/가상_사례_판단_과정.md 의 "주문 제안과 규칙 검사" 부분을 재현한다.
# 저장소의 ai-trading-bot/ 와 .claude/ 를 임시 폴더에 복사하고, 그 안의 빈 임시 저장소(SQLite)에 시연용 가상 데이터를
# 넣은 뒤(_synthetic/seed_demo.py) risk_guard.py 를 실제로 실행해 출력을 _synthetic/risk_guard_출력.txt 에 저장한다.
# 저장소 안에는 아무것도 남기지 않는다(임시 폴더에서만 실행). 증권사 키·네트워크 불필요. Python 3.9 이상.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYN="$ROOT/examples/_synthetic"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STAMP="261012"   # 시연의 가상 실행일(2026-10-12 월) — 파일 이름과 run id에 쓰인다

cp -R "$ROOT/ai-trading-bot" "$WORK/ai-trading-bot"
cp -R "$ROOT/.claude" "$WORK/.claude"
find "$WORK" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -f "$WORK/ai-trading-bot/journal/trading.db"            # 사본에 저장소가 있어도 쓰지 않는다 — 아래 AITB_DB가 빈 임시 저장소를 가리킨다
mkdir -p "$WORK/ai-trading-bot/demo_in/journal" "$WORK/ai-trading-bot/demo_in/config" "$WORK/ai-trading-bot/data" "$WORK/ai-trading-bot/signals" "$WORK/db"
cp "$SYN/watchlist.json"   "$WORK/ai-trading-bot/demo_in/config/watchlist.json"
cp "$SYN/market_map.json"  "$WORK/ai-trading-bot/demo_in/journal/market_map.json"
cp "$SYN/theses.json"      "$WORK/ai-trading-bot/demo_in/journal/theses.json"
cp "$SYN/snapshot_kr.json" "$WORK/ai-trading-bot/data/snapshot_${STAMP}_kr.json"

# 모든 사실·판단·집행 기록은 저장소 한 파일에 있다 — 시연은 임시 폴더의 빈 저장소를 쓴다(실제 운영 저장소는 열지 않는다).
export AITB_DB="$WORK/db/trading.db"

OUT="$SYN/risk_guard_출력.txt"
{
  echo "# 생성: $(date '+%Y-%m-%d %H:%M %Z') · examples/run_trace.sh · 입력은 전부 가상 데이터 · 저장소는 임시 폴더의 빈 SQLite"
  echo "\$ python3 examples/_synthetic/seed_demo.py <작업 사본> examples/_synthetic ${STAMP}"
  cd "$WORK/ai-trading-bot"
  python3 "$SYN/seed_demo.py" "$WORK/ai-trading-bot" "$SYN" "$STAMP" 2>&1
  echo
  echo "\$ python3 risk_guard.py signals/signal_${STAMP}_kr.json --snapshot data/snapshot_${STAMP}_kr.json --out signals/approved_${STAMP}_kr.json"
  echo
  python3 risk_guard.py "signals/signal_${STAMP}_kr.json" --snapshot "data/snapshot_${STAMP}_kr.json" --out "signals/approved_${STAMP}_kr.json" 2>&1 || echo "(종료 코드 $?)"
} | tee "$OUT"
cp "$WORK/ai-trading-bot/signals/approved_${STAMP}_kr.json" "$SYN/approved_kr.json" 2>/dev/null || true
echo
echo "저장: $OUT"
