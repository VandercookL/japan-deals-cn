import json
import os
import datetime
import statistics
import urllib.request
import urllib.parse
from pathlib import Path


# =========================================================
# Basic settings
# =========================================================

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"

DATA_DIR.mkdir(exist_ok=True)
SITE_DIR.mkdir(exist_ok=True)

now = datetime.datetime.now(datetime.timezone.utc)


# =========================================================
# 1. Load and validate manually verified deals
# =========================================================

manual_file = DATA_DIR / "manual_deals.json"

if manual_file.exists():
    try:
        manual = json.loads(
            manual_file.read_text(encoding="utf-8")
        )
    except Exception as e:
        print(
            "Failed to read manual_deals.json:",
            type(e).__name__,
            str(e)[:200]
        )
        manual = []
else:
    manual = []


def valid_deal(x):

    if not isinstance(x, dict):
        return False

    if x.get("verified") is not True:
        return False

    required_fields = (
        "id",
        "title",
        "merchant",
        "category",
        "url",
        "verified_at",
        "source_type",
        "deal_type",
    )

    for key in required_fields:
        value = x.get(key)

        if not isinstance(value, str) or not value.strip():
            return False

    if not x["url"].startswith("https://"):
        return False

    if x["deal_type"] not in (
        "商品降价",
        "优惠券与积分",
        "线下促销",
    ):
        return False

    price = x.get("price_jpy")

    if price is not None:

        if isinstance(price, bool):
            return False

        if not isinstance(price, (int, float)):
            return False

        if price <= 0:
            return False

    if x["deal_type"] == "商品降价" and price is None:
        return False

    try:

        verified_at = datetime.datetime.fromisoformat(
            x["verified_at"].replace(
                "Z",
                "+00:00"
            )
        )

        if verified_at.tzinfo is None:
            return False

        if verified_at > now:
            return False

        expires_at = x.get("expires_at")

        if expires_at:

            expires = datetime.datetime.fromisoformat(
                expires_at.replace(
                    "Z",
                    "+00:00"
                )
            )

            if expires.tzinfo is None:
                return False

            if expires <= now:
                return False

    except (ValueError, TypeError):
        return False

    return True


verified_items = [
    deal
    for deal in manual
    if valid_deal(deal)
]


print(
    "Valid manual deals:",
    len(verified_items)
)


# =========================================================
# 2. Rakuten API
# Discovery candidates only
# =========================================================

rakuten_app_id = os.getenv(
    "RAKUTEN_APP_ID"
)

rakuten_access_key = os.getenv(
    "RAKUTEN_ACCESS_KEY"
)

rakuten_affiliate_id = os.getenv(
    "RAKUTEN_AFFILIATE_ID"
)

rakuten_keyword = os.getenv(
    "RAKUTEN_KEYWORD",
    "ブラックフライデー"
)


