from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

class Income(BaseModel):
    """Model for income records"""
    id: Optional[str] = Field(default=None)
    amount: float = Field(..., gt=0)
    currency: str = Field(default="USD")
    source: str = Field(..., min_length=1)
    description: Optional[str] = Field(default=None)
    date: datetime = Field(...)
    tags: Optional[list] = Field(default=[])

    class Config:
        # Allow extra fields for future extensibility
        extra = "allow"