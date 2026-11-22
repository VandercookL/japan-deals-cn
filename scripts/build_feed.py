import json
import os
import datetime
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
    print("manual_deals.json not found")
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

    if (
        x["deal_type"] == "商品降价"
        and price is None
    ):
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


verified_items = []

for deal in manual:

    if valid_deal(deal):
        verified_items.append(deal)


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


if yahoo_client_id:

    yahoo_candidates = []

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

                # Same Yahoo listing may appear
                # under several keywords.
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

            # One failed keyword should not
            # stop all other keywords.
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
# 4. Publish verified deals
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
    reverse=True,
)


output = {
    "generated_at":
        now.isoformat(
            timespec="seconds"
        ),

    "deals":
        published,
}


(
    SITE_DIR
    / "deals.json"
).write_text(
    json.dumps(
        output,
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)


print(
    "Published verified deals:",
    len(published)
)

print(
    "Build completed successfully."
)
