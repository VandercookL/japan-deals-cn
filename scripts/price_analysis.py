"""Step 11: conservative historical analysis, after build_feed.py."""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


def stamp(s):
    t = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('Timezone required')
    return t.astimezone(timezone.utc)


def valid(p):
    return type(p) in (int, float) and math.isfinite(p) and p > 0


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    tmp.replace(path)


def metrics(points, observations, now, days):
    start = now - timedelta(days=days)
    # Only intervals bracketed by actual observations, at most 8 hours apart.
    seconds = weighted = 0
    prices = []
    obs = sorted(observations.items())
    for (a, p), (b, q) in zip(obs, obs[1:]):
        if b-a > timedelta(hours=8):
            continue
        duration = (min(b, now)-max(a, start)).total_seconds()
        if duration > 0:
            seconds += duration
            weighted += p*duration
            prices.append(p)
    prices += [p for t,p in points if start <= t <= now]
    prices += [p for t,p in obs if start <= t <= now]
    full = seconds >= days*86400 - 1
    return {'window_days': days, 'covered_hours': round(seconds/3600, 2),
            'coverage_percent': round(seconds/(days*86400)*100, 2),
            'complete': full, 'observed_low_jpy': min(prices) if prices else None,
            'low_jpy': min(prices) if full and prices else None,
            'estimated_average_jpy': round(weighted/seconds, 2) if seconds else None,
            'average_jpy': round(weighted/seconds, 2) if full and seconds else None}


def analyze(data=DATA):
    history_dir = data/'price_history'
    snapshot = read(data/'yahoo_candidates.json')
    legacy = read(data/'deal_candidates.json')
    now = stamp(legacy['generated_at'])
    fresh = stamp(snapshot['generated_at']) == now
    items = {x['id']: x for x in snapshot['items'] if valid(x.get('price_jpy'))} if fresh else {}
    events = defaultdict(dict)
    for path in sorted(history_dir.glob('yahoo_????-??.json')):
        for pid, product in read(path)['products'].items():
            for e in product['history']:
                if valid(e['price']) and stamp(e['time']) <= now:
                    events[pid][stamp(e['time'])] = e['price']
    # Separate monthly observation logs: preserve original change-only files.
    log_path = history_dir/f'observations_{now:%Y-%m}.json'
    log = read(log_path) if log_path.exists() else {'runs': {}}
    log['runs'][now.isoformat()] = {'snapshot_fresh': fresh, 'listing_count': len(items),
                                    'prices': {pid:x['price_jpy'] for pid,x in items.items()}}
    write(log_path, log)
    observations = defaultdict(dict)
    for path in sorted(history_dir.glob('observations_????-??.json')):
        for ts, run in read(path)['runs'].items():
            t = stamp(ts)
            if t <= now:
                for pid,p in run['prices'].items():
                    if valid(p): observations[pid][t] = p
    groups = defaultdict(dict)
    for x in items.values():
        jan = str(x.get('jan_code') or '').strip()
        shop = str(x.get('shop_name') or '').strip()
        if jan and shop:
            groups[jan][shop] = min(groups[jan].get(shop, math.inf), x['price_jpy'])
    old = {x['id']:x for x in legacy['items']}
    candidates = []
    analysis = []
    for pid,x in items.items():
        p = x['price_jpy']
        merged = dict(events[pid]); merged.update(observations[pid])
        merged[now] = p
        points = []
        for t,v in sorted(merged.items()):
            if not points or points[-1][1] != v: points.append((t,v))
        prices = [v for t,v in points]
        m7 = metrics(points, observations[pid], now, 7)
        m30 = metrics(points, observations[pid], now, 30)
        prev = prices[-2] if len(prices)>1 else None
        drop = (prev-p)/prev*100 if prev else 0
        recovery = len(prices)>=3 and prev>p and p>=min(prices[:-2]) and p<=prices[-3]
        new_low = len(prices)>1 and p<min(prices[:-1])
        baseline = m7['average_jpy']
        advantage = (baseline-p)/baseline*100 if baseline else None
        classification = ('恢复原价' if recovery else '监测期新低' if new_low else
                          '近期低价' if m7['complete'] and p==m7['low_jpy'] else
                          '普通降价' if m7['complete'] and drop>=5 else '数据不足' if not m7['complete'] else '无明显降价')
        extra = {'id':pid,'history_7d':m7,'history_30d':m30,'historical_low_jpy':min(prices),
                 'historical_high_jpy':max(prices),'price_change_points':len(points),
                 'history_classification':classification,'recovered_price':recovery,
                 'new_observed_low':new_low,'last_seen_at':now.isoformat(),
                 'price_changed_at':points[-1][0].isoformat(), 'current_price_jpy':p}
        analysis.append(extra)
        if drop<5: continue
        jan = str(x.get('jan_code') or '').strip(); shop = str(x.get('shop_name') or '').strip()
        shops = groups[jan] if jan else {}
        others = sorted(v for k,v in shops.items() if k!=shop)
        available = bool(shop and others)
        lowest = available and p==min(shops.values())
        second = others[0] if lowest else None
        gap = (second-p)/second*100 if second else None
        good = lowest and not recovery and advantage is not None and advantage>=5
        strong = good and drop>=10 and gap is not None and gap>=5 and advantage>=10
        c = dict(old.get(pid, {})); c.update(extra)
        c.update({k:x.get(k,'') for k in ('title','merchant','shop_name','jan_code','url')})
        c.update(previous_price_jpy=prev, drop_percent=round(drop,1),
                 event_median_price_jpy=__import__('statistics').median(prices),
                 cross_shop_available=available, comparison_listing_count=len(shops),
                 comparison_shop_count=len(shops), is_cross_shop_lowest=lowest,
                 lowest_cross_shop_price_jpy=min(shops.values()) if available else None,
                 second_lowest_price_jpy=second, cheaper_than_second_jpy=second-p if second else None,
                 cheaper_than_second_percent=round(gap,1) if gap is not None else None,
                 status='强好价候选' if strong else '好价候选' if good else '降价候选',
                 detected_at=points[-1][0].isoformat(), verified=False,
                 reasons=[f'较上一变价记录下降{drop:.1f}%', classification,
                          '7日覆盖完整' if m7['complete'] else '7日覆盖不足，不升级为好价'],
                 note='自动筛选，尚未人工验证；不含运费、积分及优惠券')
        candidates.append(c)
    rank={'强好价候选':3,'好价候选':2,'降价候选':1}
    candidates.sort(key=lambda c:(rank[c['status']],c['drop_percent']),reverse=True)
    legacy.update(version=3, count=len(candidates), items=candidates,
                  strong_candidate_count=sum(c['status']=='强好价候选' for c in candidates),
                  good_candidate_count=sum(c['status']=='好价候选' for c in candidates),
                  price_drop_candidate_count=sum(c['status']=='降价候选' for c in candidates),
                  rules={'minimum_drop_percent':5,'good_deal':'非恢复原价、7日完整覆盖、低于7日估算均价至少5%、不同店铺最低价',
                         'strong_deal':'好价且降幅及7日均价优势至少10%、比其他店最低价便宜至少5%'},
                  snapshot_fresh=fresh)
    write(history_dir/'analysis_latest.json', {'generated_at':now.isoformat(),'count':len(analysis),'items':analysis})
    write(data/'deal_candidates.json',legacy)
    print('Step 11:',len(analysis),'observed listings;',len(candidates),'candidates')

if __name__=='__main__':
    analyze()
