import logging
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import verify_password, hash_password, create_access_token, decode_access_token
from app.models.trading import User
from app.schemas import UserCreate, UserOut, Token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

router = APIRouter(prefix="/auth", tags=["User Authentication"])

# OAuth2PasswordBearer defines where FastAPI looks for the token (the '/api/auth/token' endpoint)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    """
    Dependency that decodes, validates, and resolves the current authenticated user from a JWT token.
    Throws a 401 Unauthorized exception if token is invalid or expired.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials, session expired.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    payload = decode_access_token(token)
    if not payload:
        raise credentials_exception
        
    email: str = payload.get("sub")
    if email is None:
        raise credentials_exception
        
    # Query user from DB
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
        
    return user


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register_user(user_in: UserCreate, db: Session = Depends(get_db)):
    """
    Registers a new single-user profile inside the platform.
    We restrict registration if a user already exists (ensuring HNX Quantum remains a single-user private node).
    """
    # Check if any user already exists
    existing_user_count = db.query(User).count()
    if existing_user_count >= 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled. HNX Quantum is a secure single-user private node."
        )

    # Check email duplicate
    email_check = db.query(User).filter(User.email == user_in.email).first()
    if email_check:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address already exists."
        )

    # Hash the password and save
    hashed = hash_password(user_in.password)
    db_user = User(
        email=user_in.email,
        hashed_password=hashed,
        is_active=True,
        is_superuser=True  # First user is superuser
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    logging.info(f"[AUTH] New superuser successfully registered: {user_in.email}")
    return db_user


@router.post("/token", response_model=Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db: Session = Depends(get_db)
):
    """
    Authenticates user credentials and issues a cryptographically signed JWT access token.
    Uses standard OAuth2 password request form (username represents email).
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User account is currently inactive."
        )

    # Issue access token (expires in 24 hours)
    access_token = create_access_token(data={"sub": user.email})
    logging.info(f"[AUTH SUCCESS] Issued new JWT token for user: {user.email}")
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


@router.get("/me", response_model=UserOut)
def read_users_me(current_user: User = Depends(get_current_user)):
    """
    Returns authenticated user profile details.
    """
    return current_user
