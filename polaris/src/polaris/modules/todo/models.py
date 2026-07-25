from dataclasses import dataclass


@dataclass
class TodoItem:
    title: str
    completed: bool = False
