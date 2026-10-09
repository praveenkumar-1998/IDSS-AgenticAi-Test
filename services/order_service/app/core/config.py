import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServiceSettings:
    service_name: str = field(default_factory=lambda: os.getenv("SERVICE_NAME", "order-service"))
    api_version: str = field(default_factory=lambda: os.getenv("API_VERSION", "v1"))
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8003")))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    db_name: str = field(default_factory=lambda: os.getenv("DB_NAME", "order_service"))
    title: str = "Order Service"
    description: str = "Microservice responsible for managing customer orders and line items."
    contact: dict = field(default_factory=lambda: {"name": "Platform Team", "email": "platform@example.com"})


settings = ServiceSettings()
