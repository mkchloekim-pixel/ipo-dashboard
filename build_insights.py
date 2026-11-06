"""
news.json(네이버 뉴스 자동 수집 결과) → insights.json 자동 생성·누적

- IPO 신호 : 기사에서 진행 단계(Pre-IPO / 주관사 선정 / 예심 청구 / 예심 승인 / 공모·수요예측 / 상장 완료)와
             지연·철회 신호를 키워드로 분류해 날짜순으로 누적한다. (뉴스가 5건만 유지돼도 이력은 남음)
- 글로벌   : 해외 사업 문맥(진출·수출·입점·현지법인 등)이 있는 문장에서 국가 / 파트너사 / 상품을 추출해
             국가별 핵심 문장 1~3개로 요약·누적한다.
- 규칙 기반(키워드) 분류이므로 오분류가 있을 수 있다. 화면에는 근거 기사 링크를 함께 표시한다.
- 외부 서비스 호출 없음(로컬 파일만 읽고 씀). 키·토큰 불필요.

사용법
  python build_insights.py                      # news.json + 기존 insights.json → insights.json
  python build_insights.py --extra seed.json    # 기본 수록 기사를 함께 분석(최초 생성용)
  python build_insights.py --selftest
"""
import argparse, json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 회사별 브랜드/상품명 (글로벌 상품 추출용)
NAMES = {"kurly": "컬리", "musinsoa": "무신사", "oasis": "오아시스", "ohouse": "오늘의집", "goodai": "구다이글로벌", "founders": "더파운더즈", "vinow": "비나우", "olive": "올리브인터내셔널", "liman": "리만코리아", "grace": "그레이스", "2020": "이공이공", "bnb": "비앤비코리아", "lafati": "레페리", "nmodelin": "앤마들린", "rebom": "리봄화장품", "highlight": "하이라이트브랜즈", "hagohouse": "하고하우스", "boosterz": "부스터즈", "peacenow": "피스피스스튜디오", "grainon": "그레인온"}


RULES_VERSION = 6     # 필터 규칙을 바꾸면 올린다 → 이전 누적 이력을 버리고 새 규칙으로 다시 만든다

# 기업별 자사 명칭(회사명·브랜드). 이 이름이 제목에 있어야 '자사 기사'로 본다.
ALIASES = {      # 모든 기업 동일 형식: [회사명, 대표 브랜드, 자회사…]  ← 이 목록에 있는 이름만 '자사'·'자사 상품'으로 본다
    'musinsoa': ['무신사', '무신사 스탠다드', '무신사 뷰티', '29CM'],
    'kurly': ['컬리', '마켓컬리', '뷰티컬리'],
    'ohouse': ['오늘의집', '버킷플레이스'],
    'oasis': ['오아시스', '오아시스마켓'],
    'goodai': ['구다이글로벌', '조선미녀', '스킨1004', '티르티르', '라운드랩', '크레이버'],
    'vinow': ['비나우', '넘버즈인', '프위'],
    'grace': ['그레이스'],
    'bnb': ['비앤비코리아', '진백글로벌'],
    '2020': ['이공이공'],
    'lafati': ['레페리'],
    'liman': ['리만코리아', '인셀덤', '보타팜', '보타랩', '라이프닝'],
    'founders': ['더파운더즈', '아누아', '프로젝트21', '프롬랩스'],
    'olive': ['올리브인터내셔널', '성분에디터', '밀크터치', '마미케어'],
    'highlight': ['하이라이트브랜즈', '말본', '코닥어패럴'],
    'peacenow': ['피스피스스튜디오', '마르디메크르디', '마르디 메크르디'],
    'hagohouse': ['하고하우스', '마뗑킴', '드파운드', '유니폼브릿지'],
    'grainon': ['그레인온'],
    'boosterz': ['부스터즈', '르무통', '링티'],
    'nmodelin': ['앤마들린'],
    'rebom': ['리봄화장품', '리봄'],
}
# 타사 혼합 기사를 걸러내기 위한 경쟁사·유통사 (투자자·증권사·파트너는 넣지 않는다)
COMPETITORS = ['롯데백화점', '롯데쇼핑', '롯데온', '롯데', '신세계', '현대백화점', '이마트', '쿠팡', 'SSG', '홈플러스',
               '올리브영', '11번가', 'G마켓', '알리익스프레스', '테무', '아모레퍼시픽', 'LG생활건강', '다이소',
               '에이블리', '지그재그', '브랜디', 'W컨셉', '코리아360', '네이버쇼핑', '카카오',
               'LG생건', '프레시안', '기픈']   # 기사 속 타 브랜드·타사(대시보드 외)


