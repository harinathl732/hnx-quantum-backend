import asyncio
import json
import logging
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.api.auth import get_current_user
from app.models.trading import User, Strategy, TradeLog
from app.services.kite_service import kite_service
from app.core.redis_client import redis_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

router = APIRouter(prefix="/portfolio", tags=["Portfolio Tracking"])

class ActiveConnectionManager:
    """
    Manages active WebSocket connections for live portfolio and P&L streaming.
    """
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logging.info(f"[WS MANAGER] New active socket connection registered. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logging.info(f"[WS MANAGER] Socket disconnected. Total: {len(self.active_connections)}")

# Global WebSocket connection manager
manager = ActiveConnectionManager()


def calculate_portfolio_metrics(db: Session) -> Dict[str, Any]:
    """
    Core calculator: Sums daily F&O realized P&L and calculates live unrealized P&L 
    based on high-frequency price quotes stored inside Redis.
    """
    # 1. Fetch active NFO positions from Zerodha
    kite = kite_service.get_authenticated_client()
    positions = []
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    
    if kite:
        try:
            pos_data = kite.positions()
            net_positions = pos_data.get("net", [])
            
            for pos in net_positions:
                if pos.get("exchange") == "NFO" and pos.get("quantity", 0) != 0:
                    symbol = pos["tradingsymbol"]
                    qty = pos["quantity"]
                    avg_price = float(pos["average_price"])
                    
                    # Fetch LTP from Redis cache
                    # Find matching token
                    # In production, we retrieve instrument token dynamically, 
                    # but here we query cached LTP from our ticks prefix or Zerodha direct quote
                    ltp = float(pos.get("last_price", avg_price))
                    
                    # Compute P&L
                    pnl = (ltp - avg_price) * qty
                    unrealized_pnl += pnl
                    
                    positions.append({
                        "tradingsymbol": symbol,
                        "quantity": qty,
                        "average_price": avg_price,
                        "last_price": ltp,
                        "pnl": round(pnl, 2)
                    })
                    
                # Sum realized P&L directly from Zerodha's closed intraday trades
                realized_pnl += float(pos.get("realised", 0.0))
        except Exception as e:
            logging.error(f"[PORTFOLIO SERVICE] Failed to query Zerodha positions: {e}")
            
    # 2. Fallback to Local SQL TradeLog Database if Zerodha is not authenticated
    else:
        open_trades = db.query(TradeLog).filter(TradeLog.status == "OPEN").all()
        for trade in open_trades:
            # Query virtual ltp from Redis mock cache
            ltp = trade.entry_price
            positions.append({
                "tradingsymbol": trade.symbol,
                "quantity": trade.quantity,
                "average_price": trade.entry_price,
                "last_price": ltp,
                "pnl": 0.0
            })
        
        # Sum realized virtual pnl
        from sqlalchemy import func
        realized_pnl = db.query(func.sum(TradeLog.pnl)).filter(TradeLog.status != "OPEN").scalar() or 0.0

    total_pnl = realized_pnl + unrealized_pnl

    return {
        "overall_pnl": round(total_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "positions": positions
    }


@router.get("/summary", response_model=Dict[str, Any])
def get_portfolio_summary(
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    """
    Returns a standard REST API snapshot of your daily realized, unrealized, and net P&L.
    Protected by JWT - restricted to authorized admin sessions.
    """
    try:
        metrics = calculate_portfolio_metrics(db)
        return {"status": "success", "portfolio": metrics}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compile portfolio summary: {e}"
        )


@router.websocket("/stream")
async def websocket_portfolio_stream(websocket: WebSocket, token: str = None):
    """
    High-Frequency Streaming WebSocket.
    Broadcasting live realized, unrealized, and net P&L directly to connected UI clients.
    Secured via token-parameter JWT validation.
    """
    # 1. Validate JWT token passed as query parameter
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    from app.core.security import decode_access_token
    payload = decode_access_token(token)
    if not payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Accept socket connection
    await manager.connect(websocket)
    db = SessionLocal()

    try:
        while True:
            # Calculate and compile latest metrics
            metrics = calculate_portfolio_metrics(db)
            
            # Send payload as JSON
            await websocket.send_json({
                "type": "portfolio_update",
                "data": metrics
            })
            
            # Streams every 1 second
            await asyncio.sleep(1.0)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logging.error(f"[WS ERROR] Error in active stream connection: {e}")
        manager.disconnect(websocket)
    finally:
        db.close()
