"""
네이버 뉴스 검색 API → news.json 자동 갱신
"""
import os, json, re, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

CLIENT_ID     = os.environ['NAVER_CLIENT_ID']
CLIENT_SECRET = os.environ['NAVER_CLIENT_SECRET']
API_URL       = 'https://openapi.naver.com/v1/search/news.json'
MAX_PER_CO    = 4

# 검색어만 사용, 필터 키워드 최소화
COMPANIES = {
    'musinsoa': '무신사',
    'kurly':    '마켓컬리',
    'ohouse':   '오늘의집',
    'oasis':    '오아시스마켓',
    'goodai':   '조선미녀',
    'vinow':    '넘버즈인',
    'grace':    '그레이스 화장품',
    'bnb':      '비앤비코리아',
    '2020':     '이공이공 뷰티',
    'lafati':   '레페리',
    'liman':    '리만코리아',
    'founders': '아누아',
    'olive':    '올리브인터내셔널',
    'highlight':'하이라이트브랜즈',
    'peacenow': '마르디메크르디',
    'hagohouse':'하고하우스',
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
             .replace('&quot;','"').replace('&#39;',"'").replace('&amp;','&')\
             .replace('&lt;','<').replace('&gt;','>').strip()

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

def fetch(keyword):
    headers = {
        'X-Naver-Client-Id': CLIENT_ID,
        'X-Naver-Client-Secret': CLIENT_SECRET
    }
    params = {'query': keyword, 'display': MAX_PER_CO, 'sort': 'date'}
    try:
        r = requests.get(API_URL, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        items = r.json().get('items', [])
        print(f"    API 응답 {len(items)}건")
    except Exception as e:
        print(f"    ⚠ API 오류: {e}")
        return []

    results = []
    for it in items:
        title = clean(it.get('title', ''))
        desc  = clean(it.get('description', ''))
        url   = it.get('originallink') or it.get('link', '')
        results.append({
            'date': to_ym(it.get('pubDate', '')),
            'hl':   title,
            'sum':  desc[:150],
            'tags': auto_tag(title, desc),
            'url':  url,
        })
    return results

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
    for key, keyword in COMPANIES.items():
        print(f"  [{key}] 검색: {keyword}")
        items = fetch(keyword)
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
