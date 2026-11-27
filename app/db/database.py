from pathlib import Path

from sqlalchemy import create_engine, inspect
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
    database=str(DATABASE_PATH),
)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

# Фабрика сессий базы данных
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# Базовый класс для таблиц
class Base(DeclarativeBase):
    pass


def initialize_database(target_engine=engine) -> None:
    """
    Создаёт отсутствующие таблицы и выполняет безопасные
    аддитивные миграции SQLite. Существующие строки не удаляются.
    """
    Base.metadata.create_all(bind=target_engine)

    inspector = inspect(target_engine)
    if "tasks" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("tasks")
    }

    with target_engine.begin() as connection:
        if "parent_task_id" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE tasks "
                "ADD COLUMN parent_task_id INTEGER REFERENCES tasks(id)"
            )

        if "final_report" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE tasks ADD COLUMN final_report TEXT"
            )

        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_tasks_parent_task_id "
            "ON tasks (parent_task_id)"
        )
