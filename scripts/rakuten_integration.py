"""Step 13: live Rakuten discovery and conservative Yahoo comparison.
No Rakuten item data or prices are written to files, Git, or CI logs.
Local --review: loopback-only, no-store HTML, expires after 15 minutes.
Requires existing scripts/product_matching.py from step 12.
"""
import argparse
import html
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

from product_matching import features, norm, price

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701'
KEYWORDS = ('Anker', 'AirPods', 'モバイルバッテリー', 'ワイヤレスイヤホン', 'SSD',
            'Nintendo Switch', '炊飯器', '掃除機', 'ドライヤー', '電動歯ブラシ',
            '加湿器', 'コーヒーメーカー')
HITS = 20
MAX_EXTRA = 6
INTERVAL = 1.25
BRANDING = '''<!-- Rakuten Web Services Attribution Snippet FROM HERE -->
<a href="https://developers.rakuten.com/" target="_blank">Supported by Rakuten Developers</a>
<!-- Rakuten Web Services Attribution Snippet TO HERE -->'''


def utcnow():
    return datetime.now(timezone.utc)


def stamp(s):
    t = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timezone required')
    return t.astimezone(timezone.utc)


def valid_jan(value):
    s = str(value or '').strip()
    if not re.fullmatch(r'(?:[0-9]{8}|[0-9]{13})', s):
        return ''
    if not any(c != '0' for c in s):
        return ''
    return s if sum(int(c)*(1 if i % 2 == 0 else 3)
                    for i,c in enumerate(reversed(s))) % 10 == 0 else ''


def title_jan(title):
    # Item Search does not supply a documented structured JAN field. A code
    # explicitly labelled JAN in a listing title is review evidence only.
    found = set(re.findall(r'jan(?:コード)?\s*[:：#]?\s*([0-9]{13}|[0-9]{8})(?![0-9])', norm(title)))
    return valid_jan(next(iter(found))) if len(found) == 1 else ''


def https_url(value, domain):
    if not isinstance(value, str):
        return False
    try:
        u = urllib.parse.urlsplit(value)
        host = u.hostname or ''
        return (u.scheme == 'https' and (host == domain or host.endswith('.'+domain))
                and not u.username and not u.password and u.port in (None,443))
    except ValueError:
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApiFailure(Exception):
    pass


class Client:
    def __init__(self, app, key, affiliate, origin='', opener=None, clock=time.monotonic, sleep=time.sleep):
        self.app, self.key, self.affiliate = app, key, affiliate
        self.origin = origin.rstrip('/')
        if self.origin and self.origin != 'https://japan-deals-cn.pages.dev':
            raise ApiFailure('configured_origin_invalid')
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.clock, self.sleep, self.last = clock, sleep, None
        self.requests = 0

    def search(self, keyword):
        if self.last is not None:
            self.sleep(max(0, INTERVAL - (self.clock()-self.last)))
        params = dict(applicationId=self.app, affiliateId=self.affiliate,
                      keyword=keyword, hits=HITS, format='json', formatVersion=2, availability=1)
        headers = {'accessKey':self.key, 'User-Agent':'JapanDealsCN/0.1', 'Accept':'application/json'}
        # Only use this if the actual project website is registered and permitted.
        if self.origin:
            headers['Origin'] = self.origin
            headers['Referer'] = self.origin+'/'
        req = urllib.request.Request(ENDPOINT+'?'+urllib.parse.urlencode(params),headers=headers)
        self.last = self.clock()
        self.requests += 1
        try:
            with self.opener.open(req, timeout=20) as response:
                body = response.read(4_000_001)
            if len(body)>4_000_000:
                raise ApiFailure('response_too_large')
            obj = json.loads(body)
        except urllib.error.HTTPError as e:
            # Never include URL, API error body or credential values in logs.
            raise ApiFailure('http_'+str(e.code)) from None
        except (OSError, ValueError, UnicodeError):
            raise ApiFailure('network_or_response_error') from None
        return parse_items(obj, keyword)


def parse_items(obj, keyword):
    if not isinstance(obj,dict) or obj.get('error') or obj.get('errorCode'):
        raise ApiFailure('api_response_error')
    raw = obj.get('Items', obj.get('items'))
    if not isinstance(raw,list):
        raise ApiFailure('unexpected_response_schema')
    result = []
    for entry in raw:
        if not isinstance(entry,dict):
            continue
        x = entry.get('Item',entry.get('item',entry))
        if not isinstance(x,dict):
            continue
        code = str(x.get('itemCode') or '').strip()
        p = x.get('itemPrice')
        url = x.get('itemUrl')
        affiliate = x.get('affiliateUrl')
        title = x.get('itemName')
        if (not re.fullmatch(r'[^:\s]+:[^:\s]+',code) or not price({'price_jpy':p})
                or str(x.get('availability')) != '1' or not isinstance(title,str) or not title.strip()
                or not https_url(url,'rakuten.co.jp') or not https_url(affiliate,'rakuten.co.jp')):
            continue
        shop = str(x.get('shopCode') or code.split(':',1)[0])
        if shop != code.split(':',1)[0]:
            continue
        # SKU range, when returned, is not a single comparable price.
        mn, mx = x.get('itemPriceMin'), x.get('itemPriceMax')
        if (mn is not None and mn != p) or (mx is not None and mx != p):
            continue
        result.append({'id':'rakuten:'+code,'title':title,'merchant':'楽天市場',
                       'shop_name':str(x.get('shopName') or shop),'shop_code':shop,
                       'price_jpy':p,'url':url,'affiliate_url':affiliate,
                       'title_jan':title_jan(title),'discovered_by':keyword,
                       'observed_at':utcnow().isoformat(),'verified':False})
    return result


