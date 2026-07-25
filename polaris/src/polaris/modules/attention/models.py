from dataclasses import dataclass


@dataclass
class AttentionTask:
    title: str
    completed: bool = False
