from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field


class TaskDispatchRequest(BaseModel):
    skill_code: str
    min_level: int = Field(default=1, ge=1, le=5)
    instructions: str | None = None


from typing import Literal
from sqlalchemy import select
from app.services.agent_executor import AgentExecutor
from app.services.web_search import search_web
from app.services.director import AIDirector
from sqlalchemy.orm import Session

from app.db.database import SessionLocal, initialize_database
from app.services.dispatcher import find_available_agents, dispatch_task
from app.services.task_manager import recalculate_task_status
from app.db.models import (
    Task,
    TaskEvent,
    Agent,
    TaskAssignment,
    AgentMessage,
    AgentCapability,
)


# Создание таблиц при запуске сервера
@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="AI Office",
    description="Центральный сервер мультиагентной системы",
    version="0.2.0",
    lifespan=lifespan,
)


# Получение сессии базы данных
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sync_agent_status(db: Session, agent_id: int):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()

    if not agent or agent.status == "offline":
        return

    active_statuses = ["pending", "in_progress", "submitted", "revision_requested"]

    active_assignments = (
        db.query(TaskAssignment)
        .filter(
            TaskAssignment.agent_id == agent_id,
            TaskAssignment.status.in_(active_statuses),
        )
        .count()
    )

    agent.status = "working" if active_assignments > 0 else "idle"


# Формат входящих данных задачи
class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    priority: str = "normal"
    parent_task_id: int | None = None


class AITaskRequest(BaseModel):
    request: str


class TaskStatusUpdate(BaseModel):
    status: Literal[
        "pending", "in_progress", "completed", "failed", "blocked", "cancelled"
    ]


class TaskEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    event_type: str
    message: str
    actor: str
    created_at: datetime


class AgentCreate(BaseModel):
    name: str
    role: str
    description: str | None = None


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    role: str
    description: str | None
    status: str
    created_at: datetime


class TaskAssignmentCreate(BaseModel):
    task_id: int
    agent_id: int
    instructions: str | None = None
    assigned_by_agent_id: int | None = None


class TaskAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    agent_id: int
    assigned_by_agent_id: int | None
    instructions: str | None
    result: str | None
    status: str
    created_at: datetime


class TaskAssignmentUpdate(BaseModel):
    status: Literal["pending", "in_progress", "failed", "blocked"]

    result: str | None = None


class AssignmentResultSubmit(BaseModel):
    sender_agent_id: int
    content: str = Field(min_length=1)


class AssignmentReview(BaseModel):
    reviewer_agent_id: int
    decision: Literal["approve", "request_changes"]
    feedback: str | None = None


class AssignmentRevisionStart(BaseModel):
    agent_id: int


class AgentMessageCreate(BaseModel):
    sender_agent_id: int | None = None
    recipient_agent_id: int
    task_id: int | None = None
    assignment_id: int | None = None

    message_type: Literal["message", "handoff", "result", "help_request"] = "message"

    content: str


class AgentMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender_agent_id: int | None
    recipient_agent_id: int
    task_id: int | None
    assignment_id: int | None
    message_type: str
    content: str
    read_at: datetime | None
    created_at: datetime


class AgentCapabilityCreate(BaseModel):
    agent_id: int
    skill_code: str
    skill_name: str
    description: str | None = None
    level: int = Field(default=3, ge=1, le=5)


class AgentCapabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    skill_code: str
    skill_name: str
    description: str | None
    level: int
    created_at: datetime


# Формат ответа с задачей
class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    status: str
    priority: str
    created_at: datetime
    parent_task_id: int | None
    final_report: str | None


class TaskTreeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    status: str
    priority: str
    created_at: datetime
    parent_task_id: int | None
    children: list[TaskResponse] = []


@app.get("/")
async def home():
    return {
        "system": "AI Office",
        "status": "online",
        "message": "Добро пожаловать в AI Office!",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "server": "running"}


@app.post("/tasks", response_model=TaskResponse)
def create_task(task_data: TaskCreate, db: Session = Depends(get_db)):

    # Если создаём подзадачу — проверяем родителя
    parent_task = None
    if task_data.parent_task_id is not None:
        parent_task = db.query(Task).filter(Task.id == task_data.parent_task_id).first()

        if not parent_task:
            raise HTTPException(
                status_code=404, detail="Родительская задача не найдена"
            )

        # Нельзя создавать подзадачи у завершённых
        # или недоступных для продолжения родительских задач.
        if parent_task.status in {"completed", "failed", "blocked", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Нельзя создать подзадачу: "
                    "родительская задача имеет статус "
                    f"{parent_task.status}"
                ),
            )

        if parent_task.parent_task_id is not None:
            raise HTTPException(
                status_code=400, detail="Нельзя создавать подзадачу у другой подзадачи"
            )
    new_task = Task(
        title=task_data.title,
        description=task_data.description,
        priority=task_data.priority,
        parent_task_id=task_data.parent_task_id,
    )

    try:
        db.add(new_task)
        db.flush()

        event = TaskEvent(
            task_id=new_task.id,
            event_type="created",
            message=f"Создана задача: {new_task.title}",
            actor="user",
        )

        db.add(event)
        db.commit()
        db.refresh(new_task)

        return new_task

    except Exception:
        db.rollback()
        raise


