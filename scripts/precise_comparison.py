"""Step 16: bounded Yahoo JAN/model search; separate evidence, no auto-publication.
Run after deal_scoring.py. Only current original listings can be approved.
"""
import argparse
import copy
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import deal_scoring as sc
import product_matching as pm

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch'
MAX_REQUESTS = 12
PAGE_SIZE = 50
INTERVAL = 1.25
MAX_AGE = timedelta(hours=8)
EXTRA_RISK = r'予約商品|予約販売|受注生産|取り寄せ|お取り寄せ|訳アリ|箱なし|箱無し|保証なし|保証無し|保証切れ|開封品|返品不可|ジャンク|アウトレット|展示処分|本体のみ|付属品なし|付属品無し'
NOTE = 'Yahoo新品有库存查询返回范围内的标价；不是全网最低价，未计运费、积分、优惠券；型号匹配仅人工核验。'

class SearchError(Exception):
    def __init__(self, code, stop=False):
        super().__init__(code); self.code = code; self.stop = stop


def stamp(s):
    return sc.timestamp(s)


def now_utc():
    return datetime.now(timezone.utc)


def canonical(url):
    if not isinstance(url, str) or any(ord(c)<33 or c in '\\<>"' for c in url): return ''
    try:
        u=urllib.parse.urlsplit(url)
        if u.scheme!='https' or u.hostname!='store.shopping.yahoo.co.jp' or u.username or u.password or u.port not in (None,443): return ''
        return urllib.parse.urlunsplit(('https',u.hostname,u.path.rstrip('/'),'',''))
    except ValueError: return ''


def risks(title):
    return sc.risk_flags(title)+(['extra_sales_condition'] if re.search(EXTRA_RISK,pm.norm(title),re.I) else [])


def identity(x):
    j=sc.valid_jan(x.get('jan_code'))
    if j: return ('jan',j)
    b,m,c,f=pm.features(x)
    if len(b)==len(m)==1 and len(c)<=1 and not f&{'used','accessory','set'}:
        brand,model=next(iter(m)); return ('model',brand,model)
    return None


def plan(items,budget=MAX_REQUESTS):
    """Risk-free review candidates first, then explicit no-JAN model review pairs.
    At most one request per key. Reserve up to two slots for review-only models.
    """
    budget=max(0,min(MAX_REQUESTS,int(budget)))
    jan_keys={}; model_keys={}
    for x in sorted(items,key=lambda x:(-x['score'],x['id'])):
        if x.get('review_priority')=='继续监测' or x.get('recovered_price'): continue
        key=identity(x)
        if key is None: continue
        if key[0]=='jan' and not risks(x['title']): jan_keys.setdefault(key,[]).append(x['id'])
        elif key[0]=='model' and (not risks(x['title']) or x.get('model_review_candidate')):
            model_keys.setdefault(key,[]).append(x['id'])
    reserve=min(2,len(model_keys),budget)
    chosen=list(jan_keys)[:budget-reserve]+list(model_keys)[:reserve]
    if len(chosen)<budget:
        chosen += [k for k in list(jan_keys)+list(model_keys) if k not in chosen][:budget-len(chosen)]
    return chosen, {**jan_keys,**model_keys}


class Client:
    def __init__(self,appid,budget=MAX_REQUESTS,opener=None,clock=time.monotonic,sleep=time.sleep):
        self.appid=appid; self.budget=max(0,min(MAX_REQUESTS,int(budget))); self.calls=0
        self.opener=opener or urllib.request.urlopen; self.clock=clock; self.sleep=sleep; self.last=self.clock()
    def search(self,key):
        if self.calls>=self.budget: raise SearchError('BUDGET_EXHAUSTED',True)
        if self.last is not None:
            wait=INTERVAL-(self.clock()-self.last)
            if wait>0: self.sleep(wait)
        params={'appid':self.appid,'results':PAGE_SIZE,'start':1,'sort':'+price','condition':'new','in_stock':'true'}
        if key[0]=='jan': params['jan_code']=key[1]
        else: params['query']=' '.join(key[1:])
        req=urllib.request.Request(ENDPOINT+'?'+urllib.parse.urlencode(params),headers={'User-Agent':'JapanDealsCN/0.2'})
        self.last=self.clock(); self.calls+=1
        try:
            with self.opener(req,timeout=15) as f: payload=json.load(f)
        except urllib.error.HTTPError as e:
            raise SearchError('HTTP_'+str(e.code),e.code in (401,403,429)) from None
        except (OSError,ValueError,TypeError): raise SearchError('NETWORK_OR_JSON_ERROR') from None
        return parse(payload)


