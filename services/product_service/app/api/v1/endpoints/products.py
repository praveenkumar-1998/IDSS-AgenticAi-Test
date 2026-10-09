from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from shared.database import get_db
from app.crud.product import product_crud
from app.schemas.product import ProductCreate, ProductUpdate, ProductResponse

router = APIRouter()


@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new product",
)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    return product_crud.create(db, obj_in=payload)


@router.get(
    "/",
    response_model=list[ProductResponse],
    summary="List all products",
)
def list_products(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return product_crud.get_all(db, skip=skip, limit=limit)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Get a product by ID",
)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = product_crud.get(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product #{product_id} not found.",
        )
    return product


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Fully replace a product record",
)
def update_product(
    product_id: int,
    payload: ProductCreate,
    db: Session = Depends(get_db),
):
    product = product_crud.get(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product #{product_id} not found.",
        )
    return product_crud.update(db, db_obj=product, obj_in=payload)


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Partially update a product record",
)
def patch_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
):
    product = product_crud.get(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product #{product_id} not found.",
        )
    return product_crud.update(db, db_obj=product, obj_in=payload)


@router.delete(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Delete a product",
)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    deleted = product_crud.remove(db, record_id=product_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product #{product_id} not found.",
        )
    return deleted