def _own(key):
    return ALIASES.get(key) or [NAMES.get(key, '')]


def _others(key):
    own = _own(key)
    pool = [a for k, v in ALIASES.items() if k != key for a in v] + COMPETITORS
    return [o for o in pool if o and not any(o in a or a in o for a in own)]


def has_own(key, text):
    return any(a and a in text for a in _own(key))


def other_hits(key, text):
    """text 안에서 자사 명칭에 포함된 부분을 제외하고, 타사·경쟁사 명칭이 있으면 목록으로 반환"""
    t = text
    for a in sorted(_own(key), key=len, reverse=True):
        t = t.replace(a, ' ')
    return [o for o in _others(key) if o in t]


# '자사가 키운/출신' 처럼 자사를 배경·출처로만 언급하는 표현 → 기사의 주인공은 다른 브랜드/기업
REF_AFTER = re.compile(r'^\s*(가|이|의)?\s*(키운|육성한|길러낸|투자한|인수한|출신|입점\s*브랜드|입점사|납품|협력사|파트너사|에서\s*(성장|출발|시작))')


def is_reference(key, text):
    for a in _own(key):
        for m in re.finditer(re.escape(a), text):
            if REF_AFTER.search(text[m.end():m.end() + 14]):
                return True
    return False


def _count(text, names):
    return sum(text.count(n) for n in names if n)


def about_others(key, text):
    """본문(제목+요약)에서 타사·타 브랜드 언급 횟수가 자사 언급 이상이면 타사 중심 기사로 본다"""
    own = _count(text, _own(key))
    t = text
    for a in sorted(_own(key), key=len, reverse=True):
        t = t.replace(a, ' ')
    oth = _count(t, _others(key))
    return oth > 0 and oth >= own


def article_ok(key, n):
    """자사 기사 판정(글로벌용, 엄격): 제목에 자사 명칭이 있고, 제목에 타사가 없으며,
    자사를 출처로만 언급(예: '컬리가 키운 브랜드')하거나 타사 중심인 기사는 제외한다."""
    hl = clean(n.get('hl'))
    full = hl + ' ' + clean(n.get('sum'))
    return has_own(key, hl) and not other_hits(key, hl) and not is_reference(key, hl) and not about_others(key, full)


def article_ok_ipo(key, n):
    """IPO 신호용(완화): 제목 또는 요약에 자사 명칭이 있고, 제목에 타사·경쟁사가 함께 나오지 않아야 한다."""
    hl = clean(n.get('hl'))
    return has_own(key, hl + ' ' + clean(n.get('sum'))) and not other_hits(key, hl)


def segment_ok(key, seg, is_headline):
    """분석에 쓸 문장: 제목이면 통과, 요약 문장은 자사 명칭이 있고 타사 명칭이 없어야 한다."""
    if is_headline:
        return True
    return has_own(key, seg) and not other_hits(key, seg)


MAX_SIGNALS = 10
MAX_REGIONS = 10
MAX_ITEMS = 5
WINDOW_MONTHS = 12      # 모든 기업 동일: 최근 12개월 기사만 반영
NEW_MONTHS = 6          # '신규 진출' 판정 기간: 최근 6개월
KST = timezone(timedelta(hours=9))
LVL_ORDER = {'confirmed': 0, 'planned': 1, 'weak': 2}
# 모든 기업 동일 판정 — 문장 단위로 '계획' 표현이 있으면 계획, 실제 행위 표현이 있으면 확정, 그 외(단순 언급)는 제외
PLAN_RE = re.compile(r'추진|검토|계획|예정|목표|준비|노린다|노리|전망|방침|나선다|나설|타진|모색|저울질|도전장|하겠다|할\s*것|채비|앞두|'
                     r'(?:진출|입점|출시|론칭|런칭|오픈|확대|수출|공략)할|공략|겨냥')
DONE_RE = re.compile(r'진출|입점|출점|론칭|런칭|출시|오픈|개점|문을\s*열|수출|공급\s*계약|계약을?\s*체결|계약\s*체결|법인\s*설립|설립했|'
                     r'현지\s*법인|(?:팝업|매장|점포|채널|입점|출점).{0,4}(?:확대|확장|추가)|\d\s*호점|판매\s*개시|판매를\s*시작|선보|상륙|팝업.{0,6}(?:개최|운영|열|오픈)')


