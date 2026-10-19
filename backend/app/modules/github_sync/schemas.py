"""github_sync 的 Pydantic 契约。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PullOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    title: str
    author: str | None = None
    head_ref: str
    base_ref: str
    state: str
    merged: bool
    checks_green: bool | None = None
    ci_failed_count: int
    verdict: str
    linked_issues: list[int]
    url: str | None = None
    updated_at: datetime
    merged_at: datetime | None = None


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    title: str
    state: str
    labels: list[str]
    url: str | None = None
    author: str | None = None
    updated_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    delivery_id: str
    event: str
    action: str | None = None
    status: str
    attempts: int
    error: str | None = None
    received_at: datetime
    processed_at: datetime | None = None


class WebhookAck(BaseModel):
    status: str  # accepted / duplicate / ignored
    delivery_id: str
    event: str
    event_id: str | None = None


class ReplayOut(BaseModel):
    event_id: str
    status: str
    error: str | None = None


class SyncResult(BaseModel):
    fetched: int = Field(ge=0)
    upserted: int = Field(ge=0)
    skipped: int = Field(ge=0)
    synced_at: datetime
