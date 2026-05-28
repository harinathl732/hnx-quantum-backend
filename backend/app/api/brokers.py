import logging
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import get_current_user
from app.models.trading import User
from app.services.kite_service import kite_service
from app.core.redis_client import redis_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

router = APIRouter(prefix="/brokers", tags=["Broker Sessions"])

@router.get("/zerodha/login-url")
def get_zerodha_login_url(current_user: User = Depends(get_current_user)):
    """
    Returns the official Zerodha OAuth login URL.
    Protected by JWT - only authenticated users can fetch the login URL.
    """
    try:
        url = kite_service.get_login_url()
        return {"login_url": url}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Zerodha login URL: {e}"
        )


@router.get("/zerodha/callback", response_class=HTMLResponse)
def zerodha_callback(request_token: str = None, error: str = None):
    """
    Public Callback Endpoint.
    When you log in through Zerodha's redirect, it lands here with a 'request_token' parameter.
    We exchange this token for an active daily session and cache it in Redis.
    """
    if error:
        logging.error(f"[ZERODHA CALLBACK] Redirect error received: {error}")
        return f"""
        <html>
            <head><title>Authentication Failed</title></head>
            <body style="font-family: Arial, sans-serif; background-color: #121212; color: #ff5252; text-align: center; padding-top: 100px;">
                <h1>Authentication Failed!</h1>
                <p>Zerodha returned an error: <strong>{error}</strong></p>
                <p style="color: #888;">Please close this tab and try logging in again.</p>
            </body>
        </html>
        """

    if not request_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing request_token parameter."
        )

    try:
        # Exchange the token and save it to Redis
        session = kite_service.generate_session(request_token)
        username = session.get("user_name", "Trader")
        user_id = session.get("user_id", "")
        
        # Proactively launch background WebSocket data streams, OHLC candle builder, and strategy engine post-login
        try:
            from app.services.ticker_service import ticker_service
            from app.services.candle_builder import candle_builder
            from app.engine.strategy_engine import strategy_engine
            
            ticker_service.start_ticker()
            candle_builder.start_builder()
            strategy_engine.start_engine()
            logging.info("[SYSTEM] Background market data stream, candle builder, & strategy engine successfully initialized post-login.")
        except Exception as stream_err:
            logging.error(f"[SYSTEM ERROR] Failed to spawn background market processes: {stream_err}")
        
        # Display a beautiful, premium glassmorphic confirmation page in the browser
        return f"""
        <html>
            <head>
                <title>Session Active - HNX Quantum</title>
                <style>
                    body {{
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        background: radial-gradient(circle at center, #1b1c2b 0%, #0d0e15 100%);
                        color: #ffffff;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        margin: 0;
                    }}
                    .container {{
                        background: rgba(255, 255, 255, 0.03);
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        border-radius: 16px;
                        padding: 40px 60px;
                        text-align: center;
                        backdrop-filter: blur(12px);
                        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
                        max-width: 450px;
                    }}
                    h1 {{
                        color: #00e676;
                        margin-bottom: 10px;
                        font-size: 28px;
                        text-shadow: 0 0 10px rgba(0, 230, 118, 0.3);
                    }}
                    p {{
                        color: #b0bec5;
                        font-size: 16px;
                        line-height: 1.6;
                    }}
                    .success-badge {{
                        background: rgba(0, 230, 118, 0.1);
                        border: 1px solid #00e676;
                        color: #00e676;
                        border-radius: 20px;
                        padding: 6px 16px;
                        display: inline-block;
                        font-weight: bold;
                        font-size: 14px;
                        margin-bottom: 25px;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="success-badge">SESSION CONNECTED</div>
                    <h1>Welcome, {username}!</h1>
                    <p>Your Zerodha account (ID: <strong>{user_id}</strong>) has been successfully authenticated.</p>
                    <p style="color: #78909c; font-size: 14px; margin-top: 20px;">
                        The access token is securely cached inside Redis. You can safely close this window now and return to your HNX Quantum terminal.
                    </p>
                </div>
            </body>
        </html>
        """
    except Exception as e:
        logging.error(f"[ZERODHA CALLBACK] Session generation failed: {e}")
        return f"""
        <html>
            <head><title>Connection Failed</title></head>
            <body style="font-family: Arial, sans-serif; background-color: #121212; color: #ff5252; text-align: center; padding-top: 100px;">
                <h1>Session Activation Failed!</h1>
                <p>Error exchanging token: <strong>{e}</strong></p>
                <p style="color: #888;">Ensure your KITE_API_SECRET matches your API key in your .env file.</p>
            </body>
        </html>
        """


@router.get("/zerodha/status")
def get_zerodha_status(current_user: User = Depends(get_current_user)):
    """
    Checks the active Zerodha session status.
    If authenticated, returns active profile details.
    """
    connected = kite_service.is_connected()
    if not connected:
        return {"status": "disconnected", "profile": None}

    # Fetch cached profile metadata from Redis
    profile_data = redis_manager.get_json(kite_service.REDIS_PROFILE_KEY)
    if not profile_data:
        # Fallback to test client connection
        client = kite_service.get_authenticated_client()
        if not client:
            return {"status": "disconnected", "profile": None}
        try:
            profile = client.profile()
            return {"status": "connected", "profile": profile}
        except Exception:
            return {"status": "disconnected", "profile": None}
            
    return {
        "status": "connected",
        "profile": {
            "user_id": profile_data.get("user_id"),
            "user_name": profile_data.get("user_name"),
            "email": profile_data.get("email"),
            "broker": "Zerodha"
        }
    }
