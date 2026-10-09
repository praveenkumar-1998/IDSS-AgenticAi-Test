from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from shared.database import get_db
from app.crud.order import order_crud
from app.models.order import Order
from app.schemas.order import OrderCreate, OrderUpdate, OrderResponse

router = APIRouter()


@router.post(
    "/",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place a new order",
)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    return order_crud.create(db, obj_in=payload)


@router.get(
    "/",
    response_model=list[OrderResponse],
    summary="List all orders",
)
def list_orders(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return order_crud.get_all(db, skip=skip, limit=limit)


@router.get(
    "/customer/{customer_id}",
    response_model=list[OrderResponse],
    summary="List all orders for a specific customer",
)
def list_orders_by_customer(
    customer_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    return (
        db.query(Order)
        .filter(Order.customer_id == customer_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Get order by ID",
)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = order_crud.get(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order #{order_id} not found.",
        )
    return order


@router.put(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Update order",
)
def update_order(
    order_id: int,
    payload: OrderUpdate,
    db: Session = Depends(get_db),
):
    order = order_crud.get(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order #{order_id} not found.",
        )
    return order_crud.update(db, db_obj=order, obj_in=payload)


@router.patch(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Partially update an order",
)
def patch_order(
    order_id: int,
    payload: OrderUpdate,
    db: Session = Depends(get_db),
):
    order = order_crud.get(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order #{order_id} not found.",
        )
    return order_crud.update(db, db_obj=order, obj_in=payload)


@router.delete(
    "/{order_id}",
    response_model=OrderResponse,
        summary="Delete an order",
)
def delete_order(order_id: int, db: Session = Depends(get_db)):
    deleted = order_crud.remove(db, record_id=order_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order #{order_id} not found.",
        )
    return deleted
