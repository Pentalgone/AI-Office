
from sqlalchemy.orm import Session

from app.db.models import Task, TaskAssignment, TaskEvent


def determine_task_status(statuses: list[str]) -> str | None:
    """Определяет общий статус по назначениям и подзадачам."""

    if not statuses:
        return None

    active_statuses = {
        "in_progress",
        "submitted",
        "revision_requested"
    }

    terminal_statuses = {
        "completed",
        "failed",
        "blocked",
        "cancelled"
    }

    if any(status in active_statuses for status in statuses):
        return "in_progress"

    if all(status == "pending" for status in statuses):
        return "pending"

    if "pending" in statuses:
        return "in_progress"

    if all(status == "completed" for status in statuses):
        return "completed"

    if all(status in terminal_statuses for status in statuses):
        if "failed" in statuses:
            return "failed"

        return "blocked"

    return None


def recalculate_task_status(
    db: Session,
    task_id: int
):
    """
    Пересчитывает статус задачи на основании:
    - её собственных назначений;
    - статусов дочерних задач.

    После пересчёта дочерней задачи обновляет родителя.
    """

    task = db.query(Task).filter(
        Task.id == task_id
    ).first()

    if not task:
        return None

    if task.status == "cancelled":
        return task

    assignments = db.query(TaskAssignment).filter(
        TaskAssignment.task_id == task_id
    ).all()

    children = db.query(Task).filter(
        Task.parent_task_id == task_id
    ).all()

    statuses = [
        assignment.status
        for assignment in assignments
    ]

    statuses.extend(
        child.status
        for child in children
    )

    new_status = determine_task_status(statuses)

    if new_status is not None and task.status != new_status:
        old_status = task.status
        task.status = new_status

        event = TaskEvent(
            task_id=task.id,
            event_type="status_changed",
            message=(
                f"Статус задачи изменён автоматически: "
                f"{old_status} → {new_status}"
            ),
            actor="system"
        )

        db.add(event)

    # Если пересчитывали дочернюю задачу,
    # пересчитываем также её родителя.
    if task.parent_task_id is not None:
        recalculate_task_status(
            db=db,
            task_id=task.parent_task_id
        )

    return task
