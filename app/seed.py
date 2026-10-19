"""Create the initial admin account. Explicit opt-in; no built-in admin password."""
import os
from sqlalchemy import select
from app.config import JWT_SECRET_KEY  # ensure env configured
from app.database import Base, engine, SessionLocal
from app.models import User
from app.security import hash_password


def main():
    username = os.getenv("SEED_ADMIN_USERNAME", "admin")
    password = os.getenv("SEED_ADMIN_PASSWORD", "")
    if len(password) < 10 or "REPLACE_WITH" in password:
        raise SystemExit("Set SEED_ADMIN_PASSWORD (at least 10 characters) in .env first")
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == username)):
            print(f"User {username!r} already exists; no changes made")
            return
        db.add(User(username=username, password_hash=hash_password(password), role="admin"))
        db.commit()
    print(f"Created admin username {username!r}; password is not printed")

if __name__ == "__main__":
    main()
