"""
DART → financials.json 자동 갱신

우선순위(회사별)
  1) 정기보고서 주요계정(fnlttSinglAcnt)  : 연간(사업보고서) + 올해 최신 3분기/반기/1분기 누적
  2) 감사보고서 원문 파싱(document.xml)   : 정기보고서 제출 의무가 없는 외감법인용 (연간만)
  3) 둘 다 없으면 기록하지 않음 → 화면은 수동 입력값 유지 (다음 실행 때 공시가 생기면 자동 반영)
- 조회 실패/데이터 없음이면 기존 financials.json의 해당 회사 값을 유지한다.
- 연결 재무제표 우선, 없으면 별도.
- 인증키는 환경변수 DART_API_KEY로만 받는다. (GitHub Actions Secrets 사용, 코드/로그에 노출 금지)

사용법
  PowerShell : $env:DART_API_KEY="발급받은키"; python fetch_dart.py
  옵션       : --year 2025 (연간 기준연도)  --out financials.json  --selftest
"""
import argparse, io, json, os, re, sys, time, zipfile
from html import unescape
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE = 'https://opendart.fss.or.kr/api'
TIMEOUT = 30
MAX_ZIP_BYTES = 60 * 1024 * 1024
MAX_DOC_BYTES = 40 * 1024 * 1024
SLEEP = 0.3

# key(대시보드) → (DART 검색 법인명, 사업자등록번호 또는 None)
COMPANIES = {
    'musinsoa':  ('무신사', None),
    'kurly':     ('컬리', None),
    'ohouse':    ('버킷플레이스', None),
    'oasis':     ('오아시스', '126-86-45211'),
    'goodai':    ('구다이글로벌', None),
    'founders':  ('더파운더즈', None),
    'vinow':     ('비나우', None),
    'olive':     ('올리브인터내셔널', None),
    'liman':     ('리만코리아', None),
    'grace':     ('그레이스', '213-81-95999'),
    '2020':      ('이공이공', None),
    'bnb':       ('비앤비코리아', '130-86-60341'),
    'lafati':    ('레페리', None),
    'nmodelin':  ('앤마들린', None),
    'rebom':     ('리봄화장품', None),
    'highlight': ('하이라이트브랜즈', None),
    'hagohouse': ('하고하우스', None),
    'boosterz':  ('부스터즈', None),
    'peacenow':  ('피스피스스튜디오', None),
    'grainon':   ('그레인온', None),
}

SALES_NAMES = ('매출액', '수익(매출액)', '영업수익', '매출')
OP_NAMES = ('영업이익', '영업이익(손실)')

# 최신 순서: (reprt_code, 화면 표기, 누적 금액을 add_amount에서 읽을지 여부)
INTERIM = [
    ('11014', '3분기 누적', True),   # 1~9월
    ('11012', '반기 누적',  True),   # 1~6월
    ('11013', '1분기',      False),  # 1~3월 (3개월 = 누적)
]


def norm_name(n):
    return re.sub(r'\(주\)|주식회사|\s', '', n or '')


def norm_biz(n):
    return re.sub(r'\D', '', n or '')


def to_won(s):
    """'2,367,100,000,000' / '-14,700,000,000' → int(원). 불가 시 None."""
    try:
        return int(str(s).replace(',', '').strip())
    except Exception:
        return None


def api_get(path, key, **params):
    params['crtfc_key'] = key
    r = requests.get(f'{BASE}/{path}', params=params, timeout=TIMEOUT)
    r.raise_for_status()
    time.sleep(SLEEP)
    return r


def load_corp_codes(key):
    r = api_get('corpCode.xml', key)
    if len(r.content) > MAX_ZIP_BYTES:
        raise RuntimeError('corpCode 응답이 비정상적으로 큽니다.')
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        raise RuntimeError('corpCode 다운로드 실패(키 유효성/일일 한도 확인): ' + r.text[:200].replace(key, '***'))
    name = zf.namelist()[0]
    if zf.getinfo(name).file_size > MAX_ZIP_BYTES * 4:
        raise RuntimeError('압축 해제 크기가 비정상적으로 큽니다.')
    root = ET.fromstring(zf.read(name))
    return [(e.findtext('corp_code', '').strip(), e.findtext('corp_name', '').strip(),
             e.findtext('stock_code', '').strip()) for e in root.iter('list')]


