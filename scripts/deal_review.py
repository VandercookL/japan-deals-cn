"""Step 15: GitHub-authenticated human review, no API secrets or dependencies.
Run via review_deals.yml. Only approved, manually verified listings are published.
"""
import argparse
import hashlib
import html
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
TYPES = {'商品降价', '优惠券与积分', '线下促销'}
CATEGORIES = {'数码家电', '综合购物', '超市日用', '食品饮料', '美妆个护', '服饰鞋包', '其他'}
KEYWORDS = {'Anker':'Anker 配件','AirPods':'AirPods 耳机','モバイルバッテリー':'移动电源','ワイヤレスイヤホン':'无线耳机','SSD':'固态硬盘','Nintendo Switch':'Nintendo Switch 游戏设备','炊飯器':'电饭煲','掃除機':'吸尘器','ドライヤー':'吹风机','電動歯ブラシ':'电动牙刷','加湿器':'加湿器','コーヒーメーカー':'咖啡机'}

class ReviewError(ValueError):
    pass


def stamp(s):
    if not isinstance(s,str): raise ReviewError('时间格式无效')
    t=datetime.fromisoformat(s.replace('Z','+00:00'))
    if t.tzinfo is None: raise ReviewError('时间缺少时区')
    return t.astimezone(timezone.utc)


def positive(p):
    return type(p) in (int,float) and math.isfinite(p) and p>0


def canon(url, yahoo=False):
    if not isinstance(url,str) or any(ord(c)<33 or c in '\\<>"' for c in url): return ''
    try:
        u=urlsplit(url)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.port not in (None,443): return ''
        if yahoo and u.hostname!='store.shopping.yahoo.co.jp': return ''
        return urlunsplit(('https',u.hostname.lower(),u.path.rstrip('/') or '/', '' if u.hostname=='store.shopping.yahoo.co.jp' else u.query,''))
    except ValueError:
        return ''


def jan(value):
    s=str(value or '').strip()
    return s if re.fullmatch(r'(?:[0-9]{8}|[0-9]{13})',s) and sum(int(c)*(3 if i%2 else 1) for i,c in enumerate(reversed(s)))%10==0 else ''


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as f:
        name=f.name; json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False); f.write('\n')
    try: os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def manual_rows(data):
    rows=read(data/'manual_deals.json')
    if not isinstance(rows,list) or any(not isinstance(x,dict) for x in rows): raise ReviewError('人工数据格式错误，停止写入')
    ids=[x.get('id') for x in rows]
    if any(not isinstance(i,str) or not i for i in ids) or len(ids)!=len(set(ids)): raise ReviewError('人工记录ID缺失或重复，停止写入')
    return rows


def active(x,now):
    # Keep the existing build_feed validation semantics for legacy manual records.
    if x.get('verified') is not True: return False
    if not all(isinstance(x.get(k),str) and x[k].strip() for k in ('id','title','merchant','category','url','verified_at','source_type','deal_type')): return False
    if not x['url'].startswith('https://') or x['deal_type'] not in TYPES: return False
    p=x.get('price_jpy')
    if p is not None and not positive(p): return False
    if x['deal_type']=='商品降价' and p is None: return False
    try: return stamp(x['verified_at'])<=now and (not x.get('expires_at') or stamp(x['expires_at'])>now)
    except (ValueError,TypeError): return False


def publish(rows,now):
    items=sorted([x for x in rows if active(x,now)],key=lambda x:x['verified_at'],reverse=True)
    return {'generated_at':now.isoformat(timespec='seconds'),'deals':items}


def decisions(data):
    path=data/'review_decisions.json'
    if not path.exists(): return []
    j=read(path)
    if not isinstance(j,dict) or j.get('version')!=1 or not isinstance(j.get('events'),list): raise ReviewError('审核记录格式错误')
    for e in j['events']:
        if not isinstance(e,dict) or not isinstance(e.get('candidate_id'),str) or e.get('action') not in ('approve','reject','restore','unpublish'): raise ReviewError('审核记录格式错误')
    return j['events']


