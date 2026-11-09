"""
상장 완료 기업 주가·시총 수집 (공공데이터포털 금융위원회 '주식시세정보' API) → price.json

- 대상: price_config.json 에 적힌 상장 기업만(미상장 기업은 시세 없음).
- 인증키는 환경변수 DATA_GO_KR_KEY 로만 받는다(코드·로그 출력 금지, 오류 메시지에서도 마스킹).
  Encoding 키(% 포함)·Decoding 키(원문) 어느 쪽을 넣어도 동작하도록 자동 처리하고, 앞뒤 공백·줄바꿈은 제거한다.
- 데이터는 영업일 기준 '전일 종가'이며 실시간 시세가 아니다. 공모가는 사람이 price_config.json 에 입력한 값만 사용한다.
- API 조회가 실패하면: ① price_config.json 의 'manual'(사람이 확인해 입력한 종가·시총)이 있으면 그 값을 '(수기)' 표시로 사용,
  ② 없으면 기존 price.json 값을 유지한다. API 조회가 성공하면 항상 API 값이 우선이다.

price_config.json 예시
  {"peacenow": {"name": "피스피스스튜디오", "code": "0117P0", "ipo_price": null,
                "manual": {"date": "2026.11.06", "close": 12300, "rate": 0.82, "mktcap": 345000000000}}}
  (manual 은 선택 항목. 값은 거래소·증권사 화면에서 확인한 것만 넣는다. mktcap 단위는 원.)

사용법
  python fetch_price.py            # 실제 수집(환경변수 필요)
  python fetch_price.py --selftest # 파싱 로직 점검(네트워크·키 불필요)
"""
import argparse, json, os, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

API_URL = 'https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo'
KST = timezone(timedelta(hours=9))


def num(v):
    try:
        return float(str(v).replace(',', ''))
    except Exception:
        return None


def key_for_url(key):
    """이미 인코딩된 키(% 포함)는 그대로, 원문(Decoding) 키는 한 번만 인코딩한다 → 이중 인코딩 방지"""
    return key if '%' in key else quote(key, safe='')


def api_status(j):
    try:
        h, b = j['response']['header'], j['response']['body']
        return str(h.get('resultCode')), str(h.get('resultMsg')), b.get('totalCount')
    except Exception:
        return '', '', None


def parse_items(j):
    try:
        items = j['response']['body']['items']['item']
    except Exception:
        return []
    if isinstance(items, dict):
        items = [items]
    return [x for x in items if isinstance(x, dict)]


def _norm(s):
    return re.sub(r'\s|\(주\)|주식회사', '', str(s or ''))


def latest(items, name='', code=''):
    """종목코드(있으면) 또는 종목명이 일치하는 항목 중 가장 최근 기준일(basDt) 1건. 정확히 일치가 없으면 이름 포함 항목도 허용."""
    ok = [x for x in items if re.fullmatch(r'\d{8}', str(x.get('basDt', '')))]

    def pick(rows):
        rows.sort(key=lambda x: x['basDt'], reverse=True)
        return rows[0] if rows else None
    if code:
        r = pick([x for x in ok if str(x.get('srtnCd', '')).strip().lstrip('A') == code.strip().lstrip('A')])
        if r:
            return r
    r = pick([x for x in ok if _norm(x.get('itmsNm')) == _norm(name)])
    if r:
        return r
    return pick([x for x in ok if _norm(name) and _norm(name) in _norm(x.get('itmsNm'))])


def build_entry(row, cfg):
    close, rate, chg = num(row.get('clpr')), num(row.get('fltRt')), num(row.get('vs'))
    mc, shares = num(row.get('mrktTotAmt')), num(row.get('lstgStCnt'))
    if close is None:
        return None
    d = row['basDt']
    e = {'name': row.get('itmsNm', ''), 'code': str(row.get('srtnCd', '')), 'market': str(row.get('mrktCtg', '')),
         'date': f'{d[:4]}.{d[4:6]}.{d[6:]}', 'close': int(close), 'change': chg, 'rate': rate,
         'mktcap': int(mc) if mc is not None else None, 'shares': int(shares) if shares is not None else None}
    ipo = num(cfg.get('ipo_price'))
    if ipo:
        e['ipo_price'] = int(ipo)
        e['vs_ipo'] = round((close / ipo - 1) * 100, 1)
    return e


def manual_entry(cfg):
    """사람이 입력한 종가·시총(manual) → price.json 항목. 기준일 뒤에 '(수기)'를 붙여 화면에서 구분한다."""
    m = cfg.get('manual')
    if not isinstance(m, dict) or num(m.get('close')) is None:
        return None
    close, mc, rate = num(m.get('close')), num(m.get('mktcap')), num(m.get('rate'))
    e = {'name': cfg.get('name', ''), 'code': str(cfg.get('code', '')), 'market': '', 'date': f"{m.get('date', '')} (수기)",
         'close': int(close), 'change': None, 'rate': rate, 'mktcap': int(mc) if mc is not None else None, 'shares': None}
    ipo = num(cfg.get('ipo_price'))
    if ipo:
        e['ipo_price'] = int(ipo)
        e['vs_ipo'] = round((close / ipo - 1) * 100, 1)
    return e


