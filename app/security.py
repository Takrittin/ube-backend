from datetime import datetime, timedelta, timezone
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import update, or_
from app.config import JWT_SECRET_KEY
from app.database import get_db
from app.models import User, now_utc

password_hasher = PasswordHasher()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

def hash_password(password: str) -> str:
    return password_hasher.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError):
        return False

def make_token(user: User) -> str:
    return jwt.encode(
        {"sub": user.id, "exp": datetime.now(timezone.utc) + timedelta(hours=8)},
        JWT_SECRET_KEY,
        algorithm="HS256",
    )

def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    try:
        user_id = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])["sub"]
    except (jwt.PyJWTError, KeyError):
        raise unauthorized
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    now = now_utc()
    # One write at most every five minutes per active account, not every request.
    last_active = user.last_active_at
    if last_active is not None and last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=timezone.utc)
    if last_active is None or now - last_active >= timedelta(minutes=5):
        changed = db.execute(update(User).where(User.id == user.id, or_(
            User.last_active_at.is_(None), User.last_active_at < now - timedelta(minutes=5)
        )).values(last_active_at=now).execution_options(synchronize_session="fetch")).rowcount
        if changed:
            db.commit()
    return user

def require_roles(*roles: str):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user
    return checker
