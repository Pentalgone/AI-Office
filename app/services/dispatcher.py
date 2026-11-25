
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import (
    Agent,
    AgentCapability,
    TaskAssignment,
    Task,
    TaskEvent,
    AgentMessage
)


def find_available_agents(
    db: Session,
    skill_code: str,
    min_level: int = 1
):
    """
    Находит подходящих свободных сотрудников
    по компетенции и минимальному уровню.
    """

    active_statuses = [
        "pending",
        "in_progress",
        "submitted",
        "revision_requested"
    ]

    candidates = (
        db.query(Agent, AgentCapability)
        .join(
            AgentCapability,
            Agent.id == AgentCapability.agent_id
        )
        .filter(
            AgentCapability.skill_code == skill_code,
            AgentCapability.level >= min_level,
            Agent.status != "offline"
        )
        .all()
    )

    available_agents = []

    for agent, capability in candidates:

        active_assignments = db.query(TaskAssignment).filter(
            TaskAssignment.agent_id == agent.id,
            TaskAssignment.status.in_(active_statuses)
        ).count()

        if active_assignments == 0 and agent.status == "idle":
            available_agents.append({
                "agent_id": agent.id,
                "name": agent.name,
                "role": agent.role,
                "status": agent.status,
                "skill_code": capability.skill_code,
                "skill_name": capability.skill_name,
                "level": capability.level,
                "active_assignments": active_assignments
            })

    available_agents.sort(
        key=lambda item: item["level"],
        reverse=True
    )

    return available_agents


def dispatch_task(
    db: Session,
    task_id: int,
    skill_code: str,
    min_level: int = 1,
    instructions: str | None = None
):
    """
    Автоматически выбирает свободного сотрудника
    и назначает ему задачу от имени Director.
    """

    # Проверяем существование задачи
    task = db.query(Task).filter(
        Task.id == task_id
    ).first()

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Задача не найдена"
        )

    # Находим Director
    director = db.query(Agent).filter(
        Agent.id == 1
    ).first()

    if not director:
        raise HTTPException(
            status_code=404,
            detail="Director не найден"
        )

    # Ищем свободных сотрудников
    candidates = find_available_agents(
        db=db,
        skill_code=skill_code,
        min_level=min_level
    )

    if not candidates:
        raise HTTPException(
            status_code=409,
            detail="Нет свободных сотрудников с подходящей компетенцией"
        )

    selected_agent = candidates[0]

    # Проверяем, не назначен ли этот сотрудник
    # на эту задачу ранее
    existing_assignment = db.query(TaskAssignment).filter(
        TaskAssignment.task_id == task_id,
        TaskAssignment.agent_id == selected_agent["agent_id"]
    ).first()

    if existing_assignment:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Агент {selected_agent['name']} "
                f"уже назначен на задачу №{task_id}"
            )
        )

    # Создаём назначение
    new_assignment = TaskAssignment(
        task_id=task_id,
        agent_id=selected_agent["agent_id"],
        assigned_by_agent_id=director.id,
        instructions=instructions,
        status="pending"
    )

    try:
        db.add(new_assignment)
        db.flush()

        # Записываем событие
        event = TaskEvent(
            task_id=task_id,
            event_type="assigned",
            message=(
                f"Director автоматически назначил задачу "
                f"агенту {selected_agent['name']} "
                f"по компетенции {skill_code}"
            ),
            actor=f"agent:{director.id}"
        )

        db.add(event)

        # Создаём сообщение исполнителю
        message = AgentMessage(
            sender_agent_id=director.id,
            recipient_agent_id=selected_agent["agent_id"],
            task_id=task_id,
            assignment_id=new_assignment.id,
            message_type="task_assignment",
            content=(
                f"Вам назначена новая задача: {task.title}\n"
                f"Компетенция: {skill_code}\n"
                f"Инструкции: {instructions or 'Дополнительных инструкций нет.'}"
            )
        )

        db.add(message)

        db.commit()
        db.refresh(new_assignment)

        return {
            "message": "Задача успешно назначена",
            "selected_agent": selected_agent,
            "assignment": new_assignment
        }

    except Exception:
        db.rollback()
        raise