ENTRY_RE = re.compile(r'진출|상륙')                       # 기사에 '진출'이라고 직접 언급된 문장 → 신규 진출 판정에 사용
EXPAND_RE = re.compile(r'확대|확장|추가|\d\s*호점|[2-9]호|늘|증가|급증|껑충|배\s*이상|성장|흥행|성료|본격|가속|확충|속도')
WITHDRAW_RE = re.compile(r'철수|폐점|영업\s*종료|사업\s*중단|운영\s*중단|매각|청산|접는다|접기로')
RESULT_RE = re.compile(r'통했다|찍고|성공|거래액|수출액|성료|흥행|돌파|달성|급증|증가|껑충|배\s*이상|신규\s*회원|방문|흑자|운영\s*중|계약을?\s*맺|독점\s*유통')
FUTURE_RE = re.compile(r'예정|목표|계획|전망|방침|추진|검토|준비|나선다|나설|노린다|노리')


def seg_level(seg):
    """문장 → 'done'(확정) / 'plan'(계획) / None(단순 언급·제외)"""
    if WITHDRAW_RE.search(seg):
        return 'done'
    if RESULT_RE.search(seg) and not FUTURE_RE.search(seg):      # 거래액·성료 등 실적/결과 표현 → 이미 진행된 해외 사업
        return 'done'
    if PLAN_RE.search(seg):
        return 'plan'
    return 'done' if DONE_RE.search(seg) else None


def in_window(date, today=None, months=None):
    m = re.match(r'(\d{4})\.(\d{1,2})', str(date or ''))
    if not m:
        return False
    t = today or datetime.now(KST)
    d = (t.year * 12 + t.month) - (int(m.group(1)) * 12 + int(m.group(2)))
    return -1 <= d <= (WINDOW_MONTHS if months is None else months)



IPO_GATE = re.compile(r'상장|IPO|기업공개|예심|예비심사|공모|주관사', re.I)
# 진척 순서(낮음 → 높음)
PROGRESS = ['pre', 'lead', 'filed', 'approved', 'offering', 'listed']
KIND_LABEL = {'pre': 'Pre-IPO', 'lead': '주관사 선정', 'filed': '예심 청구', 'approved': '예심 승인',
              'offering': '공모·수요예측', 'listed': '상장 완료', 'delay': '지연·철회'}
IPO_RULES = {
    'listed':   re.compile(r'상장했|상장\s*완료|(코스닥|코스피|유가증권시장)에\s*입성|첫\s*거래'),
    'offering': re.compile(r'수요예측|공모가|일반\s*청약|청약\s*(경쟁률|일정)'),
    'approved': re.compile(r'예비심사\s*(를|을)?\s*(승인|통과)|심사\s*통과|상장\s*승인'),
    'filed':    re.compile(r'예비심사\s*(를|을)?\s*청구|예심\s*(을)?\s*청구|심사\s*청구'),
    'lead':     re.compile(r'주관사\s*(로\s*)?(선정|확정|낙점|결정)|(선정|확정|낙점)\w*\s*주관사|대표\s*주관사'),
    'pre':      re.compile(r'프리\s*IPO|pre-?IPO', re.I),
    'delay':    re.compile(r'상장\s*(을\s*)?(연기|철회|보류|미뤄|미루|무산)|(IPO|기업공개)\s*(연기|철회|보류)|상장\s*철회'),
}

# 미래·불확실 표현이 바로 뒤따르면 해당 단계를 '이미 일어난 일'로 보지 않는다 (예: '예심 청구 시기는 조율 중')
IPO_VETO = {
    'filed':    re.compile(r'(?:청구|신청)\s*(?:을|를)?\s*(?:시기|일정|예정|예상|전망|목표|준비|앞두|계획|검토|조율|가능|임박|할|하려|추진)'),
    'lead':     re.compile(r'선정\s*(?:중|추진|예정|준비|검토|앞두|임박|단계|전)|선정\s*(?:할|하려)'),
    'approved': re.compile(r'(?:승인|통과)\s*(?:예정|예상|전망|목표|기대|가능|여부|할|하려)'),
    'offering': re.compile(r'(?:예정|예상|전망|목표|준비|앞두|계획|검토)'),
    'listed':   re.compile(r'(?:예정|예상|전망|목표|준비|앞두|계획|검토)'),
    'pre':      re.compile(r'(?:예정|예상|전망|목표|준비|앞두|계획|검토|추진)'),
}


def _hit(kind, t):
    for m in IPO_RULES[kind].finditer(t):
        tail = t[m.start(): m.end() + 24]
        v = IPO_VETO.get(kind)
        if not (v and v.search(tail)):
            return True
    return False


