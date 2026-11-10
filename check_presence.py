"""
presence.json 형식 점검 — 직접 수정하는 파일이라 쉼표·따옴표 실수가 나기 쉽다.
오류가 있으면 위치(줄·열)와 주변 내용을 출력하고 종료 코드 1로 끝낸다(→ Actions 가 빨간색으로 멈추고 insights.json 은 바뀌지 않는다).
사용법: python check_presence.py [presence.json]
"""
import json, sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else 'presence.json')
if not p.exists():
    print(f'{p} 없음 — 기준 정보 없이 진행합니다.')
    sys.exit(0)
txt = p.read_text('utf-8')
try:
    d = json.loads(txt)
except json.JSONDecodeError as e:
    lines = txt.splitlines()
    print(f'❌ {p} 형식 오류: {e.msg} (줄 {e.lineno}, 열 {e.colno})')
    for n in range(max(1, e.lineno - 2), min(len(lines), e.lineno + 1) + 1):
        print(f'{"→" if n == e.lineno else " "} {n:4d} | {lines[n - 1]}')
    print('  → 바로 앞 항목 끝에 쉼표(,)가 빠졌거나 마지막 항목 뒤에 쉼표가 남아 있지 않은지, 따옴표(")가 닫혔는지, 숫자에 쉼표(3,535)가 없는지 확인하세요.')
    sys.exit(1)
errs = []
if not isinstance(d, dict):
    errs.append('최상위는 { } 형태여야 합니다.')
else:
    for k, lst in (d.get('_investors') or {}).items():
        if not isinstance(lst, list):
            errs.append(f'_investors.{k} 는 [ ] 목록이어야 합니다.')
            continue
        for i, r in enumerate(lst, 1):
            if not isinstance(r, dict) or not str(r.get('name', '')).strip():
                errs.append(f'_investors.{k} {i}번째 항목에 name 이 없습니다.')
if errs:
    print('❌ presence.json 구조 오류:')
    for x in errs:
        print('  -', x)
    sys.exit(1)
print(f'✅ presence.json 형식 정상 (투자자 기준 정보 {sum(len(v) for v in (d.get("_investors") or {}).values())}행)')