def parse(payload):
    if not isinstance(payload,dict): raise SearchError('INVALID_RESPONSE')
    hits=payload.get('hits'); total=payload.get('totalResultsAvailable'); returned=payload.get('totalResultsReturned')
    if not isinstance(hits,list) or type(total) is not int or type(returned) is not int or returned!=len(hits) or total<returned or not 0<=returned<=PAGE_SIZE or (total>0 and returned==0) or payload.get('firstResultsPosition') not in (0,1):
        raise SearchError('INVALID_RESPONSE')
    prices=[x.get('price') if isinstance(x,dict) else None for x in hits]
    if any(type(p) is not int or p<=0 for p in prices) or prices!=sorted(prices): raise SearchError('INVALID_PRICE_ORDER')
    rows=[]; ids=set()
    for x in hits:
        code=x.get('code'); seller=x.get('seller') or {}; sid=seller.get('sellerId') if isinstance(seller,dict) else None
        url=canonical(x.get('url')); title=x.get('name'); pid='yahoo:'+code if isinstance(code,str) else ''
        # An unverifiable hit can hide a cheaper offer. Fail the whole query, not just the hit.
        if not pid or pid in ids or not isinstance(sid,str) or not sid or sc.shop_id(pid)!=sid or not url or not isinstance(title,str) or not title or x.get('condition') not in ('new','used') or type(x.get('inStock')) is not bool:
            raise SearchError('INVALID_HIT')
        ids.add(pid)
        rows.append({'id':pid,'title':title,'shop_id':sid,'shop_name':str(seller.get('name') or sid),'price_jpy':x['price'],'jan_code':str(x.get('janCode') or ''),'url':url,'in_stock':x['inStock'],'condition':x['condition'],'risk_flags':risks(title)})
    return {'total_available':total,'returned':returned,'truncated':total>returned,'offers':rows}


def compatible(a,b):
    # Exact JAN is strongest; explicit conflicting brand/model/capacity still disqualifies.
    ba,ma,ca,fa=pm.features(a); bb,mb,cb,fb=pm.features(b)
    if ba and bb and ba.isdisjoint(bb): return False
    if ma and mb and ma.isdisjoint(mb): return False
    if len(ca)>1 or len(cb)>1 or (ca and cb and ca!=cb): return False
    return True


def compare(x,key,result):
    p=x['current_price_jpy']; own=x['id']; jan=sc.valid_jan(x.get('jan_code'))
    base={'id':own,'query_type':key[0],'query_key':list(key),'candidate_price_jpy':p,'total_available':result['total_available'],'returned':result['returned'],'truncated':result['truncated'],'matched_listing_count':0,'comparison_shop_count':0,'other_shop_lowest_jpy':None,'lowest_observed_jpy':None,'cross_shop_advantage_percent':None,'cheaper_offers':[],'own_confirmed':False,'status':'no_comparable'}
    offers=result['offers']; matched=[]; rejected=Counter()
    for o in offers:
        if o['condition']!='new' or not o['in_stock']: rejected['not_new_or_in_stock']+=1; continue
        if key[0]=='jan':
            if sc.valid_jan(o['jan_code'])!=jan: rejected['jan_mismatch']+=1; continue
            if risks(x['title']) or o['risk_flags'] or not compatible(x,o): rejected['risk_or_spec_conflict']+=1; continue
        else:
            ba,ma,ca,fa=pm.features(x); bb,mb,cb,fb=pm.features(o)
            if ba!=bb or ma!=mb or ca!=cb or len(ma)!=1 or len(ca)>1 or fa!=fb or fa&{'used','accessory','set'} or (jan and sc.valid_jan(o['jan_code']) and jan!=sc.valid_jan(o['jan_code'])):
                rejected['model_or_spec_mismatch']+=1; continue
            # Model queries are review only, including bulk/import pairs; flag all conditions.
            if set(risks(x['title']))!=set(o['risk_flags']): rejected['risk_or_spec_conflict']+=1; continue
        matched.append(o)
    base['matched_listing_count']=len(matched); base['excluded']=dict(rejected)
    base['cheaper_offers']=[{k:o[k] for k in ('id','title','shop_name','price_jpy','url','jan_code','risk_flags')} for o in matched if o['price_jpy']<p][:5]
    if key[0]=='model': base['status']='model_review_only'; return base
    exact=next((o for o in offers if o['id']==own),None)
    if exact and exact['price_jpy']!=p: base['status']='price_changed'; return base
    if not exact or exact not in matched or canonical(x['url'])!=exact['url'] or pm.norm(x['title'])!=pm.norm(exact['title']): base['status']='own_not_confirmed'; return base
    base['own_confirmed']=True
    shops={}
    for o in matched: shops[o['shop_id']]=min(shops.get(o['shop_id'],math.inf),o['price_jpy'])
    others=[v for k,v in shops.items() if k!=sc.shop_id(own)]
    base['comparison_shop_count']=len(shops); base['lowest_observed_jpy']=min(shops.values()); base['other_shop_lowest_jpy']=min(others) if others else None
    gap=sc.advantage(min(others),p) if others and p==min(shops.values()) else 0
    base['cross_shop_advantage_percent']=round(gap,2)
    if base['lowest_observed_jpy']<p: base['status']='cheaper_found'
    elif not others: base['status']='no_comparable'
    elif gap>=5: base['status']='confirmed_advantage'
    else: base['status']='insufficient_advantage'
    return base


