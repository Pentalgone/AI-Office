
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker


# Папка проекта
PROJECT_DIR = Path(__file__).resolve().parents[2]

# Папка для хранения данных
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Файл базы данных
DATABASE_PATH = DATA_DIR / "ai_office.db"

# Подключение к SQLite
DATABASE_URL = URL.create(
    "sqlite",
    database=str(DATABASE_PATH)
)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

# Фабрика сессий базы данных
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# Базовый класс для таблиц
class Base(DeclarativeBase):
    pass
