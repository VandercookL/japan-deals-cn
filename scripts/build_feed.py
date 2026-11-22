# Yahoo Shopping discovery keywords
yahoo_keywords = [
    'Anker',
    'AirPods',
    'モバイルバッテリー',
    'ワイヤレスイヤホン',
    'SSD',
    'Nintendo Switch',
    '炊飯器',
    '掃除機',
    'ドライヤー',
    '電動歯ブラシ',
    '加湿器',
    'コーヒーメーカー'
]

if yahoo_client_id:

    all_candidates = []
    seen_yahoo_ids = set()

    for yahoo_keyword in yahoo_keywords:

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

            keyword_count = 0

            for x in data.get('hits', []):

                code = str(x.get('code', '')).strip()

                # Skip items without a Yahoo item code
                if not code:
                    continue

                yahoo_id = 'yahoo:' + code

                # Same product may appear under multiple keywords.
                # Do not save the same Yahoo listing twice.
                if yahoo_id in seen_yahoo_ids:
                    continue

                seen_yahoo_ids.add(yahoo_id)

                image = x.get('image') or {}
                review = x.get('review') or {}
                seller = x.get('seller') or {}

                all_candidates.append({

                    'id': yahoo_id,

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

                    'discovered_by': yahoo_keyword,

                    'verified': False,

                    'source_type': 'yahoo_shopping_api',

                    'note': '候选商品，不代表真实折扣'
                })

                keyword_count += 1

            print(
                'Yahoo keyword:',
                yahoo_keyword,
                'new candidates:',
                keyword_count
            )

        except Exception as e:

            print(
                'Yahoo discovery failed:',
                yahoo_keyword,
                type(e).__name__,
                str(e)[:300]
            )

    (
        ROOT / 'data/yahoo_candidates.json'
    ).write_text(
        json.dumps(
            {
                'generated_at': now.isoformat(
                    timespec='seconds'
                ),
                'keywords': yahoo_keywords,
                'count': len(all_candidates),
                'items': all_candidates
            },
            ensure_ascii=False,
            indent=2
        ),
        encoding='utf-8'
    )

    print(
        'Yahoo total unique candidates:',
        len(all_candidates)
    )

else:

    print(
        'Yahoo discovery skipped: '
        'YAHOO_CLIENT_ID is not configured'
    )
