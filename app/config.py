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
