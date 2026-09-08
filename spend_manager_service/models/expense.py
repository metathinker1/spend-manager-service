from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

class Expense(BaseModel):
    """Model for expense records"""
    id: Optional[str] = Field(default=None)
    amount: float = Field(..., gt=0)
    currency: str = Field(default="USD")
    category: str = Field(..., min_length=1)
    description: Optional[str] = Field(default=None)
    date: datetime = Field(...)
    payee: str = Field(..., min_length=1)
    tags: Optional[list] = Field(default=[])

    class Config:
        # Allow extra fields for future extensibility
        extra = "allow"