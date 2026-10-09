import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServiceSettings:
    service_name: str = field(default_factory=lambda: os.getenv("SERVICE_NAME", "customer-service"))
    api_version: str = field(default_factory=lambda: os.getenv("API_VERSION", "v1"))
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8001")))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    db_name: str = field(default_factory=lambda: os.getenv("DB_NAME", "customer_service"))
    title: str = "Customer Service"
    description: str = "Microservice responsible for managing customer accounts."
    contact: dict = field(default_factory=lambda: {
        "name": "Platform Team",
        "email": "platform@example.com",
    })


settings = ServiceSettings()
