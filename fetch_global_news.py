"""
해외 진출·투자 유치 전용 뉴스 수집 (네이버 뉴스 검색 API) → global_news.json

- 기존 news.json 수집기와 별개로 동작한다. (기존 파일 수정 불필요)
- ① 해외 진출: "{이름} + 해외 진출/수출/미국/일본/중국/동남아/유럽 ..." 검색 → 해외 사업 문맥(진출·수출·입점·현지법인 등)이 있는 기사만 보관
- ② 투자 유치: "{이름} + 투자 유치/지분 인수/프리IPO/시리즈 ..." 검색, 그리고 presence.json 의 _investors 에 적힌 투자사별
  "{이름} {투자사}" 검색 → 투자 문맥 기사만 보관(성사되지 않은 건 제외). build_insights.py 가 투자자 자동 추출·기사 링크에 사용한다.
- 기존 global_news.json 이력은 유지하며 누적한다(회사당 최대 50건). 새 결과가 0건이면 기존 값 유지.
- 인증정보는 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 로만 받는다(코드·로그에 출력 금지).

사용법
  python fetch_global_news.py            # 실제 수집 (환경변수 필요)
  python fetch_global_news.py --selftest # 필터 로직 점검 (네트워크/키 불필요)
"""
import argparse, json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

API_URL = 'https://openapi.naver.com/v1/search/news.json'
TIMEOUT = 10
SLEEP = 0.12
KEEP_PER_CO = 50
SUFFIXES = ['해외 진출', '수출', '글로벌', '미국', '일본', '중국', '동남아', '유럽']
EXTRA_SUFFIXES = ['해외 매출', '수출 비중', '아마존', '현지 법인']          # 대표 이름에만 추가
INV_SUFFIXES = ['투자 유치', '투자', '지분 인수', '프리IPO', '시리즈']       # 투자 유치 기사 수집(모든 기업 동일)

# key: (검색 이름(최대 3개, 제목에 하나라도 있어야 통과), 제외 키워드(제목+요약))
COMPANIES = {
    'musinsoa': (['무신사', '무신사 스탠다드', '29CM'], []),
    'kurly':    (['컬리', '마켓컬리', '뷰티컬리'], ['참프레', '오아시스', '단발', '컬리헤어']),
    'ohouse':   (['오늘의집', '버킷플레이스'], []),
    'oasis':    (['오아시스마켓', '오아시스 새벽배송', '오아시스'], ['갤러거', '록밴드']),
    'goodai':   (['조선미녀', '스킨1004', '티르티르', '라운드랩', '구다이글로벌'], []),
    'vinow':    (['넘버즈인', '비나우', '프위'], []),
    'grace':    (['그레이스 뷰티', '그레이스 화장품'], ['골프', '은혜', '성당', '교회', '기도', '찬양', '축복']),
    'bnb':      (['비앤비코리아', '진백글로벌'], []),
    '2020':     (['이공이공', 'EGONGEGONG'], ['이공계', '이공계열', '이공대', '공대']),
    'lafati':   (['레페리'], []),
    'liman':    (['리만코리아', '인셀덤', '보타팜'], []),
    'founders': (['아누아', '더파운더즈'], []),
    'olive':    (['올리브인터내셔널', '성분에디터', '밀크터치'], ['올리브영', '올리브나무', '올리브오일']),
    'highlight': (['하이라이트브랜즈', '말본골프', '코닥어패럴'], ['콘서트', '아이돌', '가수']),
    'peacenow': (['마르디메크르디', '피스피스스튜디오'], []),
    'hagohouse': (['하고하우스', '마뗑킴', '드파운드'], ['하트시그널', '드라마', '출연', '배우', '연예', '예능', '방송']),
    'grainon':  (['그레인온', '카무트 효소'], ['카무트 밀', '카무트 쌀', '카무트 재배', '카무트 효능']),
    'boosterz': (['부스터즈', '르무통', '링티'], ['게임', '스타트업 부스터', '부스터샷', '창업 부스터', '부스터 프로그램']),
    'nmodelin': (['앤마들린'], []),
    'rebom':    (['리봄화장품'], []),
}

# 투자 유치 검색에 쓰는 '회사명'(브랜드 말고 법인명). 없으면 첫 번째 이름
INV_NAME = {'oasis': '오아시스', 'goodai': '구다이글로벌', 'founders': '더파운더즈', 'vinow': '비나우', 'musinsoa': '무신사',
            'kurly': '컬리', 'ohouse': '오늘의집', 'grace': '그레이스', 'highlight': '하이라이트브랜즈', 'olive': '올리브인터내셔널',
            '2020': '이공이공', 'peacenow': '피스피스스튜디오', 'boosterz': '부스터즈', 'hagohouse': '하고하우스', 'liman': '리만코리아'}