def resolve_corp(key, rows, name, biz):
    """이름 정확 일치 → 1건이면 확정, 여러 건이면 사업자등록번호로 구분. 실패 시 None."""
    q = norm_name(name)
    exact = [r for r in rows if norm_name(r[1]) == q]
    if biz and exact:
        hits = []
        for code, nm, st in exact[:40]:
            try:
                b = api_get('company.json', key, corp_code=code).json().get('bizr_no', '')
            except Exception:
                continue
            if norm_biz(b) == norm_biz(biz):
                hits.append((code, nm, st))
        return hits[0] if len(hits) == 1 else None
    return exact[0] if len(exact) == 1 else None


# ───────────── 1) 정기보고서 주요계정 ─────────────
def extract_accounts(rows, cumulative=False):
    """
    주요계정 rows → {fs_div: {'sales': (당기, 전기), 'op': (당기, 전기)}} (원)
    cumulative=True : 반기/3분기 보고서의 '누적' 금액(thstrm_add_amount / frmtrm_add_amount)만 사용.
                      (누적 값이 없으면 3개월 값과 혼동하지 않도록 해당 계정은 건너뜀)
    cumulative=False: 연간·1분기 보고서의 thstrm_amount / frmtrm_amount 사용.
    """
    best = {}
    for row in rows:
        nm = (row.get('account_nm') or '').strip()
        kind = 'sales' if nm in SALES_NAMES else 'op' if nm in OP_NAMES else None
        if not kind:
            continue
        if cumulative:
            cur, prev = to_won(row.get('thstrm_add_amount')), to_won(row.get('frmtrm_add_amount'))
        else:
            cur, prev = to_won(row.get('thstrm_amount')), to_won(row.get('frmtrm_amount'))
        if cur is None:
            continue
        best.setdefault(row.get('fs_div'), {}).setdefault(kind, (cur, prev))
    return best


def pick_basis(best):
    """연결(CFS) 우선, 없으면 별도(OFS) → (라벨, sales(당,전), op(당,전)) 또는 None"""
    for fs, label in (('CFS', '연결'), ('OFS', '별도')):
        if fs in best and ('sales' in best[fs] or 'op' in best[fs]):
            return label, best[fs].get('sales', (None, None)), best[fs].get('op', (None, None))
    return None


def fetch_report(key, corp_code, year, reprt_code, cumulative):
    d = api_get('fnlttSinglAcnt.json', key, corp_code=corp_code,
                bsns_year=str(year), reprt_code=reprt_code).json()
    if d.get('status') != '000':
        return None
    return pick_basis(extract_accounts(d.get('list', []), cumulative))


def fetch_main(key, corp_code, year):
    got = fetch_report(key, corp_code, year, '11011', False)
    if not got:
        return None
    label, s, o = got
    return {'year': year, 'basis': label, 'src': 'periodic',
            'sales': s[0], 'sales_prev': s[1], 'op': o[0], 'op_prev': o[1]}


def fetch_interim(key, corp_code, year):
    """올해 가장 최근 분기/반기 보고서 누적값 → dict 또는 None"""
    for code, label, cum in INTERIM:
        got = fetch_report(key, corp_code, year, code, cum)
        if got:
            basis, s, o = got
            return {'year': year, 'report': label, 'basis': basis,
                    'sales': s[0], 'sales_prev': s[1], 'op': o[0], 'op_prev': o[1]}
    return None


# ───────────── 2) 감사보고서 원문 파싱 (주요계정이 없는 외감법인용) ─────────────
UNIT_RE = re.compile(r'단위\s*[:：]?\s*(백만원|천원|억원|원)')
UNIT_MUL = {'원': 1, '천원': 10**3, '백만원': 10**6, '억원': 10**8}
LEAD_RE = re.compile(r'^(?:[\dⅠ-Ⅻ]+|[IVX]+)[\.\)]\s*|^\(\d+\)\s*')
NUM_RE = re.compile(r'^[\(△\-]?\s*\d[\d,]*\s*\)?$')


def _clean_cell(s):
    return unescape(re.sub(r'<[^>]+>', '', s)).replace('\xa0', ' ').strip()


def _parse_num(t):
    t = t.strip()
    if not NUM_RE.match(t):
        return None
    neg = t.startswith('(') or t.startswith('△') or t.startswith('-')
    v = int(re.sub(r'[^\d]', '', t))
    return -v if neg else v


def _label(t):
    return LEAD_RE.sub('', re.sub(r'\s+', '', t))