GLOBAL_VERB = re.compile(r'철수|폐점|영업\s*종료|영토\s*확장|거래액|수출액|총판|독점\s*유통|유통\s*계약|진출|수출|입점|출점|현지|법인|해외|글로벌|론칭|오픈|팝업|상륙|공략|매장|계약|파트너십|공급|납품|판매\s*개시|직진출|역직구|점유율|유통망|채널')
_DESC = r'(?!\s*(?:의\s*)?(?:클래식|패션|브랜드|디자이너|스타|배우|셀럽|기업|업체|유명|명품))'
COUNTRIES = [
    ('미국', re.compile(r'(?:미국|미주|북미|\bUSA?\b|美(?=[\s·,…]|$))' + _DESC, re.I)),
    ('일본', re.compile(r'(?:일본|日本|日(?=[\s·,…]|$))' + _DESC)),
    ('중국', re.compile(r'(?:중국|中(?=[\s·,…]|$)|상하이|베이징|티몰|더우인)' + _DESC)),
    ('동남아', re.compile(r'(?:동남아|베트남|태국|인도네시아|말레이시아|싱가포르|필리핀|쇼피)' + _DESC)),
    ('유럽', re.compile(r'(?:유럽|영국|프랑스|독일|이탈리아|스페인)' + _DESC)),
    ('중동', re.compile(r'(?:중동|사우디|UAE|아랍)' + _DESC)),
    ('인도', re.compile(r'인도(?!네시아)\b' + _DESC)),
    ('호주', re.compile(r'호주' + _DESC)),
    ('대만', re.compile(r'대만' + _DESC)),
    ('캐나다', re.compile(r'캐나다' + _DESC)),
    ('홍콩', re.compile(r'홍콩' + _DESC)),
    ('중남미', re.compile(r'(?:브라질|멕시코|중남미|남미|칠레)' + _DESC)),
    ('중앙아시아·러시아', re.compile(r'(?:몽골|카자흐스탄|우즈벡|우즈베키스탄|러시아|CIS)' + _DESC)),
    ('튀르키예·아프리카', re.compile(r'(?:튀르키예|터키|아프리카|이집트)' + _DESC)),
]
PARTNERS = ['아마존', '세포라', '울타', '돈키호테', '라쿠텐', '쇼피', '틱톡샵', '티몰', '큐텐', '코스트코', '월마트',
            '타깃', '얼타', '로프트', '도큐핸즈', '라자다', '타오바오', '샤오홍슈', '틱톡', 'DHL', '로손', '아이허브', 'Amazon', 'Sephora', 'Ulta', 'Shopee', 'TikTok Shop', 'Costco',
            'Walmart', 'Target']


def clean(t):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', t or '')).strip()


def article_text(n):
    return clean(n.get('hl')) + ' ' + clean(n.get('sum'))


def classify_ipo(n):
    """기사 → (kind, label) 또는 None.  지연·철회가 있으면 우선, 아니면 가장 진척된 단계."""
    t = article_text(n)
    if not IPO_GATE.search(t):
        return None
    if IPO_RULES['delay'].search(t):
        return 'delay'
    for k in reversed(PROGRESS):
        if _hit(k, t):
            return k
    return None


def segments(n):
    """제목 → 요약문 문장 순서로 분리"""
    out = [clean(n.get('hl'))]
    out += [s.strip() for s in re.split(r'(?<=[.!?…])\s+|\.\.\.|[\n|]', clean(n.get('sum'))) if len(s.strip()) > 8]
    return [s for s in out if s]


def trim(s, n=90):
    s = s.strip(' .…')
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


def extract_global(key, n, company_name):
    """기사 1건 → [{'region','text','partners','products'}]  (해외 문맥 문장 기준)"""
    if not article_ok(key, n):                               # 타사 혼합·타사 기사는 인용하지 않는다
        return []
    full = article_text(n)
    company_name = company_name or NAMES.get(key, '')
    brands = [b for b in _own(key) if b and not (company_name and (b in company_name or company_name in b))]
    prods = {}
    for b in brands:
        if b in full:
            prods.setdefault(b.replace(' ', ''), b)           # '마르디 메크르디'/'마르디메크르디' 같은 표기 중복 제거
    products = sorted(prods.values())
    res = {}
    for i, seg in enumerate(segments(n)):
        if not segment_ok(key, seg, i == 0):                 # 요약 문장은 자사 명칭이 직접 있는 문장만
            continue
        if not GLOBAL_VERB.search(seg) or IPO_GATE.search(seg):   # 상장 관련 문장은 진출 요약에서 제외
            continue
        lvl = seg_level(seg)
        if not lvl:                                          # 국가·채널만 나오는 단순 언급은 제외
            continue
        regs = [name for name, re_ in COUNTRIES if re_.search(seg)]
        parts = [p for p in PARTNERS if p.lower() in seg.lower()]
        if not regs and parts:
            regs = ['글로벌 채널']
        for r in regs:
            e = res.setdefault(r, {'region': r, 'text': trim(seg), 'lvl': lvl, 'entry': False, 'exp': False, 'wd': False, 'partners': set(), 'products': set()})
            if lvl == 'done' and e['lvl'] != 'done':         # 같은 기사에서 확정 표현이 있으면 확정 우선
                e.update(text=trim(seg), lvl='done')
            if lvl == 'done':
                e['entry'] = e['entry'] or bool(ENTRY_RE.search(seg))
                e['exp'] = e['exp'] or bool(EXPAND_RE.search(seg))
                e['wd'] = e['wd'] or bool(WITHDRAW_RE.search(seg))
            e['partners'].update(parts)
            e['products'].update(products)
    return [{**e, 'partners': sorted(e['partners']), 'products': sorted(e['products'])} for e in res.values()]


