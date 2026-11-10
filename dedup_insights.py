"""
insights.json 후처리: 같은 내용을 다룬 중복 기사(문장)를 1건만 남긴다. (모든 기업 공통)

- 대상: companies.<key>.global[].items(국가별 근거 기사), companies.<key>.ipo.signals(IPO 신호). 투자자 목록은 건드리지 않는다.
- 판정: 문장을 정규화(공백·기호 제거, 흔한 조사 제거)한 뒤 글자 2-gram 유사도가 기준(0.62) 이상이면 같은 이야기로 본다.
        예) '…첫 오프라인 매장 2곳을 열며 해외 시장에 진출했다' / '…첫 매장 2곳을 열며 해외 오프라인 시장에 진출했다'
- 남기는 기사: 확정(done) > 계획, 그다음 최신 날짜, 그다음 먼저 나온 항목.
- 같은 국가(또는 같은 목록) 안에서만 비교하므로 서로 다른 국가의 기사는 영향받지 않는다.
- 제거 후 '계획/근거 약함' 상태의 국가는 남은 서로 다른 기사 수로 단계(계획 ↔ 근거 약함)를 다시 계산한다(중복으로 부풀려진 '계획' 방지).
- 외부 호출 없음. 사용: python dedup_insights.py [insights.json]   /   python dedup_insights.py --selftest
"""
import json, re, sys
from pathlib import Path

THRESHOLD = 0.62
_JOSA = re.compile(r'(은|는|이|가|을|를|의|에|도|와|과|로|으로)(?=\s|$)')


def norm(t):
    t = re.sub(r'<[^>]+>', '', str(t or ''))
    t = _JOSA.sub('', t)
    return re.sub(r'[^0-9A-Za-z가-힣]', '', t).lower()


def grams(t):
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def similar(a, b):
    ga, gb = grams(norm(a)), grams(norm(b))
    if not ga or not gb:
        return False
    return len(ga & gb) / len(ga | gb) >= THRESHOLD


def _text(it):
    return it.get('text') or it.get('hl') or ''


def dedup_list(items):
    """유사 기사 묶음마다 1건만 남기고 원래 순서 유지. 반환: (새 목록, 제거 건수)"""
    def rank(it):
        return (0 if it.get('lvl') == 'done' else 1, '' if not it.get('date') else '')
    keep = []
    for it in items:
        dup = next((k for k in keep if (it.get('url') and it.get('url') == k.get('url')) or similar(_text(it), _text(k))), None)
        if dup is None:
            keep.append(it)
            continue
        better = (it.get('lvl') == 'done' and dup.get('lvl') != 'done') or \
                 (it.get('lvl') == dup.get('lvl') and str(it.get('date', '')) > str(dup.get('date', '')))
        if better:
            keep[keep.index(dup)] = it
    kept_ids = {id(x) for x in keep}
    return [x for x in items if id(x) in kept_ids], len(items) - len(keep)


def walk(data):
    """대상 목록만 처리: 국가별 근거(global[].items)와 IPO 신호(ipo.signals).
    투자자 목록은 한 기사에서 투자사 여러 곳이 나올 수 있어 제외한다."""
    removed = 0
    for c in (data.get('companies') or {}).values():
        if not isinstance(c, dict):
            continue
        for g in c.get('global') or []:
            if isinstance(g, dict) and isinstance(g.get('items'), list):
                g['items'], r = dedup_list(g['items'])
                removed += r
        sig = (c.get('ipo') or {}).get('signals')
        if isinstance(sig, list):
            c['ipo']['signals'], r = dedup_list(sig)
            removed += r
    return removed


def fix_levels(comp):
    for g in comp.get('global', []) or []:
        items = g.get('items') or []
        if not items or g.get('level') == 'confirmed':
            if items:
                g['date'] = max(str(x.get('date', '')) for x in items)
            continue
        done = {x.get('url') or x.get('hl') for x in items if x.get('lvl') == 'done'}
        plan = {x.get('url') or x.get('hl') for x in items if x.get('lvl') != 'done'} - done
        g['n_done'], g['n_plan'] = len(done), len(plan)
        if not done:
            g['level'] = 'planned' if len(plan) >= 2 else 'weak'
            if g.get('tag') in ('planned', 'weak'):
                g['tag'] = g['level']
        g['date'] = max(str(x.get('date', '')) for x in items)


def process(data):
    removed = walk(data)
    for c in (data.get('companies') or {}).values():
        if isinstance(c, dict):
            fix_levels(c)
    return removed


def selftest():
    a = '무신사는 지난해 12월 중국 상하이에 첫 오프라인 매장 2곳을 열며 해외 시장에 진출했다'
    b = '무신사는 지난해 12월 중국 상하이에 첫 매장 2곳을 열며 해외 오프라인 시장에 진출했다'
    c = '무신사는 지난해 12월 중국 상하이에 매장 2곳을 열며 해외 오프라인시장에 진출했다'
    d = '무신사, 일본 법인 설립해 현지 사업 본격화'
    assert similar(a, b) and similar(a, c) and similar(b, c) and not similar(a, d)
    items = [{'text': a, 'url': 'u1', 'date': '2026.11', 'lvl': 'done'}, {'text': b, 'url': 'u2', 'date': '2026.11', 'lvl': 'done'},
             {'text': c, 'url': 'u3', 'date': '2026.11', 'lvl': 'done'}, {'text': d, 'url': 'u4', 'date': '2026.10', 'lvl': 'done'}]
    out, r = dedup_list(items)
    assert r == 2 and [x['url'] for x in out] == ['u1', 'u4']
    # 계획 중복은 근거 약함으로 되돌림
    p1, p2 = '컬리, 미국 진출 추진한다', '컬리는 미국 진출을 추진한다'
    data = {'companies': {'kurly': {'global': [{'region': '미국', 'level': 'planned', 'tag': 'planned', 'items': [
        {'text': p1, 'hl': p1, 'url': 'a', 'date': '2026.11', 'lvl': 'plan'}, {'text': p2, 'hl': p2, 'url': 'b', 'date': '2026.11', 'lvl': 'plan'}]}]}}}
    assert process(data) == 1
    g = data['companies']['kurly']['global'][0]
    assert len(g['items']) == 1 and g['level'] == 'weak' and g['tag'] == 'weak' and g['n_plan'] == 1
    print('중복 제거 로직 OK')


def main():
    if '--selftest' in sys.argv:
        selftest()
        return
    p = Path(next((a for a in sys.argv[1:] if not a.startswith('-')), 'insights.json'))
    data = json.loads(p.read_text('utf-8'))
    n = process(data)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), 'utf-8')
    print(f'중복 기사 {n}건 제거: {p}')


if __name__ == '__main__':
    main()
