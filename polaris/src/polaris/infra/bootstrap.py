from .settings import Settings


def bootstrap() -> Settings:
    return Settings.from_env()
