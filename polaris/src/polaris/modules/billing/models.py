from dataclasses import dataclass


@dataclass
class Invoice:
    amount: float
    paid: bool = False