def latest(events,pid,price):
    return next((e['action'] for e in reversed(events) if e['candidate_id']==pid and e.get('price_jpy')==price),'')


def snapshot(data,now):
    s=read(data/'deal_scores.json'); y=read(data/'yahoo_candidates.json'); h=read(data/'price_history'/'analysis_latest.json'); d=read(data/'deal_candidates.json')
    if s.get('status')!='OK' or s.get('version')!=1 or d.get('version')!=3 or d.get('snapshot_fresh') is not True: raise ReviewError('评分或上游状态不是OK')
    ts=stamp(s['source_generated_at'])
    if not timedelta(0)<=now-ts<=timedelta(hours=8) or not ts<=stamp(s['generated_at'])<=now: raise ReviewError('采集/评分数据过期或时间异常')
    if any(stamp(a['generated_at'])!=ts for a in (y,h,d)): raise ReviewError('采集与分析快照不一致')
    def index(a):
        items=a['items']; result={x['id']:x for x in items}
        if len(result)!=len(items) or a.get('count')!=len(items): raise ReviewError('快照条数或ID不一致')
        return result
    ym,hm,sm=index(y),index(h),index(s)
    if s.get('source_count')!=len(ym) or not set(sm)<=set(ym) or not set(sm)<=set(hm): raise ReviewError('评分来源不一致')
    for pid,x in sm.items():
        p=x.get('current_price_jpy'); o=ym[pid]; a=hm[pid]
        if not (isinstance(pid,str) and pid.startswith('yahoo:') and positive(p) and p==o.get('price_jpy')==a.get('current_price_jpy') and stamp(a['last_seen_at'])==ts and o.get('source_type')=='yahoo_shopping_api' and canon(x.get('url'),True) and canon(x['url'],True)==canon(o.get('url'),True) and x.get('title')==o.get('title') and str(x.get('jan_code') or '')==str(o.get('jan_code') or '') and type(x.get('score')) in (int,float) and 0<=x['score']<=100 and isinstance(x.get('risk_flags'),list) and isinstance(x.get('recovered_price'),bool)):
            raise ReviewError('评分商品和原始数据不一致')
    return s,sm


def clean_text(s,limit,label):
    s=str(s or '').strip()
    if not s or len(s)>limit or any(ord(c)<32 and c!='\n' for c in s): raise ReviewError(label+'为空、超长或含控制字符')
    if re.search(r'待核验|待填写|填写商品名称|example\.com|\[填写',s,re.I): raise ReviewError(label+'含待填写内容')
    return s


def duplicate(rows,x,now):
    pid=x['id']; u=canon(x['url'],True); j=jan(x.get('jan_code'))
    for old in rows:
        if active(old,now) and (old['id']==pid or old.get('candidate_id')==pid or canon(old.get('url'))==u or (j and jan(old.get('jan_code'))==j)):
            return old['id']
    return ''


def eligible(x):
    return not x['risk_flags'] and not x['recovered_price'] and x.get('review_priority') in ('待补证据','优先人工审核')


def event(action,pid,price,now,manual_id=''):
    return {'action':action,'candidate_id':pid,'price_jpy':price,'time':now.isoformat(),'manual_id':manual_id}