@app.post("/ai/tasks/plan")
async def create_ai_task_plan(task_data: AITaskRequest, db: Session = Depends(get_db)):
    capabilities = db.query(AgentCapability).all()

    agents = []

    for capability in capabilities:
        agent = db.query(Agent).filter(Agent.id == capability.agent_id).first()

        if not agent or agent.id == 1:
            continue

        agents.append(
            {
                "agent_id": agent.id,
                "name": agent.name,
                "role": agent.role,
                "status": agent.status,
                "skill_code": capability.skill_code,
                "skill_name": capability.skill_name,
                "description": capability.description,
                "level": capability.level,
            }
        )

    director = AIDirector()

    try:
        plan = await director.create_plan(user_request=task_data.request, agents=agents)
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error))

    return {"message": "AI-Директор подготовил план", "plan": plan}


@app.post("/tasks/{task_id}/generate-final-report")
async def generate_task_final_report(task_id: int, db: Session = Depends(get_db)):
    # Находим основную задачу
    task = db.get(Task, task_id)

    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    if task.parent_task_id is not None:
        raise HTTPException(
            status_code=400, detail="Итоговый отчёт формируется для основной задачи"
        )

    if task.status != "completed":
        raise HTTPException(
            status_code=409, detail="Сначала необходимо завершить все работы по задаче"
        )

    # Собираем основную задачу и её подзадачи
    task_ids = [task.id]

    children = db.query(Task).filter(Task.parent_task_id == task.id).all()

    task_ids.extend(child.id for child in children)

    # Получаем только принятые Директором отчёты
    assignments = (
        db.query(TaskAssignment)
        .filter(
            TaskAssignment.task_id.in_(task_ids), TaskAssignment.status == "completed"
        )
        .all()
    )

    reports = []

    for assignment in assignments:
        if not assignment.result:
            continue

        report_task = db.get(Task, assignment.task_id)
        agent = db.get(Agent, assignment.agent_id)

        reports.append(
            {
                "task_title": report_task.title if report_task else "Без названия",
                "agent_name": agent.name if agent else "Неизвестный сотрудник",
                "instructions": assignment.instructions,
                "result": assignment.result,
            }
        )

    if not reports:
        raise HTTPException(
            status_code=409,
            detail="Нет принятых отчётов для формирования итогового документа",
        )

    # Передаём результаты AI-Директору
    director = AIDirector()

    try:
        final_report = await director.create_final_report(
            task_title=task.title,
            task_description=task.description or "",
            reports=reports,
        )
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось сформировать итоговый отчёт: {str(error)}",
        )

    # Сохраняем итоговый документ
    task.final_report = final_report

    db.add(
        TaskEvent(
            task_id=task.id,
            event_type="final_report_created",
            message="AI-Директор сформировал итоговый отчёт",
            actor="director",
        )
    )

    db.commit()
    db.refresh(task)

    return {
        "message": "Итоговый отчёт успешно сформирован",
        "task_id": task.id,
        "final_report": task.final_report,
    }


# Получение списка задач
@app.get("/tasks", response_model=list[TaskResponse])
def get_tasks(db: Session = Depends(get_db)):
    tasks = db.scalars(select(Task).order_by(Task.id.desc())).all()

    return tasks


@app.post("/ai/tasks/execute")
async def execute_ai_task(task_data: AITaskRequest, db: Session = Depends(get_db)):
    # Получаем компетенции сотрудников
    capabilities = db.query(AgentCapability).all()
    agents = []

    for capability in capabilities:
        agent = db.query(Agent).filter(Agent.id == capability.agent_id).first()

        if not agent or agent.id == 1:
            continue

        agents.append(
            {
                "agent_id": agent.id,
                "name": agent.name,
                "role": agent.role,
                "status": agent.status,
                "skill_code": capability.skill_code,
                "skill_name": capability.skill_name,
                "description": capability.description,
                "level": capability.level,
            }
        )

    # AI-Директор составляет план
    director = AIDirector()

    try:
        plan = await director.create_plan(user_request=task_data.request, agents=agents)
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error))

    # Создаём основную задачу
    main_task = Task(
        title=plan["title"], description=plan.get("description"), priority="normal"
    )

    try:
        db.add(main_task)
        db.flush()

        db.add(
            TaskEvent(
                task_id=main_task.id,
                event_type="created",
                message=f"AI-Директор создал задачу: {main_task.title}",
                actor="agent:1",
            )
        )

        results = []

        # Создаём подзадачи и назначаем исполнителей
        for item in plan["subtasks"]:
            subtask = Task(
                title=item["title"],
                description=item.get("description"),
                priority="normal",
                parent_task_id=main_task.id,
            )

            db.add(subtask)
            db.flush()

            db.add(
                TaskEvent(
                    task_id=subtask.id,
                    event_type="created",
                    message=f"AI-Директор создал подзадачу: {subtask.title}",
                    actor="agent:1",
                )
            )

            try:
                assignment_result = dispatch_task(
                    db=db,
                    task_id=subtask.id,
                    skill_code=item["skill_code"],
                    instructions=item.get("instructions"),
                )

                results.append(
                    {
                        "task_id": subtask.id,
                        "title": subtask.title,
                        "status": "assigned",
                        "assignment": assignment_result["assignment"].id,
                        "agent": assignment_result["selected_agent"]["name"],
                    }
                )

            except HTTPException as error:
                results.append(
                    {
                        "task_id": subtask.id,
                        "title": subtask.title,
                        "status": "not_assigned",
                        "reason": error.detail,
                    }
                )

        db.commit()
        db.refresh(main_task)

        return {
            "message": "AI-Директор создал задачи и распределил работу",
            "main_task": {
                "id": main_task.id,
                "title": main_task.title,
                "status": main_task.status,
            },
            "subtasks": results,
        }

    except Exception:
        db.rollback()
        raise