# 타사 혼합 기사를 걸러내기 위한 경쟁사·유통사 (투자자·증권사·파트너는 넣지 않는다)
COMPETITORS = ['롯데백화점', '롯데쇼핑', '롯데온', '롯데', '신세계', '현대백화점', '이마트', '쿠팡', 'SSG', '홈플러스',
               '올리브영', '11번가', 'G마켓', '알리익스프레스', '테무', '아모레퍼시픽', 'LG생활건강', '다이소',
               '에이블리', '지그재그', '브랜디', 'W컨셉', '코리아360', '네이버쇼핑', '카카오',
               'LG생건', '프레시안', '기픈']


def others_for(key):
    own = COMPANIES[key][0]
    pool = [n for k, (names, _) in COMPANIES.items() if k != key for n in names] + COMPETITORS
    return [o for o in pool if o and not any(o in a or a in o for a in own)]


def mixed_title(title, names, others):
    """제목에서 자사 명칭 부분을 빼고도 타사·경쟁사 명칭이 남아 있으면 혼합 기사로 본다"""
    t = title
    for a in sorted(names, key=len, reverse=True):
        t = t.replace(a, ' ')
    return any(o in t for o in others)


REF_AFTER = re.compile(r'^\s*(가|이|의)?\s*(키운|육성한|길러낸|투자한|인수한|출신|입점\s*브랜드|입점사|납품|협력사|파트너사|에서\s*(성장|출발|시작))')


def is_reference(title, names):
    """'컬리가 키운 브랜드…' 처럼 자사를 출처로만 언급 → 주인공은 다른 브랜드"""
    return any(REF_AFTER.search(title[m.end():m.end() + 14]) for a in names for m in re.finditer(re.escape(a), title))


def about_others(text, names, others):
    """본문(제목+요약)에서 타사·타 브랜드 언급이 자사 언급 이상이면 타사 중심 기사"""
    own = sum(text.count(a) for a in names)
    t = text
    for a in sorted(names, key=len, reverse=True):
        t = t.replace(a, ' ')
    oth = sum(t.count(o) for o in others)
    return oth > 0 and oth >= own


GLOBAL_CTX = re.compile(r'영토\s*확장|거래액|수출액|총판|독점\s*유통|유통\s*계약|성료|진출|수출|입점|출점|현지|법인|해외|글로벌|론칭|오픈|팝업|상륙|공략|매장|계약|파트너십|공급|납품|역직구|유통망|채널')
PLACES = re.compile(r'日本|日(?=[\s·,…]|$)|中(?=[\s·,…]|$)|美(?=[\s·,…]|$)|미국|미주|북미|일본|중국|상하이|베이징|티몰|동남아|베트남|태국|인도네시아|말레이시아|싱가포르|필리핀|유럽|영국|프랑스|독일|이탈리아|스페인|'
                    r'중동|사우디|UAE|인도|호주|대만|캐나다|홍콩|브라질|멕시코|몽골|카자흐스탄|러시아|튀르키예|터키|'
                    r'아마존|세포라|얼타|울타|돈키호테|라쿠텐|쇼피|틱톡샵|큐텐|코스트코|월마트|타깃|로프트|도큐핸즈|라자다|타오바오|샤오홍슈|DHL', re.I)
IPO_ONLY = re.compile(r'상장|IPO|기업공개|예심|공모')
SALES_SHARE = re.compile(r'해외\s*매출|수출\s*비중|수출\s*매출|매출\s*비중|해외\s*비중')
INV_CTX = re.compile(r'투자|유치|지분|인수|시리즈|라운드|프리\s*IPO|펀드|엑시트|구주|참여')
FAIL_RE = re.compile(r'무산|불발|철회|좌초|결렬|취소|백지화|콜옵션')      # 성사되지 않은 건은 투자 근거에서 제외


def clean(t):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', t or '')
                  .replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&')
                  .replace('&lt;', '<').replace('&gt;', '>')).strip()


def to_ym(pub):
    try:
        return datetime.strptime(pub, '%a, %d %b %Y %H:%M:%S %z').strftime('%Y.%m')
    except Exception:
        return ''


def keep_article(title, desc, names, exclude, others=()):
    """해외 진출 기사: 제목에 회사/브랜드명, 해외 문맥+국가/파트너 존재, 제외 키워드 없음, IPO 전용 기사 아님"""
    full = title + ' ' + desc
    if not any(n.lower() in title.lower() for n in names):
        return False
    if any(x in full for x in exclude):
        return False
    if others and (mixed_title(title, names, others) or is_reference(title, names) or about_others(full, names, others)):
        return False
    if not (GLOBAL_CTX.search(full) and PLACES.search(full)):
        return False
    if IPO_ONLY.search(title) and not re.search(r'진출|수출|입점|출점|론칭|현지', title) and not SALES_SHARE.search(full):
        return False
    return True


