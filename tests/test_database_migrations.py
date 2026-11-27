from sqlalchemy import create_engine, inspect, text

from app.db.database import Base, initialize_database
from app.db import models  # noqa: F401 - register all SQLAlchemy models


def test_initialize_database_adds_missing_task_columns_without_losing_data():
    test_engine = create_engine("sqlite://")

    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                title VARCHAR(200) NOT NULL,
                description TEXT,
                status VARCHAR(30),
                priority VARCHAR(30),
                created_at DATETIME
            )
            """
        )
        connection.execute(
            text(
                """
                INSERT INTO tasks
                    (id, title, description, status, priority, created_at)
                VALUES
                    (41, 'Сохранённая задача', 'Старые данные', 'pending', 'normal',
                     '2026-11-24 10:00:00')
                """
            )
        )

    initialize_database(test_engine)

    columns = {
        column["name"] for column in inspect(test_engine).get_columns("tasks")
    }
    assert "parent_task_id" in columns
    assert "final_report" in columns

    with test_engine.connect() as connection:
        row = connection.execute(
            text("SELECT id, title, description FROM tasks WHERE id = 41")
        ).one()

    assert row == (41, "Сохранённая задача", "Старые данные")
    test_engine.dispose()


def test_initialize_database_is_safe_to_run_more_than_once():
    test_engine = create_engine("sqlite://")

    initialize_database(test_engine)
    initialize_database(test_engine)

    columns = {
        column["name"] for column in inspect(test_engine).get_columns("tasks")
    }
    assert {"parent_task_id", "final_report"}.issubset(columns)
    test_engine.dispose()
