"""
Test configuration — sets environment defaults BEFORE any app import.
"""
import os

# Must be set before any app module is imported
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("DB_PASSWORD", "test_password_123")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("SECURITY_JWT_SECRET_KEY", "test_jwt_secret_key_32_chars_minimum_length_here")
os.environ.setdefault("ENVIRONMENT", "development")
