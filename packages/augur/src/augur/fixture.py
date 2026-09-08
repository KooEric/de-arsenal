"""결정적 합성 데이터 — eval 케이스의 골든 SQL이 항상 같은 결과를 내도록 seed 고정.

생성: customers / products / orders 세 테이블 Parquet. 스크립트 진입점은
`packages/augur/eval/make_fixture.py`(이 모듈을 호출), 테스트 fixture도 같은 함수를 쓴다.
"""

import random
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

SEED = 20260908
N_CUSTOMERS = 40
N_ORDERS = 400
CITIES = ["Seoul", "Busan", "Incheon", "Daegu", "Daejeon"]
SEGMENTS = ["consumer", "smb", "enterprise"]
STATUSES = ["paid", "pending", "refunded", "cancelled"]
PRODUCTS: list[tuple[int, str, str, float]] = [
    (1, "Keyboard", "hardware", 49.0),
    (2, "Monitor", "hardware", 199.0),
    (3, "Mouse", "hardware", 19.0),
    (4, "IDE License", "software", 89.0),
    (5, "Cloud Credits", "software", 120.0),
    (6, "Headset", "hardware", 59.0),
    (7, "VPN Subscription", "software", 9.0),
]
TABLE_NAMES = ("customers", "products", "orders")

Row = dict[str, object]


def make(out: Path) -> None:
    rng = random.Random(SEED)
    customers: list[Row] = [
        {
            "customer_id": i,
            "name": f"cust_{i:03d}",
            "city": rng.choice(CITIES),
            "segment": rng.choice(SEGMENTS),
            "signup_date": date(2025, 1, 1) + timedelta(days=rng.randint(0, 400)),
        }
        for i in range(1, N_CUSTOMERS + 1)
    ]
    products: list[Row] = [
        {"product_id": p[0], "product_name": p[1], "category": p[2], "price": p[3]}
        for p in PRODUCTS
    ]
    orders: list[Row] = []
    for i in range(1, N_ORDERS + 1):
        p = rng.choice(PRODUCTS)
        qty = rng.randint(1, 5)
        orders.append(
            {
                "order_id": i,
                "customer_id": rng.randint(1, N_CUSTOMERS),
                "product_id": p[0],
                "quantity": qty,
                "amount": round(qty * p[3], 2),
                "status": rng.choice(STATUSES),
                "ordered_at": date(2026, 1, 1) + timedelta(days=rng.randint(0, 240)),
            }
        )
    for name, rows in (("customers", customers), ("products", products), ("orders", orders)):
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(  # pyright: ignore[reportUnknownMemberType]
            pa.Table.from_pylist(rows), d / "part-0.parquet"
        )