# Получение одной задачи по номеру
@app.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)

    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    return task


@app.get("/tasks/{task_id}/children", response_model=list[TaskResponse])
def get_task_children(task_id: int, db: Session = Depends(get_db)):
    # Проверяем, существует ли родительская задача
    parent_task = db.get(Task, task_id)

    if parent_task is None:
        raise HTTPException(status_code=404, detail="Родительская задача не найдена")

    # Получаем непосредственные подзадачи
    children = (
        db.query(Task)
        .filter(Task.parent_task_id == task_id)
        .order_by(Task.id.asc())
        .all()
    )

    return children


@app.get("/tasks/{task_id}/tree", response_model=TaskTreeResponse)
def get_task_tree(task_id: int, db: Session = Depends(get_db)):
    # Получаем родительскую задачу
    task = db.get(Task, task_id)

    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    # Получаем непосредственные подзадачи
    children = (
        db.query(Task)
        .filter(Task.parent_task_id == task_id)
        .order_by(Task.id.asc())
        .all()
    )

    # Формируем дерево
    return TaskTreeResponse(
        id=task.id,
        title=task.title,
        description=task.description,
        status=task.status,
        priority=task.priority,
        created_at=task.created_at,
        parent_task_id=task.parent_task_id,
        children=children,
    )


@app.post("/agents", response_model=AgentResponse)
def create_agent(agent_data: AgentCreate, db: Session = Depends(get_db)):
    existing_agent = db.query(Agent).filter(Agent.name == agent_data.name).first()

    if existing_agent:
        raise HTTPException(
            status_code=409, detail="Агент с таким именем уже существует"
        )

    new_agent = Agent(
        name=agent_data.name, role=agent_data.role, description=agent_data.description
    )

    db.add(new_agent)
    db.commit()
    db.refresh(new_agent)

    return new_agent


@app.get("/agents", response_model=list[AgentResponse])
def get_agents(db: Session = Depends(get_db)):
    return db.query(Agent).order_by(Agent.id).all()


