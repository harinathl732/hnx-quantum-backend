import os
import json
import logging
import redis
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

class RedisClientManager:
    def __init__(self):
        self.pool = redis.ConnectionPool.from_url(
            REDIS_URL, 
            max_connections=50, 
            decode_responses=True # Decodes bytes to standard strings automatically
        )
        self.client = redis.Redis(connection_pool=self.pool)
        logging.info("Redis Connection Pool successfully initialized.")

    def get_client(self) -> redis.Redis:
        return self.client

    def set_value(self, key: str, value: str, expire_seconds: int = None) -> bool:
        try:
            return self.client.set(key, value, ex=expire_seconds)
        except Exception as e:
            logging.error(f"Redis set failed for {key}: {e}")
            return False

    def get_value(self, key: str) -> str:
        try:
            return self.client.get(key)
        except Exception as e:
            logging.error(f"Redis get failed for {key}: {e}")
            return None

    def set_json(self, key: str, value: dict, expire_seconds: int = None) -> bool:
        try:
            serialized = json.dumps(value)
            return self.client.set(key, serialized, ex=expire_seconds)
        except Exception as e:
            logging.error(f"Redis set_json failed for {key}: {e}")
            return False

    def get_json(self, key: str) -> dict:
        try:
            data = self.client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logging.error(f"Redis get_json failed for {key}: {e}")
            return None

    def delete_key(self, key: str) -> bool:
        try:
            return bool(self.client.delete(key))
        except Exception as e:
            logging.error(f"Redis delete failed for {key}: {e}")
            return False

# Global redis manager instance
redis_manager = RedisClientManager()
