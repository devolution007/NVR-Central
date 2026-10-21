from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User, UserCampusPermission
from app.schemas.user import UserDetailOut, UserOut


def user_detail(db: Session, user: User) -> UserDetailOut:
    ids = list(db.scalars(select(UserCampusPermission.campus_id).where(UserCampusPermission.user_id == user.id)))
    return UserDetailOut(**UserOut.model_validate(user).model_dump(), campus_ids=ids)
