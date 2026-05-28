import os
import time
import json
import logging
import threading
from typing import List, Dict, Any, Optional
from kiteconnect import KiteTicker
from app.services.kite_service import kite_service
from app.core.redis_client import redis_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class TickerService:
    REDIS_TICK_PREFIX = "hnx_quantum:ticks"

    def __init__(self):
        self.ticker: Optional[KiteTicker] = None
        self.subscribed_tokens: List[int] = []
        self._thread = None
        self.is_running = False

    def start_ticker(self):
        """
        Retrieves the authenticated Kite client, fetches the cached access token,
        and launches the KiteTicker in a dedicated non-blocking background thread.
        """
        if self.is_running:
            logging.info("[TICKER] Ticker is already running in the background.")
            return

        kite = kite_service.get_authenticated_client()
        if not kite:
            logging.error("[TICKER] Cannot start ticker. Zerodha session is not authenticated.")
            return

        api_key = kite_service.api_key
        access_token = redis_manager.get_value(kite_service.REDIS_TOKEN_KEY)

        # Initialize official Kite WebSocket client
        self.ticker = KiteTicker(api_key, access_token)
        
        # Bind WebSocket events
        self.ticker.on_ticks = self._on_ticks
        self.ticker.on_connect = self._on_connect
        self.ticker.on_close = self._on_close
        self.ticker.on_reconnect = self._on_reconnect

        self.is_running = True
        
        # Run WebSocket loop in background thread to keep main server responsive
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logging.info("[TICKER] Background Ticker WebSocket thread spawned.")

    def _run_loop(self):
        """
        Internal worker running the WebSocket main event loop.
        Includes automatic 5-second reconnect attempts if the connection drops.
        """
        while self.is_running:
            try:
                # connect() blocks this thread. It runs auto-reconnections natively.
                self.ticker.connect()
            except Exception as e:
                logging.error(f"[TICKER] WebSocket loop encountered an error: {e}")
                time.sleep(5)

    def stop_ticker(self):
        """
        Disconnects the WebSocket client and terminates the background loop cleanly.
        """
        self.is_running = False
        if self.ticker:
            try:
                self.ticker.close()
            except Exception:
                pass
        logging.info("[TICKER] Background Ticker WebSocket has been stopped.")

    def subscribe(self, tokens: List[int]):
        """
        Subscribes to a list of instrument tokens.
        Example: [256265] -> Nifty Futures token.
        """
        # Save tokens to handle disconnect/reconnect states
        for token in tokens:
            if token not in self.subscribed_tokens:
                self.subscribed_tokens.append(token)

        if self.ticker and self.ticker.is_connected():
            self.ticker.subscribe(tokens)
            # Set mode to full which yields high-resolution data (LTP, bid/ask depth, volumes)
            self.ticker.set_mode(self.ticker.MODE_FULL, tokens)
            logging.info(f"[TICKER] Subscribed to tokens: {tokens} (Mode: FULL)")

    def unsubscribe(self, tokens: List[int]):
        """
        Unsubscribes from specific instrument tokens.
        """
        for token in tokens:
            if token in self.subscribed_tokens:
                self.subscribed_tokens.remove(token)

        if self.ticker and self.ticker.is_connected():
            self.ticker.unsubscribe(tokens)
            logging.info(f"[TICKER] Unsubscribed from tokens: {tokens}")

    # ==========================================
    # WEBSOCKET EVENT CALLBACKS
    # ==========================================
    def _on_connect(self, ws, response):
        """
        Fires once the WebSocket successfully establishes a connection.
        Automatically re-subscribes to all active tokens from the session.
        """
        logging.info("[TICKER SUCCESS] WebSocket connected to Zerodha streaming server.")
        if self.subscribed_tokens:
            ws.subscribe(self.subscribed_tokens)
            ws.set_mode(ws.MODE_FULL, self.subscribed_tokens)
            logging.info(f"[TICKER] Re-subscribed to {len(self.subscribed_tokens)} tokens on reconnect.")

    def _on_close(self, ws, code, reason):
        logging.warning(f"[TICKER WARNING] WebSocket connection closed. Code: {code} | Reason: {reason}")

    def _on_reconnect(self, ws, attempts_count):
        logging.info(f"[TICKER] Attempting to reconnect (Attempt {attempts_count})...")

    def _on_ticks(self, ws, ticks: List[Dict[str, Any]]):
        """
        Fires on every new streaming tick.
        Saves live quotes to high-speed Redis hash caches and publishes raw prices.
        """
        for tick in ticks:
            token = tick.get("instrument_token")
            if not token:
                continue

            # Standardize parsed details
            tick_data = {
                "token": token,
                "ltp": tick.get("last_price", 0.0),
                "volume": tick.get("volume_traded", 0),
                "buy_qty": tick.get("buy_quantity", 0),
                "sell_qty": tick.get("sell_quantity", 0),
                "timestamp": time.time(),
                "ohlc": tick.get("ohlc", {})
            }

            # Cache the latest LTP in Redis for instant API access
            redis_manager.set_json(f"{self.REDIS_TICK_PREFIX}:{token}", tick_data)
            
            # Publish the raw tick to a Redis channel for real-time consumers (like our CandleBuilder)
            redis_manager.get_client().publish(f"hnx_quantum:raw_ticks:{token}", json.dumps(tick_data))

# Global ticker instance
ticker_service = TickerService()
