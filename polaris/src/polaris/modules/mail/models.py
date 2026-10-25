"""Pydantic-модели запросов модуля Mail."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    provider: str = Field(max_length=20)
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=80)


class AccountUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class DestinationCreate(BaseModel):
    target: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=80)


class DestinationUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
