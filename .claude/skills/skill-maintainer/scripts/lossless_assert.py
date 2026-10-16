# -*- coding: utf-8 -*-
"""스킬 구조 개편 무손실 검증 — before(백업) vs after 두 파일의 '비어있지 않은 줄 다중집합' 대조.

순수 재배치(reorg)면 removed=0 이어야 한다(추가된 배너/헤더만 added로 나옴).
의도적 편집(de-cryptify·리워딩 등)이 섞이면 removed가 0이 아닐 수 있으니, 그때는
removed의 각 줄이 '내가 의도한 변경'과 1:1 대응하는지 사람이 확인한다.

사용: python lossless_assert.py <before.md> <after.md>
종료코드: removed=0 → 0(클린), removed>0 → 1(검토 필요)
"""
import sys, collections

try:  # PowerShell 콘솔 cp949 mojibake 방지(em-dash 등) — 출력 UTF-8 강제
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def lines_ms(path):
    ms = collections.Counter()
    with open(path, encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if s:
                ms[s] += 1
    return ms


def main():
    if len(sys.argv) < 3:
        print("usage: python lossless_assert.py <before> <after>")
        sys.exit(2)
    before, after = lines_ms(sys.argv[1]), lines_ms(sys.argv[2])
    removed = before - after   # before엔 있고 after엔 없음(또는 횟수 감소) = 유실 위험
    added = after - before     # after에만 = 배너/신규/편집결과
    print("[before] %d non-empty lines  /  [after] %d" % (sum(before.values()), sum(after.values())))
    if not removed:
        print("OK -- 유실 0 (before의 모든 줄이 after에 보존됨).  added(배너/신규): %d 줄" % sum(added.values()))
    else:
        print("WARNING -- REMOVED %d 줄. 순수 reorg면 0이어야 함. 아래 각 줄이 *의도한 편집*인지 확인:" % sum(removed.values()))
        for line, n in removed.most_common():
            print("  -[%d] %s" % (n, line[:160]))
    if added:
        print("\nADDED %d 줄(배너/신규/편집결과, 상위 40):" % sum(added.values()))
        for line, n in added.most_common(40):
            print("  +[%d] %s" % (n, line[:160]))
    sys.exit(1 if removed else 0)


if __name__ == "__main__":
    main()