@app.get("/agents/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()

    if not agent:
        raise HTTPException(status_code=404, detail="Агент не найден")

    return agent


# Изменение статуса задачи


@app.patch("/tasks/{task_id}/status", response_model=TaskResponse)
def update_task_status(
    task_id: int, status_data: TaskStatusUpdate, db: Session = Depends(get_db)
):
    task = db.get(Task, task_id)

    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    # Если статус не изменился, не создаём событие
    if task.status == status_data.status:
        return task

        # Отменённую задачу нельзя возобновить
    if task.status == "cancelled":
        raise HTTPException(
            status_code=409, detail="Отменённую задачу нельзя возобновить"
        )

    # Завершённую задачу нельзя отменить
    if task.status == "completed" and status_data.status == "cancelled":
        raise HTTPException(
            status_code=409, detail="Нельзя отменить уже завершённую задачу"
        )

    # Проверки при попытке завершить задачу
    if status_data.status == "completed":

        # Нельзя завершить задачу, пока не завершены подзадачи
        unfinished_children = (
            db.query(Task)
            .filter(Task.parent_task_id == task_id, Task.status != "completed")
            .all()
        )

        if unfinished_children:
            unfinished_ids = [child.id for child in unfinished_children]

            raise HTTPException(
                status_code=409,
                detail=(
                    "Нельзя завершить задачу: "
                    f"не завершены подзадачи {unfinished_ids}"
                ),
            )

        # Нельзя завершить задачу, пока не завершены назначения
        unfinished_assignments = (
            db.query(TaskAssignment)
            .filter(
                TaskAssignment.task_id == task_id, TaskAssignment.status != "completed"
            )
            .all()
        )

        if unfinished_assignments:
            unfinished_ids = [assignment.id for assignment in unfinished_assignments]

            raise HTTPException(
                status_code=409,
                detail=(
                    "Нельзя завершить задачу: "
                    f"не завершены назначения {unfinished_ids}"
                ),
            )

    old_status = task.status
    new_status = status_data.status

    task.status = new_status

    # Каскадная отмена подзадач и назначений
    if new_status == "cancelled":

        active_assignment_statuses = [
            "pending",
            "in_progress",
            "submitted",
            "revision_requested",
        ]

        # Собираем основную задачу и её подзадачи
        affected_tasks = [task]

        children = db.query(Task).filter(Task.parent_task_id == task_id).all()

        for child in children:
            if child.status in ["pending", "in_progress"]:
                old_child_status = child.status
                child.status = "cancelled"

                db.add(
                    TaskEvent(
                        task_id=child.id,
                        event_type="status_changed",
                        message=(
                            f"Подзадача отменена вместе с родителем: "
                            f"{old_child_status} → cancelled"
                        ),
                        actor="user",
                    )
                )

                affected_tasks.append(child)

        # Отменяем активные назначения родителя и подзадач
        affected_task_ids = [affected_task.id for affected_task in affected_tasks]

        assignments = (
            db.query(TaskAssignment)
            .filter(
                TaskAssignment.task_id.in_(affected_task_ids),
                TaskAssignment.status.in_(active_assignment_statuses),
            )
            .all()
        )

        affected_agent_ids = set()

        for assignment in assignments:
            assignment.status = "cancelled"
            affected_agent_ids.add(assignment.agent_id)

        db.flush()

        # Синхронизируем статусы сотрудников
        for agent_id in affected_agent_ids:
            sync_agent_status(db, agent_id)

    # Записываем событие изменения статуса основной задачи
    event = TaskEvent(
        task_id=task.id,
        event_type="status_changed",
        message=f"Статус изменён: {old_status} → {task.status}",
        actor="user",
    )

    db.add(event)

    db.commit()
    db.refresh(task)

    return task


# Получение всех событий
@app.get("/events", response_model=list[TaskEventResponse])
def get_events(db: Session = Depends(get_db)):
    events = db.scalars(select(TaskEvent).order_by(TaskEvent.id.desc())).all()

    return events


# Получение истории конкретной задачи
@app.get("/tasks/{task_id}/events", response_model=list[TaskEventResponse])
def get_task_events(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)

    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    events = db.scalars(
        select(TaskEvent)
        .where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.id.desc())
    ).all()

    return events


@app.post("/assignments", response_model=TaskAssignmentResponse)
def create_assignment(
    assignment_data: TaskAssignmentCreate, db: Session = Depends(get_db)
):
    task = db.query(Task).filter(Task.id == assignment_data.task_id).first()

    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    agent = db.query(Agent).filter(Agent.id == assignment_data.agent_id).first()

    if not agent:
        raise HTTPException(status_code=404, detail="Агент не найден")

    if assignment_data.assigned_by_agent_id is not None:
        director = (
            db.query(Agent)
            .filter(Agent.id == assignment_data.assigned_by_agent_id)
            .first()
        )

        if not director:
            raise HTTPException(status_code=404, detail="Назначающий агент не найден")

    new_assignment = TaskAssignment(
        task_id=assignment_data.task_id,
        agent_id=assignment_data.agent_id,
        instructions=assignment_data.instructions,
        assigned_by_agent_id=assignment_data.assigned_by_agent_id,
    )

    db.add(new_assignment)

    event = TaskEvent(
        task_id=assignment_data.task_id,
        event_type="assigned",
        message=f"Задача назначена агенту {agent.name}",
        actor=(
            f"agent:{assignment_data.assigned_by_agent_id}"
            if assignment_data.assigned_by_agent_id is not None
            else "user"
        ),
    )

    db.add(event)

    try:
        db.commit()
        db.refresh(new_assignment)
        return new_assignment
    except Exception:
        db.rollback()
        raise


@app.get("/assignments", response_model=list[TaskAssignmentResponse])
def get_assignments(db: Session = Depends(get_db)):
    return db.query(TaskAssignment).order_by(TaskAssignment.id.desc()).all()


