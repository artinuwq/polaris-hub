from dataclasses import dataclass


@dataclass
class ApiResponse:
    success: bool
    message: str
    data: dict | None = None
