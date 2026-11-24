"""Public discovery catalogue: never mark API listings verified or discounted."""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
AGE = timedelta(hours=8)
HOSTS = {'store.shopping.yahoo.co.jp', 'shopping.yahoo.co.jp', 'lohaco.yahoo.co.jp'}
PART = re.compile(r'バラ売り|片耳のみ|右耳用|左耳用|充電ケース(?:のみ|単品)|ケースのみ|交換用|替えブラシのみ|保護フィルム')
CONDITION = re.compile(r'保証開始(?!前)|保証登録済|保証サポート登録済|中古|整備済|再生品|訳[あア]リ|訳あり|外箱.{0,5}(?:傷|破損|汚)|並行輸入|平行輸入|バルク|bulk', re.I)


def stamp(s):
    t = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timezone required')
    return t.astimezone(timezone.utc)


def safe_url(s):
    try:
        if not isinstance(s, str) or any(ord(c) < 33 or c in '\\<>"' for c in s):
            return ''
        u = urlsplit(s)
        if u.scheme != 'https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None, 443) or not u.path.strip('/'):
            return ''
        return s
    except ValueError:
        return ''


def key(url):
    u = urlsplit(url)
    return urlunsplit(('https', u.hostname, u.path.rstrip('/'), '', ''))


def read(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def build(yahoo, precise, decisions, now):
    out = {'version': 1, 'generated_at': now.isoformat(), 'source_generated_at': None, 'items': [], 'status': 'STALE_OR_INVALID'}
    try:
        ts = stamp(yahoo['generated_at'])
        rows = yahoo['items']
        if not timedelta(0) <= now-ts <= AGE or not isinstance(rows, list) or yahoo['count'] != len(rows):
            return out
    except (KeyError, TypeError, ValueError, AttributeError):
        return out
    out['source_generated_at'] = yahoo['generated_at']
    expiry = (ts+AGE).isoformat()
    by_id = {}
    def add(x, checked, is_precise=False):
        if not isinstance(x, dict):
            return
        pid, title, price, url = (x.get(k) for k in ('id','title','price_jpy','url'))
        if not isinstance(pid,str) or not pid.startswith('yahoo:') or not isinstance(title,str) or not title.strip() or type(price) not in (int,float) or not math.isfinite(price) or price <= 0 or not safe_url(url):
            return
        if not is_precise and x.get('source_type') != 'yahoo_shopping_api':
            return
        if is_precise and x.get('in_stock') is not True:
            return
        old = by_id.get(pid, {})
        flags = x.get('risk_flags')
        by_id[pid] = {'id':pid, 'title':title, 'price_jpy':price, 'url':url,
            'merchant':'Yahoo! Shopping', 'shop_name':str(x.get('shop_name') or ''),
            'category':'数码家电', 'location':'线上', 'deal_type':'自动发现',
            'verified':False, 'auto_discovered':True, 'observed_at':checked,
            'expires_at':expiry, 'source_type':'yahoo_shopping_api',
            'jan_code':str(x.get('jan_code') or ''),
            'risk_hint':bool(PART.search(title) or CONDITION.search(title) or (isinstance(flags,list) and flags) or old.get('risk_hint'))}
    for x in rows:
        add(x, yahoo['generated_at'])
    try:
        checked = stamp(precise['generated_at'])
        if precise['status'] in ('OK','PARTIAL') and stamp(precise['source_generated_at']) == ts and ts <= checked <= now and isinstance(precise['query_results'], list):
            for q in precise['query_results']:
                if isinstance(q,dict) and isinstance(q.get('offers'),list):
                    for x in q['offers']:
                        add(x, precise['generated_at'], True)
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    # A deliberate rejection/downlisting is still respected for that price.
    states = {}
    events = decisions.get('events', []) if isinstance(decisions,dict) else []
    for e in events if isinstance(events,list) else []:
        if isinstance(e,dict) and isinstance(e.get('candidate_id'),str) and type(e.get('price_jpy')) in (int,float) and e.get('action') in ('approve','reject','restore','unpublish'):
            states[(e['candidate_id'],e['price_jpy'])] = e['action']
    seen = set()
    for x in sorted(by_id.values(), key=lambda z: (z['observed_at'],z['id']), reverse=True):
        if states.get((x['id'],x['price_jpy'])) in ('reject','unpublish'):
            continue
        u = key(x['url'])
        if u in seen:
            continue
        seen.add(u)
        out['items'].append(x)
    out['status'] = 'OK'
    return out


def main(root=ROOT, now=None):
    data = root/'data'; dest = root/'site'/'catalog.json'
    result = build(read(data/'yahoo_candidates.json',{}), read(data/'precise_comparison.json',{}), read(data/'review_decisions.json',{}), now or datetime.now(timezone.utc))
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n', encoding='utf-8')
    tmp.replace(dest)
    print('Public discovery catalogue:',result['status'],len(result['items']))
    return result

if __name__ == '__main__':
    main()
