"""
네이버 뉴스 검색 API → news.json 자동 갱신
여러 검색어로 각각 검색 후 합산, 제외 키워드 필터 적용
"""
import os, json, re, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

CLIENT_ID     = os.environ['NAVER_CLIENT_ID']
CLIENT_SECRET = os.environ['NAVER_CLIENT_SECRET']
API_URL       = 'https://openapi.naver.com/v1/search/news.json'
MAX_PER_CO    = 5  # 기업당 최대 뉴스 수

# (검색어 리스트, 제외 키워드 리스트)
COMPANIES = {
    'musinsoa': (
        ['무신사', '무신사스탠다드', '29CM'],
        []
    ),
    'kurly': (
        ['마켓컬리', '뷰티컬리', '컬리USA'],
        []
    ),
    'ohouse': (
        ['오늘의집', '버킷플레이스'],
        []
    ),
    'oasis': (
        ['오아시스마켓', '오아시스 새벽배송'],
        ['오아시스 음악', '갤러거', '록밴드']
    ),
    'goodai': (
        ['조선미녀', '스킨1004', '티르티르', '라운드랩', '구다이글로벌'],
        []
    ),
    'vinow': (
        ['넘버즈인', '비나우', '프위'],
        []
    ),
    'grace': (
        ['그레이스 뷰티', '그레이스 화장품'],
        ['그레이스 클럽', '골프', '은혜', '성당', '교회', '기도', '찬양', '축복', '그레이스풀']
    ),
    'bnb': (
        ['비앤비코리아', '진백글로벌'],
        []
    ),
    '2020': (
        ['이공이공', '이공이공 뷰티'],
        ['이공계', '이공계열', '이공대', '공대', '수학', '과학', '물리', '화학', '공학']
    ),
    'lafati': (
        ['레페리', '레페리 뷰티'],
        []
    ),
    'liman': (
        ['리만코리아', '인셀덤', '보타팜'],
        []
    ),
    'founders': (
        ['아누아', '더파운더즈'],
        []
    ),
    'olive': (
        ['올리브인터내셔널', '성분에디터', '밀크터치'],
        ['올리브영', '올리브나무', '올리브오일']
    ),
    'highlight': (
        ['하이라이트브랜즈', '말본골프', '코닥어패럴'],
        ['하이라이트 콘서트', '하이라이트 아이돌', '하이라이트 가수']
    ),
    'peacenow': (
        ['마르디메크르디', '피스피스스튜디오'],
        []
    ),
    'hagohouse': (
        ['하고하우스', '마뗑킴', '드파운드'],
        ['하트시그널', '드라마', '출연', '배우', '연예', '예능', '방송']
    ),
}

TAG_RULES = {
    'ipo':    ['상장','IPO','공모','주관사','예심','코스닥','코스피','기업공개'],
    'invest': ['투자','유치','펀드','지분','인수','M&A','PE','시리즈'],
    'global': ['글로벌','해외','미국','일본','유럽','중국','동남아','수출','아마존','세포라'],
    'risk':   ['적자','손실','하락','위기','리스크','과징금','소송','급락'],
    'biz':    ['매출','실적','브랜드','론칭','오픈','협업','캠페인','출시'],
}

def clean(text):
    return re.sub(r'<[^>]+>', '', text)\
             .replace('&quot;','"').replace('&#39;',"'")\
             .replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').strip()

def to_ym(pub_date):
    try:
        dt = datetime.strptime(pub_date, '%a, %d %b %Y %H:%M:%S %z')
        return dt.strftime('%Y.%m')
    except:
        return ''

def auto_tag(title, desc):
    text = title + ' ' + desc
    tags = [tag for tag, kws in TAG_RULES.items() if any(k in text for k in kws)]
    return tags or ['biz']

def fetch_one(keyword, exclude):
    """단일 키워드 검색 — 제목에 키워드 포함된 기사만 통과"""
    headers = {
        'X-Naver-Client-Id': CLIENT_ID,
        'X-Naver-Client-Secret': CLIENT_SECRET
    }
    params = {'query': keyword, 'display': 10, 'sort': 'date'}
    try:
        r = requests.get(API_URL, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        raw = r.json().get('items', [])
    except Exception as e:
        print(f"      ⚠ [{keyword}] API 오류: {e}")
        return []

    results = []
    for it in raw:
        title = clean(it.get('title', ''))
        desc  = clean(it.get('description', ''))
        full  = title + ' ' + desc

        # ① 제목에 검색 키워드가 반드시 포함돼야 통과
        if keyword not in title:
            continue

        # ② 제외 키워드가 제목+본문에 있으면 제외
        if any(ex in full for ex in exclude):
            continue

        url = it.get('originallink') or it.get('link', '')
        results.append({
            'date': to_ym(it.get('pubDate', '')),
            'hl':   title,
            'sum':  desc[:150],
            'tags': auto_tag(title, desc),
            'url':  url,
            '_kw':  keyword,
        })
    return results

def fetch_all(keywords, exclude, max_count):
    """여러 키워드 검색 후 합산·중복제거·최신순 정렬"""
    all_items = []
    seen_titles = set()

    for kw in keywords:
        items = fetch_one(kw, exclude)
        for item in items:
            if item['hl'] not in seen_titles:
                seen_titles.add(item['hl'])
                all_items.append(item)

    # 날짜 최신순 정렬
    all_items.sort(key=lambda x: x.get('date', ''), reverse=True)

    # 임시 키 제거
    for item in all_items:
        item.pop('_kw', None)

    return all_items[:max_count]

def main():
    existing = {}
    nf = Path('news.json')
    if nf.exists():
        try:
            existing = json.loads(nf.read_text('utf-8'))
        except:
            pass

    result = {}
    total = 0
    for key, (keywords, exclude) in COMPANIES.items():
        print(f"  [{key}] 검색어: {', '.join(keywords)}")
        items = fetch_all(keywords, exclude, MAX_PER_CO)
        if items:
            result[key] = items
            total += len(items)
            print(f"    → {len(items)}건 저장")
        else:
            result[key] = existing.get(key, [])
            print(f"    → 기존 유지 ({len(result[key])}건)")

    result['_updated'] = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S KST')
    nf.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(f"\n✅ 완료 — 총 {total}건")

if __name__ == '__main__':
    main()
