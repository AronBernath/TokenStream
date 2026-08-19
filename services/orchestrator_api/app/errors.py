from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ServiceError(Exception):
    code: str
    message: str
    status_code: int = 500
    details: Optional[Dict[str, Any]] = None
