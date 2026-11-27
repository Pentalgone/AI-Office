from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.db.database import Base
from app.db.models import Agent, Task, TaskAssignment, TaskRun


def test_enqueue_task_run_is_idempotent_while_pending():
    test_engine = create_engine("sqlite://")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as db:
        director = Agent(name="Director", role="director", status="idle")
        worker = Agent(name="Analyst", role="analyst", status="idle")
        db.add_all([director, worker])
        db.flush()

        root = Task(title="Основная задача", status="pending", priority="normal")
        db.add(root)
        db.flush()

        child = Task(
            title="Подзадача",
            parent_task_id=root.id,
            status="pending",
            priority="normal",
        )
        db.add(child)
        db.flush()

        db.add(
            TaskAssignment(
                task_id=child.id,
                agent_id=worker.id,
                assigned_by_agent_id=director.id,
                status="pending",
            )
        )
        db.commit()

        first = main_module.enqueue_ai_task_run(root.id, db)
        second = main_module.enqueue_ai_task_run(root.id, db)

        assert first.id == second.id
        assert first.status == "pending"
        assert db.query(TaskRun).filter(TaskRun.task_id == root.id).count() == 1

        fetched = main_module.get_ai_task_run(first.id, db)
        assert fetched.status == "pending"

    test_engine.dispose()
