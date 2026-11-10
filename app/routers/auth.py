from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from typing import Literal
from datetime import timezone
from fastapi import Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, now_utc
from app.schemas import UserCreate, UserUpdate
from app.security import current_user, hash_password, make_token, require_roles, verify_password

router = APIRouter(prefix="/api/v1", tags=["authentication and users"])

def user_data(user: User):
    def timestamp(value):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(timezone.utc) if value else None
    return {"id": user.id, "username": user.username, "full_name": user.full_name or user.username,
            "role": user.role, "is_active": user.is_active, "email": user.email,
            "created_at": timestamp(user.created_at), "last_active_at": timestamp(user.last_active_at)}

@router.post("/auth/token")
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    user.last_active_at = now_utc()
    db.commit()
    return {"access_token": make_token(user), "token_type": "bearer"}

@router.get("/users/me")
def me(user: User = Depends(current_user)):
    return user_data(user)

@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    return [user_data(item) for item in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.username))]

@router.get("/users/page")
def users_page(search: str = "", role: Literal["admin", "qa", "inspector"] | None = None,
               is_active: bool | None = None, offset: int = Query(0, ge=0),
               limit: int = Query(20, ge=1, le=100),
               sort: Literal["name", "created_at", "last_active_at"] = "last_active_at",
               direction: Literal["asc", "desc"] = "desc",
               db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    conditions = []
    if search.strip():
        conditions.append(or_(*(column.icontains(search.strip(), autoescape=True)
                               for column in (User.username, User.full_name, User.email))))
    if role:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active == is_active)
    total = db.scalar(select(func.count()).select_from(User).where(*conditions))
    column = User.full_name if sort == "name" else getattr(User, sort)
    order = (column.asc() if direction == "asc" else column.desc()).nulls_last()
    rows = db.scalars(select(User).where(*conditions).order_by(order, User.id).offset(offset).limit(limit))
    return {"items": [user_data(item) for item in rows], "total": total, "offset": offset, "limit": limit}


def commit_user(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username or email already exists") from None


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(409, "Username already exists")
    obj = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role,
               full_name=payload.full_name.strip(), email=payload.email)
    db.add(obj)
    commit_user(db)
    db.refresh(obj)
    return user_data(obj)

@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    if target.id == user.id and (payload.is_active is False or payload.role not in {None, "admin"}):
        raise HTTPException(409, "You cannot deactivate or demote your own admin account")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key in {"role", "is_active", "full_name"} and value is None:
            raise HTTPException(422, f"{key} must not be null")
        if key == "full_name":
            value = value.strip()
            if not value:
                raise HTTPException(422, "Name must not be blank")
        setattr(target, key, value)
    commit_user(db)
    return user_data(target)

@router.delete("/users/{user_id}", status_code=204)
def deactivate_user(user_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    # Retain the account and its inspection attribution for traceability.
    update_user(user_id, UserUpdate(is_active=False), db, user)
