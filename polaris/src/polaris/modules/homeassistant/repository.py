class HomeAssistantRepository:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def list(self) -> list[dict]:
        return list(self._items)