def keep_invest_article(title, desc, names, exclude, others=(), investor=''):
    """투자 유치 기사: 제목에 자사 명칭, 투자 문맥, 제외 키워드·무산 표현 없음, 제목에 타사 혼합 없음, 자사를 출처로만 언급한 기사 아님.
    investor 를 주면 제목·요약에 그 투자사명이 있어야 한다."""
    full = title + ' ' + desc
    if not any(n.lower() in title.lower() for n in names):
        return False
    if any(x in full for x in exclude) or FAIL_RE.search(full):
        return False
    if (others and mixed_title(title, names, others)) or is_reference(title, names):
        return False
    if not INV_CTX.search(full):
        return False
    if investor and re.sub(r'\s', '', investor).lower() not in re.sub(r'\s', '', full).lower():
        return False
    return True


def safe_url(u):
    return u if isinstance(u, str) and re.match(r'^https?://', u) else ''


def fetch_query(session, headers, q):
    r = session.get(API_URL, headers=headers, params={'query': q, 'display': 20, 'sort': 'date'}, timeout=TIMEOUT)
    r.raise_for_status()
    time.sleep(SLEEP)
    return r.json().get('items', [])


def _norm_title(t):
    t = re.sub(r'\[[^\]]*\]|\([^)]*\)|【[^】]*】', ' ', (t or '').lower())
    return re.sub(r'[^0-9a-z가-힣]+', '', t)


def _norm_url(u):
    m = re.match(r'^https?://(?:www\.)?([^?#]+)', u or '')
    return m.group(1).rstrip('/').lower() if m else (u or '')


def _bg(t):
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _similar(a, b):
    if not a or not b:
        return False
    i = len(a & b)
    return i / min(len(a), len(b)) >= 0.7 and i / len(a | b) >= 0.55


def merge(old, new):
    kept = []                                    # (url, title, bigrams, article)
    for a in new + old:
        u, t = _norm_url(a.get('url')), _norm_title(a['hl'])
        b = _bg(t)
        if any((u and u == k[0]) or (t and t == k[1]) or _similar(b, k[2]) for k in kept):
            continue                             # 같은 URL·같은 제목·유사 제목은 1건만
        kept.append((u, t, b, a))
    out = [k[3] for k in kept]
    out.sort(key=lambda a: a.get('date', ''), reverse=True)
    return out[:KEEP_PER_CO]


def investor_names(presence, key):
    """presence.json 의 _investors[key] 에서 투자사 이름 목록(검색용)"""
    out = []
    for i in (presence.get('_investors') or {}).get(key) or []:
        nm = str(i.get('name', '')).strip() if isinstance(i, dict) else ''
        if 2 <= len(nm) <= 30 and nm not in out:
            out.append(nm)
    return out


