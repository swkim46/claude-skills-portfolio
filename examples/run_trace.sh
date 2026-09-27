#!/usr/bin/env bash
# examples/사고흐름_추적.md 의 "주문 규칙 검사" 부분을 재현한다.
# 저장소의 ai-trading-bot/ 와 .claude/ 를 임시 폴더에 복사하고, 시연용 가상 파일을 그 위에 얹어
# risk_guard.py 를 실제로 실행한 뒤 출력을 _synthetic/risk_guard_출력.txt 에 저장한다.
# 저장소 안에는 아무것도 남기지 않는다(임시 폴더에서만 실행). 증권사 키·네트워크 불필요.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYN="$ROOT/examples/_synthetic"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp -R "$ROOT/ai-trading-bot" "$WORK/ai-trading-bot"
cp -R "$ROOT/.claude" "$WORK/.claude"
rm -rf "$WORK/ai-trading-bot/__pycache__"
cp "$SYN/watchlist.json"   "$WORK/ai-trading-bot/config/watchlist.json"
cp "$SYN/market_map.json"  "$WORK/ai-trading-bot/journal/market_map.json"
cp "$SYN/theses.json"      "$WORK/ai-trading-bot/journal/theses.json"
cp "$SYN/snapshot_kr.json" "$WORK/ai-trading-bot/data/snapshot_DEMO_kr.json"

# 시그널의 generated_at 을 지금(KST)으로 다시 찍는다 — 180분 지난 시그널은 코드가 통째로 거부한다.
python3 - "$SYN/signal_kr.json" "$WORK/ai-trading-bot/signals/signal_DEMO_kr.json" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
src, dst = sys.argv[1], sys.argv[2]
sig = json.load(open(src, encoding="utf-8"))
sig["generated_at"] = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
json.dump(sig, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY

OUT="$SYN/risk_guard_출력.txt"
{
  echo "# 생성: $(date '+%Y-%m-%d %H:%M %Z') · examples/run_trace.sh · 입력은 전부 가상 데이터"
  echo "\$ python3 risk_guard.py signals/signal_DEMO_kr.json --snapshot data/snapshot_DEMO_kr.json --out signals/approved_DEMO_kr.json"
  echo
  cd "$WORK/ai-trading-bot"
  python3 risk_guard.py signals/signal_DEMO_kr.json --snapshot data/snapshot_DEMO_kr.json --out signals/approved_DEMO_kr.json 2>&1 || echo "(종료 코드 $?)"
} | tee "$OUT"
cp "$WORK/ai-trading-bot/signals/approved_DEMO_kr.json" "$SYN/approved_kr.json" 2>/dev/null || true
echo
echo "저장: $OUT"
