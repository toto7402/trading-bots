import os
from dotenv import load_dotenv

load_dotenv()


class DatabentoCfg:
    def __init__(self):
        self.api_key: str = os.getenv('DATABENTO_API_KEY', '')


class Settings:
    def __init__(self):
        # IB Gateway connection
        self.ib_host: str = os.getenv('IB_HOST', '127.0.0.1')
        self.ib_port: int = int(os.getenv('IB_PORT', '4001'))
        self.ib_client_id: int = int(os.getenv('IB_CLIENT_ID', '1'))

        # Alpaca
        self.alpaca_api_key: str = os.getenv('ALPACA_API_KEY', '')
        self.alpaca_secret_key: str = os.getenv('ALPACA_SECRET_KEY', '')
        self.alpaca_paper: bool = os.getenv('ALPACA_PAPER', 'true').lower() in ('1', 'true', 'yes')

        # AI / LLM keys
        self.google_api_key: str = os.getenv('GOOGLE_API_KEY', '')
        self.mistral_api_key: str = os.getenv('MISTRAL_API_KEY', '')
        self.openai_api_key: str = os.getenv('OPENAI_API_KEY', '')

        # Data providers
        self.fred_api_key: str = os.getenv('FRED_API_KEY', '')
        self.databento: DatabentoCfg = DatabentoCfg()

        # Infrastructure
        self.redis_url: str = os.getenv('REDIS_URL', 'redis://localhost:6379')

        # Telegram
        self.telegram_token: str = os.getenv('TELEGRAM_TOKEN', '')
        self.telegram_chat_id: str = os.getenv('TELEGRAM_CHAT_ID', '')


settings = Settings()
