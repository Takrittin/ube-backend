from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.schemas import UserCreate
from app.security import current_user, hash_password, make_token, require_roles, verify_password

router = APIRouter(prefix="/api/v1", tags=["authentication and users"])

@router.post("/auth/token")
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    return {"access_token": make_token(user), "token_type": "bearer"}

@router.get("/users/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}

@router.post("/users", status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(409, "Username already exists")
    obj = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return {"id": obj.id, "username": obj.username, "role": obj.role}
