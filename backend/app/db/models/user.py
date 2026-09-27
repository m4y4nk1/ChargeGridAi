from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "app_user"

    # OIDC users are provisioned on first sign-in, keyed by the token's subject.
    oidc_subject: Mapped[str | None] = mapped_column(Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
