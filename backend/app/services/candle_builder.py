import time
import json
import logging
import threading
from typing import Dict, Any, List
from app.core.redis_client import redis_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class CandleBuilder:
    def __init__(self, interval_minutes: int = 1):
        self.interval_seconds = interval_minutes * 60
        self.buffers: Dict[int, List[Dict[str, Any]]] = {} # token -> list of ticks in current candle
        self.current_intervals: Dict[int, float] = {}      # token -> start time of current candle
        self._thread = None
        self.is_running = False

    def start_builder(self):
        """
        Launches the candle builder listener in a background thread.
        """
        if self.is_running:
            return
            
        self.is_running = True
        self._thread = threading.Thread(target=self._ticks_listener_loop, daemon=True)
        self._thread.start()
        logging.info(f"[CANDLE BUILDER] Background {self.interval_seconds // 60}m OHLC builder started.")

    def stop_builder(self):
        self.is_running = False
        logging.info("[CANDLE BUILDER] OHLC builder has been stopped.")

    def _ticks_listener_loop(self):
        """
        Subscribes to all raw tick channels in Redis and aggregates them into candles.
        """
        pubsub = redis_manager.get_client().pubsub()
        # Pattern match: listen to all raw tick channels
        pubsub.psubscribe("hnx_quantum:raw_ticks:*")
        
        while self.is_running:
            try:
                # Read message with a timeout of 1 second
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    continue

                # Parse tick data from channel
                payload = json.loads(message["data"])
                self._process_tick(payload)
                
            except Exception as e:
                logging.error(f"[CANDLE BUILDER] Error in ticks listener loop: {e}")
                time.sleep(1)

    def _process_tick(self, tick: Dict[str, Any]):
        """
        Processes a single live tick. Checks for candle boundary completions.
        """
        token = tick["token"]
        ltp = tick["ltp"]
        volume = tick["volume"]
        timestamp = tick["timestamp"]
        
        # Initialize buffer for this token if it doesn't exist
        if token not in self.buffers:
            self.buffers[token] = []
            # Align candle start to boundary (e.g. start of the minute)
            self.current_intervals[token] = timestamp - (timestamp % self.interval_seconds)

        # Check if the tick falls into a NEW candle interval boundary
        candle_start = self.current_intervals[token]
        if timestamp >= candle_start + self.interval_seconds:
            # The current minute closed! Compile and publish the completed candle
            self._close_candle(token)
            
            # Start the new candle interval
            self.current_intervals[token] = timestamp - (timestamp % self.interval_seconds)

        # Append tick to current candle buffer
        self.buffers[token].append({
            "price": ltp,
            "volume": volume,
            "timestamp": timestamp
        })

    def _close_candle(self, token: int):
        """
        Compiles the buffered ticks into a final OHLC candle, resets buffers,
        caches the candle in Redis list, and publishes the closed event to subscribers.
        """
        ticks = self.buffers.get(token, [])
        if not ticks:
            return

        prices = [t["price"] for t in ticks]
        volumes = [t["volume"] for t in ticks]
        start_time = self.current_intervals[token]

        # Calculate Open, High, Low, Close (OHLC)
        open_price = prices[0]
        high_price = max(prices)
        low_price = min(prices)
        close_price = prices[-1]
        
        # Calculate traded volume inside the candle (end volume minus start volume)
        candle_volume = volumes[-1] - volumes[0] if len(volumes) > 1 else 0

        # Construct final OHLC candle schema
        candle = {
            "token": token,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": max(0, candle_volume),
            "timestamp": start_time,
            "datetime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))
        }

        # Clear the old tick buffers
        self.buffers[token] = []

        # Cache completed candle inside a Redis list (keeps historical trace of last 500 candles)
        redis_list_key = f"hnx_quantum:candles:1m:{token}"
        redis_client = redis_manager.get_client()
        redis_client.rpush(redis_list_key, json.dumps(candle))
        redis_client.ltrim(redis_list_key, -500, -1) # Keep only the most recent 500 candles
        
        # Publish the finalized candle to a pub/sub channel
        # This will immediately wake up any active strategy listening to this contract!
        redis_client.publish(f"hnx_quantum:completed_candles:1m:{token}", json.dumps(candle))
        
        logging.info(
            f"[CANDLE CLOSED] Token {token} closed 1m OHLC: "
            f"O: {open_price} | H: {high_price} | L: {low_price} | C: {close_price} | Vol: {candle_volume}"
        )

    def get_historical_candles(self, token: int) -> List[Dict[str, Any]]:
        """
        Retrieves the cached historical candles from Redis.
        """
        redis_list_key = f"hnx_quantum:candles:1m:{token}"
        data = redis_manager.get_client().lrange(redis_list_key, 0, -1)
        return [json.loads(c) for c in data]

# Global candle builder service for 1-minute OHLC aggregation
candle_builder = CandleBuilder(interval_minutes=1)
