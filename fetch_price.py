"""
상장 완료 기업 주가·시총 수집 (공공데이터포털 금융위원회 '주식시세정보' API) → price.json

- 대상: price_config.json 에 적힌 상장 기업만(미상장 기업은 시세 없음).
- 인증키는 환경변수 DATA_GO_KR_KEY 로만 받는다(코드·로그 출력 금지, 오류 메시지에서도 마스킹).
  Encoding 키(% 포함)·Decoding 키(원문) 어느 쪽을 넣어도 동작하도록 자동 처리하고, 앞뒤 공백·줄바꿈은 제거한다.
- 데이터는 영업일 기준 '전일 종가'이며 실시간 시세가 아니다. 공모가는 사람이 price_config.json 에 입력한 값만 사용한다.
- 조회 실패 시 기존 price.json 값을 유지한다(빈 값으로 덮어쓰지 않음).
- 실패 원인을 알 수 있도록 API 응답 코드·반환 종목명과 키 형태(길이·% 포함 여부만, 값 제외)를 로그에 남긴다.

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
    assert key_for_url('ab+c/d==') == 'ab%2Bc%2Fd%3D%3D'          # Decoding 키 → 한 번 인코딩
    assert key_for_url('ab%2Bc%2Fd%3D%3D') == 'ab%2Bc%2Fd%3D%3D'  # Encoding 키 → 그대로(이중 인코딩 안 함)
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
    if not key:
        sys.exit('환경변수 DATA_GO_KR_KEY 가 비어 있습니다. (저장소 Settings → Secrets and variables → Actions → Repository secrets 에 등록, 코드에 직접 넣지 마세요)')
    print(f'  키 형태 점검: 길이={len(key)}, % 포함={"예" if "%" in key else "아니오"}, 앞뒤 공백 제거됨={"예" if raw != key else "아니오"} (값은 출력하지 않음)')
    import requests
    kq = key_for_url(key)
    mask = lambda t: str(t).replace(key, '***').replace(kq, '***')
    cfg = json.loads(Path(a.config).read_text('utf-8')) if Path(a.config).exists() else {}
    out_p = Path(a.out)
    prev = {}
    try:
        prev = json.loads(out_p.read_text('utf-8')) if out_p.exists() else {}
    except Exception:
        prev = {}
    comp = dict(prev.get('companies', {})) if isinstance(prev, dict) else {}
    begin = (datetime.now(KST) - timedelta(days=21)).strftime('%Y%m%d')
    for k, c in cfg.items():
        if k.startswith('_') or not isinstance(c, dict) or not c.get('name'):
            continue
        code = str(c.get('code') or '').strip()
        params = {'resultType': 'json', 'numOfRows': 50, 'pageNo': 1, 'beginBasDt': begin}
        params.update({'likeSrtnCd': code.lstrip('A')} if code else {'likeItmsNm': c['name']})
        url = f'{API_URL}?serviceKey={kq}&{urlencode(params)}'          # 키는 직접 붙여 이중 인코딩을 막는다
        try:
            r = requests.get(url, timeout=20)
            body = mask(r.text[:200]).replace('\n', ' ')
            if r.status_code != 200:
                print(f'  ⚠ [{k}] HTTP {r.status_code}: {body}')
                continue
            try:
                j = r.json()
            except Exception:
                print(f'  ⚠ [{k}] JSON 아님(오류 응답 가능): {body}')
                continue
            rc, rm, tc = api_status(j)
            items = parse_items(j)
            print(f'  [{k}] API 응답 코드={rc} 메시지={rm} 전체건수={tc} 수신={len(items)}건 종목명={sorted({str(x.get("itmsNm")) for x in items})[:5]}')
            row = latest(items, c['name'], code)
            e = build_entry(row, c) if row else None
        except Exception as ex:
            print(f'  ⚠ [{k}] 조회 실패: {mask(ex)[:120]}')
            continue
        if e:
            comp[k] = e
            print(f'  [{k}] {e["name"]} {e["date"]} 종가 {e["close"]:,}원')
        else:
            print(f'  [{k}] 일치하는 종목 없음 → 기존 값 유지 (price_config.json 의 name/code 확인)')
    res = {'companies': comp, '_updated': datetime.now(KST).strftime('%Y-%m-%dT%H:%M:%S KST'),
           '_note': '금융위원회 주식시세정보(공공데이터포털). 영업일 기준 전일 종가이며 실시간 시세가 아닙니다.'}
    out_p.write_text(json.dumps(res, ensure_ascii=False, indent=1), 'utf-8')
    print(f'저장: {a.out}')


if __name__ == '__main__':
    main()
