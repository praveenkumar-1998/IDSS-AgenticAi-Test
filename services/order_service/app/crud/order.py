from sqlalchemy.orm import Session
from shared.crud_base import CRUDBase
from app.models.order import Order, OrderItem
from app.schemas.order import OrderCreate, OrderUpdate


class OrderCRUD(CRUDBase[Order, OrderCreate, OrderUpdate]):
    def create(self, db: Session, *, obj_in: OrderCreate) -> Order:
        items_data = obj_in.items
        total = sum(item.quantity * item.unit_price for item in items_data)

        db_order = Order(
            customer_id=obj_in.customer_id,
            shipping_address=obj_in.shipping_address,
            notes=obj_in.notes,
            total_amount=total,
        )
        db.add(db_order)
        db.flush()

        for item in items_data:
            db_item = OrderItem(
                order_id=db_order.id,
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                subtotal=item.quantity * item.unit_price,
            )
            db.add(db_item)

        db.commit()
        db.refresh(db_order)
        return db_order


order_crud = OrderCRUD(Order)
