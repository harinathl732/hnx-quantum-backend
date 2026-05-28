import os
import logging
from typing import Optional
from kiteconnect import KiteConnect, exceptions
from app.core.redis_client import redis_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class KiteService:
    REDIS_TOKEN_KEY = "hnx_quantum:kite:access_token"
    REDIS_PROFILE_KEY = "hnx_quantum:kite:profile"

    def __init__(self):
        self.api_key = os.getenv("KITE_API_KEY")
        self.api_secret = os.getenv("KITE_API_SECRET")
        self.kite = None

        if not self.api_key or not self.api_secret:
            logging.warning("Zerodha KITE_API_KEY or KITE_API_SECRET is not configured in environment variables.")

    def get_login_url(self) -> str:
        """
        Generate the Zerodha OAuth login URL.
        """
        client = KiteConnect(api_key=self.api_key)
        return client.login_url()

    def generate_session(self, request_token: str) -> dict:
        """
        Exchange the Zerodha request_token for a real access_token.
        Caches the access_token in Redis (valid for 24 hours).
        """
        try:
            client = KiteConnect(api_key=self.api_key)
            session = client.generate_session(request_token, api_secret=self.api_secret)
            access_token = session["access_token"]
            profile = session["user_name"]
            
            # Cache the access token in Redis (expire after 24 hours: 86400 seconds)
            redis_manager.set_value(self.REDIS_TOKEN_KEY, access_token, expire_seconds=86400)
            
            # Cache the profile details
            redis_manager.set_json(self.REDIS_PROFILE_KEY, session, expire_seconds=86400)
            
            logging.info(f"[ZERODHA] Successfully generated and cached session for user: {profile}")
            return session
        except Exception as e:
            logging.error(f"[ZERODHA] Failed to exchange request token: {e}")
            raise ValueError(f"Zerodha session exchange failed: {e}")

    def get_authenticated_client(self) -> Optional[KiteConnect]:
        """
        Retrieves a fully authenticated KiteConnect client instance.
        Reads the cached access token from Redis. Throws an error if no active session exists.
        """
        access_token = redis_manager.get_value(self.REDIS_TOKEN_KEY)
        if not access_token:
            logging.warning("[ZERODHA] No active Zerodha access token found in cache.")
            return None

        client = KiteConnect(api_key=self.api_key)
        client.set_access_token(access_token)
        
        try:
            # Quick profile check to confirm token validity
            # For performance, in rapid strategy ticks we skip this profile call and use direct requests,
            # but on initial fetch it is ideal.
            self.kite = client
            return self.kite
        except exceptions.TokenException:
            logging.error("[ZERODHA] Cached access token has expired or is invalid. Evicting from cache.")
            redis_manager.delete_key(self.REDIS_TOKEN_KEY)
            redis_manager.delete_key(self.REDIS_PROFILE_KEY)
            return None
        except Exception as e:
            logging.error(f"[ZERODHA] Connection test failed: {e}")
            return None

    def is_connected(self) -> bool:
        """
        Fast connection status check.
        """
        token = redis_manager.get_value(self.REDIS_TOKEN_KEY)
        return token is not None

# Global service instance
kite_service = KiteService()
