import time
import logging
from typing import Dict, Any, List, Optional
from kiteconnect import KiteConnect
from app.services.kite_service import kite_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class OMSService:
    def __init__(self):
        pass

    def _get_client(self) -> KiteConnect:
        client = kite_service.get_authenticated_client()
        if not client:
            raise ConnectionError("OMS Error: Zerodha session is not authenticated. Cannot execute trade.")
        return client

    def place_order(
        self,
        symbol: str,
        quantity: int,
        transaction_type: str, # BUY or SELL
        order_type: str = "MARKET", # MARKET, LIMIT, SL
        product: str = "MIS", # MIS or NRML
        price: float = 0.0,
        trigger_price: float = 0.0
    ) -> Dict[str, Any]:
        """
        Generic order placement method covering all F&O order types.
        """
        try:
            kite = self._get_client()
            
            # Resolve Kite Connect constants
            kite_trans = kite.TRANSACTION_TYPE_BUY if transaction_type.upper() == "BUY" else kite.TRANSACTION_TYPE_SELL
            
            # Resolve order type constants
            if order_type.upper() == "MARKET":
                kite_order_type = kite.ORDER_TYPE_MARKET
            elif order_type.upper() == "LIMIT":
                kite_order_type = kite.ORDER_TYPE_LIMIT
            elif order_type.upper() == "SL":
                # Stop-Loss Limit
                kite_order_type = kite.ORDER_TYPE_SL
            elif order_type.upper() == "SL-M":
                # Stop-Loss Market
                kite_order_type = kite.ORDER_TYPE_SLM
            else:
                raise ValueError(f"Invalid order type: {order_type}")

            logging.info(
                f"[OMS EXECUTE] Submitting order: {transaction_type} {quantity} shares of {symbol} "
                f"({order_type} / {product}) | Price: ₹{price} | Trigger: ₹{trigger_price}"
            )

            # Submit actual F&O order to Zerodha
            response = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=kite_trans,
                quantity=quantity,
                product=kite.PRODUCT_MIS if product.upper() == "MIS" else kite.PRODUCT_NRML,
                order_type=kite_order_type,
                price=price if kite_order_type in [kite.ORDER_TYPE_LIMIT, kite.ORDER_TYPE_SL] else 0.0,
                trigger_price=trigger_price if kite_order_type in [kite.ORDER_TYPE_SL, kite.ORDER_TYPE_SLM] else 0.0,
                validity=kite.VALIDITY_DAY
            )
            
            logging.info(f"[OMS SUCCESS] Trade submitted successfully! Order ID: {response.get('order_id')}")
            return response
            
        except Exception as e:
            logging.error(f"[OMS ERROR] Order placement failed for {symbol}: {e}")
            # Fallback mock response for sandbox simulation testing
            if not kite_service.is_connected():
                mock_id = f"MOCK_{int(time.time())}"
                logging.info(f"[OMS SIMULATION] Generated virtual Order ID: {mock_id}")
                return {"order_id": mock_id}
            raise

    def modify_order(
        self,
        order_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        order_type: str = "LIMIT"
    ) -> Dict[str, Any]:
        """
        Modifies a pending F&O order (e.g. updating Stop Loss triggers or Limit prices).
        """
        try:
            kite = self._get_client()
            
            # Resolve order type constants
            if order_type.upper() == "LIMIT":
                kite_order_type = kite.ORDER_TYPE_LIMIT
            elif order_type.upper() == "SL":
                kite_order_type = kite.ORDER_TYPE_SL
            elif order_type.upper() == "SL-M":
                kite_order_type = kite.ORDER_TYPE_SLM
            else:
                kite_order_type = kite.ORDER_TYPE_MARKET

            logging.info(
                f"[OMS MODIFY] Modifying Order ID: {order_id} | "
                f"New Qty: {quantity} | Price: ₹{price} | Trigger: ₹{trigger_price}"
            )

            response = kite.modify_order(
                variety=kite.VARIETY_REGULAR,
                order_id=order_id,
                quantity=quantity,
                price=price if price else 0.0,
                trigger_price=trigger_price if trigger_price else 0.0,
                order_type=kite_order_type
            )
            return response
        except Exception as e:
            logging.error(f"[OMS ERROR] Order modification failed for {order_id}: {e}")
            raise

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancels a pending active order.
        """
        try:
            kite = self._get_client()
            logging.info(f"[OMS CANCEL] Submitting cancellation for Order ID: {order_id}")
            response = kite.cancel_order(
                variety=kite.VARIETY_REGULAR,
                order_id=order_id
            )
            return response
        except Exception as e:
            logging.error(f"[OMS ERROR] Order cancellation failed for {order_id}: {e}")
            raise

    def get_order_history(self, order_id: str) -> List[Dict[str, Any]]:
        """
        Queries exact execution logs and audit trail for a specific order.
        """
        try:
            kite = self._get_client()
            return kite.order_history(order_id)
        except Exception as e:
            logging.error(f"[OMS ERROR] Failed to fetch order history for {order_id}: {e}")
            raise

# Global OMS Service instance
oms_service = OMSService()
