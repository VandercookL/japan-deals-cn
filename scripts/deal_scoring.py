"""Step 14: conservative Yahoo listing scoring. Standard library only.
Read existing snapshots; write a separate review-only JSON. Never publish deals.
"""
import argparse
import html
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = {'history_advantage':35, 'cross_shop_advantage':25,
           'history_coverage':15, 'review_evidence':15, 'match_evidence':10}
RISK_PATTERNS = {
    'condition': r'中古|整備済|再生品|refurbished|訳あり|外箱.{0,5}(?:傷|破損|汚)|箱.{0,3}(?:傷|破損)|保証開始済|開封済|展示品|ジャンク|未使用品|nランク',
    'packaging_or_import': r'並行輸入|バルク|\bbulk\b',
    'accessory_or_bundle': r'交換用|替えブラシのみ|専用ケース|保護フィルム|[0-9]+\s*(?:個|本|台)セット',
    'variant_ambiguity': r'ホワイト\s*[/、・ ]+\s*ブラック|ブラック\s*[/、・ ]+\s*ホワイト',
}
LEVELS = [(90,'极强好价候选'),(80,'强好价候选'),(70,'值得推荐候选'),(60,'普通优惠候选'),(0,'暂不推荐')]


def number(x):
    return type(x) in (int,float) and math.isfinite(x)


def positive(x):
    return number(x) and x>0


def clamp(x):
    return max(0,min(1,x))


