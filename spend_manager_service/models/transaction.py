from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

class Transaction(BaseModel):
    """Base model for transaction records"""
    id: Optional[str] = Field(default=None)
    amount: float = Field(..., gt=0)
    currency: str = Field(default="USD")
    description: Optional[str] = Field(default=None)
    date: datetime = Field(...)
    tags: Optional[list] = Field(default=[])

    class Config:
        # Allow extra fields for future extensibility
        extra = "allow"