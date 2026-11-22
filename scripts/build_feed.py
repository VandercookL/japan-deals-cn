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
# 3.5 Yahoo JAN statistics
# =========================================================

jan_groups = {}

candidates_with_jan = 0


for item in yahoo_candidates:

    jan_code = str(
        item.get(
            "jan_code",
            ""
        )
    ).strip()

    if not jan_code:
        continue

    candidates_with_jan += 1

    if jan_code not in jan_groups:
        jan_groups[jan_code] = []

    jan_groups[jan_code].append(
        item
    )


unique_jan_products = len(
    jan_groups
)

multi_listing_jan_groups = {
    jan: items
    for jan, items in jan_groups.items()
    if len(items) >= 2
}

multi_listing_jan_count = len(
    multi_listing_jan_groups
)

cross_shop_listing_count = sum(
    len(items)
    for items in
    multi_listing_jan_groups.values()
)


print(
    "Yahoo JAN statistics:"
)

print(
    "  Total candidates:",
    len(yahoo_candidates)
)

print(
    "  Candidates with JAN:",
    candidates_with_jan
)

print(
    "  Unique JAN products:",
    unique_jan_products
)

print(
    "  JAN products with multiple listings:",
    multi_listing_jan_count
)

print(
    "  Listings in multi-listing JAN groups:",
    cross_shop_listing_count
)


# =========================================================
# 3.6 Build JAN-based product comparison
# =========================================================

product_comparisons = []

comparison_by_jan = {}


