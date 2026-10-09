from shared.crud_base import CRUDBase
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate

product_crud = CRUDBase[Product, ProductCreate, ProductUpdate](Product)
