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
        ['컬리', '마켓컬리', '뷰티컬리'],
        ['참프레', '쿠팡', '이마트', '롯데', 'SSG', '오아시스', '코스트코', '홈플러스']
    ),
    'ohouse': (
        ['오늘의집', '버킷플레이스'],
        []
    ),
    'oasis': (
        ['오아시스마켓', '오아시스 새벽배송'],
        ['갤러거', '록밴드']
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
        ['골프', '은혜', '성당', '교회', '기도', '찬양', '축복']
    ),
    'bnb': (
        ['비앤비코리아', '진백글로벌'],
        []
    ),
    '2020': (
        ['이공이공'],
        ['이공계', '이공계열', '이공대', '공대', '수학', '과학', '물리', '화학', '공학']
    ),
    'lafati': (
        ['레페리'],
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
        ['콘서트', '아이돌', '가수']
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

# risk 태그 긍정 문맥 예외 패턴
RISK_EXCLUDE = [
    '적자 탈피', '적자 해소', '적자 졸업', '적자에서 흑자',
    '흑자 전환', '흑자전환', '적자폭 축소', '적자 탈출',
    '첫 흑자', '흑자 달성', '적자 개선', '손실 축소',
    '적자 축소', '턴어라운드', '흑자로'
]

def auto_tag(title, desc):
    text = title + ' ' + desc
    tags = []
    for tag, kws in TAG_RULES.items():
        if tag == 'risk':
            has_risk = any(k in text for k in kws)
            is_positive = any(ex in text for ex in RISK_EXCLUDE)
            if has_risk and not is_positive:
                tags.append('risk')
        else:
            if any(k in text for k in kws):
                tags.append(tag)
    return tags or ['biz']

def fetch_one(keyword, all_keywords, exclude):
    """단일 키워드 검색"""
    headers = {
        'X-Naver-Client-Id': CLIENT_ID,
        'X-Naver-Client-Secret': CLIENT_SECRET
    }
    params = {'query': keyword, 'display': 20, 'sort': 'date'}
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

        # ① 제목에 검색어 리스트 중 하나라도 반드시 포함돼야 통과
        if not any(kw in title for kw in all_keywords):
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
        })
    return results

def fetch_all(keywords, exclude, max_count):
    """여러 키워드 검색 후 합산·중복제거·최신순 정렬·최대 개수 제한"""
    all_items = []
    seen_titles = set()

    for kw in keywords:
        items = fetch_one(kw, keywords, exclude)
        for item in items:
            if item['hl'] not in seen_titles:
                seen_titles.add(item['hl'])
                all_items.append(item)

    # 날짜 최신순 정렬
    all_items.sort(key=lambda x: x.get('date', ''), reverse=True)

    # 최대 개수 제한
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