def enrich(base,evidence,generated):
    """Preserve original evidence explicitly; only use verified JAN results for cross-shop points."""
    out=copy.deepcopy(base); rows=[]
    for x in out['items']:
        e=evidence.get(x['id'],{'id':x['id'],'status':'not_checked','candidate_price_jpy':x['current_price_jpy']})
        x['initial_score']=x['score']; x['initial_cross_shop_advantage_percent']=x.get('cross_shop_advantage_percent'); x['initial_review_priority']=x['review_priority']
        x['initial_scoring_evidence']={k:copy.deepcopy(x.get(k)) for k in ('raw_score','level','score_breakdown','score_caps','comparison_shop_count','other_shop_lowest_jpy','is_comparable_lowest')}
        x['precise_comparison']=e; x['precise_status']=e['status']; x['precise_checked_at']=generated
        points=x['score_breakdown']; caps=x['score_caps']; verified=e.get('query_type')=='jan' and e.get('own_confirmed') is True and e.get('comparison_shop_count',0)>=2
        gap=(e.get('cross_shop_advantage_percent') or 0) if verified else 0
        points['cross_shop_advantage']=round(25*sc.clamp(gap/15),2); points['match_evidence']=10 if verified else 0
        x['comparison_shop_count']=e.get('comparison_shop_count',0) if verified else 0; x['other_shop_lowest_jpy']=e.get('other_shop_lowest_jpy') if verified else None
        x['cross_shop_advantage_percent']=gap if verified else None; x['is_comparable_lowest']=bool(verified and e.get('lowest_observed_jpy')==x['current_price_jpy'])
        if verified: caps.pop('无可比跨店JAN证据',None)
        else: caps['无可比跨店JAN证据']=69
        if max(x.get('history_7d_advantage_percent') or 0,x.get('history_30d_advantage_percent') or 0,gap)<5: caps['未证实至少5%价格优势']=59
        else: caps.pop('未证实至少5%价格优势',None)
        if e['status']!='confirmed_advantage': caps['精准二次比价未确认至少5%优势']=59
        x['raw_score']=round(sum(points.values()),2); x['score']=min([x['raw_score'],*caps.values()]); x['level']=next(l for c,l in sc.LEVELS if x['score']>=c)
        x['review_priority']='优先人工审核' if x['score']>=70 else '待补证据' if x['initial_review_priority']!='继续监测' or e['status']=='confirmed_advantage' else '继续监测'
        x['eligible_for_auto_publish']=False; x['verified']=False
        rows.append(x)
    out['items']=sorted(rows,key=lambda x:(-x['score'],x['id'])); out['precise_version']=1; out['precise_generated_at']=generated
    out['level_counts']=dict(Counter(x['level'] for x in rows)); out['review_priority_counts']=dict(Counter(x['review_priority'] for x in rows)); out['precise_status_counts']=dict(Counter(x['precise_status'] for x in rows))
    return out


