import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "")
engine = create_engine(DATABASE_URL, pool_size=20, max_overflow=0) if DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine) if engine else None

def get_db():
    with SessionLocal() as session:
        yield session