@app.get("/agents/{agent_id}/assignments", response_model=list[TaskAssignmentResponse])
def get_agent_assignments(agent_id: int, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()

    if not agent:
        raise HTTPException(status_code=404, detail="Агент не найден")

    return (
        db.query(TaskAssignment)
        .filter(TaskAssignment.agent_id == agent_id)
        .order_by(TaskAssignment.id.desc())
        .all()
    )


@app.patch("/assignments/{assignment_id}", response_model=TaskAssignmentResponse)
def update_assignment(
    assignment_id: int, update_data: TaskAssignmentUpdate, db: Session = Depends(get_db)
):
    assignment = (
        db.query(TaskAssignment).filter(TaskAssignment.id == assignment_id).first()
    )

    if not assignment:
        raise HTTPException(status_code=404, detail="Назначение не найдено")

    protected_statuses = ["submitted", "revision_requested", "completed"]

    if assignment.status in protected_statuses:
        raise HTTPException(
            status_code=409,
            detail=(
                "Нельзя менять статус назначения через этот маршрут. "
                "Используйте специальные маршруты обработки отчётов."
            ),
        )

    old_status = assignment.status

    try:
        assignment.status = update_data.status

        if update_data.result is not None:
            assignment.result = update_data.result

        db.flush()

        sync_agent_status(db, assignment.agent_id)

        recalculate_task_status(db=db, task_id=assignment.task_id)

        event = TaskEvent(
            task_id=assignment.task_id,
            event_type="assignment_status_changed",
            message=(
                f"Назначение №{assignment.id}: " f"{old_status} → {assignment.status}"
            ),
            actor=f"agent:{assignment.agent_id}",
        )

        db.add(event)

        db.commit()
        db.refresh(assignment)

        return assignment

    except Exception:
        db.rollback()
        raise


@app.post("/assignments/{assignment_id}/submit-result")
def submit_assignment_result(
    assignment_id: int,
    result_data: AssignmentResultSubmit,
    db: Session = Depends(get_db),
):
    assignment = (
        db.query(TaskAssignment).filter(TaskAssignment.id == assignment_id).first()
    )

    if not assignment:
        raise HTTPException(status_code=404, detail="Назначение не найдено")

    if assignment.status != "in_progress":
        raise HTTPException(
            status_code=409,
            detail="Можно отправить отчёт только по выполняемому назначению",
        )

    if result_data.sender_agent_id != assignment.agent_id:
        raise HTTPException(
            status_code=403, detail="Отправитель не является исполнителем задачи"
        )

    if assignment.assigned_by_agent_id is None:
        raise HTTPException(
            status_code=409, detail="У назначения не указан принимающий отчёт агент"
        )

    try:
        assignment.result = result_data.content
        assignment.status = "submitted"

        message = AgentMessage(
            sender_agent_id=assignment.agent_id,
            recipient_agent_id=assignment.assigned_by_agent_id,
            task_id=assignment.task_id,
            assignment_id=assignment.id,
            message_type="result",
            content=result_data.content,
        )

        event = TaskEvent(
            task_id=assignment.task_id,
            event_type="result_submitted",
            message=(
                f"Агент {assignment.agent_id} отправил отчёт "
                f"по назначению №{assignment.id}"
            ),
            actor=f"agent:{assignment.agent_id}",
        )

        db.add(message)
        db.add(event)
        db.flush()

        sync_agent_status(db, assignment.agent_id)

        recalculate_task_status(db=db, task_id=assignment.task_id)

        db.commit()
        db.refresh(assignment)
        db.refresh(message)

        return {
            "message": "Отчёт успешно отправлен директору",
            "assignment": assignment,
            "sent_message": message,
        }

    except Exception:
        db.rollback()
        raise


def process_assignment_review(
    assignment, reviewer_agent_id: int, decision: str, feedback: str | None, db: Session
):
    if reviewer_agent_id != 1:
        raise HTTPException(
            status_code=403, detail="Проверять отчёты может только директор"
        )

    if assignment.assigned_by_agent_id != reviewer_agent_id:
        raise HTTPException(
            status_code=403, detail="Этот директор не назначал исполнителя"
        )

    if assignment.status != "submitted":
        raise HTTPException(
            status_code=409, detail="Проверять можно только отправленные отчёты"
        )

    if decision == "approve" and not assignment.result:
        raise HTTPException(status_code=400, detail="Нельзя принять пустой отчёт")

    if decision == "request_changes":
        if not feedback or not feedback.strip():
            raise HTTPException(
                status_code=400, detail="При возврате на доработку укажите замечания"
            )

    if decision == "approve":
        assignment.status = "completed"

        message = AgentMessage(
            sender_agent_id=reviewer_agent_id,
            recipient_agent_id=assignment.agent_id,
            task_id=assignment.task_id,
            assignment_id=assignment.id,
            message_type="message",
            content=(
                f"Директор принял отчёт по назначению "
                f"№{assignment.id}. Работа засчитана."
            ),
        )

        db.add(message)

        response_message = "Отчёт успешно принят директором"

        event = TaskEvent(
            task_id=assignment.task_id,
            event_type="assignment_approved",
            message=(f"Директор принял отчёт " f"по назначению №{assignment.id}"),
            actor="agent:1",
        )

    elif decision == "request_changes":
        assignment.status = "revision_requested"

        message = AgentMessage(
            sender_agent_id=reviewer_agent_id,
            recipient_agent_id=assignment.agent_id,
            task_id=assignment.task_id,
            assignment_id=assignment.id,
            message_type="message",
            content=(
                "Отчёт возвращён на доработку. "
                f"Замечания директора: {feedback.strip()}"
            ),
        )

        db.add(message)

        response_message = "Отчёт возвращён исполнителю на доработку"

        event = TaskEvent(
            task_id=assignment.task_id,
            event_type="revision_requested",
            message=(f"Директор вернул назначение №{assignment.id} " "на доработку"),
            actor="agent:1",
        )

    else:
        raise HTTPException(status_code=400, detail="Неизвестное решение директора")

    db.add(event)
    db.flush()

    sync_agent_status(db, assignment.agent_id)

    recalculate_task_status(db=db, task_id=assignment.task_id)

    return {"message": response_message, "assignment": assignment}


@app.post("/assignments/{assignment_id}/review")
async def review_assignment(
    assignment_id: int, review_data: AssignmentReview, db: Session = Depends(get_db)
):
    assignment = (
        db.query(TaskAssignment).filter(TaskAssignment.id == assignment_id).first()
    )

    if not assignment:
        raise HTTPException(status_code=404, detail="Назначение не найдено")

    try:
        result = process_assignment_review(
            assignment=assignment,
            reviewer_agent_id=review_data.reviewer_agent_id,
            decision=review_data.decision,
            feedback=review_data.feedback,
            db=db,
        )

        db.commit()
        db.refresh(assignment)

    except Exception:
        db.rollback()
        raise

    # Автоматическая генерация после завершения основной задачи
    final_report_status = "not_ready"

    try:
        reviewed_task = db.get(Task, assignment.task_id)

        if reviewed_task:
            root_task = reviewed_task

            if reviewed_task.parent_task_id is not None:
                root_task = db.get(Task, reviewed_task.parent_task_id)

            if root_task and root_task.status == "completed":
                task_ids = [root_task.id]

                children = (
                    db.query(Task).filter(Task.parent_task_id == root_task.id).all()
                )

                task_ids.extend(child.id for child in children)

                assignments = (
                    db.query(TaskAssignment)
                    .filter(
                        TaskAssignment.task_id.in_(task_ids),
                        TaskAssignment.status == "completed",
                    )
                    .all()
                )

                reports = []

                for item in assignments:
                    if not item.result:
                        continue

                    report_task = db.get(Task, item.task_id)
                    agent = db.get(Agent, item.agent_id)

                    reports.append(
                        {
                            "task_title": (
                                report_task.title if report_task else "Без названия"
                            ),
                            "agent_name": (
                                agent.name if agent else "Неизвестный сотрудник"
                            ),
                            "instructions": item.instructions,
                            "result": item.result,
                        }
                    )

                if reports:
                    director = AIDirector()

                    final_report = await director.create_final_report(
                        task_title=root_task.title,
                        task_description=root_task.description or "",
                        reports=reports,
                    )

                    root_task.final_report = final_report

                    db.add(
                        TaskEvent(
                            task_id=root_task.id,
                            event_type="final_report_created",
                            message="AI-Директор автоматически сформировал итоговый отчёт",
                            actor="director",
                        )
                    )

                    db.commit()

                    final_report_status = "created"

    except Exception as error:
        db.rollback()
        final_report_status = f"generation_failed: {str(error)}"

    return {"review": result, "final_report_status": final_report_status}


@app.post("/assignments/{assignment_id}/start-revision")
def start_assignment_revision(assignment_id: int, db: Session = Depends(get_db)):
    assignment = (
        db.query(TaskAssignment).filter(TaskAssignment.id == assignment_id).first()
    )

    if not assignment:
        raise HTTPException(status_code=404, detail="Назначение не найдено")

    if assignment.status != "revision_requested":
        raise HTTPException(status_code=409, detail="Назначение не ожидает доработки")

    try:
        assignment.status = "in_progress"

        sync_agent_status(db, assignment.agent_id)

        event = TaskEvent(
            task_id=assignment.task_id,
            event_type="revision_started",
            message=f"Агент {assignment.agent_id} приступил к доработке назначения №{assignment.id}",
            actor=f"agent:{assignment.agent_id}",
        )

        db.add(event)
        db.commit()
        db.refresh(assignment)

        return {"message": "Доработка отчёта начата", "assignment": assignment}

    except Exception:
        db.rollback()
        raise


@app.post("/messages", response_model=AgentMessageResponse)
def send_message(message_data: AgentMessageCreate, db: Session = Depends(get_db)):
    recipient = (
        db.query(Agent).filter(Agent.id == message_data.recipient_agent_id).first()
    )

    if not recipient:
        raise HTTPException(status_code=404, detail="Получатель не найден")

    if message_data.sender_agent_id is not None:
        sender = (
            db.query(Agent).filter(Agent.id == message_data.sender_agent_id).first()
        )

        if not sender:
            raise HTTPException(status_code=404, detail="Отправитель не найден")

    if message_data.task_id is not None:
        task = db.query(Task).filter(Task.id == message_data.task_id).first()

        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")

    if message_data.assignment_id is not None:
        assignment = (
            db.query(TaskAssignment)
            .filter(TaskAssignment.id == message_data.assignment_id)
            .first()
        )

        if not assignment:
            raise HTTPException(status_code=404, detail="Назначение не найдено")

    # Проверяем права при отправке отчёта
    if message_data.message_type == "result":

        if message_data.sender_agent_id is None:
            raise HTTPException(
                status_code=400, detail="У отчёта должен быть указан отправитель"
            )

        if message_data.assignment_id is None:
            raise HTTPException(
                status_code=400, detail="Для отчёта необходимо указать назначение"
            )

        if message_data.task_id != assignment.task_id:
            raise HTTPException(
                status_code=400, detail="Назначение не соответствует задаче"
            )

        if message_data.sender_agent_id != assignment.agent_id:
            raise HTTPException(
                status_code=403, detail="Отправитель не является исполнителем задачи"
            )

        if message_data.recipient_agent_id != assignment.assigned_by_agent_id:
            raise HTTPException(
                status_code=403,
                detail="Отчёт можно отправить только назначившему агенту",
            )

        if assignment.status != "in_progress":
            raise HTTPException(
                status_code=409,
                detail="Отчёт можно отправить только по выполняемой задаче",
            )

    new_message = AgentMessage(
        sender_agent_id=message_data.sender_agent_id,
        recipient_agent_id=message_data.recipient_agent_id,
        task_id=message_data.task_id,
        assignment_id=message_data.assignment_id,
        message_type=message_data.message_type,
        content=message_data.content,
    )

    db.add(new_message)

    try:
        db.commit()
        db.refresh(new_message)
        return new_message
    except Exception:
        db.rollback()
        raise


@app.get("/agents/{agent_id}/messages", response_model=list[AgentMessageResponse])
def get_agent_messages(
    agent_id: int, unread_only: bool = False, db: Session = Depends(get_db)
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()

    if not agent:
        raise HTTPException(status_code=404, detail="Агент не найден")

    query = db.query(AgentMessage).filter(AgentMessage.recipient_agent_id == agent_id)

    if unread_only:
        query = query.filter(AgentMessage.read_at.is_(None))

    messages = query.order_by(AgentMessage.created_at.desc()).all()

    return messages


@app.get("/tasks/{task_id}/messages", response_model=list[AgentMessageResponse])
def get_task_messages(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    return (
        db.query(AgentMessage)
        .filter(AgentMessage.task_id == task_id)
        .order_by(AgentMessage.id.asc())
        .all()
    )


@app.patch("/messages/{message_id}/read")
def mark_message_as_read(message_id: int, db: Session = Depends(get_db)):
    message = db.query(AgentMessage).filter(AgentMessage.id == message_id).first()

    if not message:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")

    if message.read_at is None:
        message.read_at = datetime.now(timezone.utc)
        db.commit()

    return {"status": "read", "message_id": message.id, "read_at": message.read_at}


@app.post("/capabilities", response_model=AgentCapabilityResponse)
def create_capability(
    capability_data: AgentCapabilityCreate, db: Session = Depends(get_db)
):
    agent = db.query(Agent).filter(Agent.id == capability_data.agent_id).first()

    if not agent:
        raise HTTPException(status_code=404, detail="Агент не найден")

    existing = (
        db.query(AgentCapability)
        .filter(
            AgentCapability.agent_id == capability_data.agent_id,
            AgentCapability.skill_code == capability_data.skill_code,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=409, detail="Такая компетенция уже есть у агента"
        )

    capability = AgentCapability(
        agent_id=capability_data.agent_id,
        skill_code=capability_data.skill_code,
        skill_name=capability_data.skill_name,
        description=capability_data.description,
        level=capability_data.level,
    )

    db.add(capability)
    db.commit()
    db.refresh(capability)

    return capability


@app.get("/capabilities", response_model=list[AgentCapabilityResponse])
def get_capabilities(db: Session = Depends(get_db)):
    return (
        db.query(AgentCapability)
        .order_by(AgentCapability.agent_id, AgentCapability.id)
        .all()
    )


@app.get(
    "/agents/{agent_id}/capabilities", response_model=list[AgentCapabilityResponse]
)
def get_agent_capabilities(agent_id: int, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()

    if not agent:
        raise HTTPException(status_code=404, detail="Агент не найден")

    return db.query(AgentCapability).filter(AgentCapability.agent_id == agent_id).all()


@app.patch(
    "/capabilities/{capability_id}/level", response_model=AgentCapabilityResponse
)
def update_capability_level(
    capability_id: int, level: int = Query(ge=1, le=5), db: Session = Depends(get_db)
):
    capability = (
        db.query(AgentCapability).filter(AgentCapability.id == capability_id).first()
    )

    if not capability:
        raise HTTPException(status_code=404, detail="Компетенция не найдена")

    capability.level = level

    db.commit()
    db.refresh(capability)

    return capability


@app.get("/dispatcher/candidates")
def get_dispatcher_candidates(
    skill_code: str,
    min_level: int = Query(default=1, ge=1, le=5),
    db: Session = Depends(get_db),
):
    candidates = find_available_agents(
        db=db, skill_code=skill_code, min_level=min_level
    )

    return {
        "skill_code": skill_code,
        "min_level": min_level,
        "total": len(candidates),
        "candidates": candidates,
    }


@app.post("/assignments/{assignment_id}/execute")
async def execute_assignment(assignment_id: int, db: Session = Depends(get_db)):
    assignment = (
        db.query(TaskAssignment).filter(TaskAssignment.id == assignment_id).first()
    )

    if not assignment:
        raise HTTPException(status_code=404, detail="Назначение не найдено")

    if assignment.status not in ("pending", "revision_requested", "failed"):
        raise HTTPException(
            status_code=409,
            detail="Запустить можно только ожидающее, возвращённое на доработку или завершённое ошибкой назначение",
        )

    # Запоминаем, требуется ли доработка
    is_revision = assignment.status == "revision_requested"

    task = db.query(Task).filter(Task.id == assignment.task_id).first()

    agent = db.query(Agent).filter(Agent.id == assignment.agent_id).first()

    if not task or not agent:
        raise HTTPException(status_code=404, detail="Задача или сотрудник не найдены")

    try:
        # Получаем замечания директора до смены статуса
        feedback_message = None

        if is_revision:
            feedback_message = (
                db.query(AgentMessage)
                .filter(
                    AgentMessage.assignment_id == assignment.id,
                    AgentMessage.sender_agent_id == 1,
                    AgentMessage.recipient_agent_id == agent.id,
                    AgentMessage.message_type == "message",
                )
                .order_by(AgentMessage.id.desc())
                .first()
            )

        instructions = assignment.instructions or ""

        if feedback_message:
            instructions += (
                "\n\nЗАМЕЧАНИЯ ДИРЕКТОРА К ПРЕДЫДУЩЕМУ ОТЧЁТУ:\n"
                + feedback_message.content
                + "\n\nОбязательно учти эти замечания при доработке."
            )

        assignment.status = "in_progress"
        sync_agent_status(db, agent.id)
        db.commit()

        # Собираем результаты других сотрудников по этой задаче
        root_task = task

        if task.parent_task_id:
            parent = db.query(Task).filter(Task.id == task.parent_task_id).first()

            if parent:
                root_task = parent

        related_task_ids = [root_task.id]

        sibling_tasks = db.query(Task).filter(Task.parent_task_id == root_task.id).all()

        related_task_ids.extend(sibling.id for sibling in sibling_tasks)

        related_assignments = (
            db.query(TaskAssignment)
            .filter(
                TaskAssignment.task_id.in_(related_task_ids),
                TaskAssignment.id != assignment.id,
                TaskAssignment.status == "completed",
                TaskAssignment.result.isnot(None),
            )
            .all()
        )

        related_reports = ""

        for related in related_assignments:
            related_task = db.query(Task).filter(Task.id == related.task_id).first()

            related_agent = db.query(Agent).filter(Agent.id == related.agent_id).first()

            related_reports += (
                f"\n--- ОТЧЁТ СОТРУДНИКА "
                f"{related_agent.name if related_agent else related.agent_id} ---\n"
                f"Задача: "
                f"{related_task.title if related_task else related.task_id}\n"
                f"Результат:\n{related.result[:6000]}\n"
            )

        related_reports = related_reports[:15000]

        executor = AgentExecutor()

        result = await executor.execute(
            agent_role=agent.role,
            agent_name=agent.name,
            task_title=task.title,
            task_description=task.description,
            instructions=instructions,
            related_reports=related_reports,
        )

        return submit_assignment_result(
            assignment_id=assignment.id,
            result_data=AssignmentResultSubmit(
                sender_agent_id=agent.id, content=result
            ),
            db=db,
        )

    except Exception as e:
        db.rollback()

        assignment = (
            db.query(TaskAssignment).filter(TaskAssignment.id == assignment_id).first()
        )

        if assignment:
            assignment.status = "failed"
            sync_agent_status(db, assignment.agent_id)

            db.add(
                TaskEvent(
                    task_id=assignment.task_id,
                    event_type="execution_failed",
                    message=f"Ошибка выполнения назначения №{assignment.id}: {str(e)[:300]}",
                    actor="system",
                )
            )

            recalculate_task_status(db, assignment.task_id)
            db.commit()

        raise HTTPException(
            status_code=500, detail=f"Ошибка выполнения задания: {str(e)}"
        )


@app.get("/tools/web-search")
async def web_search_endpoint(q: str, limit: int = 5):
    if not q.strip():
        raise HTTPException(
            status_code=400, detail="Поисковый запрос не может быть пустым"
        )

    try:
        results = await search_web(query=q, limit=limit)

        return {"query": q, "count": len(results), "results": results}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Ошибка поискового сервиса: {str(e)[:300]}"
        )


@app.post("/tasks/{task_id}/dispatch")
def dispatch_task_endpoint(
    task_id: int, dispatch_data: TaskDispatchRequest, db: Session = Depends(get_db)
):
    return dispatch_task(
        db=db,
        task_id=task_id,
        skill_code=dispatch_data.skill_code,
        min_level=dispatch_data.min_level,
        instructions=dispatch_data.instructions,
    )
