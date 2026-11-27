import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.db.database import Base
from app.db.models import Agent, Task, TaskAssignment


def test_task_run_executes_reviews_and_retries_revision(monkeypatch):
    test_engine = create_engine("sqlite://")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    db = TestSession()

    try:
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

        assignment = TaskAssignment(
            task_id=child.id,
            agent_id=worker.id,
            assigned_by_agent_id=director.id,
            instructions="Подготовить результат",
            status="pending",
        )
        db.add(assignment)
        db.commit()

        class FakeDirector:
            def __init__(self):
                self.review_count = 0

            async def review_report(self, **kwargs):
                self.review_count += 1
                if self.review_count == 1:
                    return {
                        "decision": "request_changes",
                        "feedback": "Добавить недостающие детали.",
                    }
                return {"decision": "approve", "feedback": "Исправления приняты."}

        fake_director = FakeDirector()
        monkeypatch.setattr(main_module, "AIDirector", lambda: fake_director)

        execution_count = {"value": 0}

        async def fake_execute_assignment(assignment_id, db):
            execution_count["value"] += 1
            current = db.get(TaskAssignment, assignment_id)
            current.status = "submitted"
            current.result = (
                "Подробный отчёт с исправленными деталями и конкретным результатом."
            )
            db.commit()
            return {"assignment": current}

        async def fake_final_report(task_id, db):
            return {"task_id": task_id, "final_report": "Итог"}

        monkeypatch.setattr(
            main_module, "execute_assignment", fake_execute_assignment
        )
        monkeypatch.setattr(
            main_module, "generate_task_final_report", fake_final_report
        )

        response = asyncio.run(main_module.execute_task_workflow(root.id, db))

        db.refresh(root)
        db.refresh(child)
        db.refresh(assignment)

        assert response["task_status"] == "completed"
        assert response["final_report_status"] == "created"
        assert response["assignments"][0]["attempts"] == 2
        assert execution_count["value"] == 2
        assert assignment.status == "completed"
        assert child.status == "completed"
        assert root.status == "completed"
    finally:
        db.close()
        test_engine.dispose()
