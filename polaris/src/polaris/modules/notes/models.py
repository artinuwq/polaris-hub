from dataclasses import dataclass


@dataclass
class Note:
    title: str
    content: str = ""
