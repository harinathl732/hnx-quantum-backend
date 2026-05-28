import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    strategies = relationship("Strategy", back_populates="owner", cascade="all, delete-orphan")


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    
    # Contract parameters
    underlying = Column(String, default="NIFTY")         # NIFTY, BANKNIFTY, FINNIFTY
    option_type = Column(String, default="BOTH")        # CE, PE, BOTH
    expiry_type = Column(String, default="Current Week") # Current Week, Next Week, Monthly
    order_type = Column(String, default="MIS")           # MIS or NRML
    timeframe = Column(String, default="1min")           # 1min, 3min, 5min
    
    # Entry parameters
    entry_start_time = Column(String, default="09:17:00")
    entry_end_time = Column(String, default="15:00:00")
    reference_candle_time = Column(String, default="09:16:00")
    
    # Exit parameters
    target_points = Column(Float, default=35.0)          # e.g. 35 points profit
    sl_type1_enabled = Column(Boolean, default=True)     # Candle close based SL
    sl_type2_points = Column(Float, default=20.0)        # Fixed 20 points SL
    universal_exit_time = Column(String, default="15:15:00")
    
    # Risk & Quantity
    base_qty_lots = Column(Integer, default=1)           # Base trading lots
    martingale_enabled = Column(Boolean, default=True)
    max_martingale_level = Column(Integer, default=5)
    separate_ce_pe_martingale = Column(Boolean, default=True)
    mtm_max_loss = Column(Float, default=5000.0)        # Max daily loss limit
    mtm_max_profit = Column(Float, default=10000.0)      # Max daily profit target
    
    # Real-time state parameters (dynamically managed by strategy engine)
    is_active = Column(Boolean, default=False)          # Running or Paused
    
    # CE states
    ce_reference_close = Column(Float, nullable=True)   # 9:16 Close of CE option
    ce_current_qty_lots = Column(Integer, default=1)    # Dynamic Martingale Qty
    ce_state = Column(String, default="IDLE")           # IDLE, ACTIVE, TARGET_HIT_WAIT
    
    # PE states
    pe_reference_close = Column(Float, nullable=True)   # 9:16 Close of PE option
    pe_current_qty_lots = Column(Integer, default=1)    # Dynamic Martingale Qty
    pe_state = Column(String, default="IDLE")           # IDLE, ACTIVE, TARGET_HIT_WAIT
    
    owner_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    owner = relationship("User", back_populates="strategies")
    trades = relationship("TradeLog", back_populates="strategy", cascade="all, delete-orphan")


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    symbol = Column(String, nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    
    # Prices
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    stop_loss_trigger = Column(Float, nullable=False)
    target_trigger = Column(Float, nullable=False)
    
    # Financial metrics
    status = Column(String, default="OPEN")             # OPEN, CLOSED, STOP_LOSS_HIT, TARGET_HIT, SQUARED_OFF
    pnl = Column(Float, default=0.0)                    # Realized P&L
    
    # Execution details
    entry_order_id = Column(String, nullable=True)
    sl_order_id = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Relationships
    strategy = relationship("Strategy", back_populates="trades")