def selftest():
    assert keep_article('조선미녀, 미국 세포라 입점 확대', '', ['조선미녀'], [])
    assert not keep_article('조선미녀 신제품 출시', '국내 올리브영 판매', ['조선미녀'], [])
    assert not keep_article('미국 세포라 입점 확대', '', ['조선미녀'], [])
    assert not keep_article('오아시스 미국 투어 공연', '갤러거 해외 공연', ['오아시스마켓', '오아시스'], ['갤러거'])
    assert not keep_article('구다이글로벌 코스피 상장 추진', '일본 자회사 상장 무산', ['구다이글로벌'], [])
    assert keep_article('컬리, 미국 진출…역직구 확대', '', ['컬리'], ['단발'])
    assert keep_article('무신사, 하라주쿠 팝업 성료…日 신규 회원 302% 증가', '', ['무신사'], [])
    m = merge([{'hl': 'a', 'url': 'u1', 'date': '2026.01'}], [{'hl': 'a2', 'url': 'u1', 'date': '2026.02'}, {'hl': 'b', 'url': 'u2', 'date': '2026.03'}])
    assert [x['url'] for x in m] == ['u2', 'u1'] and m[1]['hl'] == 'a2'
    d = merge([], [{'hl': '컬리, 미국 진출 [단독]', 'url': 'https://a.com/1?x=1', 'date': '2026.05'},
                   {'hl': '컬리 미국 진출', 'url': 'https://b.com/2', 'date': '2026.05'},
                   {'hl': '컬리, 미국 진출…역직구 확대', 'url': 'https://c.com/3', 'date': '2026.05'},
                   {'hl': '조선미녀 세포라 입점', 'url': 'https://a.com/1', 'date': '2026.04'}])
    assert len(d) == 2, d
    assert safe_url('javascript:alert(1)') == '' and safe_url('https://a.com') == 'https://a.com'
    oth = others_for('kurly')
    assert keep_article('컬리, 미국 진출…역직구 확대', '', ['컬리'], [], oth)
    assert not keep_article('"컬리가 키운 브랜드 이제 세계로"…LG생건 프레시안, K웰니스 뷰티 도전장', '미국 일본 진출', ['컬리'], [], oth)
    assert not keep_article('컬리 미국 진출', '무신사·29CM 등 K패션도 미국 공략', ['컬리'], [], oth)
    assert not keep_article('롯데백화점·컬리, 동남아 진출 경쟁', '', ['컬리'], [], oth)
    # 투자 유치 기사
    on = ['오아시스마켓', '오아시스 새벽배송', '오아시스']
    assert keep_invest_article('오아시스, 토스서 300억 유치…', '', on, ['갤러거'], others_for('oasis'))
    assert keep_invest_article('오아시스 지분 인수 나선 사모펀드', '', on, [], others_for('oasis'))
    assert not keep_invest_article('부스터즈 커머스부문 투자유치 콜옵션 무산', '', ['부스터즈'], [], others_for('boosterz'))    # 무산
    assert not keep_invest_article('오아시스 신제품 출시', '새벽배송 확대', on, [], others_for('oasis'))                           # 투자 문맥 없음
    assert not keep_invest_article('컬리가 키운 브랜드, 투자 유치', '', ['컬리'], [], others_for('kurly'))                          # 출처로만 언급
    assert keep_invest_article('컬리, 네이버로부터 330억 투자 유치', '', ['컬리'], [], others_for('kurly'), '네이버')
    assert not keep_invest_article('컬리, 투자 유치 추진', '카카오 참여', ['컬리'], [], others_for('kurly'), '네이버')             # 투자사명 없음
    assert investor_names({'_investors': {'a': [{'name': 'IMM인베스트먼트'}, {'name': 'IMM인베스트먼트'}, {'name': 'x'}]}}, 'a') == ['IMM인베스트먼트']
    assert clean('<b>가</b>&quot;나&quot;') == '가"나"'
    print('필터 로직 OK')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='global_news.json')
    ap.add_argument('--presence', default='presence.json')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return

    cid, sec = os.environ.get('NAVER_CLIENT_ID', ''), os.environ.get('NAVER_CLIENT_SECRET', '')
    if not cid or not sec:
        sys.exit('환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 가 필요합니다. (코드에 직접 넣지 마세요)')
    import requests
    headers = {'X-Naver-Client-Id': cid, 'X-Naver-Client-Secret': sec}
    session = requests.Session()

    def load(p):
        try:
            return json.loads(Path(p).read_text('utf-8')) if Path(p).exists() else {}
        except Exception:
            return {}

    out_path = Path(a.out)
    existing, presence = load(out_path), load(a.presence)

    result, total = {}, 0
    for key, (names, exclude) in COMPANIES.items():
        found, errors = [], [0]
        others = others_for(key)
        inv_name = INV_NAME.get(key, names[0])

        def run(q, keep):
            try:
                items = fetch_query(session, headers, q)
            except Exception as e:                           # 키가 메시지에 섞이지 않도록 마스킹
                errors[0] += 1
                if errors[0] <= 2:
                    print(f'    ⚠ [{q}] {str(e).replace(cid, "***").replace(sec, "***")[:80]}')
                return
            for it in items:
                title, desc = clean(it.get('title')), clean(it.get('description'))
                if keep(title, desc):
                    found.append({'date': to_ym(it.get('pubDate', '')), 'hl': title,
                                  'sum': desc[:150], 'url': safe_url(it.get('originallink') or it.get('link', ''))})

        for ni, name in enumerate(names[:3]):                # ① 해외 진출
            for suf in SUFFIXES + (EXTRA_SUFFIXES if ni == 0 else []):
                run(f'{name} {suf}', lambda t, d: keep_article(t, d, names, exclude, others))
        inv_names = list(dict.fromkeys([inv_name] + names[:1]))
        for nm in inv_names:                                 # ② 투자 유치
            for suf in INV_SUFFIXES:
                run(f'{nm} {suf}', lambda t, d: keep_invest_article(t, d, names + [inv_name], exclude, others))
        for inv in investor_names(presence, key):            # ③ 투자사별 연관 기사
            run(f'{inv_name} {inv}', lambda t, d, inv=inv: keep_invest_article(t, d, names + [inv_name], exclude, others, inv))
        merged = merge(existing.get(key, []), found)
        result[key] = merged
        total += len(found)
        print(f'  [{key}] 이번 수집 {len(found)}건 → 누적 {len(merged)}건')

    result['_updated'] = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S KST')
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), 'utf-8')
    print(f'\n완료 — 이번 수집 총 {total}건 (저장: {out_path})')


if __name__ == '__main__':
    main()
