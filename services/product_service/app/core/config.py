import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServiceSettings:
    service_name: str = field(default_factory=lambda: os.getenv("SERVICE_NAME", "product-service"))
    api_version: str = field(default_factory=lambda: os.getenv("API_VERSION", "v1"))
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8002")))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    db_name: str = field(default_factory=lambda: os.getenv("DB_NAME", "product_service"))
    title: str = "Product Service"
    description: str = "Microservice responsible for managing the product catalogue."
    contact: dict = field(default_factory=lambda: {"name": "Platform Team", "email": "platform@example.com"})


settings = ServiceSettings()