def apply(root,action,pid='',price='',title='',description='',category='数码家电',hours='6',confirmed=False,now=None):
    now=now or datetime.now(timezone.utc); data=root/'data'; site=root/'site'
    rows=manual_rows(data); events=decisions(data)
    if action=='review': return report(data,rows,events,now)
    if action not in ('approve','reject','restore','unpublish') or confirmed is not True: raise ReviewError('请选择操作并勾选人工确认')
    pid=str(pid or '').strip()
    if not pid: raise ReviewError('缺少商品/人工记录ID')
    if action=='unpublish':
        old=next((x for x in rows if x['id']==pid),None)
        if old is None or old.get('verified') is not True: raise ReviewError('人工记录不存在或已经下架')
        old['verified']=False
        events.append(event(action,old.get('candidate_id') or pid,old.get('price_jpy'),now,pid))
    else:
        source,byid=snapshot(data,now); x=byid.get(pid)
        if x is None: raise ReviewError('当前快照无此商品')
        p=x['current_price_jpy']; state=latest(events,pid,p)
        if action=='reject':
            if duplicate(rows,x,now): raise ReviewError('商品已发布，先主动下架')
            if state in ('reject','unpublish'): raise ReviewError('该价格已拒绝/下架')
            events.append(event(action,pid,p,now))
        elif action=='restore':
            if state not in ('reject','unpublish'): raise ReviewError('当前价格没有需要恢复的拒绝/下架记录')
            events.append(event(action,pid,p,now))
        else:
            if not eligible(x): raise ReviewError('风险、恢复原价或继续监测商品不能通过快捷审批')
            if state in ('reject','unpublish'): raise ReviewError('先恢复被拒绝/下架的候选')
            if duplicate(rows,x,now): raise ReviewError('已存在有效同商品/链接/JAN记录')
            if not re.fullmatch(r'[1-9][0-9]{0,9}',str(price)) or int(price)!=p: raise ReviewError('人工核验标价必须等于当前采集价；价格变化时重新采集')
            if str(hours) not in ('1','3','6','12','24') or category not in CATEGORIES: raise ReviewError('分类或有效期无效')
            title=clean_text(title,160,'商品标题'); description=clean_text(description,1000,'中文介绍及核验理由')
            if len(description)<10: raise ReviewError('请填写具体核验理由和优惠条件')
            mid='review-'+hashlib.sha256((pid+'|'+now.isoformat()).encode()).hexdigest()[:24]
            if any(r['id']==mid for r in rows): raise ReviewError('审核记录ID冲突')
            row={'id':mid,'candidate_id':pid,'deal_type':'商品降价','title':title,'description':description,'merchant':'Yahoo! Shopping','shop_name':x.get('shop_name') or '', 'category':category,'location':'线上','price_jpy':int(price),'url':canon(x['url'],True),'affiliate_url':'','source_type':'official_product_page','verified_at':now.astimezone(JST).isoformat(timespec='seconds'),'expires_at':(now+timedelta(hours=int(hours))).astimezone(JST).isoformat(timespec='seconds'),'verified':True,'jan_code':jan(x.get('jan_code')),'review_source_generated_at':source['source_generated_at']}
            if not active(row,now): raise ReviewError('新记录不符合发布要求')
            rows.append(row); events.append(event(action,pid,p,now,mid))
    # Validation finished. GitHub workflow commits the files together only on success.
    if action in ('approve','unpublish'):
        write(data/'manual_deals.json',rows); write(site/'deals.json',publish(rows,now))
    write(data/'review_decisions.json',{'version':1,'events':events})
    return '### 审核操作已准备\n\n'+md(action)+' / `'+md(pid)+'`\n\n等待本次工作流提交成功；以 GitHub 提交及 Cloudflare 部署结果为准。\n'


def md(value):
    # Untrusted titles must be rendered as text, not Markdown or HTML.
    s=' '.join(str(value if value is not None else '').split())
    s=re.sub(r'([\\*_[\]#~])',r'\\\1',s)
    return html.escape(s,quote=True).replace('|','&#124;').replace('`','&#96;')


def draft(x):
    name=KEYWORDS.get(x.get('discovered_by'),'商品')
    return f'{name}：{x.get("shop_name") or "Yahoo! Shopping"} 采集标价 ¥{x["current_price_jpy"]:,}。请核对具体型号、库存、商品状态、运费、积分及优惠券条件；仅在确认价格优势后发布。'


