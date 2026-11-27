# AI-Office

Локальная мультиагентная система: AI-Директор планирует задачи, Dispatcher распределяет работу между сотрудниками, агенты выполняют поручения, Director проверяет отчёты, а Developer готовит изменения проекта через GitHub Pull Request.

## Текущее состояние

- Backend: FastAPI + SQLAlchemy + SQLite.
- LLM: GigaChat через общий LLM Manager.
- Поиск: Yandex Search API.
- Агентские роли: Director, Secretary, Analyst, Developer, Tester.
- Есть иерархия задач, назначения, сообщения и журнал событий.
- Есть фоновая очередь TaskRun с восстановлением незавершённых запусков после перезапуска.
- Developer может готовить Draft PR через GitHub API; для этого нужен отдельный fine-grained token.
- UI Pixel Office пока не реализован.

Подробный статус: [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md).  
План MVP: [docs/MVP_EXECUTION_PLAN.md](docs/MVP_EXECUTION_PLAN.md).

## Установка на Windows

Требуется Python 3.14 и Git.

Из PowerShell в папке проекта:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Если виртуального окружения ещё нет:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Создай `.env` на основе `.env.example` только если локального `.env` ещё нет. Если он уже существует, не перезаписывай его — добавь недостающие переменные вручную.

Обязательная переменная текущего LLM-провайдера:
- `GIGACHAT_CREDENTIALS`

Для веб-поиска Analyst:
- `YANDEX_SEARCH_API_KEY`
- `YANDEX_FOLDER_ID`

Для Developer:
- `GITHUB_TOKEN`
- `GITHUB_REPOSITORY=Pentalgone/AI-Office`
- `GITHUB_BASE_BRANCH=ai-office-upgrade`

Инструкция по GitHub token: [DEVELOPER_GITHUB_SETUP.md](docs/DEVELOPER_GITHUB_SETUP.md).

## Запуск

```powershell
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Swagger: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

При старте сервер создаёт отсутствующие таблицы и выполняет безопасные аддитивные миграции SQLite. Пользовательская БД хранится в `data/ai_office.db`; не удаляй её при обновлении.

## Основной рабочий цикл через Swagger

1. Открой `POST /ai/tasks/execute` и передай запрос:

```json
{
  "request": "Подготовь анализ рынка и предложи план действий"
}
```

2. Из ответа возьми `task_id`.
3. Вызови `POST /ai/tasks/{task_id}/run`. Этот маршрут ставит запуск в фоновую очередь и возвращает `run_id`.
4. Проверяй состояние через `GET /ai/runs/{run_id}`.
5. При статусе `completed` смотри `result_summary`; при `needs_attention` изучи `error` и результат запуска.

Фоновый worker работает последовательно. Параллельное выполнение независимых подзадач находится в плане дальнейшей разработки.

## Безопасность и данные

- Не добавляй `.env`, GitHub token, базу данных и личные данные в Git.
- Не отправляй GitHub token в чат.
- Developer может менять только разрешённые исходники, тесты, документацию и отдельные корневые файлы.
- Developer создаёт Draft PR; автоматического merge нет.
- Не перезаписывай `.env`, `data/ai_office.db` или `.venv` при обновлении проекта.

## Проверки разработчика

Установка dev-зависимостей:

```powershell
pip install -r requirements-dev.txt
python -m compileall -q app tests
python -m pytest -q
```

GitHub Actions выполняет те же проверки при push и pull request.
