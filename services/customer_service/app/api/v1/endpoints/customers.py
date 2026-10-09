from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from shared.database import get_db
from app.crud.customer import customer_crud
from app.schemas.customer import CustomerCreate, CustomerUpdate, CustomerResponse

router = APIRouter()


@router.post(
    "/",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new customer",
)
def create_customer(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
) -> CustomerResponse:
    return customer_crud.create(db, obj_in=payload)


@router.get(
    "/",
    response_model=list[CustomerResponse],
    summary="List customers",
)
def list_customers(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[CustomerResponse]:
    return customer_crud.get_all(db, skip=skip, limit=limit)


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="Get customer by ID",
)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
) -> CustomerResponse:
    customer = customer_crud.get(db, customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer #{customer_id} not found.",
        )
    return customer


@router.put(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="Fully replace a customer record",
)
def update_customer(
    customer_id: int,
    payload: CustomerCreate,
    db: Session = Depends(get_db),
) -> CustomerResponse:
    customer = customer_crud.get(db, customer_id)
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer #{customer_id} not found.")
    return customer_crud.update(db, db_obj=customer, obj_in=payload)


@router.patch(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="Partially update a customer record",
)
def patch_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
) -> CustomerResponse:
    customer = customer_crud.get(db, customer_id)
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer #{customer_id} not found.")
    return customer_crud.update(db, db_obj=customer, obj_in=payload)


@router.delete(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="Delete a customer",
)
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
) -> CustomerResponse:
    deleted = customer_crud.remove(db, record_id=customer_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer #{customer_id} not found.")
    return deleted
