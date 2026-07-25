from dataclasses import dataclass


@dataclass
class AthenaQuery:
    prompt: str
    response: str = ""
