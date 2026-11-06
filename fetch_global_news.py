"""
해외 진출 전용 뉴스 수집 (네이버 뉴스 검색 API) → global_news.json

- 기존 news.json 수집기와 별개로 동작한다. (기존 파일 수정 불필요)
- 모든 기업에 대해 "{이름} + 해외 진출/수출/미국/일본/중국/동남아/유럽 ..." 형태로 검색해
  해외 사업 문맥(진출·수출·입점·현지법인 등)이 있는 기사만 남긴다.
- 기존 global_news.json 이력은 유지하며 누적한다(회사당 최대 30건). 새 결과가 0건이면 기존 값 유지.
- build_insights.py 가 이 파일을 읽어 국가/파트너/상품 요약을 만든다.
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
KEEP_PER_CO = 30
SUFFIXES = ['해외 진출', '수출', '글로벌', '미국', '일본', '중국', '동남아', '유럽']

# key: (검색 이름(최대 3개, 제목에 하나라도 있어야 통과), 제외 키워드(제목+요약))
COMPANIES = {
    'musinsoa': (['무신사', '무신사 스탠다드', '29CM'], []),
    'kurly':    (['컬리', '마켓컬리', '뷰티컬리'], ['참프레', '오아시스', '단발', '컬리헤어']),
    'ohouse':   (['오늘의집', '버킷플레이스'], []),
    'oasis':    (['오아시스마켓', '오아시스 새벽배송'], ['갤러거', '록밴드']),
    'goodai':   (['조선미녀', '스킨1004', '티르티르', '라운드랩', '구다이글로벌'], []),
    'vinow':    (['넘버즈인', '비나우', '프위'], []),
    'grace':    (['그레이스 뷰티', '그레이스 화장품'], ['골프', '은혜', '성당', '교회', '기도', '찬양', '축복']),
    'bnb':      (['비앤비코리아', '진백글로벌'], []),
    '2020':     (['이공이공', 'egongegong'], ['이공계', '이공계열', '이공대', '공대']),
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

# 해외 사업 문맥 + 국가/파트너 중 하나
GLOBAL_CTX = re.compile(r'진출|수출|입점|출점|현지|법인|해외|글로벌|론칭|오픈|팝업|상륙|공략|매장|계약|파트너십|공급|납품|역직구|유통망|채널')
PLACES = re.compile(r'미국|미주|북미|일본|중국|상하이|베이징|티몰|동남아|베트남|태국|인도네시아|말레이시아|싱가포르|필리핀|유럽|영국|프랑스|독일|이탈리아|스페인|'
                    r'중동|사우디|UAE|인도|호주|대만|캐나다|홍콩|브라질|멕시코|몽골|카자흐스탄|러시아|튀르키예|터키|'
                    r'아마존|세포라|얼타|울타|돈키호테|라쿠텐|쇼피|틱톡샵|큐텐|코스트코|월마트|타깃|로프트|도큐핸즈|라자다|타오바오|샤오홍슈|DHL', re.I)
# 'IPO·상장'만 다룬 기사는 진출 정보가 아니므로 제외
IPO_ONLY = re.compile(r'상장|IPO|기업공개|예심|공모')


def clean(t):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', t or '')
                  .replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&')
                  .replace('&lt;', '<').replace('&gt;', '>')).strip()


def to_ym(pub):
    try:
        return datetime.strptime(pub, '%a, %d %b %Y %H:%M:%S %z').strftime('%Y.%m')
    except Exception:
        return ''


def keep_article(title, desc, names, exclude):
    """제목에 회사/브랜드명, 해외 문맥+국가/파트너 존재, 제외 키워드 없음, IPO 전용 기사 아님"""
    full = title + ' ' + desc
    if not any(n in title for n in names):
        return False
    if any(x in full for x in exclude):
        return False
    if not (GLOBAL_CTX.search(full) and PLACES.search(full)):
        return False
    if IPO_ONLY.search(title) and not re.search(r'진출|수출|입점|출점|론칭|현지', title):
        return False
    return True


def safe_url(u):
    return u if isinstance(u, str) and re.match(r'^https?://', u) else ''


def fetch_query(session, headers, q):
    r = session.get(API_URL, headers=headers, params={'query': q, 'display': 20, 'sort': 'date'}, timeout=TIMEOUT)
    r.raise_for_status()
    time.sleep(SLEEP)
    return r.json().get('items', [])


def merge(old, new):
    seen, out = set(), []
    for a in new + old:
        k = a.get('url') or a['hl']
        if k in seen:
            continue
        seen.add(k)
        out.append(a)
    out.sort(key=lambda a: a.get('date', ''), reverse=True)
    return out[:KEEP_PER_CO]


def selftest():
    ok = keep_article('조선미녀, 미국 세포라 입점 확대', '', ['조선미녀'], [])
    assert ok
    assert not keep_article('조선미녀 신제품 출시', '국내 올리브영 판매', ['조선미녀'], [])          # 해외 문맥 없음
    assert not keep_article('미국 세포라 입점 확대', '', ['조선미녀'], [])                         # 제목에 회사명 없음
    assert not keep_article('오아시스 미국 투어 공연', '갤러거 해외 공연', ['오아시스마켓', '오아시스'], ['갤러거'])
    assert not keep_article('구다이글로벌 코스피 상장 추진', '일본 자회사 상장 무산', ['구다이글로벌'], [])   # IPO 전용
    assert keep_article('컬리, 미국 진출…역직구 확대', '', ['컬리'], ['단발'])
    m = merge([{'hl': 'a', 'url': 'u1', 'date': '2026.01'}], [{'hl': 'a2', 'url': 'u1', 'date': '2026.02'}, {'hl': 'b', 'url': 'u2', 'date': '2026.03'}])
    assert [x['url'] for x in m] == ['u2', 'u1'] and m[1]['hl'] == 'a2'
    assert safe_url('javascript:alert(1)') == '' and safe_url('https://a.com') == 'https://a.com'
    assert clean('<b>가</b>&quot;나&quot;') == '가"나"'
    print('필터 로직 OK')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='global_news.json')
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

    out_path = Path(a.out)
    existing = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text('utf-8'))
        except Exception:
            existing = {}

    result, total = {}, 0
    for key, (names, exclude) in COMPANIES.items():
        found, errors = [], 0
        for name in names[:3]:
            for suf in SUFFIXES:
                try:
                    items = fetch_query(session, headers, f'{name} {suf}')
                except Exception as e:                       # 키가 메시지에 섞이지 않도록 마스킹
                    errors += 1
                    msg = str(e).replace(cid, '***').replace(sec, '***')[:80]
                    if errors <= 2:
                        print(f'    ⚠ [{name} {suf}] {msg}')
                    continue
                for it in items:
                    title, desc = clean(it.get('title')), clean(it.get('description'))
                    if keep_article(title, desc, names, exclude):
                        found.append({'date': to_ym(it.get('pubDate', '')), 'hl': title,
                                      'sum': desc[:150], 'url': safe_url(it.get('originallink') or it.get('link', ''))})
        merged = merge(existing.get(key, []), found)
        result[key] = merged
        total += len(found)
        print(f'  [{key}] 이번 수집 {len(found)}건 → 누적 {len(merged)}건')

    result['_updated'] = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S KST')
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), 'utf-8')
    print(f'\n완료 — 이번 수집 총 {total}건 (저장: {out_path})')


if __name__ == '__main__':
    main()
