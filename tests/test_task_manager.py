from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Agent, Task, TaskAssignment
from app.services.task_manager import determine_task_status, recalculate_task_status


def test_determine_task_status():
    assert determine_task_status([]) is None
    assert determine_task_status(["pending", "pending"]) == "pending"
    assert determine_task_status(["completed", "in_progress"]) == "in_progress"
    assert determine_task_status(["completed", "completed"]) == "completed"
    assert determine_task_status(["cancelled", "completed"]) == "blocked"
    assert determine_task_status(["failed", "cancelled"]) == "failed"


def test_recalculate_child_updates_parent_status():
    test_engine = create_engine("sqlite://")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    db = TestSession()

    try:
        director = Agent(name="Director", role="director", status="idle")
        worker = Agent(name="Analyst", role="analyst", status="idle")
        db.add_all([director, worker])
        db.flush()

        parent = Task(title="Основная задача", status="pending", priority="normal")
        db.add(parent)
        db.flush()

        child = Task(
            title="Подзадача",
            parent_task_id=parent.id,
            status="pending",
            priority="normal",
        )
        db.add(child)
        db.flush()

        assignment = TaskAssignment(
            task_id=child.id,
            agent_id=worker.id,
            assigned_by_agent_id=director.id,
            status="completed",
            result="Проверенный результат",
        )
        db.add(assignment)
        db.commit()

        recalculate_task_status(db, child.id)
        db.commit()

        db.refresh(child)
        db.refresh(parent)
        assert child.status == "completed"
        assert parent.status == "completed"
    finally:
        db.close()
        test_engine.dispose()