def merge_signals(old, new):
    seen, out = set(), []
    for s in new + old:                                   # 새 기사가 같은 항목이면 새 값 우선
        k = s.get('url') or s['hl']
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    out.sort(key=lambda s: s.get('date', ''), reverse=True)
    return out[:MAX_SIGNALS]


TAG_ORDER = {'new': 0, 'expand': 1, 'ongoing': 2, 'withdraw': 3, 'planned': 4, 'weak': 5}


def region_tag(level, items):
    """국가별 태그: 신규 진출 / 확장 중 / 운영 중 / 축소·철수 / 예정 / 근거 약함 (모든 기업 동일 기준)
    - 신규 진출: 최근 6개월 안에 '진출'이라고 직접 언급된 확정 기사가 있고, 6개월보다 오래된 확정 기사는 없을 때
    - 확장 중: 최근 6개월 확정 기사에 확대 표현(추가·호점·증가 등)이 있을 때
    - 축소·철수: 최근 6개월 기사에 철수·폐점·매각 표현이 있을 때"""
    rec = lambda x: in_window(x.get('date'), months=NEW_MONTHS)
    done = [x for x in items if x.get('lvl') == 'done']
    if any(x.get('wd') and rec(x) for x in done):
        return 'withdraw'
    if level == 'confirmed':
        if any(x.get('entry') and rec(x) for x in done) and not any(not rec(x) for x in done):
            return 'new'
        if any(x.get('exp') and rec(x) for x in done):
            return 'expand'
        return 'ongoing'
    return 'planned' if level == 'planned' else 'weak'


def merge_global(old, new):
    regs = {g['region']: {**g, 'items': list(g.get('items', [])), 'partners': set(g.get('partners', [])),
                          'products': set(g.get('products', []))} for g in old}
    for e in new:
        g = regs.setdefault(e['region'], {'region': e['region'], 'items': [], 'partners': set(), 'products': set()})
        g['partners'].update(e['partners'])
        g['products'].update(e['products'])
        it = {'date': e['date'], 'text': e['text'], 'lvl': e.get('lvl', 'plan'), 'entry': e.get('entry', False), 'exp': e.get('exp', False),
              'wd': e.get('wd', False), 'hl': e['hl'], 'url': e['url']}
        dup = next((x for x in g['items'] if (x.get('url') and x.get('url') == it['url']) or x.get('text') == it['text']), None)
        if dup is None:
            g['items'].append(it)
        elif it['lvl'] == 'done':
            dup.update(it)
    out = []
    for g in regs.values():
        items = [x for x in g['items'] if in_window(x.get('date'))]          # 최근 12개월 기준 통일
        if not items:
            continue
        items.sort(key=lambda x: x.get('date', ''), reverse=True)
        items.sort(key=lambda x: x.get('lvl') != 'done')                       # 확정 근거 우선
        done = {x.get('url') or x['hl'] for x in items if x.get('lvl') == 'done'}
        plan = {x.get('url') or x['hl'] for x in items if x.get('lvl') != 'done'} - done
        g['n_done'], g['n_plan'] = len(done), len(plan)
        # 확정 1건 이상 → 확정 / 계획 2건 이상 → 계획 / 그 외 → 근거 약함
        g['level'] = 'confirmed' if done else ('planned' if len(plan) >= 2 else 'weak')
        g['tag'] = region_tag(g['level'], items)
        g['items'] = items[:MAX_ITEMS]
        g['date'] = max(x.get('date', '') for x in items)
        g['partners'], g['products'] = sorted(g['partners']), sorted(g['products'])
        out.append(g)
    out.sort(key=lambda g: g['date'], reverse=True)
    out.sort(key=lambda g: TAG_ORDER[g['tag']])
    return out[:MAX_REGIONS]


