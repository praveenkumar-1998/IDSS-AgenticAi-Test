from datetime import datetime
from typing import Optional
from shared.schemas_base import BaseSchema


class CustomerCreate(BaseSchema):
    name: str
    email: str
    phone: Optional[str] = None
    address: Optional[str] = None
    is_active: bool = True


class CustomerUpdate(BaseSchema):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None


class CustomerResponse(BaseSchema):
    id: int
    name: str
    email: str
    phone: Optional[str] = None
    address: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
