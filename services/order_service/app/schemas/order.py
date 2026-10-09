from datetime import datetime
from typing import Optional, List
from shared.schemas_base import BaseSchema
from app.models.order import OrderStatus


class OrderItemCreate(BaseSchema):
    product_id: int
    product_name: str
    quantity: int
    unit_price: float

    @property
    def subtotal(self) -> float:
        return self.quantity * self.unit_price


class OrderItemResponse(BaseSchema):
    id: int
    product_id: int
    product_name: str
    quantity: int
    unit_price: float
    subtotal: float


class OrderCreate(BaseSchema):
    customer_id: int
    shipping_address: Optional[str] = None
    notes: Optional[str] = None
    items: List[OrderItemCreate]


class OrderUpdate(BaseSchema):
    status: Optional[OrderStatus] = None
    shipping_address: Optional[str] = None
    notes: Optional[str] = None


class OrderResponse(BaseSchema):
    id: int
    customer_id: int
    status: OrderStatus
    total_amount: float
    shipping_address: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    items: List[OrderItemResponse] = []