def timestamp(s):
    t=datetime.fromisoformat(s.replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('timezone required')
    return t.astimezone(timezone.utc)


def valid_jan(x):
    s=str(x or '').strip()
    return s if re.fullmatch(r'(?:[0-9]{8}|[0-9]{13})',s) and sum(int(c)*(3 if i%2 else 1) for i,c in enumerate(reversed(s)))%10==0 else ''


def risk_flags(title):
    t=unicodedata.normalize('NFKC',html.unescape(str(title or ''))).lower()
    return [k for k,p in RISK_PATTERNS.items() if re.search(p,t)]


def shop_id(pid):
    return pid[6:].split('_',1)[0] if isinstance(pid,str) and pid.startswith('yahoo:') and '_' in pid[6:] else ''


def metric(m, days):
    hours=m.get('covered_hours'); pct=m.get('coverage_percent'); avg=m.get('average_jpy')
    coverage=min(clamp(hours/(days*24)),clamp(pct/100)) if number(hours) and number(pct) else 0
    complete=m.get('complete') is True and coverage>=0.9999 and positive(avg)
    return coverage,avg if complete else None


def unique_map(items):
    result={}
    for x in items:
        pid=x['id']
        if not isinstance(pid,str) or not pid or pid in result: raise ValueError('invalid or duplicate ID')
        result[pid]=x
    return result


def prepare(y,h,d,m,now):
    ts=timestamp(y['generated_at'])
    if not (timedelta(0)<=now-ts<=timedelta(hours=8)):
        raise ValueError('source expired or future dated')
    if d.get('version')!=3 or d.get('snapshot_fresh') is not True or any(timestamp(x['generated_at'])!=ts for x in (h,d)):
        raise ValueError('upstream snapshots inconsistent or stale')
    ym=unique_map(y['items']); hm=unique_map(h['items']); dm=unique_map(d['items'])
    if any(x.get('count')!=len(x['items']) for x in (y,h,d)) or not set(dm)<=set(hm):
        raise ValueError('upstream counts or IDs inconsistent')
    model_ids=set(); model_status='unavailable_or_stale'
    if m and m.get('snapshot_fresh') is True and m.get('source_generated_at')==y['generated_at']:
        model_status='current_review_only'
        for pair in m.get('pairs',[]):
            offers=pair.get('offers',[])
            if len(offers)==2 and all(o.get('id') in ym and o.get('price_jpy')==ym[o['id']].get('price_jpy') for o in offers):
                model_ids.update(o['id'] for o in offers)
    rows=[]; skipped=Counter()
    for pid,x in ym.items():
        p=x.get('price_jpy'); a=hm.get(pid); c=dm.get(pid,{})
        if not positive(p) or not shop_id(pid) or not x.get('title') or not str(x.get('url','')).startswith('https://') or x.get('source_type')!='yahoo_shopping_api':
            skipped['invalid_listing']+=1; continue
        if not a or a.get('current_price_jpy')!=p or timestamp(a['last_seen_at'])!=ts:
            skipped['missing_or_mismatched_history']+=1; continue
        if c and c.get('current_price_jpy')!=p:
            skipped['mismatched_drop_candidate']+=1; continue
        cov7,avg7=metric(a['history_7d'],7); cov30,avg30=metric(a['history_30d'],30)
        drop=c.get('drop_percent',0)
        rows.append({'id':pid,'price':p,'jan':valid_jan(x.get('jan_code')),'shop':shop_id(pid),
                     'rate':x.get('review_rate'),'reviews':x.get('review_count'),'risks':risk_flags(x['title']),
                     'recovery':a.get('recovered_price') is True,'new_low':a.get('new_observed_low') is True,
                     'model_review':pid in model_ids,'drop':drop if number(drop) else 0,
                     'cov7':cov7,'cov30':cov30,'avg7':avg7,'avg30':avg30,
                     'listing':{k:x.get(k) for k in ('title','merchant','shop_name','url','image_url','discovered_by','jan_code')},
                     'price_changed_at':a.get('price_changed_at')})
    return rows,dict(skipped),model_status


def advantage(reference,p):
    return max(0,(reference-p)/reference*100) if positive(reference) else 0


def score_rows(rows):
    groups=defaultdict(dict)
    for r in rows:
        if r['jan'] and not r['risks']:
            g=groups[r['jan']]; s=r['shop']
            g[s]=min(g.get(s,math.inf),r['price'])
    out=[]
    for r in rows:
        p=r['price']; avg7=r['avg7']; avg30=r['avg30']
        a7=advantage(avg7,p); a30=advantage(avg30,p)
        g=groups.get(r['jan'],{}) if not r['risks'] else {}
        others=[v for s,v in g.items() if s!=r['shop']]
        reference=min(others) if others else None
        lowest=bool(others and p==min(g.values()))
        gap=advantage(reference,p) if lowest else 0
        rate,n=r['rate'],r['reviews']
        review=15*clamp((rate-3)/2)*n/(n+20) if number(rate) and 0<rate<=5 and number(n) and n>0 else 0
        points={'history_advantage':20*clamp(a7/20)+15*clamp(a30/20),
                'cross_shop_advantage':25*clamp(gap/15),
                'history_coverage':10*clamp(r['cov7'])+5*clamp(r['cov30']),
                'review_evidence':review,'match_evidence':10 if others else 0}
        points={k:round(v,2) for k,v in points.items()}; raw=round(sum(points.values()),2)
        limits={}
        if r['recovery']: limits['恢复原价']=59
        if r['risks']: limits['销售条件或规格需核验']=59
        if not positive(avg7): limits['7日完整历史不足']=59
        if not others: limits['无可比跨店JAN证据']=69
        if r['model_review'] and not others: limits['型号匹配仅供人工核验']=59
        if max(a7,a30,gap)<5: limits['未证实至少5%价格优势']=59
        score=min([raw,*limits.values()]); level=next(label for cutoff,label in LEVELS if score>=cutoff)
        priority='优先人工审核' if score>=70 else '待补证据' if r['drop']>=5 or r['new_low'] or gap>=5 or r['model_review'] or r['risks'] else '继续监测'
        out.append({'id':r['id'],**r.get('listing',{}),'current_price_jpy':p,
                    'score':score,'raw_score':raw,'level':level,'score_breakdown':points,'score_caps':limits,
                    'review_priority':priority,'verified':False,'eligible_for_auto_publish':False,
                    'risk_flags':r['risks'],'recovered_price':r['recovery'],'new_observed_low':r['new_low'],
                    'drop_percent':r['drop'],'price_changed_at':r.get('price_changed_at'),
                    'history_7d_coverage_percent':round(r['cov7']*100,2),'history_30d_coverage_percent':round(r['cov30']*100,2),
                    'history_7d_average_jpy':avg7,'history_30d_average_jpy':avg30,
                    'history_7d_advantage_percent':round(a7,2) if avg7 is not None else None,
                    'history_30d_advantage_percent':round(a30,2) if avg30 is not None else None,
                    'valid_jan':bool(r['jan']),'comparison_shop_count':len(g),'other_shop_lowest_jpy':reference,
                    'is_comparable_lowest':lowest,'cross_shop_advantage_percent':round(gap,2) if others else None,
                    'model_review_candidate':r['model_review'],'review_rate':rate,'review_count':n,
                    'note':'标价比较；运费、积分、优惠券、库存、卖家资质及销售条件仍需人工核验。'})
    return sorted(out,key=lambda x:(-x['score'],x['id']))


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def build(data,now):
    y=read(data/'yahoo_candidates.json'); h=read(data/'price_history'/'analysis_latest.json'); d=read(data/'deal_candidates.json')
    try: m=read(data/'model_match_candidates.json')
    except (OSError,ValueError): m=None
    rows,skipped,model_status=prepare(y,h,d,m,now); items=score_rows(rows)
    return {'version':1,'status':'OK' if items else 'EMPTY','generated_at':now.isoformat(),'source_generated_at':y['generated_at'],
            'weights':WEIGHTS,'source_count':len(y['items']),'count':len(items),'skipped':skipped,
            'model_match_status':model_status,'level_counts':dict(Counter(x['level'] for x in items)),
            'review_priority_counts':dict(Counter(x['review_priority'] for x in items)),
            'risk_listing_count':sum(bool(x['risk_flags']) for x in items),'recovery_count':sum(x['recovered_price'] for x in items),
            'items':items,'note':'仅供审核；不改变V3候选及网站，不持久化乐天数据。'}


def run(data=ROOT/'data',now=None):
    now=now or datetime.now(timezone.utc)
    try: result=build(data,now)
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as e:
        result={'version':1,'status':'INPUT_ERROR','generated_at':now.isoformat(),'count':0,'items':[],
                'error_type':type(e).__name__,'note':'输入缺失、过期或不一致；停止评分，不影响原有数据。'}
    path=data/'deal_scores.json'; tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); tmp.replace(path)
    print('Step 14:',result['status'],'scored:',result['count'])
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    run(parser.parse_args().data_dir)
