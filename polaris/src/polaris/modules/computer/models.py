from dataclasses import dataclass


@dataclass
class ComputerDevice:
    name: str
    online: bool = False
