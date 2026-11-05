from typing import Literal, Annotated
from datetime import date
from pydantic import BaseModel, Field, field_validator, ConfigDict

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["inspector", "qa", "admin"]
    full_name: str = Field(default="", max_length=120)

class UserUpdate(BaseModel):
    role: Literal["inspector", "qa", "admin"] | None = None
    is_active: bool | None = None

class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    bag_type: Literal["paper", "jumbo", "unknown"] = "unknown"

class LotCreate(BaseModel):
    lot_code: str = Field(min_length=1, max_length=100)
    product_id: str
    production_date: date | None = None
    quantity: int = Field(default=0, ge=0)

class InspectionCreate(BaseModel):
    lot_code: str | None = Field(default=None, min_length=1, max_length=100)
    pallet_code: str = Field(default="", max_length=100)
    material_code: str = Field(default="", max_length=100)
    qr_raw: str | None = Field(default=None, max_length=3000)

class ConfirmRequest(BaseModel):
    decision: Literal["PASS", "FAIL"]
    notes: str | None = Field(default=None, max_length=3000)
    override_reason: str | None = Field(default=None, max_length=3000)
    defects: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=10)

    @field_validator("defects")
    @classmethod
    def normalize_defects(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("Defect names must not be blank")
        return list(dict.fromkeys(value.strip() for value in values))


class BagOutline(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    points: list[tuple[float, float]] = Field(min_length=4, max_length=4)
    layer: int = Field(ge=1, le=50)


class GeometryRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    image_width: int = Field(gt=0, le=25000)
    image_height: int = Field(gt=0, le=25000)
    baseline: list[tuple[float, float]] = Field(min_length=2, max_length=2)
    bags: list[BagOutline] = Field(min_length=1, max_length=40)
    verified: Literal[True]
