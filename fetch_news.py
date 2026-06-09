"""
네이버 뉴스 검색 API → news.json 자동 갱신
GitHub Actions에서 실행 (환경변수: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)
"""
import os, json, re, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

CLIENT_ID     = os.environ['NAVER_CLIENT_ID']
CLIENT_SECRET = os.environ['NAVER_CLIENT_SECRET']
API_URL       = 'https://openapi.naver.com/v1/search/news.json'
MAX_PER_CO    = 4

# (검색 키워드, 필터 키워드) — 필터는 OR 조건, 하나만 포함돼도 통과
COMPANIES = {
    'musinsoa': ('무신사',           ['무신사']),
    'kurly':    ('마켓컬리',         ['컬리']),
    'ohouse':   ('오늘의집',         ['오늘의집']),
    'oasis':    ('오아시스마켓',     ['오아시스']),
    'goodai':   ('조선미녀',         ['조선미녀', '구다이', '스킨1004', '티르티르']),
    'vinow':    ('넘버즈인',         ['넘버즈인', '비나우', '프위']),
    'grace':    ('그레이스 뷰티',    ['그레이스']),
    'bnb':      ('비앤비코리아',     ['비앤비코리아', '진백글로벌']),
    '2020':     ('이공이공',         ['이공이공']),
    'lafati':   ('레페리',           ['레페리']),
    'liman':    ('리만코리아',       ['리만코리아', '인셀덤']),
    'founders': ('아누아',           ['아누아', '더파운더즈']),
    'olive':    ('올리브인터내셔널', ['올리브인터내셔널', '성분에디터', '밀크터치']),
    'highlight':('하이라이트브랜즈', ['하이라이트브랜즈', '말본골프', '코닥어패럴']),
    'peacenow': ('마르디메크르디',   ['마르디', '피스피스']),
    'hagohouse':('하고하우스',       ['하고하우스', '마뗑킴', '드파운드']),
}

TAG_RULES = {
    'ipo':    ['상장', 'IPO', '공모', '주관사', '예심', '코스닥', '코스피', '기업공개'],
    'invest': ['투자', '유치', '펀드', '지분', '인수', 'M&A', 'PE', '시리즈'],
    'global': ['글로벌', '해외', '미국', '일본', '유럽', '중국', '동남아', '수출', '아마존', '세포라'],
    'risk':   ['적자', '손실', '하락', '위기', '리스크', '과징금', '소송', '급락'],
    'biz':    ['매출', '실적', '브랜드', '론칭', '오픈', '협업', '캠페인', '출시'],
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
        return pub_date[:7] if pub_date else ''

def auto_tag(title, desc):
    text = title + ' ' + desc
    tags = [tag for tag, kws in TAG_RULES.items() if any(k in text for k in kws)]
    return tags or ['biz']

def fetch(keyword, must_include):
    headers = {
        'X-Naver-Client-Id': CLIENT_ID,
        'X-Naver-Client-Secret': CLIENT_SECRET
    }
    params = {'query': keyword, 'display': 20, 'sort': 'date'}  # 최신순
    try:
        r = requests.get(API_URL, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        items = r.json().get('items', [])
    except Exception as e:
        print(f"  ⚠ API 오류: {e}")
        return []

    results = []
    seen = set()
    for it in items:
        title = clean(it.get('title', ''))
        desc  = clean(it.get('description', ''))
        full  = title + ' ' + desc
        # 필수 키워드 중 하나라도 포함되면 통과
        if not any(k in full for k in must_include):
            continue
        if title in seen:
            continue
        seen.add(title)
        results.append({
            'date': to_ym(it.get('pubDate', '')),
            'hl':   title,
            'sum':  desc[:150],
            'tags': auto_tag(title, desc),
            'url':  it.get('originallink') or it.get('link', ''),
        })
        if len(results) >= MAX_PER_CO:
            break
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
    for key, (keyword, must) in COMPANIES.items():
        print(f"  수집: {key} ({keyword})")
        items = fetch(keyword, must)
        if items:
            result[key] = items
            total += len(items)
            print(f"    → {len(items)}건 수집")
        else:
            result[key] = existing.get(key, [])
            print(f"    → 기존 데이터 유지 ({len(result[key])}건)")

    result['_updated'] = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S KST')
    nf.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(f"\n✅ 완료 — 총 {total}건 신규 수집")

if __name__ == '__main__':
    main()