def selftest():
    j = {'response': {'header': {'resultCode': '00', 'resultMsg': 'NORMAL SERVICE.'}, 'body': {'totalCount': 3, 'items': {'item': [
        {'basDt': '20261106', 'itmsNm': '피스피스스튜디오', 'srtnCd': '0117P0', 'mrktCtg': 'KOSDAQ', 'clpr': '12,300', 'vs': '100', 'fltRt': '0.82', 'mrktTotAmt': '345000000000', 'lstgStCnt': '28000000'},
        {'basDt': '20261105', 'itmsNm': '피스피스스튜디오', 'clpr': '12200'},
        {'basDt': '20261106', 'itmsNm': '피스피스스튜디오홀딩스', 'clpr': '1'}]}}}}
    assert api_status(j)[0] == '00'
    row = latest(parse_items(j), '피스피스스튜디오')
    assert row['basDt'] == '20261106' and row['clpr'] == '12,300'
    assert latest(parse_items(j), '다른이름', '0117P0')['clpr'] == '12,300'
    assert latest(parse_items(j), '피스피스 스튜디오')['clpr'] == '12,300'
    e = build_entry(row, {'ipo_price': 10000})
    assert e['close'] == 12300 and e['date'] == '2026.11.06' and e['vs_ipo'] == 23.0 and e['mktcap'] == 345000000000
    assert parse_items({'response': {'body': {'items': ''}}}) == [] and parse_items({}) == []
    assert key_for_url('ab+c/d==') == 'ab%2Bc%2Fd%3D%3D' and key_for_url('ab%2Bc') == 'ab%2Bc'
    m = manual_entry({'name': 'A', 'ipo_price': 10000, 'manual': {'date': '2026.11.06', 'close': 12300, 'rate': -1.2, 'mktcap': 345000000000}})
    assert m['date'] == '2026.11.06 (수기)' and m['close'] == 12300 and m['vs_ipo'] == 23.0 and m['mktcap'] == 345000000000
    assert manual_entry({'name': 'A'}) is None and manual_entry({'name': 'A', 'manual': {'close': ''}}) is None
    print('시세 파싱 로직 OK')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='price_config.json')
    ap.add_argument('--out', default='price.json')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    raw = os.environ.get('DATA_GO_KR_KEY', '')
    key = raw.strip().strip('"\'')
    try:
        cfg = json.loads(Path(a.config).read_text('utf-8')) if Path(a.config).exists() else {}
    except ValueError as ex:
        sys.exit(f'price_config.json 형식 오류: {ex}  (쉼표·따옴표·중괄호 위치와 숫자 쉼표(3,535 → 3535)를 확인하세요. 기존 price.json 은 변경하지 않았습니다.)')
    out_p = Path(a.out)
    prev = {}
    try:
        prev = json.loads(out_p.read_text('utf-8')) if out_p.exists() else {}
    except Exception:
        prev = {}
    comp = dict(prev.get('companies', {})) if isinstance(prev, dict) else {}
    kq, requests = '', None
    if key:
        print(f'  키 형태 점검: 길이={len(key)}, % 포함={"예" if "%" in key else "아니오"}, 앞뒤 공백 제거됨={"예" if raw != key else "아니오"} (값은 출력하지 않음)')
        import requests
        kq = key_for_url(key)
    else:
        print('  ⚠ 환경변수 DATA_GO_KR_KEY 가 비어 있습니다 → API 조회 없이 수기(manual) 값만 사용합니다.')
    mask = lambda t: str(t).replace(key, '***').replace(kq, '***') if key else str(t)
    begin = (datetime.now(KST) - timedelta(days=21)).strftime('%Y%m%d')
    for k, c in cfg.items():
        if k.startswith('_') or not isinstance(c, dict) or not c.get('name'):
            continue
        e = None
        if key:
            code = str(c.get('code') or '').strip()
            params = {'resultType': 'json', 'numOfRows': 50, 'pageNo': 1, 'beginBasDt': begin}
            params.update({'likeSrtnCd': code.lstrip('A')} if code else {'likeItmsNm': c['name']})
            url = f'{API_URL}?serviceKey={kq}&{urlencode(params)}'          # 키는 직접 붙여 이중 인코딩을 막는다
            try:
                r = requests.get(url, timeout=20)
                body = mask(r.text[:200]).replace('\n', ' ')
                if r.status_code != 200:
                    print(f'  ⚠ [{k}] HTTP {r.status_code}: {body}')
                else:
                    try:
                        j = r.json()
                        rc, rm, tc = api_status(j)
                        items = parse_items(j)
                        print(f'  [{k}] API 응답 코드={rc} 메시지={rm} 전체건수={tc} 수신={len(items)}건 종목명={sorted({str(x.get("itmsNm")) for x in items})[:5]}')
                        row = latest(items, c['name'], code)
                        e = build_entry(row, c) if row else None
                    except ValueError:
                        print(f'  ⚠ [{k}] JSON 아님(오류 응답 가능): {body}')
            except Exception as ex:
                print(f'  ⚠ [{k}] 조회 실패: {mask(ex)[:120]}')
        if e:
            comp[k] = e
            print(f'  [{k}] API: {e["name"]} {e["date"]} 종가 {e["close"]:,}원')
            continue
        m = manual_entry(c)
        if m:
            comp[k] = m
            print(f'  [{k}] API 값 없음 → 수기 입력값 사용: {m["date"]} 종가 {m["close"]:,}원')
        else:
            print(f'  [{k}] API 값·수기 값 모두 없음 → 기존 값 유지')
    res = {'companies': comp, '_updated': datetime.now(KST).strftime('%Y-%m-%dT%H:%M:%S KST'),
           '_note': '금융위원회 주식시세정보(공공데이터포털). 영업일 기준 전일 종가이며 실시간 시세가 아닙니다. "(수기)" 표시는 사람이 입력한 값입니다.'}
    out_p.write_text(json.dumps(res, ensure_ascii=False, indent=1), 'utf-8')
    print(f'저장: {a.out}')


if __name__ == '__main__':
    main()