def load_yahoo(data=ROOT/'data', now=None):
    now = now or utcnow()
    try:
        source = json.loads((data/'yahoo_candidates.json').read_text(encoding='utf-8'))
        analysis = json.loads((data/'price_history'/'analysis_latest.json').read_text(encoding='utf-8'))
        deals = json.loads((data/'deal_candidates.json').read_text(encoding='utf-8'))
        ts = stamp(source['generated_at'])
        if (deals.get('snapshot_fresh') is not True or stamp(analysis['generated_at']) != ts
                or stamp(deals['generated_at']) != ts or not timedelta(0)<=now-ts<=timedelta(hours=8)):
            return [],None
        ids = {x['id'] for x in analysis['items']}
        rows = [dict(x,observed_at=source['generated_at']) for x in source['items']
                if x['id'] in ids and x['id'].startswith('yahoo:') and price(x)
                and https_url(x.get('url'),'yahoo.co.jp')]
        return rows,source['generated_at']
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return [],None


def search_terms(yahoo):
    # At most 6 extra queries, deterministic. Do not treat search terms as identity.
    models = set()
    for x in yahoo:
        brands, ms, caps, flags = features(x)
        if len(brands)==len(ms)==1 and len(caps)<=1 and not flags & {'used','accessory','set'}:
            models.add(next(iter(ms)))
    return list(KEYWORDS)+[model for _,model in sorted(models)[:MAX_EXTRA]]


def collect(client, terms):
    rows, errors, succeeded = {}, [], 0
    for term in terms:
        try:
            items = client.search(term)
            succeeded += 1
            for x in items:
                rows[x['id']] = x
        except ApiFailure as e:
            errors.append(str(e))  # only predefined error codes
            if str(e) in {'http_400','http_401','http_403','http_404','http_429'}:
                break  # do not hammer an invalid key, endpoint or rate limit
    return list(rows.values()), succeeded, errors


EXCLUDE = re.compile(r'中古|整備済|再生品|ジャンク|訳あり|展示品|アウトレット|refurbished|\bused\b|レンタル|交換用|専用ケース|保護フィルム|本体なし|替えブラシのみ|ケースのみ|部品のみ|\d+\s*(?:個|本|台|点)\s*(?:入り|セット)|福袋')
VARIANTS = re.compile(r'選べる|選択可|カラー選択|容量選択|色選択|(?:\d+\s*(?:gb|tb|mah)\s*[/／])')


def condition(x):
    text = norm(x.get('title'))
    brands, models, caps, flags = features(x)
    # additional specs and colors as conservative conflict checks
    spec = set(re.findall(r'\d+(?:\.\d+)?\s*(?:mah|w|ml|l)\b', text))
    spec = {re.sub(r'\s+','',s) for s in spec}
    colors = {c for c,p in {'black':r'ブラック|黒色|\bblack\b', 'white':r'ホワイト|白色|\bwhite\b',
                             'blue':r'ブルー|青色|\bblue\b', 'pink':r'ピンク|\bpink\b'}.items() if re.search(p,text)}
    bad = bool(EXCLUDE.search(text) or VARIANTS.search(text) or flags & {'used','accessory','set'})
    return brands,models,caps,flags,spec,colors,bad


def compare(yahoo, rakuten):
    result=[]
    yf=[(x,condition(x)) for x in yahoo]
    rf=[(x,condition(x)) for x in rakuten]
    for a,fa in yf:
        for b,fb in rf:
            ba,ma,ca,xa,sa,coa,bada=fa
            bb,mb,cb,xb,sb,cob,badb=fb
            if bada or badb or xa!=xb or len(ca)>1 or len(cb)>1 or ca!=cb:
                continue
            if (sa and sb and sa!=sb) or (coa and cob and coa!=cob) or len(coa)>1 or len(cob)>1:
                continue
            if ba and bb and ba!=bb or ma and mb and ma!=mb or len(ma)>1 or len(mb)>1:
                continue
            ja,jb=valid_jan(a.get('jan_code')),b.get('title_jan','')
            if ja and jb and ja!=jb:
                continue
            if ja and ja==jb:
                method='Yahoo JAN 与乐天标题明确标注JAN一致（待核验）'
            elif len(ba)==len(bb)==len(ma)==len(mb)==1 and ba==bb and ma==mb:
                method='品牌与制造商型号一致（待核验）'
            else:
                continue
            pa,pb=a['price_jpy'],b['price_jpy']
            result.append({'yahoo':a,'rakuten':b,'method':method,'verified':False,
                           'eligible_for_auto_deal':False,'difference_jpy':abs(pa-pb),
                           'difference_percent':round(abs(pa-pb)/max(pa,pb)*100,2)})
    return sorted(result,key=lambda x:(x['difference_jpy'],x['yahoo']['id'],x['rakuten']['id']),reverse=True)


