import os
import logging
import threading
from dotenv import load_dotenv

load_dotenv()

class ApiKeyManager:
    def __init__(self, key_env_var="ODDS_API_KEYS", single_env_var="ODDS_API_KEY", name="API"):
        self.key_env_var = key_env_var
        self.single_env_var = single_env_var
        self.name = name
        self.keys = []
        self.current_index = 0
        self.lock = threading.Lock()
        self._load_keys()

    def _load_keys(self):
        # Virgülle ayrılmış API keylerini al
        keys_str = os.getenv(self.key_env_var)
        if keys_str:
            self.keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        else:
            # Geriye dönük uyumluluk (tek key varsa)
            single_key = os.getenv(self.single_env_var)
            if single_key:
                self.keys = [single_key.strip()]
        
        if not self.keys:
            logging.warning(f"⚠️ No {self.name} keys found in environment variables!")

    def get_current_key(self):
        with self.lock:
            if not self.keys:
                return None
            return self.keys[self.current_index]

    def rotate_key(self):
        with self.lock:
            if not self.keys:
                return None
            old_key = self.keys[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.keys)
            new_key = self.keys[self.current_index]
            
            # Key değiştiyse logla
            if old_key != new_key:
                logging.warning(f"🔄 Rotating {self.name} Key! Changed from {old_key[:6]}... to {new_key[:6]}...")
            
            return new_key

    def get_max_retries(self):
        """Maksimum deneme sayısı toplam key sayısı kadardır."""
        return len(self.keys) if self.keys else 1

# Sistemin her yerinde kullanılacak global instance
odds_api_manager = ApiKeyManager(key_env_var="ODDS_API_KEYS", single_env_var="ODDS_API_KEY", name="Odds")
gemini_api_manager = ApiKeyManager(key_env_var="GEMINI_API_KEYS", single_env_var="GEMINI_API_KEY", name="Gemini")
