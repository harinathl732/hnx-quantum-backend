import os
from sqlalchemy import create_all, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

# Ensure env variables are loaded
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hnx_user:hnx_secure_pass@db:5432/hnx_quantum_db")

# Create engine with pool settings optimized for production
# pool_size: number of persistent connections to keep open
# max_overflow: number of temporary connections we can open beyond pool_size
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # Test connection health before using it to prevent stale socket errors
    pool_recycle=1800    # Close and recreate idle connections every 30 minutes
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """
    FastAPI dependency that provides a thread-safe database session.
    Automatically closes the session once the API request lifecycle ends.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
