import asyncio
import json
from datetime import datetime, timezone

from app.db.database import SessionLocal
from app.db.models import Agent, Task, TaskAssignment, TaskEvent, TaskRun


ACTIVE_ASSIGNMENT_STATUSES = {
    "pending",
    "in_progress",
    "submitted",
    "revision_requested",
}


def _now():
    return datetime.now(timezone.utc)


def recover_interrupted_runs(session_factory=SessionLocal) -> int:
    """
    Requeues runs interrupted by a server restart and resets only assignments
    that were in progress inside those runs.
    """
    recovered = 0

    with session_factory() as db:
        runs = (
            db.query(TaskRun)
            .filter(TaskRun.status == "running")
            .order_by(TaskRun.id.asc())
            .all()
        )

        for run in runs:
            task = db.get(Task, run.task_id)

            if task is None:
                run.status = "failed"
                run.error = "Основная задача запуска больше не существует."
                run.completed_at = _now()
                continue

            if task.status == "cancelled":
                run.status = "cancelled"
                run.error = "Задача была отменена до восстановления запуска."
                run.completed_at = _now()
                continue

            if task.status == "completed" and task.final_report:
                run.status = "completed"
                run.error = None
                run.completed_at = _now()
                continue

            task_ids = [task.id]
            children = (
                db.query(Task)
                .filter(Task.parent_task_id == task.id)
                .all()
            )
            task_ids.extend(child.id for child in children)

            interrupted = (
                db.query(TaskAssignment)
                .filter(
                    TaskAssignment.task_id.in_(task_ids),
                    TaskAssignment.status == "in_progress",
                )
                .all()
            )

            affected_agent_ids = set()
            for assignment in interrupted:
                assignment.status = "pending"
                affected_agent_ids.add(assignment.agent_id)

            for agent_id in affected_agent_ids:
                agent = db.get(Agent, agent_id)
                if not agent or agent.status == "offline":
                    continue

                active_count = (
                    db.query(TaskAssignment)
                    .filter(
                        TaskAssignment.agent_id == agent_id,
                        TaskAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                    )
                    .count()
                )
                agent.status = "working" if active_count else "idle"

            run.status = "pending"
            run.started_at = None
            run.error = (
                "Работа была прервана перезапуском сервера. "
                "Запуск автоматически возвращён в очередь."
            )

            db.add(
                TaskEvent(
                    task_id=task.id,
                    event_type="execution_recovered",
                    message=(
                        f"Запуск №{run.id} восстановлен после перезапуска сервера; "
                        f"назначений возвращено в очередь: {len(interrupted)}"
                    ),
                    actor="system",
                )
            )
            recovered += 1

        db.commit()

    return recovered


def claim_next_run(session_factory=SessionLocal):
    with session_factory() as db:
        run = (
            db.query(TaskRun)
            .filter(TaskRun.status == "pending")
            .order_by(TaskRun.id.asc())
            .first()
        )

        if run is None:
            return None

        run.status = "running"
        run.started_at = _now()
        run.error = None
        db.commit()

        return run.id, run.task_id


def finish_run(
    run_id: int,
    status: str,
    result_summary: str | None = None,
    error: str | None = None,
    session_factory=SessionLocal,
):
    with session_factory() as db:
        run = db.get(TaskRun, run_id)
        if run is None:
            return

        run.status = status
        run.result_summary = result_summary
        run.error = error
        run.completed_at = _now()
        db.commit()


async def task_worker_loop(
    execute_task,
    stop_event: asyncio.Event,
    session_factory=SessionLocal,
    poll_interval: float = 1.5,
):
    recover_interrupted_runs(session_factory=session_factory)

    while not stop_event.is_set():
        claimed = claim_next_run(session_factory=session_factory)

        if claimed is None:
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=poll_interval,
                )
            except asyncio.TimeoutError:
                pass
            continue

        run_id, task_id = claimed

        try:
            with session_factory() as db:
                result = await execute_task(task_id, db)

            task_completed = result.get("task_status") == "completed"
            report_ready = result.get("final_report_status") in {
                "created",
                "already_exists",
            }
            task_cancelled = result.get("task_status") == "cancelled"
            status = (
                "cancelled"
                if task_cancelled
                else "completed"
                if task_completed and report_ready
                else "needs_attention"
            )
            summary = json.dumps(
                result,
                ensure_ascii=False,
                default=str,
            )[:12000]
            finish_run(
                run_id=run_id,
                status=status,
                result_summary=summary,
                error=(
                    None
                    if status == "completed"
                    else result.get("message")
                    or result.get("final_report_status")
                ),
                session_factory=session_factory,
            )

        except asyncio.CancelledError:
            # Leave the run marked as running; startup recovery will requeue it.
            raise

        except Exception as error:
            with session_factory() as db:
                task = db.get(Task, task_id)
                cancelled = bool(task and task.status == "cancelled")

            finish_run(
                run_id=run_id,
                status="cancelled" if cancelled else "failed",
                error=str(error)[:1000],
                session_factory=session_factory,
            )
