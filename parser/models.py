from dataclasses import dataclass


@dataclass
class ProductInfo:
    article: str
    name: str
    current_price: int    # kopecks
    original_price: int   # kopecks
    discount_pct: int
    url: str
    available: bool
