from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./attendance.db"
    jwt_secret: str = "development-jwt-secret-change-me"
    code_secret: str = "development-code-secret-change-me"
    access_token_minutes: int = 30
    qr_ttl_seconds: int = 30
    attempt_window_minutes: int = 10
    attempt_limit_per_student: int = 5
    attempt_limit_per_ip: int = 30
    attempt_limit_per_device: int = 30
    login_window_minutes: int = 15
    login_attempt_limit: int = 10
    # Professors register with this shared code (given out by the department). Empty disables professor sign-up.
    professor_signup_code: str = ""
    message_window_minutes: int = 10
    message_limit_per_user: int = 30
    # Only read for attachments uploaded before files were stored in the database.
    upload_dir: str = "./uploads"
    max_upload_bytes: int = 5 * 1024 * 1024
    # Sheet scans: longest image side given to OCR and onnxruntime threads (0 = all cores). Detection memory
    # grows with the pixel count: about 600 MB peak at 2000, about 400 MB at 1400 (for hosts with 512 MB RAM).
    ocr_max_side: int = 2000
    ocr_threads: int = 0
    # Dates in notification texts are written in this time zone.
    timezone: str = "Europe/Bucharest"
    # "production" refuses to start with the built-in development secrets.
    app_env: str = "development"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
