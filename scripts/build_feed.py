import json
import os
import datetime
import urllib.request
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
now = datetime.datetime.now(datetime.timezone.utc)

manual = json.loads(
    (ROOT / 'data/manual_deals.json').read_text(encoding='utf-8')
)

items = []


# =========================================================
# 1. Validate manually verified deals
# =========================================================

def valid(x):
    if not isinstance(x, dict) or x.get('verified') is not True:
        return False

    required = (
        'id',
        'title',
        'merchant',
        'category',
        'url',
        'verified_at',
        'source_type',
        'deal_type'
    )

    if not all(
        isinstance(x.get(k), str) and x[k].strip()
        for k in required
    ):
        return False

    if not x['url'].startswith('https://'):
        return False

    if x['deal_type'] not in (
        '商品降价',
        '优惠券与积分',
        '线下促销'
    ):
        return False

    price = x.get('price_jpy')

    if price is not None and (
        isinstance(price, bool)
        or not isinstance(price, (int, float))
        or price <= 0
    ):
        return False

    if x['deal_type'] == '商品降价' and price is None:
        return False

    try:
        verified_at = datetime.datetime.fromisoformat(
            x['verified_at'].replace('Z', '+00:00')
        )

        if verified_at.tzinfo is None or verified_at > now:
            return False

        if x.get('expires_at'):
            expires = datetime.datetime.fromisoformat(
                x['expires_at'].replace('Z', '+00:00')
            )

            if expires.tzinfo is None or expires <= now:
                return False

    except (ValueError, TypeError):
        return False

    return True


for x in manual:
    if valid(x):
        items.append(x)


# =========================================================
# 2. Rakuten API
# Discovery candidates only
# =========================================================

app = os.getenv('RAKUTEN_APP_ID')
key = os.getenv('RAKUTEN_ACCESS_KEY')
keyword = os.getenv(
    'RAKUTEN_KEYWORD',
    'ブラックフライデー'
)

if app and key:

    params = {
        'applicationId': app,
        'accessKey': key,
        'keyword': keyword,
        'hits': 20,
        'format': 'json'
    }

    if os.getenv('RAKUTEN_AFFILIATE_ID'):
        params['affiliateId'] = os.environ[
            'RAKUTEN_AFFILIATE_ID'
        ]

    url = (
        'https://app.rakuten.co.jp/services/api/'
        'IchibaItem/Search/2026-07-01?'
        + urllib.parse.urlencode(params)
    )

    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'JapanDealsCN/0.1'
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=20
        ) as response:
            data = json.load(response)

        candidates = []

        for raw in data.get('Items', []):

            x = raw.get('Item', raw)

            candidates.append({
                'id': 'rakuten:' + str(
                    x.get('itemCode', '')
                ),
                'title': x.get(
                    'itemName',
                    ''
                ),
                'merchant': '楽天市場',
                'price_jpy': x.get(
                    'itemPrice'
                ),
                'url': x.get(
                    'itemUrl'
                ),
                'affiliate_url': x.get(
                    'affiliateUrl'
                ),
                'verified': False,
                'source_type': 'rakuten_api',
                'note': '候选商品，不代表真实折扣'
            })

        (
            ROOT / 'data/rakuten_candidates.json'
        ).write_text(
            json.dumps(
                candidates,
                ensure_ascii=False,
                indent=2
            ),
            encoding='utf-8'
        )

        print(
            'Rakuten candidates:',
            len(candidates)
        )

    except Exception as e:

        print(
            'Rakuten discovery failed:',
            type(e).__name__,
            str(e)[:160]
        )


# =========================================================
# 3. Yahoo! Shopping API
# Discovery candidates only
# =========================================================

yahoo_client_id = os.getenv('YAHOO_CLIENT_ID')

# First test keyword.
# We can expand this later.
yahoo_keyword = os.getenv(
    'YAHOO_KEYWORD',
    'ワイヤレスイヤホン'
)

if yahoo_client_id:

    params = {
        'appid': yahoo_client_id,
        'query': yahoo_keyword,
        'results': 20,
        'sort': '-score'
    }

    url = (
        'https://shopping.yahooapis.jp/'
        'ShoppingWebService/V3/itemSearch?'
        + urllib.parse.urlencode(params)
    )

    try:

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'JapanDealsCN/0.1'
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=20
        ) as response:
            data = json.load(response)

        candidates = []

        for x in data.get('hits', []):

            image = x.get('image') or {}

            review = x.get('review') or {}

            seller = x.get('seller') or {}

            candidates.append({

                'id': 'yahoo:' + str(
                    x.get('code', '')
                ),

                'title': x.get(
                    'name',
                    ''
                ),

                'merchant': 'Yahoo!ショッピング',

                'shop_name': seller.get(
                    'name',
                    ''
                ),

                'price_jpy': x.get(
                    'price'
                ),

                'jan_code': x.get(
                    'janCode',
                    ''
                ),

                'image_url': image.get(
                    'medium',
                    ''
                ),

                'url': x.get(
                    'url',
                    ''
                ),

                'review_rate': review.get(
                    'rate'
                ),

                'review_count': review.get(
                    'count'
                ),

                'verified': False,

                'source_type': 'yahoo_shopping_api',

                'note': '候选商品，不代表真实折扣'
            })

        (
            ROOT / 'data/yahoo_candidates.json'
        ).write_text(
            json.dumps(
                {
                    'generated_at': now.isoformat(
                        timespec='seconds'
                    ),
                    'keyword': yahoo_keyword,
                    'count': len(candidates),
                    'items': candidates
                },
                ensure_ascii=False,
                indent=2
            ),
            encoding='utf-8'
        )

        print(
            'Yahoo candidates:',
            len(candidates)
        )

    except Exception as e:

        print(
            'Yahoo discovery failed:',
            type(e).__name__,
            str(e)[:300]
        )

else:

    print(
        'Yahoo discovery skipped: '
        'YAHOO_CLIENT_ID is not configured'
    )


# =========================================================
# 4. Publish verified deals
# =========================================================

seen = set()
result = []

for x in items:

    identity = x.get('id') or x['url']

    if identity not in seen:
        result.append(x)
        seen.add(identity)


result.sort(
    key=lambda x: x['verified_at'],
    reverse=True
)


(
    ROOT / 'site/deals.json'
).write_text(
    json.dumps(
        {
            'generated_at': now.isoformat(
                timespec='seconds'
            ),
            'deals': result
        },
        ensure_ascii=False,
        indent=2
    ),
    encoding='utf-8'
)


print(
    'Published verified deals:',
    len(result)
)