def parse_income_rows(text):
    """
    감사보고서 본문(XML/HTML)에서 손익계산서의 매출액·영업이익 첫 행을 찾는다.
    반환: {'sales': (당기, 전기), 'op': (당기, 전기)} (원 단위) — 없으면 빈 dict
    (표 순서상 재무상태표 → 손익계산서이므로 첫 일치 행 사용. 연결 보고서면 연결 값이 먼저 나옴)
    """
    units = [(m.start(), UNIT_MUL[m.group(1)]) for m in UNIT_RE.finditer(text)]
    out = {}
    for tr in re.finditer(r'<TR[^>]*>(.*?)</TR>', text, re.S | re.I):
        cells = [_clean_cell(c) for c in re.findall(r'<T[DEH][^>]*>(.*?)</T[DEH]>', tr.group(1), re.S | re.I)]
        if len(cells) < 2:
            continue
        lab = _label(cells[0])
        kind = 'sales' if lab in SALES_NAMES else 'op' if lab in OP_NAMES else None
        if not kind or kind in out:
            continue
        raw = [c for c in cells[1:] if _parse_num(c) is not None]
        nums = [_parse_num(c) for c in raw]
        # 주석 번호 열(콤마 없는 작은 정수)이 앞에 끼는 경우 제거
        if len(nums) >= 3 and ',' not in raw[0] and abs(nums[0]) < 100:
            nums = nums[1:]
        if not nums:
            continue
        mul = 1
        for pos, m in units:
            if pos < tr.start():
                mul = m
        out[kind] = (nums[0] * mul, nums[1] * mul if len(nums) > 1 else None)
        if len(out) == 2:
            break
    return out


def find_audit_report(key, corp_code, year):
    """해당 사업연도 감사보고서 접수번호 → (rcept_no, 연결여부) 또는 None"""
    d = api_get('list.json', key, corp_code=corp_code, bgn_de=f'{year + 1}0101',
                end_de=f'{year + 1}1231', pblntf_ty='F', page_count='100').json()
    if d.get('status') != '000':
        return None
    cands = [x for x in d.get('list', []) if '감사보고서' in (x.get('report_nm') or '')]
    if not cands:
        return None
    cons = [x for x in cands if '연결' in x['report_nm']]
    pick = max(cons or cands, key=lambda x: x.get('rcept_dt', ''))
    return pick['rcept_no'], bool(cons)


def download_report_text(key, rcept_no):
    r = api_get('document.xml', key, rcept_no=rcept_no)
    if len(r.content) > MAX_DOC_BYTES:
        raise RuntimeError('보고서 원문이 비정상적으로 큽니다.')
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        raise RuntimeError('document.xml 응답이 zip이 아닙니다(한도/오류).')
    texts = []
    for info in zf.infolist():
        if info.file_size > MAX_DOC_BYTES:
            continue
        raw = zf.read(info.filename)
        for enc in ('utf-8', 'euc-kr', 'cp949'):
            try:
                texts.append(raw.decode(enc))
                break
            except UnicodeDecodeError:
                continue
    return '\n'.join(texts)


def fetch_audit(key, corp_code, year):
    """감사보고서 원문 파싱 → fetch_main과 같은 형태(src='audit') 또는 None"""
    found = find_audit_report(key, corp_code, year)
    if not found:
        return None
    rcept_no, is_cons = found
    got = parse_income_rows(download_report_text(key, rcept_no))
    if 'sales' not in got and 'op' not in got:
        return None
    s, o = got.get('sales', (None, None)), got.get('op', (None, None))
    return {'year': year, 'basis': '연결' if is_cons else '별도', 'src': 'audit', 'rcept_no': rcept_no,
            'sales': s[0], 'sales_prev': s[1], 'op': o[0], 'op_prev': o[1]}


