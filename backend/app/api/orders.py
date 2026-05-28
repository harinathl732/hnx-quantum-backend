import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.models.trading import User
from app.services.oms_service import oms_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

router = APIRouter(prefix="/orders", tags=["Order Management (OMS)"])

# Validation schemas for order actions
class ManualOrderRequest(BaseModel):
    symbol: str = Field(..., example="NIFTY2652819000CE")
    quantity: int = Field(..., gt=0, example=75)
    transaction_type: str = Field("BUY", description="BUY or SELL")
    order_type: str = Field("MARKET", description="MARKET, LIMIT, SL")
    product: str = Field("MIS", description="MIS or NRML")
    price: float = Field(0.0, description="Required for LIMIT and SL orders")
    trigger_price: float = Field(0.0, description="Required for SL orders")

class ModifyOrderRequest(BaseModel):
    order_id: str
    quantity: Optional[int] = None
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    order_type: str = "LIMIT"

@router.post("/place")
def place_manual_order(
    req: ManualOrderRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Submits a manual F&O order to Zerodha NFO segment.
    Protected by JWT - restricted to authorized admin sessions.
    """
    try:
        response = oms_service.place_order(
            symbol=req.symbol,
            quantity=req.quantity,
            transaction_type=req.transaction_type,
            order_type=req.order_type,
            product=req.product,
            price=req.price,
            trigger_price=req.trigger_price
        )
        return {"status": "success", "data": response}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Order execution failed: {e}"
        )


@router.put("/modify")
def modify_active_order(
    req: ModifyOrderRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Modifies a pending stop-loss or limit order.
    """
    try:
        response = oms_service.modify_order(
            order_id=req.order_id,
            quantity=req.quantity,
            price=req.price,
            trigger_price=req.trigger_price,
            order_type=req.order_type
        )
        return {"status": "success", "data": response}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Order modification failed: {e}"
        )


@router.delete("/cancel/{order_id}")
def cancel_active_order(
    order_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Cancels a pending order.
    """
    try:
        response = oms_service.cancel_order(order_id)
        return {"status": "success", "data": response}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Order cancellation failed: {e}"
        )


@router.get("/history/{order_id}")
def get_order_audit_trail(
    order_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Queries execution audit trails and state changes for a specific order.
    """
    try:
        response = oms_service.get_order_history(order_id)
        return {"status": "success", "history": response}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch order history: {e}"
        )