def write(path,value):
    tmp=path.with_suffix('.step16.tmp'); tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8'); tmp.replace(path)


def run(data,appid,budget=MAX_REQUESTS,client=None,now=None):
    fixed_now=now
    now=now or now_utc()
    # Rebuild baseline using current raw inputs so a failed previous scoring step cannot reuse old results.
    base=sc.build(data,now)
    if base['status']!='OK': raise ValueError('no current scoring input')
    chosen,targets=plan(base['items'],budget); items={x['id']:x for x in base['items']}; bykey={}; errors={}; evidence={}
    if appid or client:
        client=client or Client(appid,budget)
        stopped=False
        for key in chosen:
            if stopped: errors[key]='STOPPED_AFTER_API_LIMIT'; continue
            try: bykey[key]=client.search(key)
            except SearchError as e: errors[key]=e.code; stopped=e.stop
    else:
        errors={k:'MISSING_CREDENTIALS' for k in chosen}
    for key in chosen:
        for pid in targets[key]:
            if key in bykey: evidence[pid]=compare(items[pid],key,bykey[key])
            else: evidence[pid]={'id':pid,'query_type':key[0],'query_key':list(key),'candidate_price_jpy':items[pid]['current_price_jpy'],'status':'query_error','error':errors[key]}
    generated=(fixed_now or now_utc()).isoformat()
    # Live timestamps use wall clock; tests pass an explicit deterministic time.
    report={'version':1,'status':'MISSING_CREDENTIALS' if not appid and not client else 'PARTIAL' if errors and bykey else 'API_ERROR' if errors else 'OK' if chosen else 'NO_TARGETS','source_generated_at':base['source_generated_at'],'generated_at':generated,'request_budget':max(0,min(MAX_REQUESTS,int(budget))),'requests':client.calls if client else 0,'planned_queries':len(chosen),'successful_queries':len(bykey),'failed_queries':len(errors),'query_results':[{'key':list(k),**v} for k,v in bykey.items()],'errors':[{'key':list(k),'code':v} for k,v in errors.items()],'evidence':evidence,'note':NOTE}
    scored=enrich(base,evidence,generated)
    write(data/'precise_comparison.json',report); write(data/'deal_scores.json',scored)
    print('Step 16:',report['status'],'requests:',report['requests'],'successful:',report['successful_queries'],'confirmed:',scored['precise_status_counts'].get('confirmed_advantage',0))
    return report,scored


def approval_check(data,source,x,now):
    """Independent persisted-evidence validation; fail closed on stale/missing report."""
    try:
        r=sc.read(data/'precise_comparison.json'); e=r['evidence'][x['id']]
        checked=stamp(r['generated_at']); ts=stamp(source['source_generated_at'])
        if not (r['version']==1 and r['status'] in ('OK','PARTIAL') and source.get('precise_version')==1 and r['source_generated_at']==source['source_generated_at'] and source.get('precise_generated_at')==r['generated_at']==x.get('precise_checked_at') and ts<=checked<=now and timedelta(0)<=now-ts<=MAX_AGE and e==x.get('precise_comparison') and e.get('candidate_price_jpy')==x['current_price_jpy'] and e.get('query_type')=='jan' and e.get('query_key')==['jan',sc.valid_jan(x.get('jan_code'))] and e.get('status')=='confirmed_advantage' and x.get('precise_status')=='confirmed_advantage' and e.get('own_confirmed') is True and e.get('comparison_shop_count',0)>=2 and (e.get('cross_shop_advantage_percent') or 0)>=5): return False
        # Recompute from the stored raw normalized hits, not just a status flag.
        q=next(z for z in r['query_results'] if z['key']==e['query_key'])
        return compare(x,tuple(q['key']),q)==e
    except (OSError,ValueError,KeyError,TypeError,AttributeError,StopIteration): return False


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--data-dir',type=Path,default=ROOT/'data'); p.add_argument('--budget',type=int,default=MAX_REQUESTS); args=p.parse_args()
    try: run(args.data_dir,os.getenv('YAHOO_CLIENT_ID','').strip(),args.budget)
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as e:
        # Never leak HTTP URLs or credentials; leave base data untouched on unexpected input failure.
        print('Step 16 INPUT_ERROR:',type(e).__name__); raise SystemExit(1)
