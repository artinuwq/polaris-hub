from dataclasses import dataclass


@dataclass
class Subscription:
    name: str
    active: bool = True
