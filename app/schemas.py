from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class OpportunityOut(BaseModel):
    id: int
    title: str
    link: str
    description: Optional[str] = None
    deadline: Optional[str] = None
    thumbnail: Optional[str] = None
    tags: list[str] = []
    created_at: Optional[datetime] = None
    posted_to_telegram: Optional[bool] = None

class OpportunityCreate(BaseModel):
    title: str
    link: str
    description: Optional[str] = None
    deadline: Optional[str] = None
    thumbnail: Optional[str] = None
    tags: list[str] = []
    created_at: Optional[str] = None

class OpportunityUpdate(BaseModel):
    title: Optional[str] = None
    link: Optional[str] = None
    description: Optional[str] = None
    deadline: Optional[str] = None
    thumbnail: Optional[str] = None
    tags: Optional[list[str]] = None
    posted_to_telegram: Optional[bool] = None

class SearchResultOut(BaseModel):
    results: list[OpportunityOut]
    total: int
    offset: int
    limit: int

class StatsOut(BaseModel):
    total: int
    unposted: int
    posted: int
    today: int
    week: int
    month: int
    last_posted: str
    oldest: str
    top_tags: list[tuple[str, int]]

class AdminOut(BaseModel):
    user_id: int
    name: str
    added_by: Optional[int] = None
    created_at: Optional[datetime] = None

class AdminCreate(BaseModel):
    user_id: int
    name: str = ""

class PingOut(BaseModel):
    status: str

class RootOut(BaseModel):
    message: str

class RunOnceOut(BaseModel):
    status: str

class WebhookOut(BaseModel):
    ok: bool
