"""Base service with reusable authorization helpers.

Concrete services inherit from ``BaseService`` and get ownership checks,
pagination helpers, and dependency injection patterns for free.
"""

import uuid
from typing import Generic, TypeVar, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ForbiddenError

ModelT = TypeVar("ModelT")


class Ownable:
    """Protocol mixin: any model with a ``user_id`` attribute."""

    user_id: uuid.UUID


class BaseService:
    """Base class for domain services.

    Provides:
    - ``assert_ownership`` – raises ``ForbiddenError`` when the current user
      does not own the resource.
    - ``assert_access`` – generic guard for resources that have a ``user_id``.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def assert_ownership(
        resource: Ownable, user_id: uuid.UUID, label: str = "resource"
    ) -> None:
        if resource.user_id != user_id:
            raise ForbiddenError(
                f"You do not have access to this {label}"
            )

    @staticmethod
    def assert_access(
        resource_owner_id: uuid.UUID,
        user_id: uuid.UUID,
        label: str = "resource",
    ) -> None:
        if resource_owner_id != user_id:
            raise ForbiddenError(
                f"You do not have access to this {label}"
            )