for jan_code, items in jan_groups.items():

    valid_listings = []

    for item in items:

        price = item.get(
            "price_jpy"
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

        valid_listings.append(
            item
        )


    if not valid_listings:
        continue


    valid_listings.sort(
        key=lambda x:
            x["price_jpy"]
    )


    lowest_price = (
        valid_listings[0][
            "price_jpy"
        ]
    )

    highest_price = (
        valid_listings[-1][
            "price_jpy"
        ]
    )

    price_spread = (
        highest_price
        - lowest_price
    )


    if lowest_price > 0:

        price_spread_percent = (
            price_spread
            / lowest_price
            * 100
        )

    else:

        price_spread_percent = 0


    best_listing = (
        valid_listings[0]
    )


    comparison = {

        "jan_code":
            jan_code,

        "title":
            best_listing.get(
                "title",
                ""
            ),

        "listing_count":
            len(
                valid_listings
            ),

        "lowest_price_jpy":
            lowest_price,

        "highest_price_jpy":
            highest_price,

        "price_spread_jpy":
            price_spread,

        "price_spread_percent":
            round(
                price_spread_percent,
                1
            ),

        "best_offer": {

            "id":
                best_listing.get(
                    "id",
                    ""
                ),

            "merchant":
                best_listing.get(
                    "merchant",
                    ""
                ),

            "shop_name":
                best_listing.get(
                    "shop_name",
                    ""
                ),

            "price_jpy":
                lowest_price,

            "url":
                best_listing.get(
                    "url",
                    ""
                )
        },

        "listings": []
    }


    for item in valid_listings:

        comparison[
            "listings"
        ].append(
            {
                "id":
                    item.get(
                        "id",
                        ""
                    ),

                "merchant":
                    item.get(
                        "merchant",
                        ""
                    ),

                "shop_name":
                    item.get(
                        "shop_name",
                        ""
                    ),

                "price_jpy":
                    item.get(
                        "price_jpy"
                    ),

                "url":
                    item.get(
                        "url",
                        ""
                    ),

                "review_rate":
                    item.get(
                        "review_rate"
                    ),

                "review_count":
                    item.get(
                        "review_count"
                    )
            }
        )


    product_comparisons.append(
        comparison
    )

    comparison_by_jan[
        jan_code
    ] = comparison


product_comparisons.sort(
    key=lambda x: (
        x["listing_count"] >= 2,
        x["price_spread_percent"]
    ),
    reverse=True
)


comparison_file = (
    DATA_DIR
    / "product_comparison.json"
)


comparison_file.write_text(

    json.dumps(
        {
            "generated_at":
                now.isoformat(
                    timespec="seconds"
                ),

            "matching_method":
                "JAN exact match",

            "product_count":
                len(
                    product_comparisons
                ),

            "multi_listing_product_count":
                sum(
                    1
                    for x
                    in product_comparisons
                    if x[
                        "listing_count"
                    ] >= 2
                ),

            "products":
                product_comparisons
        },

        ensure_ascii=False,
        indent=2
    ),

    encoding="utf-8"
)


print(
    "JAN product comparison:"
)

print(
    "  Products:",
    len(product_comparisons)
)

print(
    "  Multi-listing products:",
    sum(
        1
        for x in product_comparisons
        if x["listing_count"] >= 2
    )
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
            "month":
                month_key,

            "products":
                {}
        }

else:

    price_history = {
        "month":
            month_key,

        "products":
            {}
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

            "history":
                []
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


    last_price = (
        history[-1].get(
            "price"
        )
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
#
# Step 9:
# Combine historical price drop
# with JAN-based cross-shop comparison.
#
# Rules:
#
# < 5% drop
#     -> not a candidate
#
# >= 5% drop
#     -> 降价候选
#
# >= 5% drop + cross-shop lowest
#     -> 好价候选
#
# >= 10% drop
# + cross-shop lowest
# + >= 5% cheaper than second-lowest listing
#     -> 强好价候选
# =========================================================

deal_candidates = []


for product_id, product in products.items():

    history = product.get(
        "history",
        []
    )


    # Because history only stores price changes,
    # two entries mean at least two observed prices.
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


    previous_price = (
        prices[-2]
    )

    current_price = (
        prices[-1]
    )


    # Current observation must be a drop.
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


    # Minimum threshold
    if drop_percent < 5:
        continue


    historical_low = min(
        prices
    )

    historical_high = max(
        prices
    )

    # NOTE:
    # This median is only descriptive for now.
    # History stores change events rather than
    # regular daily snapshots, so this is NOT
    # treated as a reliable 30-day median.
    event_median_price = (
        statistics.median(
            prices
        )
    )


    jan_code = str(
        product.get(
            "jan_code",
            ""
        )
    ).strip()


    # -----------------------------------------------------
    # Cross-shop comparison defaults
    # -----------------------------------------------------

    cross_shop_available = False

    comparison_listing_count = 0

    is_cross_shop_lowest = False

    lowest_price_jpy = None

    second_lowest_price_jpy = None

    cheaper_than_second_jpy = None

    cheaper_than_second_percent = None


    # -----------------------------------------------------
    # If JAN exists, look up exact-match comparison
    # -----------------------------------------------------

    comparison = (
        comparison_by_jan.get(
            jan_code
        )
        if jan_code
        else None
    )


    if comparison:

        comparison_listings = (
            comparison.get(
                "listings",
                []
            )
        )

        comparison_listing_count = len(
            comparison_listings
        )


        if comparison_listing_count >= 2:

            cross_shop_available = True

            sorted_listings = sorted(
                comparison_listings,
                key=lambda x:
                    x.get(
                        "price_jpy",
                        float("inf")
                    )
            )


            valid_comparison_prices = [
                x.get("price_jpy")
                for x in sorted_listings
                if isinstance(
                    x.get("price_jpy"),
                    (int, float)
                )
                and not isinstance(
                    x.get("price_jpy"),
                    bool
                )
                and x.get(
                    "price_jpy"
                ) > 0
            ]


            if len(
                valid_comparison_prices
            ) >= 2:

                lowest_price_jpy = (
                    valid_comparison_prices[0]
                )

                second_lowest_price_jpy = (
                    valid_comparison_prices[1]
                )


                # A tie at the lowest price still counts
                # as being one of the lowest-price listings.
                is_cross_shop_lowest = (
                    current_price
                    == lowest_price_jpy
                )


                if (
                    is_cross_shop_lowest
                    and second_lowest_price_jpy
                    > current_price
                ):

                    cheaper_than_second_jpy = (
                        second_lowest_price_jpy
                        - current_price
                    )

                    cheaper_than_second_percent = (
                        cheaper_than_second_jpy
                        / second_lowest_price_jpy
                        * 100
                    )

                elif is_cross_shop_lowest:

                    cheaper_than_second_jpy = 0

                    cheaper_than_second_percent = 0


    # -----------------------------------------------------
    # Classification
    # -----------------------------------------------------

    status = "降价候选"


    if (
        cross_shop_available
        and is_cross_shop_lowest
    ):

        status = "好价候选"


    if (
        drop_percent >= 10
        and cross_shop_available
        and is_cross_shop_lowest
        and cheaper_than_second_percent
        is not None
        and cheaper_than_second_percent >= 5
    ):

        status = "强好价候选"


    # -----------------------------------------------------
    # Human-readable reason
    # -----------------------------------------------------

    reasons = [
        (
            "当前价格较上一记录价格下降"
            f"{drop_percent:.1f}%"
        )
    ]


    if not jan_code:

        reasons.append(
            "暂无JAN，无法进行同款跨店验证"
        )

    elif not cross_shop_available:

        reasons.append(
            "当前JAN暂无足够的跨店listing进行比较"
        )

    elif is_cross_shop_lowest:

        reasons.append(
            "当前价格为同JAN商品的跨店最低价"
        )

        if (
            cheaper_than_second_percent
            is not None
            and cheaper_than_second_percent > 0
        ):

            reasons.append(
                "比第二低价便宜"
                f"{cheaper_than_second_percent:.1f}%"
            )

    else:

        reasons.append(
            "当前价格不是同JAN商品的跨店最低价"
        )


    candidate = {

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
            jan_code,

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

        "event_median_price_jpy":
            event_median_price,

        "price_change_points":
            len(prices),

        "cross_shop_available":
            cross_shop_available,

        "comparison_listing_count":
            comparison_listing_count,

        "is_cross_shop_lowest":
            is_cross_shop_lowest,

        "lowest_cross_shop_price_jpy":
            lowest_price_jpy,

        "second_lowest_price_jpy":
            second_lowest_price_jpy,

        "cheaper_than_second_jpy":
            cheaper_than_second_jpy,

        "cheaper_than_second_percent":
            (
                round(
                    cheaper_than_second_percent,
                    1
                )
                if cheaper_than_second_percent
                is not None
                else None
            ),

        "status":
            status,

        "reasons":
            reasons,

        "detected_at":
            now.isoformat(
                timespec="seconds"
            ),

        "verified":
            False,

        "note":
            "系统自动筛选结果，尚未人工验证"
    }


    deal_candidates.append(
        candidate
    )


# =========================================================
# Candidate ranking
#
# Strong candidates first,
# then good candidates,
# then ordinary price-drop candidates.
# Within each class, larger drops first.
# =========================================================

status_rank = {
    "强好价候选": 3,
    "好价候选": 2,
    "降价候选": 1,
}


deal_candidates.sort(
    key=lambda x: (
        status_rank.get(
            x["status"],
            0
        ),
        x["drop_percent"]
    ),
    reverse=True
)


strong_candidate_count = sum(
    1
    for x in deal_candidates
    if x["status"]
    == "强好价候选"
)

good_candidate_count = sum(
    1
    for x in deal_candidates
    if x["status"]
    == "好价候选"
)

price_drop_candidate_count = sum(
    1
    for x in deal_candidates
    if x["status"]
    == "降价候选"
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

            "version":
                2,

            "rules": {
                "minimum_drop_percent":
                    5,

                "good_deal":
                    (
                        "降价>=5%且当前价格为"
                        "同JAN跨店最低价"
                    ),

                "strong_deal":
                    (
                        "降价>=10%，且为同JAN跨店最低价，"
                        "且比第二低价便宜>=5%"
                    )
            },

            "count":
                len(
                    deal_candidates
                ),

            "strong_candidate_count":
                strong_candidate_count,

            "good_candidate_count":
                good_candidate_count,

            "price_drop_candidate_count":
                price_drop_candidate_count,

            "items":
                deal_candidates
        },

        ensure_ascii=False,
        indent=2
    ),

    encoding="utf-8"
)


print(
    "Deal candidate analysis:"
)

print(
    "  Total:",
    len(deal_candidates)
)

print(
    "  Strong:",
    strong_candidate_count
)

print(
    "  Good:",
    good_candidate_count
)

print(
    "  Price-drop only:",
    price_drop_candidate_count
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
