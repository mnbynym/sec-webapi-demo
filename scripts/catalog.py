"""シードデータの定義（決定的に生成し、make reset で同じ状態に戻せるようにする）"""

import random
from datetime import UTC, datetime, timedelta
from typing import Any

BASE_TIME = datetime(2026, 9, 1, tzinfo=UTC)
CATEGORIES = ["books", "electronics", "food", "toys", "apparel", "sports", "home", "beauty"]
NOUNS = {
    "books": ["Book", "Guide", "Handbook", "Novel"],
    "electronics": ["Headphones", "Keyboard", "Monitor", "Charger"],
    "food": ["Coffee", "Tea", "Chocolate", "Rice"],
    "toys": ["Puzzle", "Robot", "Blocks", "Kite"],
    "apparel": ["Jacket", "Shirt", "Cap", "Socks"],
    "sports": ["Ball", "Racket", "Shoes", "Bottle"],
    "home": ["Lamp", "Mug", "Towel", "Clock"],
    "beauty": ["Soap", "Lotion", "Brush", "Mask"],
}
ADJECTIVES = ["Classic", "Smart", "Compact", "Premium", "Eco", "Daily", "Pro", "Mini"]

# (email, display_name, role) — alice に大量の注文、bob に少数の注文、carol は管理者
DEMO_USERS = [
    ("alice@example.com", "Alice", "member"),
    ("bob@example.com", "Bob", "member"),
    ("carol@example.com", "Carol", "admin"),
]

# bob の注文（note で識別し、make reset で状態を戻す）
BOB_ORDERS = [
    ("bob-1", "pending", [("SKU-000001", 1)]),
    ("bob-2", "paid", [("SKU-000002", 2)]),
    ("bob-3", "shipped", [("SKU-000003", 1)]),
]


def sku(i: int) -> str:
    return f"SKU-{i:06d}"


def product(i: int) -> dict[str, Any]:
    rnd = random.Random(i)
    category = CATEGORIES[i % len(CATEGORIES)]
    name = f"{rnd.choice(ADJECTIVES)} {rnd.choice(NOUNS[category])} {i}"
    return {
        "sku": sku(i),
        "name": name,
        "category": category,
        "price": rnd.randrange(300, 20_000, 10),
        "tags": rnd.sample(["new", "sale", "popular", "limited", "gift"], k=rnd.randint(0, 2)),
        "description": f"{name} の説明文",
        "created_at": BASE_TIME - timedelta(minutes=i * 5),
        "version": 1,
        "source": "seed",
    }


def price_of(i: int) -> int:
    return product(i)["price"]
