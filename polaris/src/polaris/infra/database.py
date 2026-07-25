from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "database" / "data" / "polaris.db"


def get_database_path() -> Path:
    return DB_PATH