def analyze(key, name, articles, prev, glb_extra=()):
    sig_new, glb_new = [], []
    for n in articles:
        if not article_ok_ipo(key, n):
            continue
        kind = classify_ipo(n)
        if kind:
            sig_new.append({'date': n.get('date', ''), 'kind': kind, 'label': KIND_LABEL[kind],
                            'hl': clean(n.get('hl')), 'url': n.get('url', '')})
    for n in list(articles) + list(glb_extra):               # 글로벌 요약은 해외 전용 수집분(global_news.json)도 함께 분석
        for e in extract_global(key, n, name):
            e.update(date=n.get('date', ''), hl=clean(n.get('hl')), url=n.get('url', ''))
            glb_new.append(e)
    old_sig = [x for x in (prev.get('ipo') or {}).get('signals', []) if not other_hits(key, clean(x.get('hl')))]
    old_glb = []
    for g in prev.get('global', []):                         # 이전 이력도 같은 규칙으로 다시 검증
        items = [it for it in g.get('items', []) if article_ok(key, {'hl': it.get('hl'), 'sum': it.get('text')})
                 and (clean(it.get('text')) == trim(clean(it.get('hl'))) or segment_ok(key, clean(it.get('text')), False))]
        for it in items:
            it['lvl'] = it.get('lvl') or seg_level(clean(it.get('text'))) or 'plan'
            tx = clean(it.get('text'))
            if it['lvl'] == 'done':
                it.setdefault('entry', bool(ENTRY_RE.search(tx)))
                it.setdefault('exp', bool(EXPAND_RE.search(tx)))
                it.setdefault('wd', bool(WITHDRAW_RE.search(tx)))
        if items:
            old_glb.append({**g, 'items': items})
    return {'ipo': {'signals': merge_signals(old_sig, sig_new)},
            'global': merge_global(old_glb, glb_new)}