def report(rows, pairs, status):
    e=lambda x:html.escape(str(x),quote=True)
    def offer(x):
        link=x.get('affiliate_url') or x['url']
        return (f'<a href="{e(link)}" target="_blank" rel="noopener noreferrer sponsored">{e(x["title"])}</a>'
                f'<br>{e(x["merchant"])} / {e(x["shop_name"])}: ¥{e(x["price_jpy"])}'
                f'<br><small>采集：{e(x["observed_at"])}</small>')
    body=''.join(f'<tr><td>{offer(p["yahoo"])}</td><td>{offer(p["rakuten"])}</td>'
                 f'<td>{e(p["method"])}<br>差 ¥{e(p["difference_jpy"])}</td></tr>' for p in pairs)
    items=''.join(f'<li>{offer(x)}</li>' for x in rows)
    return ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>在日好价 · 临时审核</title>'
            '<meta name="referrer" content="no-referrer"><style>body{font:16px sans-serif;max-width:1100px;margin:2em auto;padding:1em}td,th{padding:12px;border-bottom:1px solid #ccc;text-align:left}small{color:#555}li{margin:12px 0}</style>'
            '<h1>在日好价 · 临时审核</h1><p>含乐天联盟推广链接。价格为商品标价，不含运费、积分及优惠券。所有匹配均待核验。</p>'
            f'<p>采集状态：{e(status)}。本地页面15分钟后失效，不写入Git。</p>'
            '<p>このサイトで掲載されている情報は、在日好价の作成者により運営されています。価格、販売可能情報は、変更される場合があります。購入時に楽天市場店舗に表示されている価格が、その商品の販売に適用されます。</p>'
            f'{BRANDING}<h2>跨平台候选（{len(pairs)}组）</h2><table><tr><th>Yahoo</th><th>乐天</th><th>依据与价差</th></tr>{body}</table>'
            f'<h2>乐天商品（{len(rows)}件）</h2><ul>{items}</ul></html>')


def serve_local(page):
    # No file output, no access log, local loopback only, bounded lifetime.
    content=page.encode('utf-8'); deadline=time.monotonic()+900
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            if self.path!='/' or time.monotonic()>=deadline:
                self.send_error(404); return
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store, max-age=0')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers(); self.wfile.write(content)
    with HTTPServer(('127.0.0.1',0),Handler) as server:
        server.timeout=1
        print(f'Local review: http://127.0.0.1:{server.server_port}/ (15 minutes; Ctrl+C to close)',flush=True)
        try:
            while time.monotonic()<deadline:
                server.handle_request()
        except KeyboardInterrupt:
            pass


def summary(status, **counts):
    # Aggregate operational metadata only: no item identifiers, prices or URLs.
    line='Step 13: '+status+'; '+', '.join(f'{k}={v}' for k,v in counts.items())
    print(line)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:
            f.write('### Rakuten integration\n\n'+line+'\n\nNo Rakuten item data retained; long-term history disabled.\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review',action='store_true',help='local-only, temporary browser preview')
    args=parser.parse_args()
    if args.review and os.getenv('GITHUB_ACTIONS')=='true':
        parser.error('review mode not permitted in CI')
    app,key,affiliate=[os.getenv(n,'').strip() for n in ('RAKUTEN_APP_ID','RAKUTEN_ACCESS_KEY','RAKUTEN_AFFILIATE_ID')]
    if not (app and key and affiliate):
        summary('SKIPPED_MISSING_CREDENTIALS',requests=0); return
    try:
        client=Client(app,key,affiliate,os.getenv('RAKUTEN_ALLOWED_ORIGIN','').strip())
    except ApiFailure as e:
        summary('CONFIG_ERROR',reason=str(e)); return
    yahoo,ts=load_yahoo()
    terms=search_terms(yahoo)
    rows,success,errors=collect(client,terms)
    pairs=compare(yahoo,rows)
    status=('API_FAILED' if not success else 'PARTIAL' if errors else 'OK_EMPTY' if not rows else 'OK')
    summary(status,requests=client.requests,queries_ok=success,queries_failed=len(errors),
            rakuten_listings=len(rows),yahoo_fresh=bool(ts),yahoo_listings=len(yahoo),review_pairs=len(pairs),
            error_codes=','.join(sorted(set(errors))) or 'none')
    if args.review and success:
        serve_local(report(rows,pairs,status))


if __name__=='__main__':
    main()
