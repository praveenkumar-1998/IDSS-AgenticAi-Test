from datetime import datetime
from typing import Optional
from shared.schemas_base import BaseSchema


class ProductCreate(BaseSchema):
    name: str
    description: Optional[str] = None
    sku: str
    price: float
    stock_quantity: int = 0
    category: Optional[str] = None
    is_available: bool = True


class ProductUpdate(BaseSchema):
    name: Optional[str] = None
    description: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None
    stock_quantity: Optional[int] = None
    category: Optional[str] = None
    is_available: Optional[bool] = None


class ProductResponse(BaseSchema):
    id: int
    name: str
    description: Optional[str] = None
    sku: str
    price: float
    stock_quantity: int
    category: Optional[str] = None
    is_available: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
