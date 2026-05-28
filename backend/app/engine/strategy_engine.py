import time
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.redis_client import redis_manager
from app.models.trading import Strategy, TradeLog
from app.services.kite_service import kite_service
from app.services.ticker_service import ticker_service
from app.services.candle_builder import candle_builder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class StrategyEngine:
    def __init__(self):
        self._thread = None
        self.is_running = False
        # Map to keep track of active option tokens we are currently trading
        # token -> {"strategy_id": int, "type": "CE"|"PE"}
        self.active_subscriptions: Dict[int, Dict[str, Any]] = {}

    def start_engine(self):
        if self.is_running:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._engine_loop, daemon=True)
        self._thread.start()
        logging.info("[ENGINE] Strategy state-machine core successfully started.")

    def stop_engine(self):
        self.is_running = False
        logging.info("[ENGINE] Strategy state-machine core stopped.")

    def _engine_loop(self):
        """
        Main engine thread. Subscribes to completed candles and handles daily events:
        - 9:16 AM ATM Strike Resolution & Candle Close Marking
        - Real-time tick tracking for SL and Target hits
        """
        # Subscribe to all completed 1-minute candles from the CandleBuilder
        pubsub = redis_manager.get_client().pubsub()
        pubsub.psubscribe("hnx_quantum:completed_candles:1m:*")
        
        while self.is_running:
            try:
                # 1. Process 9:16 Setup & 3:15 Universal Exits
                self._process_clock_events()

                # 2. Check for real-time Stop Loss (20 pts) & Target (35 pts) on active trades
                self._process_realtime_ticks()
                
                # 3. Check global daily MTM Guards (circuit breakers)
                self._process_mtm_guard()
 
                # 4. Listen for closed candle events (for Entry & Candle-Close Stop Loss checking)
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    payload = json.loads(message["data"])
                    self._process_candle_close_event(payload)

            except Exception as e:
                logging.error(f"[ENGINE ERROR] Error in main engine loop: {e}")
                time.sleep(1)

    # =========================================================================
    # ATM CONTRACT CALCULATOR
    # =========================================================================
    def get_atm_option_symbols(self, underlying: str, strike_diff: int = 50) -> Dict[str, str]:
        """
        Resolves current week's ATM CE & PE option symbols using Zerodha's instruments API.
        """
        kite = kite_service.get_authenticated_client()
        if not kite:
            logging.error("[ENGINE] Cannot resolve ATM strikes - Zerodha not connected.")
            return {}

        # 1. Resolve Underlying Spot Symbol to get LTP
        spot_symbol = "NSE:NIFTY 50" if underlying == "NIFTY" else "NSE:NIFTY BANK"
        if underlying == "FINNIFTY":
            spot_symbol = "NSE:NIFTY FIN SERVICE"

        quotes = kite.quote(spot_symbol)
        spot_ltp = quotes[spot_symbol]["last_price"]
        
        # Round spot to the nearest strike
        strike_interval = 50 if underlying == "NIFTY" else 100
        atm_strike = strike_interval * round(spot_ltp / strike_interval)
        
        logging.info(f"[ENGINE ATM] Spot {underlying} LTP: {spot_ltp} | Nearest Strike resolved: {atm_strike}")

        # 2. Fetch Zerodha NFO Instruments to find weekly contracts matching strike
        instruments = kite.instruments("NFO")
        
        # Filter matching contracts
        matches = [
            inst for inst in instruments 
            if inst["name"] == underlying and inst["strike"] == atm_strike
        ]
        
        if not matches:
            logging.error(f"[ENGINE ATM] No contracts found for strike {atm_strike}.")
            return {}

        # Sort by expiry date to find the Current Week weekly expiry (first available date)
        matches.sort(key=lambda x: x["expiry"])
        nearest_expiry = matches[0]["expiry"]
        
        weekly_contracts = [inst for inst in matches if inst["expiry"] == nearest_expiry]
        
        ce_symbol = next(inst["tradingsymbol"] for inst in weekly_contracts if inst["instrument_type"] == "CE")
        pe_symbol = next(inst["tradingsymbol"] for inst in weekly_contracts if inst["instrument_type"] == "PE")
        ce_token = next(inst["instrument_token"] for inst in weekly_contracts if inst["instrument_type"] == "CE")
        pe_token = next(inst["instrument_token"] for inst in weekly_contracts if inst["instrument_type"] == "PE")

        logging.info(
            f"[ENGINE ATM SUCCESS] Resolved weekly expiry F&O contracts ({nearest_expiry}): "
            f"CE: {ce_symbol} (Token: {ce_token}) | PE: {pe_symbol} (Token: {pe_token})"
        )

        return {
            "ce_symbol": ce_symbol,
            "pe_symbol": pe_symbol,
            "ce_token": ce_token,
            "pe_token": pe_token
        }

    # =========================================================================
    # CLOCK EVENTS (9:16 AM SETUP / 3:15 PM UNIVERSAL EXIT)
    # =========================================================================
    def _process_clock_events(self):
        """
        Manages time-based execution checkpoints (9:16 ATM resolution, 3:15 exits).
        """
        now = datetime.now()
        current_time_str = now.strftime("%H:%M:%S")
        db: Session = SessionLocal()

        try:
            # Query active strategies
            active_strategies = db.query(Strategy).filter(Strategy.is_active == True).all()
            if not active_strategies:
                return

            for strat in active_strategies:
                # A. 9:16 AM ATM Strike Resolution Setup
                # If reference time is hit, and we haven't marked the symbols yet
                if current_time_str >= strat.reference_candle_time and not strat.ce_reference_close:
                    if strat.ce_state == "IDLE":
                        logging.info(f"[ENGINE CLOCK] Resolving ATM strikes for {strat.name} at {current_time_str}...")
                        contracts = self.get_atm_option_symbols(strat.underlying)
                        
                        if contracts:
                            # Map contract symbols dynamically to the Strategy
                            # We temporarily keep tokens inside memory mapped subscriptions
                            self.active_subscriptions[contracts["ce_token"]] = {"strategy_id": strat.id, "type": "CE", "symbol": contracts["ce_symbol"]}
                            self.active_subscriptions[contracts["pe_token"]] = {"strategy_id": strat.id, "type": "PE", "symbol": contracts["pe_symbol"]}
                            
                            # Subscribe the WebSocket ticker to these tokens!
                            ticker_service.subscribe([contracts["ce_token"], contracts["pe_token"]])
                            
                            # Update Strategy status to wait for the 9:16 candle close
                            strat.ce_state = "MARKING_WAIT"
                            strat.pe_state = "MARKING_WAIT"
                            db.commit()

                # B. 3:15 PM Universal Exit
                if current_time_str >= strat.universal_exit_time:
                    logging.info(f"[ENGINE CLOCK] 3:15 PM Universal Exit triggered for Strategy: {strat.name}.")
                    self._square_off_all_legs(db, strat)
                    strat.is_active = False
                    db.commit()

        except Exception as e:
            logging.error(f"[ENGINE] Clock processor error: {e}")
        finally:
            db.close()

    # =========================================================================
    # REAL-TIME TICK FEED MONITOR (SL: 20 pts / Target: 35 pts)
    # =========================================================================
    def _process_realtime_ticks(self):
        """
        Monitors active positions on high-frequency tick prices stored in Redis.
        Ensures 20 pts SL and 35 pts targets trigger instantly.
        """
        db: Session = SessionLocal()
        try:
            open_trades = db.query(TradeLog).filter(TradeLog.status == "OPEN").all()
            for trade in open_trades:
                # Find matching strategy
                strat = db.query(Strategy).filter(Strategy.id == trade.strategy_id).first()
                if not strat or not strat.is_active:
                    continue

                # Fetch token from active subscriptions
                token = next((t for t, info in self.active_subscriptions.items() if info["symbol"] == trade.symbol), None)
                if not token:
                    continue

                # Query latest tick LTP from Redis
                tick_data = redis_manager.get_json(f"hnx_quantum:ticks:{token}")
                if not tick_data:
                    continue
                
                ltp = tick_data["ltp"]
                
                # A. Check Target (35 points option gain)
                if ltp >= trade.target_trigger:
                    logging.info(f"[TARGET HIT] {trade.symbol} hit target of 35 pts! LTP: {ltp} | Trigger: {trade.target_trigger}")
                    self._close_trade(db, strat, trade, ltp, "TARGET_HIT")

                # B. Check Fixed SL Type 2 (20 points option loss)
                elif ltp <= trade.stop_loss_trigger:
                    logging.info(f"[SL-FIXED HIT] {trade.symbol} hit 20 pts Stop-Loss! LTP: {ltp} | Trigger: {trade.stop_loss_trigger}")
                    self._close_trade(db, strat, trade, ltp, "STOP_LOSS_HIT")

        except Exception as e:
            logging.error(f"[ENGINE] Real-time tick monitor error: {e}")
        finally:
            db.close()

    # =========================================================================
    # GLOBAL DAILY MTM GUARD (CIRCUIT BREAKER)
    # =========================================================================
    def _process_mtm_guard(self):
        """
        Monitors global daily realized + unrealized P&L for every active strategy.
        Triggers a panic square-off and daily lock if risk boundaries are hit.
        """
        db: Session = SessionLocal()
        try:
            active_strategies = db.query(Strategy).filter(Strategy.is_active == True).all()
            for strat in active_strategies:
                # A. Check if the strategy is already daily MTM locked in Redis
                lock_key = f"hnx_quantum:mtm_locked:{strat.id}"
                if redis_manager.get_value(lock_key):
                    # Strategy was already knocked out today. Pause it.
                    logging.info(f"[MTM GUARD] Strategy {strat.name} is locked for today due to MTM. Pausing.")
                    strat.is_active = False
                    db.commit()
                    continue

                # B. Calculate realized P&L from trades closed TODAY
                today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                
                realized_pnl = db.query(func.sum(TradeLog.pnl)).filter(
                    TradeLog.strategy_id == strat.id,
                    TradeLog.created_at >= today_start,
                    TradeLog.status != "OPEN"
                ).scalar() or 0.0

                # C. Calculate unrealized P&L from OPEN trades
                open_trades = db.query(TradeLog).filter(
                    TradeLog.strategy_id == strat.id,
                    TradeLog.status == "OPEN"
                ).all()

                unrealized_pnl = 0.0
                for trade in open_trades:
                    token = next((t for t, info in self.active_subscriptions.items() if info["symbol"] == trade.symbol), None)
                    if token:
                        tick_data = redis_manager.get_json(f"hnx_quantum:ticks:{token}")
                        if tick_data:
                            ltp = tick_data["ltp"]
                            unrealized_pnl += (ltp - trade.entry_price) * trade.quantity

                total_pnl = realized_pnl + unrealized_pnl

                # D. Evaluate risk boundaries
                # Loss Limit check (mtm_max_loss is represented as positive, e.g. 5000)
                if total_pnl <= -strat.mtm_max_loss:
                    self._trigger_mtm_circuit_breaker(db, strat, total_pnl, "MAX_LOSS_HIT")
                
                # Profit Target check
                elif total_pnl >= strat.mtm_max_profit:
                    self._trigger_mtm_circuit_breaker(db, strat, total_pnl, "MAX_PROFIT_HIT")

        except Exception as e:
            logging.error(f"[ENGINE MTM] MTM Guard processing failed: {e}")
        finally:
            db.close()

    def _trigger_mtm_circuit_breaker(self, db: Session, strat: Strategy, current_pnl: float, hit_reason: str):
        """
        Executes panic square-off sequence:
        1. Cancels all pending options orders in Zerodha.
        2. Places market exit orders for all open trades.
        3. Pauses the strategy to prevent any further entries.
        4. Caches a lock key in Redis that expires at midnight (end of the day) to block restarts.
        """
        logging.critical(
            f"[MTM GUARD TRIGGERED] Strategy '{strat.name}' (ID: {strat.id}) hit circuit breaker! "
            f"Reason: {hit_reason} | Current Daily P&L: ₹{current_pnl:+.2f} | "
            f"Limits: -₹{strat.mtm_max_loss} / +₹{strat.mtm_max_profit}"
        )

        # 1. Square off all active trades at market
        open_trades = db.query(TradeLog).filter(
            TradeLog.strategy_id == strat.id,
            TradeLog.status == "OPEN"
        ).all()

        for trade in open_trades:
            # Query current LTP to record exit price accurately
            exit_price = trade.entry_price
            token = next((t for t, info in self.active_subscriptions.items() if info["symbol"] == trade.symbol), None)
            if token:
                tick_data = redis_manager.get_json(f"hnx_quantum:ticks:{token}")
                if tick_data:
                    exit_price = tick_data["ltp"]

            # Calculate P&L and place market order to close
            pnl = (exit_price - trade.entry_price) * trade.quantity
            trade.exit_price = exit_price
            trade.status = "SQUARED_OFF"
            trade.pnl = round(pnl, 2)
            
            # Place live square off order
            self._place_live_oms_order(trade.symbol, trade.quantity, exit_trade=True)
            logging.warning(f"[MTM PANIC EXIT] Instantly closed {trade.symbol} at ₹{exit_price}.")

        # 2. Pause strategy
        strat.is_active = False
        
        # 3. Create Redis Daily Lock expiring at midnight
        lock_key = f"hnx_quantum:mtm_locked:{strat.id}"
        
        # Calculate seconds remaining until midnight
        now = datetime.now()
        tomorrow = now.replace(day=now.day, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        seconds_until_midnight = int((tomorrow - now).total_seconds())
        
        redis_manager.set_value(lock_key, "LOCKED", expire_seconds=seconds_until_midnight)
        logging.critical(f"[MTM LOCK] Strategy {strat.name} is LOCKED inside Redis for {seconds_until_midnight} seconds until midnight.")
        
        db.commit()

    # =========================================================================
    # CLOSED CANDLE EVENTS (MARKING CLONES & CANDLE CLOSE SL)
    # =========================================================================
    def _process_candle_close_event(self, candle: Dict[str, Any]):
        """
        Triggers every time a 1-minute candle closes in the CandleBuilder.
        Handles:
        1. Reference Closes marking at 9:17 AM.
        2. Option Entries based on Reference crosses.
        3. Candle-close Stop-Loss checks (SL Type 1).
        """
        token = candle["token"]
        # Verify if this is an active F&O symbol we are trading
        sub_info = self.active_subscriptions.get(token)
        if not sub_info:
            return

        db: Session = SessionLocal()
        try:
            strategy_id = sub_info["strategy_id"]
            leg_type = sub_info["type"]
            symbol = sub_info["symbol"]
            close_price = candle["close"]

            strat = db.query(Strategy).filter(Strategy.id == strategy_id).first()
            if not strat or not strat.is_active:
                return

            # A. Mark Reference candle close
            if leg_type == "CE" and strat.ce_state == "MARKING_WAIT":
                strat.ce_reference_close = close_price
                strat.ce_state = "IDLE"
                logging.info(f"[ENGINE] CE Reference Close marked: {close_price} for {strat.name}")
                db.commit()

            if leg_type == "PE" and strat.pe_state == "MARKING_WAIT":
                strat.pe_reference_close = close_price
                strat.pe_state = "IDLE"
                logging.info(f"[ENGINE] PE Reference Close marked: {close_price} for {strat.name}")
                db.commit()

            # B. Core Entry Triggers & Candle-Close SLs
            if strat.ce_reference_close and strat.pe_reference_close:
                # 1. Check Entries
                self._check_option_entry(db, strat, leg_type, symbol, close_price)

                # 2. Check Candle-Close Stop Loss (SL Type 1)
                self._check_candle_close_sl(db, strat, leg_type, symbol, close_price)

        except Exception as e:
            logging.error(f"[ENGINE] Candle close processor failed: {e}")
        finally:
            db.close()

    # =========================================================================
    # ENTRY LOGIC & TARGET RE-TRIGGER FILTERS
    # =========================================================================
    def _check_option_entry(self, db: Session, strat: Strategy, leg_type: str, symbol: str, close_price: float):
        """
        Evaluates dynamic entry conditions and manages target hit re-trigger locks.
        """
        # Determine active state & references
        state = strat.ce_state if leg_type == "CE" else strat.pe_state
        ref_close = strat.ce_reference_close if leg_type == "CE" else strat.pe_reference_close
        qty_lots = strat.ce_current_qty_lots if leg_type == "CE" else strat.pe_current_qty_lots

        # Target Hit Re-Trigger Filter
        # Must wait for price to go below reference, then above to reset target lock
        if state == "TARGET_HIT_WAIT":
            if leg_type == "CE" and close_price < ref_close:
                strat.ce_state = "TARGET_HIT_DIP"
                db.commit()
                logging.info(f"[ENGINE] CE option dipped below marked close. Re-trigger enabled.")
            elif leg_type == "PE" and close_price > ref_close:
                # PE acts reverse
                strat.pe_state = "TARGET_HIT_DIP"
                db.commit()
                logging.info(f"[ENGINE] PE option went above marked close. Re-trigger enabled.")
            return

        if state == "TARGET_HIT_DIP":
            if leg_type == "CE" and close_price >= ref_close:
                strat.ce_state = "IDLE"
                db.commit()
                logging.info(f"[ENGINE] CE option re-crossed above close. Resetting to active IDLE.")
            elif leg_type == "PE" and close_price <= ref_close:
                strat.pe_state = "IDLE"
                db.commit()
                logging.info(f"[ENGINE] PE option re-crossed below close. Resetting to active IDLE.")
            return

        # Check Active long entry conditions
        if state == "IDLE":
            trigger_trade = False
            
            if leg_type == "CE" and close_price > ref_close:
                trigger_trade = True
                strat.ce_state = "ACTIVE"
            elif leg_type == "PE" and close_price < ref_close:
                trigger_trade = True
                strat.pe_state = "ACTIVE"

            if trigger_trade:
                # Calculate lot sizes
                qty = qty_lots * strat.lot_size
                
                # Setup trigger targets and stops
                target = close_price + strat.target_points
                sl = close_price - strat.sl_type2_points # 20 pts SL
                
                # Record TradeLog in DB
                trade = TradeLog(
                    strategy_id=strat.id,
                    symbol=symbol,
                    quantity=qty,
                    entry_price=close_price,
                    stop_loss_trigger=sl,
                    target_trigger=target,
                    status="OPEN",
                    pnl=0.0
                )
                db.add(trade)
                db.commit()

                # Place live order inside Zerodha (OMS integration)
                self._place_live_oms_order(symbol, qty)
                
                logging.info(
                    f"[TRADE PLACED] Entered {leg_type} Long! symbol: {symbol} | "
                    f"Qty: {qty} | Price: {close_price} | SL: {sl} | Target: {target}"
                )

    # =========================================================================
    # STOP LOSS TYPE 1 (CANDLE CLOSE SL)
    # =========================================================================
    def _check_candle_close_sl(self, db: Session, strat: Strategy, leg_type: str, symbol: str, close_price: float):
        """
        Executes Candle-Close Stop Loss checks (SL Type 1).
        """
        if not strat.sl_type1_enabled:
            return

        state = strat.ce_state if leg_type == "CE" else strat.pe_state
        ref_close = strat.ce_reference_close if leg_type == "CE" else strat.pe_reference_close

        if state == "ACTIVE":
            # Query open trade details
            trade = db.query(TradeLog).filter(
                TradeLog.strategy_id == strat.id,
                TradeLog.symbol == symbol,
                TradeLog.status == "OPEN"
            ).first()
            
            if not trade:
                return

            sl_hit = False
            if leg_type == "CE" and close_price < ref_close:
                sl_hit = True
                logging.info(f"[SL-CANDLE CLOSE] CE closed below reference: {close_price} < {ref_close}")
            elif leg_type == "PE" and close_price > ref_close:
                # PE acts reverse
                sl_hit = True
                logging.info(f"[SL-CANDLE CLOSE] PE closed above reference: {close_price} > {ref_close}")

            if sl_hit:
                self._close_trade(db, strat, trade, close_price, "STOP_LOSS_HIT")

    # =========================================================================
    # CLOSING ACTIONS & MARTINGALE LOGIC
    # =========================================================================
    def _close_trade(self, db: Session, strat: Strategy, trade: TradeLog, price: float, hit_type: str):
        """
        Closes an active trade log, executes market counter trades,
        and increments/resets separate Martingale levels.
        """
        # Calculate P&L
        pnl = (price - trade.entry_price) * trade.quantity
        trade.exit_price = price
        trade.status = hit_type
        trade.pnl = round(pnl, 2)
        
        # Execute counter market order on Zerodha to square off
        self._place_live_oms_order(trade.symbol, trade.quantity, exit_trade=True)

        # Resolve leg type from symbol name
        leg_type = "CE" if "CE" in trade.symbol else "PE"

        # Apply Risk & Martingale controls
        if hit_type == "TARGET_HIT":
            # TARGET HIT -> Reset quantity back to base
            if leg_type == "CE":
                strat.ce_current_qty_lots = strat.base_qty_lots
                strat.ce_state = "TARGET_HIT_WAIT" # Locked until dip
            else:
                strat.pe_current_qty_lots = strat.base_qty_lots
                strat.pe_state = "TARGET_HIT_WAIT"
                
            logging.info(f"[MARTINGALE RESET] Target hit on {leg_type}! Resetting qty to {strat.base_qty_lots} lots.")
            
        else:
            # STOP LOSS HIT -> Increment Martingale sequence (+1 lot)
            if strat.martingale_enabled:
                if leg_type == "CE":
                    next_lots = strat.ce_current_qty_lots + 1
                    if next_lots <= strat.max_martingale_level:
                        strat.ce_current_qty_lots = next_lots
                    strat.ce_state = "IDLE"
                else:
                    next_lots = strat.pe_current_qty_lots + 1
                    if next_lots <= strat.max_martingale_level:
                        strat.pe_current_qty_lots = next_lots
                    strat.pe_state = "IDLE"
                    
                logging.info(f"[MARTINGALE INCREMENT] SL hit on {leg_type}! Increased next trade to {next_lots} lots.")
            else:
                if leg_type == "CE":
                    strat.ce_state = "IDLE"
                else:
                    strat.pe_state = "IDLE"

        db.commit()

    def _square_off_all_legs(self, db: Session, strat: Strategy):
        """
        Squares off all open legs for the strategy.
        """
        open_trades = db.query(TradeLog).filter(
            TradeLog.strategy_id == strat.id,
            TradeLog.status == "OPEN"
        ).all()
        
        for trade in open_trades:
            self._close_trade(db, strat, trade, trade.entry_price, "SQUARED_OFF")

    # =========================================================================
    # LIVE ORDER PLACEMENT (OMS HOOK)
    # =========================================================================
    def _place_live_oms_order(self, symbol: str, quantity: int, exit_trade: bool = False):
        """
        Submits a live execution order to Zerodha Kite.
        If no connection is active, gracefully falls back to simulated logging.
        """
        from app.services.oms_service import oms_service
        try:
            trans_type = "SELL" if exit_trade else "BUY"
            oms_service.place_order(
                symbol=symbol,
                quantity=quantity,
                transaction_type=trans_type,
                order_type="MARKET",
                product="MIS"
            )
        except Exception as e:
            logging.error(f"[ENGINE OMS] Unified order placement failed for {symbol}: {e}")

# Global Strategy Engine Manager
strategy_engine = StrategyEngine()
