"""Step 15: GitHub-authenticated human review, no API secrets or dependencies.
Run via review_deals.yml. Only approved, manually verified listings are published.
"""
import argparse
from precise_comparison import approval_check
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
