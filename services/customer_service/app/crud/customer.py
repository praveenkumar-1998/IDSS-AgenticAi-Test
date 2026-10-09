from shared.crud_base import CRUDBase
from app.models.customer import Customer
from app.schemas.customer import CustomerCreate, CustomerUpdate

customer_crud = CRUDBase[Customer, CustomerCreate, CustomerUpdate](Customer)
