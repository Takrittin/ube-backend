from typing import Literal
from pydantic import BaseModel, Field

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["inspector", "qa", "admin"]

class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)

class LotCreate(BaseModel):
    lot_code: str = Field(min_length=1, max_length=100)
    product_id: str

class InspectionCreate(BaseModel):
    lot_code: str

class ConfirmRequest(BaseModel):
    decision: Literal["PASS", "FAIL"]
    notes: str | None = Field(default=None, max_length=3000)
    override_reason: str | None = Field(default=None, max_length=3000)
