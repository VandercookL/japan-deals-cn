"""Step 12: conservative model matching; review only, no automatic publication."""
import json, re, html, unicodedata, math
from pathlib import Path
from itertools import combinations
from datetime import datetime, timezone
from collections import Counter
ROOT = Path(__file__).resolve().parents[1]
BRANDS = {'crucial':r'crucial|クルーシャル','sandisk':r'sandisk|サンディスク','delonghi':r'delonghi|デロンギ','anker':r'anker|アンカー','salonia':r'salonia|サロニア','orico':r'orico','nitori':r'ニトリ'}
# Explicit manufacturer model families, not seller listing IDs.
MODELS = {'crucial':r'\bct\d{3,5}[a-z0-9]{4,15}\b','sandisk':r'\bsdss[a-z0-9-]{4,25}\b','delonghi':r'\becam\d{5}[a-z]{1,3}\b','anker':r'\ba\d{4}\b','salonia':r'\bsal-?\d{3}[a-z0-9-]*\b','nitori':r'\bhm\d{2}[a-z]{1,4}\b'}
def norm(s):
    return unicodedata.normalize('NFKC',html.unescape(str(s or ''))).lower()
def jan(x):
    return str(x.get('jan_code') or '').strip()
def shop(x):
    # Yahoo listing ID includes store code; never use the item suffix as model.
    return str(x.get('id','')).removeprefix('yahoo:').partition('_')[0] if '_' in str(x.get('id','')) else ''
def price(x):
    p=x.get('price_jpy'); return type(p) in (int,float) and math.isfinite(p) and p>0
def features(x):
    t=norm(x.get('title')); brands={b for b,p in BRANDS.items() if re.search(p,t)}
    models={(b,m) for b in brands for m in re.findall(MODELS.get(b,r'(?!)'),t)}
    caps={float(n)*(1000 if u=='tb' else 1) for n,u in re.findall(r'(\d+(?:\.\d+)?)\s*(tb|gb)\b',t)}
    flags={k for k,p in {'bulk':r'バルク|bulk','import':r'並行輸入','used':r'中古|整備済|refurbished','accessory':r'交換用|専用ケース|替えブラシのみ|保護フィルム','set':r'\d+\s*(?:個|本|台)セット'}.items() if re.search(p,t)}
    return brands,models,caps,flags

def match(items):
    rows=[x for x in items if price(x) and shop(x)]
    f={x['id']:features(x) for x in rows}; pairs=[]; rejected=Counter()
    for a,b in combinations(rows,2):
        if jan(a) and jan(b): continue
        if shop(a)==shop(b): continue
        ba,ma,ca,fa=f[a['id']]; bb,mb,cb,fb=f[b['id']]
        shared=ma&mb
        if not shared: continue
        if len(ma)!=1 or len(mb)!=1 or ba!=bb or len(ba)!=1:
            rejected['ambiguous_model']+=1; continue
        if ca!=cb or len(ca)>1:
            rejected['capacity_conflict_or_ambiguity']+=1; continue
        if fa!=fb or fa&{'used','accessory','set'}:
            rejected['condition_or_bundle_conflict']+=1; continue
        brand,model=next(iter(shared))
        offers=sorted([{k:x.get(k) for k in ('id','title','shop_name','price_jpy','url','jan_code')} for x in (a,b)],key=lambda x:x['price_jpy'])
        lo,hi=[x['price_jpy'] for x in offers]
        pairs.append({'brand':brand,'model':model,'match_level':'型号一致，待人工核验','verified':False,'eligible_for_auto_deal':False,'evidence':['标题品牌一致','制造商型号一致','容量字段无冲突','不同店铺'],'limitations':['颜色、版本、包装、保修及运费仍需核验'],'offers':offers,'price_difference_jpy':hi-lo,'price_difference_percent':round((hi-lo)/hi*100,2)})
    return pairs,dict(rejected)

def main():
    data=ROOT/'data'; source=json.loads((data/'yahoo_candidates.json').read_text(encoding='utf-8'))
    history=json.loads((data/'price_history'/'analysis_latest.json').read_text(encoding='utf-8'))
    # Require the same successful snapshot as step 11, never reuse stale results.
    candidates=json.loads((data/'deal_candidates.json').read_text(encoding='utf-8'))
    fresh=candidates.get('snapshot_fresh') is True and history.get('generated_at')==source.get('generated_at')
    if fresh:
        ids={x['id'] for x in history['items']}; items=[x for x in source['items'] if x['id'] in ids]; pairs,rejected=match(items)
    else: items=[]; pairs=[]; rejected={}
    out={'version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'source_generated_at':source.get('generated_at'),'snapshot_fresh':fresh,'listing_count':len(items),'missing_jan_count':sum(not jan(x) for x in items),'pair_count':len(pairs),'rejected':rejected,'pairs':pairs,'note':'仅人工审核候选，不改变JAN比价、V3候选或网站发布。'}
    path=data/'model_match_candidates.json'; tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); tmp.replace(path)
    print('Step 12:',len(pairs),'review pairs; fresh:',fresh)
if __name__=='__main__': main()
