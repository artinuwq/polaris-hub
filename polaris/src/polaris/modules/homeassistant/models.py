from dataclasses import dataclass


@dataclass
class HomeEntity:
    entity_id: str
    state: str = "unknown"
