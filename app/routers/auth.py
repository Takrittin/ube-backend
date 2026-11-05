from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserUpdate
from app.security import current_user, hash_password, make_token, require_roles, verify_password

router = APIRouter(prefix="/api/v1", tags=["authentication and users"])

def user_data(user: User):
    return {"id": user.id, "username": user.username, "full_name": user.full_name or user.username,
            "role": user.role, "is_active": user.is_active}

@router.post("/auth/token")
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    return {"access_token": make_token(user), "token_type": "bearer"}

@router.get("/users/me")
def me(user: User = Depends(current_user)):
    return user_data(user)

@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    return [user_data(item) for item in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.username))]

@router.post("/users", status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(409, "Username already exists")
    obj = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role,
               full_name=payload.full_name)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return user_data(obj)

@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    if target.id == user.id and (payload.is_active is False or payload.role not in {None, "admin"}):
        raise HTTPException(409, "You cannot deactivate or demote your own admin account")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(target, key, value)
    db.commit()
    return user_data(target)

@router.delete("/users/{user_id}", status_code=204)
def deactivate_user(user_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    # Retain the account and its inspection attribution for traceability.
    update_user(user_id, UserUpdate(is_active=False), db, user)