if rakuten_app_id and rakuten_access_key:

    params = {
        "applicationId": rakuten_app_id,
        "accessKey": rakuten_access_key,
        "keyword": rakuten_keyword,
        "hits": 20,
        "format": "json",
    }

    if rakuten_affiliate_id:
        params["affiliateId"] = (
            rakuten_affiliate_id
        )

    rakuten_url = (
        "https://app.rakuten.co.jp/"
        "services/api/IchibaItem/"
        "Search/2026-07-01?"
        + urllib.parse.urlencode(params)
    )

    try:

        request = urllib.request.Request(
            rakuten_url,
            headers={
                "User-Agent":
                    "JapanDealsCN/0.1"
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            rakuten_data = json.load(
                response
            )

        rakuten_candidates = []

        for raw in rakuten_data.get(
            "Items",
            []
        ):

            item = raw.get(
                "Item",
                raw
            )

            item_code = str(
                item.get(
                    "itemCode",
                    ""
                )
            ).strip()

            if not item_code:
                continue

            rakuten_candidates.append(
                {
                    "id":
                        "rakuten:"
                        + item_code,

                    "title":
                        item.get(
                            "itemName",
                            ""
                        ),

                    "merchant":
                        "楽天市場",

                    "price_jpy":
                        item.get(
                            "itemPrice"
                        ),

                    "url":
                        item.get(
                            "itemUrl",
                            ""
                        ),

                    "affiliate_url":
                        item.get(
                            "affiliateUrl",
                            ""
                        ),

                    "verified":
                        False,

                    "source_type":
                        "rakuten_api",

                    "note":
                        "候选商品，不代表真实折扣",
                }
            )

        (
            DATA_DIR
            / "rakuten_candidates.json"
        ).write_text(
            json.dumps(
                {
                    "generated_at":
                        now.isoformat(
                            timespec="seconds"
                        ),

                    "keyword":
                        rakuten_keyword,

                    "count":
                        len(
                            rakuten_candidates
                        ),

                    "items":
                        rakuten_candidates,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            "Rakuten candidates:",
            len(rakuten_candidates)
        )

    except Exception as e:

        print(
            "Rakuten discovery failed:",
            type(e).__name__,
            str(e)[:300],
        )

else:

    print(
        "Rakuten discovery skipped: "
        "API credentials are not configured"
    )


# =========================================================
# 3. Yahoo! Shopping API
# Multi-keyword discovery candidates
# =========================================================

yahoo_client_id = os.getenv(
    "YAHOO_CLIENT_ID"
)


yahoo_keywords = [
    "Anker",
    "AirPods",
    "モバイルバッテリー",
    "ワイヤレスイヤホン",
    "SSD",
    "Nintendo Switch",
    "炊飯器",
    "掃除機",
    "ドライヤー",
    "電動歯ブラシ",
    "加湿器",
    "コーヒーメーカー",
]


yahoo_candidates = []


if yahoo_client_id:

    seen_yahoo_ids = set()

    for yahoo_keyword in yahoo_keywords:

        params = {
            "appid":
                yahoo_client_id,

            "query":
                yahoo_keyword,

            "results":
                20,

            "sort":
                "-score",
        }

        yahoo_url = (
            "https://shopping.yahooapis.jp/"
            "ShoppingWebService/V3/"
            "itemSearch?"
            + urllib.parse.urlencode(params)
        )

        try:

            request = urllib.request.Request(
                yahoo_url,
                headers={
                    "User-Agent":
                        "JapanDealsCN/0.1"
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=20
            ) as response:

                yahoo_data = json.load(
                    response
                )

            new_count = 0

            for item in yahoo_data.get(
                "hits",
                []
            ):

                code = str(
                    item.get(
                        "code",
                        ""
                    )
                ).strip()

                if not code:
                    continue

                yahoo_id = (
                    "yahoo:"
                    + code
                )

                if yahoo_id in seen_yahoo_ids:
                    continue

                seen_yahoo_ids.add(
                    yahoo_id
                )

                image = (
                    item.get("image")
                    or {}
                )

                review = (
                    item.get("review")
                    or {}
                )

                seller = (
                    item.get("seller")
                    or {}
                )

                yahoo_candidates.append(
                    {
                        "id":
                            yahoo_id,

                        "title":
                            item.get(
                                "name",
                                ""
                            ),

                        "merchant":
                            "Yahoo!ショッピング",

                        "shop_name":
                            seller.get(
                                "name",
                                ""
                            ),

                        "price_jpy":
                            item.get(
                                "price"
                            ),

                        "jan_code":
                            item.get(
                                "janCode",
                                ""
                            ),

                        "image_url":
                            image.get(
                                "medium",
                                ""
                            ),

                        "url":
                            item.get(
                                "url",
                                ""
                            ),

                        "review_rate":
                            review.get(
                                "rate"
                            ),

                        "review_count":
                            review.get(
                                "count"
                            ),

                        "discovered_by":
                            yahoo_keyword,

                        "verified":
                            False,

                        "source_type":
                            "yahoo_shopping_api",

                        "note":
                            "候选商品，不代表真实折扣",
                    }
                )

                new_count += 1

            print(
                "Yahoo keyword:",
                yahoo_keyword,
                "| new candidates:",
                new_count,
            )

        except Exception as e:

            print(
                "Yahoo discovery failed:",
                yahoo_keyword,
                "|",
                type(e).__name__,
                "|",
                str(e)[:300],
            )


    (
        DATA_DIR
        / "yahoo_candidates.json"
    ).write_text(
        json.dumps(
            {
                "generated_at":
                    now.isoformat(
                        timespec="seconds"
                    ),

                "keywords":
                    yahoo_keywords,

                "count":
                    len(
                        yahoo_candidates
                    ),

                "items":
                    yahoo_candidates,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


    print(
        "Yahoo total unique candidates:",
        len(yahoo_candidates)
    )

else:

    print(
        "Yahoo discovery skipped: "
        "YAHOO_CLIENT_ID is not configured"
    )


# =========================================================
# 4. Yahoo price history
# =========================================================

price_history_dir = (
    DATA_DIR
    / "price_history"
)

price_history_dir.mkdir(
    exist_ok=True
)

month_key = now.strftime(
    "%Y-%m"
)

price_history_file = (
    price_history_dir
    / f"yahoo_{month_key}.json"
)


if price_history_file.exists():

    try:

        price_history = json.loads(
            price_history_file.read_text(
                encoding="utf-8"
            )
        )

    except Exception as e:

        print(
            "Failed to read price history:",
            type(e).__name__,
            str(e)[:200]
        )

        price_history = {
            "month": month_key,
            "products": {}
        }

else:

    price_history = {
        "month": month_key,
        "products": {}
    }


products = price_history.setdefault(
    "products",
    {}
)

history_updates = 0


for item in yahoo_candidates:

    product_id = item.get(
        "id"
    )

    price = item.get(
        "price_jpy"
    )

    if not product_id:
        continue

    if (
        isinstance(price, bool)
        or not isinstance(
            price,
            (int, float)
        )
        or price <= 0
    ):
        continue


    if product_id not in products:

        products[product_id] = {

            "title":
                item.get(
                    "title",
                    ""
                ),

            "jan_code":
                item.get(
                    "jan_code",
                    ""
                ),

            "shop_name":
                item.get(
                    "shop_name",
                    ""
                ),

            "url":
                item.get(
                    "url",
                    ""
                ),

            "history": []
        }


    product = products[
        product_id
    ]

    history = product.setdefault(
        "history",
        []
    )


    product["title"] = item.get(
        "title",
        product.get(
            "title",
            ""
        )
    )

    product["jan_code"] = item.get(
        "jan_code",
        product.get(
            "jan_code",
            ""
        )
    )

    product["shop_name"] = item.get(
        "shop_name",
        product.get(
            "shop_name",
            ""
        )
    )

    product["url"] = item.get(
        "url",
        product.get(
            "url",
            ""
        )
    )


    if not history:

        history.append(
            {
                "time":
                    now.isoformat(
                        timespec="seconds"
                    ),

                "price":
                    price
            }
        )

        history_updates += 1

        continue


    last_price = history[-1].get(
        "price"
    )


    if last_price != price:

        history.append(
            {
                "time":
                    now.isoformat(
                        timespec="seconds"
                    ),

                "price":
                    price
            }
        )

        history_updates += 1


price_history[
    "updated_at"
] = now.isoformat(
    timespec="seconds"
)


price_history_file.write_text(

    json.dumps(
        price_history,
        ensure_ascii=False,
        indent=2
    ),

    encoding="utf-8"
)


print(
    "Yahoo price history file:",
    price_history_file.name
)

print(
    "Yahoo price history updates:",
    history_updates
)


# =========================================================
# 5. Detect price-drop candidates
# =========================================================

deal_candidates = []


for product_id, product in products.items():

    history = product.get(
        "history",
        []
    )

    # Need at least two actual price levels.
    if len(history) < 2:
        continue


    prices = []

    for observation in history:

        price = observation.get(
            "price"
        )

        if (
            isinstance(price, bool)
            or not isinstance(
                price,
                (int, float)
            )
            or price <= 0
        ):
            continue

        prices.append(
            price
        )


    if len(prices) < 2:
        continue


    previous_price = prices[-2]
    current_price = prices[-1]


    # Only interested in a current price drop.
    if current_price >= previous_price:
        continue


    drop_percent = (
        (
            previous_price
            - current_price
        )
        / previous_price
        * 100
    )


    # First-stage threshold:
    # price must fall at least 5%.
    if drop_percent < 5:
        continue


    historical_low = min(
        prices
    )

    historical_high = max(
        prices
    )

    median_price = statistics.median(
        prices
    )


    if median_price > 0:

        below_median_percent = (
            (
                median_price
                - current_price
            )
            / median_price
            * 100
        )

    else:

        below_median_percent = 0


    deal_candidates.append(
        {
            "id":
                product_id,

            "title":
                product.get(
                    "title",
                    ""
                ),

            "merchant":
                "Yahoo!ショッピング",

            "shop_name":
                product.get(
                    "shop_name",
                    ""
                ),

            "jan_code":
                product.get(
                    "jan_code",
                    ""
                ),

            "url":
                product.get(
                    "url",
                    ""
                ),

            "previous_price_jpy":
                previous_price,

            "current_price_jpy":
                current_price,

            "drop_percent":
                round(
                    drop_percent,
                    1
                ),

            "historical_low_jpy":
                historical_low,

            "historical_high_jpy":
                historical_high,

            "median_price_jpy":
                median_price,

            "below_median_percent":
                round(
                    below_median_percent,
                    1
                ),

            "price_points":
                len(prices),

            "detected_at":
                now.isoformat(
                    timespec="seconds"
                ),

            "verified":
                False,

            "note":
                "系统检测到降价，尚未人工验证"
        }
    )


deal_candidates.sort(
    key=lambda x:
        x["drop_percent"],
    reverse=True
)


(
    DATA_DIR
    / "deal_candidates.json"
).write_text(

    json.dumps(
        {
            "generated_at":
                now.isoformat(
                    timespec="seconds"
                ),

            "rule":
                "当前价格较上一价格下降至少5%",

            "count":
                len(
                    deal_candidates
                ),

            "items":
                deal_candidates
        },

        ensure_ascii=False,
        indent=2
    ),

    encoding="utf-8"
)


print(
    "Detected price-drop candidates:",
    len(deal_candidates)
)


# =========================================================
# 6. Publish verified deals
# =========================================================

published = []
seen_deals = set()


for deal in verified_items:

    identity = (
        deal.get("id")
        or deal.get("url")
    )

    if not identity:
        continue

    if identity in seen_deals:
        continue

    seen_deals.add(
        identity
    )

    published.append(
        deal
    )


published.sort(
    key=lambda x:
        x["verified_at"],
    reverse=True
)


(
    SITE_DIR
    / "deals.json"
).write_text(

    json.dumps(
        {
            "generated_at":
                now.isoformat(
                    timespec="seconds"
                ),

            "deals":
                published
        },

        ensure_ascii=False,
        indent=2
    ),

    encoding="utf-8"
)


print(
    "Published verified deals:",
    len(published)
)

print(
    "Build completed successfully."
)
