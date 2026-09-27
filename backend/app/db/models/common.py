from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class H3Index(UserDefinedType[str]):
    """The h3-pg `h3index` column type; bound and returned as its hex string."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "h3index"
