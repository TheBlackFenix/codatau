"""Production entry point, independent of the local development runner."""
import os
from app import create_app

app = create_app(os.environ.get('APP_ENV', 'production'))