def report(data,rows,events,now):
    out=['# 在日好价｜审核管理','',f'生成时间（日本）：{now.astimezone(JST):%Y-%m-%d %H:%M:%S}','', '## 人工发布记录','', '| 人工记录ID | 标题 | 状态 | 截止时间 |','|---|---|---|---|']
    for x in rows:
        state='有效' if active(x,now) else '已过期/无效' if x.get('verified') is True else '未核验/已下架'
        out.append(f'| {md(x["id"])} | {md(x.get("title"))} | {state} | {md(x.get("expires_at"))} |')
    out+=['','## 最新审核候选','']
    try: s,byid=snapshot(data,now)
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as e:
        out+=['评分输入缺失、过期或不一致：请先运行 Update deals data。仍可主动下架已有人工记录。']; return '\n'.join(out)+'\n'
    candidates=[x for x in byid.values() if x.get('review_priority')!='继续监测']
    out+=[f'采集：{md(s["source_generated_at"])}；评分 {len(byid)} 件；待审/补证据 {len(candidates)} 件。', '低分不等于无优惠。评分仅辅助审核；不得将未经核验的标题或折扣当成事实。','', '| 候选ID | 商品原始标题 / 店铺 | 标价 | 分数 | 审核状态 | 7日覆盖 | 风险/限制 |','|---|---|---:|---:|---|---:|---|']
    for x in candidates[:100]:
        state=latest(events,x['id'],x['current_price_jpy']); dup=duplicate(rows,x,now)
        label='已发布' if dup else '已拒绝/下架' if state in ('reject','unpublish') else '可人工核验' if eligible(x) else '快捷审批禁用'
        reasons=list(x.get('risk_flags',[]))+list(x.get('score_caps',{}))
        url=canon(x['url'],True); link=f'[商品页]({url.replace("(","%28").replace(")","%29")})'
        out.append(f'| {md(x["id"])} | {md(x["title"])} / {md(x.get("shop_name"))} {link} | ¥{x["current_price_jpy"]:,} | {x["score"]} | {label} | {md(x.get("history_7d_coverage_percent"))}% | {md("；".join(reasons))} |')
    out+=['','## 中文介绍草稿（需编辑并人工核验）','']
    for x in candidates[:100]:
        if eligible(x) and not duplicate(rows,x,now) and latest(events,x['id'],x['current_price_jpy']) not in ('reject','unpublish'):
            def yen(v): return f'¥{v:,}' if positive(v) else '数据不足'
            out.append(f'- **{md(x["id"])}**：{md(draft(x))}')
            out.append(f'  - 7日均价：{yen(x.get("history_7d_average_jpy"))}；30日均价：{yen(x.get("history_30d_average_jpy"))}；其他可比店最低标价：{yen(x.get("other_shop_lowest_jpy"))}。')
            out.append(f'  - 跨店标价优势：{md(x.get("cross_shop_advantage_percent") if x.get("cross_shop_advantage_percent") is not None else "无证据")}；上一变价时间：{md(x.get("price_changed_at") or "无记录")}。均非人工确认结果。')
    out+=['','操作：重新运行 Review and publish deals，选择 approve / reject / restore / unpublish。批准前必须打开商品页核对实际价格、优势依据及条件，填写中文标题和介绍，并勾选确认。','公开仓库：不要在输入、日志或审核记录中填写个人信息、密钥或私密备注。']
    return '\n'.join(out)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--root',type=Path,default=ROOT); args=parser.parse_args()
    try:
        result=apply(args.root,os.getenv('REVIEW_ACTION','review'),os.getenv('REVIEW_ID',''),os.getenv('REVIEW_PRICE',''),os.getenv('REVIEW_TITLE',''),os.getenv('REVIEW_DESCRIPTION',''),os.getenv('REVIEW_CATEGORY','数码家电'),os.getenv('REVIEW_HOURS','6'),os.getenv('REVIEW_CONFIRMED')=='true')
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as e:
        print('Review failed:',str(e) if isinstance(e,ReviewError) else type(e).__name__); raise SystemExit(1)
    summary=os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as f: f.write(result)
    else: print(result)

if __name__=='__main__': main()