def selftest():
    assert to_won('2,367,100,000,000') == 2367100000000
    assert to_won('-14,700,000,000') == -14700000000 and to_won('') is None
    rows = [
        {'account_nm': '매출액', 'fs_div': 'OFS', 'thstrm_amount': '100', 'frmtrm_amount': '80'},
        {'account_nm': '영업이익', 'fs_div': 'OFS', 'thstrm_amount': '10', 'frmtrm_amount': '8'},
        {'account_nm': '매출액', 'fs_div': 'CFS', 'thstrm_amount': '1,200', 'frmtrm_amount': '1,000'},
        {'account_nm': '영업이익(손실)', 'fs_div': 'CFS', 'thstrm_amount': '-30', 'frmtrm_amount': '5'},
        {'account_nm': '당기순이익', 'fs_div': 'CFS', 'thstrm_amount': '9', 'frmtrm_amount': '9'},
    ]
    b = extract_accounts(rows)
    assert b['CFS']['sales'] == (1200, 1000) and b['CFS']['op'] == (-30, 5)
    assert b['OFS']['sales'] == (100, 80)
    assert pick_basis(b)[0] == '연결'
    half = [
        {'account_nm': '매출액', 'fs_div': 'CFS', 'thstrm_amount': '600', 'thstrm_add_amount': '1,100',
         'frmtrm_amount': '500', 'frmtrm_add_amount': '900'},
        {'account_nm': '영업이익', 'fs_div': 'CFS', 'thstrm_amount': '40', 'thstrm_add_amount': '70',
         'frmtrm_amount': '30', 'frmtrm_add_amount': '50'},
        {'account_nm': '매출액', 'fs_div': 'OFS', 'thstrm_amount': '300', 'thstrm_add_amount': '',
         'frmtrm_amount': '200', 'frmtrm_add_amount': ''},   # 누적 없음 → 건너뜀
    ]
    h = extract_accounts(half, cumulative=True)
    assert h['CFS']['sales'] == (1100, 900) and h['CFS']['op'] == (70, 50)
    assert 'OFS' not in h
    assert pick_basis({}) is None
    # 감사보고서 원문 파싱 (천원 단위, 주석번호 열, 괄호 음수)
    doc = '''<BODY><P>(단위 : 원)</P><TABLE><TR><TD>자산총계</TD><TD>9</TD></TR></TABLE>
    <P>손익계산서</P><P>(단위 : 천원)</P><TABLE>
    <TR><TD>과목</TD><TD>주석</TD><TD>제5기</TD><TD>제4기</TD></TR>
    <TR><TD>Ⅰ. 매출액</TD><TD>20</TD><TD>123,456,789</TD><TD>100,000,000</TD></TR>
    <TR><TD>Ⅴ. 영업이익(손실)</TD><TD></TD><TD>(2,345,000)</TD><TD>5,000</TD></TR>
    <TR><TD>매출액</TD><TD>1</TD><TD>1</TD></TR></TABLE></BODY>'''
    g = parse_income_rows(doc)
    assert g['sales'] == (123456789000, 100000000000), g
    assert g['op'] == (-2345000000, 5000000), g
    assert parse_income_rows('<TR><TD>당기순이익</TD><TD>1,000</TD></TR>') == {}
    assert _parse_num('△1,234') == -1234 and _parse_num('abc') is None
    print('파싱 로직 OK')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=datetime.now().year - 1)
    ap.add_argument('--out', default='financials.json')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return

    key = os.environ.get('DART_API_KEY', '').strip()
    if not key:
        sys.exit('환경변수 DART_API_KEY가 없습니다. (키를 코드에 직접 넣지 마세요)')

    this_year = datetime.now().year
    out = Path(a.out)
    existing = {}
    if out.exists():
        try:
            existing = json.loads(out.read_text('utf-8'))
        except Exception:
            existing = {}

    rows = load_corp_codes(key)
    result, ok = {}, 0
    for k, (name, biz) in COMPANIES.items():
        try:
            hit = resolve_corp(key, rows, name, biz)
            if not hit:
                print(f'  [{k}] 법인 특정 실패 → 기존 유지')
                if k in existing:
                    result[k] = existing[k]
                continue
            main_d = fetch_main(key, hit[0], a.year)
            if main_d is None:                       # 정기보고서 없음 → 감사보고서 원문 파싱
                try:
                    main_d = fetch_audit(key, hit[0], a.year)
                except Exception as e:
                    print(f"  [{k}] 감사보고서 파싱 오류: {str(e).replace(key, '***')[:100]}")
            interim = fetch_interim(key, hit[0], this_year)
            if main_d is None and interim is None:
                print(f'  [{k}] 공시 데이터 없음 → 기존 유지')
                if k in existing:
                    result[k] = existing[k]
                continue
            data = main_d or {'year': None, 'basis': None, 'src': None, 'sales': None,
                              'sales_prev': None, 'op': None, 'op_prev': None}
            if interim:
                data['interim'] = interim
            elif isinstance(existing.get(k), dict) and existing[k].get('interim'):
                data['interim'] = existing[k]['interim']      # 이번에 못 받으면 이전 값 유지
            data['corp_code'] = hit[0]
            result[k] = data
            ok += 1
            msg = f"  [{k}] ({data.get('src')}) 연간 {data['year']} 매출 {data['sales']} / OP {data['op']}"
            if interim:
                msg += f" | {interim['year']} {interim['report']} 매출 {interim['sales']} / OP {interim['op']}"
            print(msg)
        except Exception as e:                    # 키가 메시지에 섞이지 않도록 마스킹
            print(f"  [{k}] 오류: {str(e).replace(key, '***')[:120]} → 기존 유지")
            if k in existing:
                result[k] = existing[k]

    result['_year'] = a.year
    result['_updated'] = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S KST')
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(f'\n저장: {out}  |  이번 실행 갱신 {ok}개사')


if __name__ == '__main__':
    main()
