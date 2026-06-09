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
MAX_PER_CO    = 4   # 기업당 최대 뉴스 수

# 기업 key → (검색 키워드, 필수포함 키워드 목록)
COMPANIES = {
    'musinsoa': ('무신사', ['무신사']),
    'kurly':    ('컬리 마켓컬리', ['컬리']),
    'ohouse':   ('오늘의집', ['오늘의집']),
    'oasis':    ('오아시스마켓', ['오아시스']),
    'goodai':   ('구다이글로벌', ['구다이', '조선미녀', '스킨1004', '티르티르']),
    'vinow':    ('비나우 넘버즈인', ['비나우', '넘버즈인']),
    'grace':    ('그레이스 grace-bt', ['그레이스']),
    'bnb':      ('비앤비코리아', ['비앤비코리아']),
    '2020':     ('이공이공 egongegong', ['이공이공']),
    'lafati':   ('레페리', ['레페리']),
    'liman':    ('리만코리아', ['리만코리아', '리만']),
    'founders': ('더파운더즈 아누아', ['더파운더즈', '아누아']),
    'olive':    ('올리브인터내셔널', ['올리브인터내셔널']),
    'highlight':('하이라이트브랜즈', ['하이라이트브랜즈', '말본골프', '코닥어패럴']),
    'peacenow': ('피스피스스튜디오 마르디메크르디', ['피스피스', '마르디']),
    'hagohouse':('하고하우스 마뗑킴', ['하고하우스', '마뗑킴']),
}

# 태그 자동 분류 키워드
TAG_RULES = {
    'ipo':    ['상장', 'IPO', '공모', '주관사', '예심', '코스닥', '코스피'],
    'invest': ['투자', '유치', '펀드', '지분', '인수', 'M&A', 'PE'],
    'global': ['글로벌', '해외', '미국', '일본', '유럽', '중국', '동남아', '수출'],
    'risk':   ['적자', '손실', '하락', '위기', '리스크', '과징금', '소송'],
    'biz':    ['매출', '실적', '브랜드', '론칭', '오픈', '협업', '캠페인'],
}

def clean(text):
    return re.sub(r'<[^>]+>', '', text).replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&').strip()

def to_ym(pub_date):
    try:
        dt = datetime.strptime(pub_date, '%a, %d %b %Y %H:%M:%S %z')
        return dt.strftime('%Y.%m')
    except:
        return pub_date[:7]

def auto_tag(title, desc):
    text = title + ' ' + desc
    tags = []
    for tag, kws in TAG_RULES.items():
        if any(k in text for k in kws):
            tags.append(tag)
    return tags or ['biz']

def fetch(keyword, must_include):
    headers = {'X-Naver-Client-Id': CLIENT_ID, 'X-Naver-Client-Secret': CLIENT_SECRET}
    params  = {'query': keyword, 'display': 20, 'sort': 'sim'}
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
        if not any(k in title or k in desc for k in must_include):
            continue
        if title in seen:
            continue
        seen.add(title)
        results.append({
            'date': to_ym(it.get('pubDate', '')),
            'hl':   title,
            'sum':  desc[:120],
            'tags': auto_tag(title, desc),
            'url':  it.get('link', ''),
        })
        if len(results) >= MAX_PER_CO:
            break
    return results

def main():
    # 기존 news.json 로드 (폴백용)
    existing = {}
    nf = Path('news.json')
    if nf.exists():
        try:
            existing = json.loads(nf.read_text('utf-8'))
        except:
            pass

    result = {}
    for key, (keyword, must) in COMPANIES.items():
        print(f"  수집: {key} ({keyword})")
        items = fetch(keyword, must)
        if items:
            result[key] = items
            print(f"    → {len(items)}건")
        else:
            # 수집 실패 시 기존 데이터 유지
            result[key] = existing.get(key, [])
            print(f"    → 기존 데이터 유지 ({len(result[key])}건)")

    result['_updated'] = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S KST')
    nf.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(f"\n✅ news.json 저장 완료 — {sum(len(v) for v in result.values() if isinstance(v, list))}건")

if __name__ == '__main__':
    main()