def selftest():
    NOW = datetime.now(KST).strftime('%Y.%m')
    OLD = (datetime.now(KST) - timedelta(days=500)).strftime('%Y.%m')
    n = {'hl': '무신사, 코스피 상장예비심사 청구', 'sum': '한국투자·씨티 주관. 공모 2,660만주 예정.', 'url': 'u1', 'date': '2026.09'}
    assert classify_ipo(n) == 'filed'
    assert classify_ipo({'hl': 'A사 IPO 주관사 선정 확정', 'sum': ''}) == 'lead'
    assert classify_ipo({'hl': 'B사 상장 철회 후 재추진', 'sum': '예비심사 청구 했다가'}) == 'delay'
    assert classify_ipo({'hl': 'C사 신제품 출시', 'sum': ''}) is None
    assert classify_ipo({'hl': 'E사, KB증권 주관사 선정', 'sum': '예심 청구 시기는 조율 중.'}) == 'lead'
    assert classify_ipo({'hl': 'F사 IPO 주관사 선정 추진', 'sum': ''}) is None
    assert classify_ipo({'hl': 'G사 예심 청구 예상', 'sum': ''}) is None
    assert classify_ipo({'hl': 'H사 27년 상반기 예심 청구를 전망', 'sum': ''}) is None
    assert classify_ipo({'hl': 'I사 예심 청구 일정 확정 예정', 'sum': ''}) is None
    assert classify_ipo({'hl': 'D사 수요예측 흥행, 공모가 확정', 'sum': ''}) == 'offering'
    g = extract_global('goodai', {'hl': '스킨1004, 일본 돈키호테 입점 확대', 'sum': '조선미녀도 일본 현지 매장 오픈.'}, '구다이글로벌')
    assert [e['region'] for e in g] == ['일본'] and 'DHL' not in g[0]['partners'] and '돈키호테' in g[0]['partners']
    g = extract_global('goodai', {'hl': '스킨1004, 일본 돈키호테 입점 확대', 'sum': ''}, '구다이글로벌')
    assert g[0]['products'] == ['스킨1004'] and g[0]['lvl'] == 'done'
    # 해외 문맥 없는 '미국 브랜드' 협업 기사는 제외
    assert extract_global('musinsoa', {'hl': '무신사, 미국 클래식 패션 브랜드와 협업 컬렉션 선발매', 'sum': ''}, '무신사') == []
    assert extract_global('goodai', {'hl': '구다이글로벌 자회사, 일본 IPO 무산위기', 'sum': ''}, '구다이글로벌') == []
    # 파트너만 있는 경우
    assert extract_global('kurly', {'hl': '컬리, 아마존 입점', 'sum': ''}, '컬리')[0]['region'] == '글로벌 채널'
    # 누적: 뉴스가 사라져도 이력 유지, 중복 제거
    prev = analyze('musinsoa', '무신사', [n], {})
    again = analyze('musinsoa', '무신사', [], prev)
    assert again['ipo']['signals'][0]['kind'] == 'filed'
    twice = analyze('musinsoa', '무신사', [n], prev)
    assert len(twice['ipo']['signals']) == 1
    # 해외 전용 수집분은 글로벌 요약에만 반영(IPO 신호에는 영향 없음)
    r = analyze('goodai', '구다이글로벌', [], {}, [{'hl': '스킨1004, 일본 돈키호테 입점', 'sum': '', 'url': 'g1', 'date': NOW}])
    assert r['global'][0]['region'] == '일본' and r['ipo']['signals'] == [] and r['global'][0]['level'] == 'confirmed'
    # 자사 기사 필터: 타사 혼합 기사·타사 문장은 인용하지 않는다
    assert article_ok('kurly', {'hl': '컬리, 미국 진출 확대'})
    assert not article_ok('kurly', {'hl': '"컬리가 키운 브랜드 이제 세계로"…LG생건 프레시안, K웰니스 뷰티 도전장', 'sum': ''})   # 타사 중심
    assert not article_ok('kurly', {'hl': '컬리가 키운 브랜드 세계로', 'sum': ''})                                      # 출처로만 언급
    assert not article_ok('boosterz', {'hl': "FSN 자회사 부스터즈 지원 '기픈', 무신사 뷰티 실시간 랭킹 1위", 'sum': ''})
    assert not article_ok('musinsoa', {'hl': "FSN 자회사 부스터즈 지원 '기픈', 무신사 뷰티 실시간 랭킹 1위", 'sum': ''})
    assert not article_ok('kurly', {'hl': '컬리 동남아 진출', 'sum': '코리아360은 롯데백화점과 함께 동남아 진출'})   # 요약에 타사
    assert not article_ok('kurly', {'hl': '컬리 미국 진출', 'sum': '무신사·29CM도 미국 공략'})                      # 요약에 타 브랜드
    assert article_ok('kurly', {'hl': '컬리 미국 진출', 'sum': '뷰티컬리가 미국 소비자 대상 배송을 시작'})
    assert not article_ok('kurly', {'hl': '롯데백화점·컬리, 동남아 진출 경쟁'})        # 제목에 타사
    assert not article_ok('kurly', {'hl': '롯데백화점, 동남아 진출'})                  # 제목에 자사 없음
    mixed = {'hl': '컬리, 오프라인 확대', 'sum': '코리아360은 롯데백화점과 동남아 진출을 추진한다.'}
    assert extract_global('kurly', mixed, '컬리') == []                              # 요약 문장에 자사 명칭 없음
    ok = {'hl': '컬리, 뷰티컬리 일본 진출', 'sum': '컬리는 일본 현지 팝업을 열었다.'}
    assert [e['region'] for e in extract_global('kurly', ok, '컬리')] == ['일본']
    assert other_hits('olive', '올리브인터내셔널과 올리브영') == ['올리브영']
    r = analyze('kurly', '컬리', [mixed], {'global': [{'region': '동남아', 'items': [{'date': '2026.11', 'text': '코리아360은 동남아', 'hl': '컬리, 오프라인 확대', 'url': ''}], 'partners': [], 'products': []}]})
    assert r['global'] == [], r                                                      # 이전 이력의 잘못된 항목도 제거
    assert article_ok_ipo('grainon', {'hl': '신한투자증권 IPO 주관사 선정', 'sum': '그레인온은 27.1Q 상장을 목표로 한다.'})
    assert not article_ok_ipo('highlight', {'hl': '무신사 풍선효과 노린다…하이라이트브랜즈 상장 저울질', 'sum': ''})   # 제목에 타사
    # 공통 기준: 확정 / 계획 / 단순 언급 구분
    assert seg_level('조선미녀, 일본 돈키호테 입점') == 'done'
    assert seg_level('컬리, 미국 진출 추진') == 'plan'
    assert seg_level('일본 시장 공략 나선다') == 'plan'
    assert seg_level('일본 소비자 반응 뜨겁다') is None
    assert extract_global('kurly', {'hl': '컬리, 일본 소비자 반응 뜨겁다 해외 글로벌 채널', 'sum': ''}, '컬리') == []     # 단순 언급 제외
    def mk(h, d, u):
        return {'hl': h, 'sum': '', 'url': u, 'date': d}
    # 확정 1건 → 확정
    r = analyze('musinsoa', '무신사', [], {}, [mk('무신사, 일본 진출', NOW, 'a1')])
    assert r['global'][0]['level'] == 'confirmed' and r['global'][0]['n_done'] == 1
    # 계획 1건 → 약함(보도 1건), 계획 2건 → 계획
    r = analyze('musinsoa', '무신사', [], {}, [mk('무신사, 미국 진출 추진', NOW, 'b1')])
    assert r['global'][0]['level'] == 'weak'
    r = analyze('musinsoa', '무신사', [], {}, [mk('무신사, 미국 진출 추진', NOW, 'b1'), mk('무신사 미국 진출 검토 중', NOW, 'b2')])
    assert r['global'][0]['level'] == 'planned' and r['global'][0]['n_plan'] == 2
    # 12개월 초과 기사는 제외
    r = analyze('musinsoa', '무신사', [], {}, [mk('무신사, 일본 진출', OLD, 'c1')])
    assert r['global'] == []
    # 확정이 계획보다 위에 정렬
    r = analyze('musinsoa', '무신사', [], {}, [mk('무신사, 미국 진출 추진', NOW, 'b1'), mk('무신사, 일본 진출', OLD[:0] or NOW, 'a1')])
    assert [g['level'] for g in r['global']] == ['confirmed', 'weak']
    # 태그: 신규 진출 / 확장 중 / 운영 중 / 축소·철수
    M = lambda n: (datetime.now(KST) - timedelta(days=30 * n)).strftime('%Y.%m')
    tg = lambda arts: analyze('musinsoa', '무신사', [], {}, arts)['global'][0]['tag']
    assert tg([mk('무신사, 베트남 진출', M(1), 't1')]) == 'new'
    assert tg([mk('무신사, 베트남 진출', M(1), 't1'), mk('무신사, 베트남 거래액 증가', M(9), 't0')]) == 'ongoing'   # 6개월 넘은 확정 기사가 있으면 신규 아님
    assert tg([mk('무신사, 일본 팝업 오픈', M(1), 't2')]) == 'ongoing'                    # '진출' 언급 없음 → 신규 아님
    assert tg([mk('무신사, 일본 진출', M(10), 't3')]) == 'ongoing'                         # 6개월 넘은 진출 → 운영 중
    assert tg([mk('무신사, 일본 팝업 확대', M(1), 't4'), mk('무신사, 일본 진출', M(10), 't5')]) == 'expand'
    assert tg([mk('무신사, 일본 거래액 증가', M(2), 't6')]) == 'expand'
    assert tg([mk('무신사, 중국 매장 철수', M(1), 't7')]) == 'withdraw'
    assert tg([mk('무신사, 미국 진출 추진', M(1), 'p1'), mk('무신사 미국 진출 검토', M(1), 'p2')]) == 'planned'
    assert tg([mk('무신사, 미국 진출 추진', M(1), 'p1')]) == 'weak'
    print('분석 로직 OK')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--news', default='news.json')
    ap.add_argument('--extra', default='')
    ap.add_argument('--global-news', dest='gnews', default='global_news.json')
    ap.add_argument('--out', default='insights.json')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return

    def load(p):
        try:
            return json.loads(Path(p).read_text('utf-8')) if p and Path(p).exists() else {}
        except Exception:
            return {}

    news, extra, prev, gnews = load(a.news), load(a.extra), load(a.out), load(a.gnews)
    prev_c = prev.get('companies', {}) if isinstance(prev, dict) and prev.get('_rules') == RULES_VERSION else {}
    keys = [k for k in news if not k.startswith('_')]
    keys += [k for k in list(extra) + list(gnews) if k not in keys and not k.startswith('_')]
    out = {}
    for k in keys:
        arts = [x for x in (news.get(k) or []) if isinstance(x, dict)] + [x for x in (extra.get(k) or []) if isinstance(x, dict)]
        gl = [x for x in (gnews.get(k) or []) if isinstance(x, dict)]
        out[k] = analyze(k, NAMES.get(k, ''), arts, prev_c.get(k, {}), gl)
    for k, v in prev_c.items():                           # 이번에 기사가 없는 회사도 이력 유지
        out.setdefault(k, v)
    res = {'companies': out, '_rules': RULES_VERSION, '_updated': datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S KST')}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), 'utf-8')
    n_sig = sum(len(v['ipo']['signals']) for v in out.values())
    n_glb = sum(len(v['global']) for v in out.values())
    print(f'저장: {a.out} | 회사 {len(out)}곳 | IPO 신호 {n_sig}건 | 글로벌 국가 항목 {n_glb}건')


if __name__ == '__main__':
    main()
