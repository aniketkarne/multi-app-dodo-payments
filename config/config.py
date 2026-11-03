"""
Configuration management for Dodo Payments Microservice
"""
from pydantic_settings import BaseSettings
from typing import List, Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings"""
    
    # Environment
    ENVIRONMENT: str = "development"
    SERVICE_PORT: int = 8001
    LOG_LEVEL: str = "INFO"
    
    # Dodo Payments
    DODO_API_KEY: str
    DODO_BASE_URL: str = "https://api.dodopayments.com"
    DODO_ENVIRONMENT: str = "live_mode"  # or "test_mode"
    
    # Database
    MYSQL_HOST: str
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    MYSQL_DATABASE: str = "dodo_payments_db"
    MYSQL_PORT: int = 3306
    
    # Security
    API_KEY_ENCRYPTION_KEY: str
    WEBHOOK_SIGNING_SECRET: str
    ENABLE_IP_WHITELIST: bool = False
    ALLOWED_IPS: str = "127.0.0.1,::1"
    
    # Features
    ENABLE_REFUNDS: bool = True
    ENABLE_SUBSCRIPTIONS: bool = True
    ENABLE_ANALYTICS: bool = True
    
    # Mixpanel Analytics
    MIXPANEL_ENABLED: bool = False
    MIXPANEL_PROJECT_TOKEN: str = ""
    MIXPANEL_DATA_RESIDENCY: Optional[str] = None  # "US" (default), "EU", or "IN"
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_PER_MINUTE: int = 100
    RATE_LIMIT_PER_HOUR: int = 1000
    
    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"
    
    class Config:
        env_file = ".env"
        case_sensitive = True
    
    @property
    def database_url(self) -> str:
        """Construct MySQL database URL"""
        from sqlalchemy.engine import URL

        # URL.create escapes username/password correctly -- string concatenation
        # breaks on credentials containing @ : / # % (legal in MySQL passwords).
        return URL.create(
            "mysql+pymysql",
            username=self.MYSQL_USER,
            password=self.MYSQL_PASSWORD,
            host=self.MYSQL_HOST,
            port=self.MYSQL_PORT,
            database=self.MYSQL_DATABASE,
        ).render_as_string(hide_password=False)
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]
    
    @property
    def allowed_ips_list(self) -> List[str]:
        """Parse allowed IPs"""
        return [ip.strip() for ip in self.ALLOWED_IPS.split(",")]
    
    @property
    def is_production(self) -> bool:
        """Check if running in production"""
        return self.ENVIRONMENT.lower() == "production"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()

