import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field

# ==========================================
# AUTHENTICATION SCHEMAS
# ==========================================
class UserBase(BaseModel):
    email: EmailStr

class UserCreate(UserBase):
    password: str = Field(..., min_length=6, description="Password must be at least 6 characters.")

class UserOut(UserBase):
    id: int
    is_active: bool
    is_superuser: bool
    created_at: datetime.datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None


# ==========================================
# STRATEGY SCHEMAS
# ==========================================
class StrategyBase(BaseModel):
    name: str = Field(..., example="Nifty Time Formula 35pts")
    
    # Contract specs
    underlying: str = Field("NIFTY", description="NIFTY, BANKNIFTY, FINNIFTY")
    option_type: str = Field("BOTH", description="CE, PE, BOTH")
    expiry_type: str = Field("Current Week", description="Current Week, Next Week, Monthly")
    order_type: str = Field("MIS", description="MIS or NRML")
    timeframe: str = Field("1min", description="1min, 3min, 5min")
    
    # Entry criteria
    entry_start_time: str = "09:17:00"
    entry_end_time: str = "15:00:00"
    reference_candle_time: str = "09:16:00"
    
    # Exit criteria
    target_points: float = 35.0
    sl_type1_enabled: bool = True
    sl_type2_points: float = 20.0
    universal_exit_time: str = "15:15:00"
    
    # Risk parameters
    base_qty_lots: int = 1
    martingale_enabled: bool = True
    max_martingale_level: int = 5
    separate_ce_pe_martingale: bool = True

class StrategyCreate(StrategyBase):
    pass

class StrategyEdit(BaseModel):
    name: Optional[str] = None
    underlying: Optional[str] = None
    option_type: Optional[str] = None
    expiry_type: Optional[str] = None
    order_type: Optional[str] = None
    timeframe: Optional[str] = None
    entry_start_time: Optional[str] = None
    entry_end_time: Optional[str] = None
    reference_candle_time: Optional[str] = None
    target_points: Optional[float] = None
    sl_type1_enabled: Optional[bool] = None
    sl_type2_points: Optional[float] = None
    universal_exit_time: Optional[str] = None
    base_qty_lots: Optional[int] = None
    martingale_enabled: Optional[bool] = None
    max_martingale_level: Optional[int] = None
    separate_ce_pe_martingale: Optional[bool] = None

class StrategyOut(StrategyBase):
    id: int
    is_active: bool
    ce_reference_close: Optional[float] = None
    ce_current_qty_lots: int
    ce_state: str
    pe_reference_close: Optional[float] = None
    pe_current_qty_lots: int
    pe_state: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True
