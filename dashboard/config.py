import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))


def get_database_url():
    url = os.environ.get('DATABASE_URL', '')
    if url:
        # Railway provides postgres:// — SQLAlchemy requires postgresql://
        return url.replace('postgres://', 'postgresql://', 1)
    # Local dev: SQLite
    return 'sqlite:///' + os.path.join(os.path.dirname(__file__), 'local.db')


class Config:
    DATABASE_URL     = get_database_url()
    SECRET_KEY       = os.environ.get('SECRET_KEY', 'dev-secret-change-in-prod')
    INGEST_API_KEY   = os.environ.get('INGEST_API_KEY', 'dev-ingest-key')
    SQLALCHEMY_DATABASE_URI              = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS       = False
