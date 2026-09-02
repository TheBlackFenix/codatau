import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(__file__))


def _env_int(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f'{name} debe ser un número entero.') from exc

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-insegura-cambiar-en-produccion'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = _env_int('MAX_CONTENT_LENGTH', 50 * 1024 * 1024)
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or os.path.join(BASE_DIR, 'uploads')
    ANALYTICS_FOLDER = os.environ.get('ANALYTICS_FOLDER') or os.path.join(BASE_DIR, 'artifacts')
    PROFILE_SAMPLE_SIZE = _env_int('PROFILE_SAMPLE_SIZE', 12)
    ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}
    WTF_CSRF_ENABLED = True
    AI_PROVIDER = os.environ.get('AI_PROVIDER', 'disabled')
    AI_MODEL = os.environ.get('AI_MODEL', '')
    AI_API_KEY = os.environ.get('AI_API_KEY', '')
    AI_BASE_URL = os.environ.get('AI_BASE_URL', 'https://api.openai.com/v1')
    AI_RESPONSE_MODE = os.environ.get('AI_RESPONSE_MODE', 'json_schema')
    AI_TIMEOUT_SECONDS = _env_int('AI_TIMEOUT_SECONDS', 30)
    AI_MAX_OUTPUT_TOKENS = _env_int('AI_MAX_OUTPUT_TOKENS', 1200)
    AI_MAX_CANDIDATE_COLUMNS = _env_int('AI_MAX_CANDIDATE_COLUMNS', 8)
    AI_SAMPLE_VALUES = _env_int('AI_SAMPLE_VALUES', 4)

class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get('DATABASE_URL') or
        'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'pymes_ai.db')
    )

class ProductionConfig(Config):
    DEBUG = False
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')

class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
