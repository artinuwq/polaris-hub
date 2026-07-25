from dataclasses import dataclass


@dataclass
class Scheduler:
    interval_seconds: int = 60

    def run(self) -> None:
        print("Scheduler placeholder")
