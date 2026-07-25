from dataclasses import dataclass


@dataclass
class Server:
    name: str
    host: str
    online: bool = False
