from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Agent, Task, TaskAssignment, TaskRun
from app.services.task_worker import (
    claim_next_run,
    finish_run,
    recover_interrupted_runs,
)


def test_recover_interrupted_run_requeues_in_progress_assignment():
    test_engine = create_engine("sqlite://")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as db:
        director = Agent(name="Director", role="director", status="idle")
        worker = Agent(name="Analyst", role="analyst", status="working")
        db.add_all([director, worker])
        db.flush()

        root = Task(title="Основная задача", status="in_progress", priority="normal")
        db.add(root)
        db.flush()

        child = Task(
            title="Подзадача",
            parent_task_id=root.id,
            status="in_progress",
            priority="normal",
        )
        db.add(child)
        db.flush()

        assignment = TaskAssignment(
            task_id=child.id,
            agent_id=worker.id,
            assigned_by_agent_id=director.id,
            status="in_progress",
        )
        run = TaskRun(task_id=root.id, status="running")
        db.add_all([assignment, run])
        db.commit()

        run_id = run.id
        assignment_id = assignment.id
        worker_id = worker.id

    assert recover_interrupted_runs(TestSession) == 1

    with TestSession() as db:
        run = db.get(TaskRun, run_id)
        assignment = db.get(TaskAssignment, assignment_id)
        worker = db.get(Agent, worker_id)

        assert run.status == "pending"
        assert "перезапуском" in run.error
        assert assignment.status == "pending"
        # A requeued assignment is waiting for the worker, not being executed.
        assert worker.status == "idle"


def test_claim_and_finish_task_run():
    test_engine = create_engine("sqlite://")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as db:
        task = Task(title="Задача очереди", status="pending", priority="normal")
        db.add(task)
        db.flush()
        run = TaskRun(task_id=task.id, status="pending")
        db.add(run)
        db.commit()
        run_id = run.id
        task_id = task.id

    assert claim_next_run(TestSession) == (run_id, task_id)
    assert claim_next_run(TestSession) is None

    finish_run(
        run_id=run_id,
        status="completed",
        result_summary='{"task_status":"completed"}',
        session_factory=TestSession,
    )

    with TestSession() as db:
        run = db.get(TaskRun, run_id)
        assert run.status == "completed"
        assert run.result_summary == '{"task_status":"completed"}'
        assert run.completed_at is not None

    test_engine.dispose()



def test_task_worker_processes_pending_run_and_persists_result():
    import asyncio

    from app.services.task_worker import task_worker_loop

    test_engine = create_engine("sqlite://")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    stop_event = asyncio.Event()

    with TestSession() as db:
        task = Task(title="Фоновая задача", status="pending", priority="normal")
        db.add(task)
        db.flush()
        run = TaskRun(task_id=task.id, status="pending")
        db.add(run)
        db.commit()
        run_id = run.id
        task_id = task.id

    async def fake_execute(task_id_arg, db):
        assert task_id_arg == task_id
        stop_event.set()
        return {
            "task_id": task_id_arg,
            "task_status": "completed",
            "final_report_status": "created",
            "message": "Готово",
        }

    asyncio.run(
        task_worker_loop(
            execute_task=fake_execute,
            stop_event=stop_event,
            session_factory=TestSession,
            poll_interval=0.01,
        )
    )

    with TestSession() as db:
        run = db.get(TaskRun, run_id)
        assert run.status == "completed"
        assert '"task_status": "completed"' in run.result_summary
        assert run.completed_at is not None

    test_engine.dispose()
