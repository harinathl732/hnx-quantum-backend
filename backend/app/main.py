import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Database imports to trigger table provisioning on load
from app.core.database import engine, Base
import app.models.trading  # Ensure models are loaded to register metadata

try:
    Base.metadata.create_all(bind=engine)
    print("[DATABASE] PostgreSQL tables successfully checked/created.")
except Exception as e:
    print(f"[DATABASE ERROR] Failed to initialize PostgreSQL tables: {e}")

app = FastAPI(
    title="HNX Quantum Trading Backend",
    description="Enterprise Multi-Container Algorithmic Trading platform for Zerodha F&O derivatives.",
    version="1.0.0"
)

# Include Auth Router
from app.api import auth
app.include_router(auth.router)

# Include Brokers Router
from app.api import brokers
app.include_router(brokers.router)

# Include Strategies Router
from app.api import strategies
app.include_router(strategies.router)

# Include Orders Router
from app.api import orders
app.include_router(orders.router)

# Include Portfolio Router
from app.api import portfolio
app.include_router(portfolio.router)

# Allow CORS for localhost development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """
    Triggers automatically when FastAPI boots.
    If a valid daily Zerodha session exists, starts the background WebSocket streaming, candle building, and strategy engine.
    """
    from app.services.kite_service import kite_service
    if kite_service.is_connected():
        print("[SYSTEM] Active Zerodha session detected. Launching background streaming tickers...")
        from app.services.ticker_service import ticker_service
        from app.services.candle_builder import candle_builder
        from app.engine.strategy_engine import strategy_engine
        
        ticker_service.start_ticker()
        candle_builder.start_builder()
        strategy_engine.start_engine()
    else:
        print("[SYSTEM WARNING] No active Zerodha session detected. Ticker startup delayed until browser login.")

@app.on_event("shutdown")
async def shutdown_event():
    """
    Triggers cleanly when FastAPI terminates.
    Stops background loops and releases connection resources.
    """
    print("[SYSTEM] Gracefully shutting down background market data threads...")
    try:
        from app.services.ticker_service import ticker_service
        from app.services.candle_builder import candle_builder
        from app.engine.strategy_engine import strategy_engine
        
        ticker_service.stop_ticker()
        candle_builder.stop_builder()
        strategy_engine.stop_engine()
    except Exception as e:
        print(f"[SYSTEM ERROR] Failed to terminate background workers cleanly: {e}")

@app.get("/health", tags=["System Health"])
async def system_health_check():
    """
    Validates operational status of the central trading web server.
    """
    return {
        "status": "operational",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platform": "HNX Quantum Enterprise"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
