
import json

from app.llm.manager import create_llm_manager


class AIDirector:
    def __init__(self):
        self.llm = create_llm_manager()

    async def create_plan(self, user_request: str, agents: list[dict]) -> dict:
        agents_info = json.dumps(
            agents,
            ensure_ascii=False,
            indent=2
        )

        prompt = f"""
Ты — AI-Директор компании AI-Office.

Проанализируй запрос пользователя, разбей работу на подзадачи
и подбери исполнителей из доступных сотрудников.

Сотрудники и их компетенции:
{agents_info}

Запрос пользователя:
{user_request}

Правила:
1. Создай понятный план.
2. Разбей работу на конкретные подзадачи.
3. Для каждой подзадачи выбери skill_code из списка сотрудников.
4. Не назначай Director исполнителем.
5. Не создавай лишних подзадач.
6. Если задача простая, создай одну подзадачу.

Верни исключительно JSON:

{{
    "title": "Название основной задачи",
    "description": "Описание основной задачи",
    "subtasks": [
        {{
            "title": "Название подзадачи",
            "description": "Что необходимо сделать",
            "skill_code": "код компетенции",
            "instructions": "Инструкции исполнителю"
        }}
    ]
}}
"""

        response = await self.llm.chat(
            agent_role="director",
            message=prompt
        )

        try:
            plan = json.loads(response)
        except json.JSONDecodeError:
            raise ValueError(
                "AI-Директор вернул некорректный JSON-план: "
                + response[:500]
            )

        if not isinstance(plan, dict) or not isinstance(
            plan.get("subtasks"), list
        ):
            raise ValueError("AI-Директор вернул план неверного формата.")

        available_skills = {
            agent["skill_code"] for agent in agents
        }

        for subtask in plan["subtasks"]:
            if subtask.get("skill_code") not in available_skills:
                raise ValueError(
                    f"AI-Директор выбрал неизвестную компетенцию: "
                    f"{subtask.get('skill_code')}"
                )

        return plan

    async def create_final_report(
        self,
        task_title: str,
        task_description: str,
        reports: list[dict]
    ) -> str:
        reports_info = json.dumps(
            reports,
            ensure_ascii=False,
            indent=2
        )

        prompt = f"""
Ты — AI-Директор компании AI-Office.
Твоя задача — подготовить итоговый отчёт для пользователя
на основе результатов работы подчинённых.

Основная задача:
{task_title}

Описание:
{task_description}

Принятые отчёты исполнителей:
{reports_info}

Правила:
1. Используй только сведения из переданных отчётов.
2. Объедини результаты в единый логичный документ.
3. Удали дублирование информации.
4. Сохраняй ссылки на источники, если они есть в отчётах.
5. Не придумывай факты, компании, ссылки и цифры.
6. Если отчёты противоречат друг другу, явно укажи это.
7. Отделяй подтверждённые сведения от предположений.
8. Укажи, какие вопросы остались без ответа.
9. В конце сформулируй общий вывод и рекомендации.
10. Если информации недостаточно, честно сообщи об этом.

Подготовь содержательный итоговый отчёт в Markdown.
Не возвращай JSON.
"""

        response = await self.llm.chat(
            agent_role="director",
            message=prompt
        )

        if not response or not response.strip():
            raise ValueError(
                "AI-Директор не сформировал итоговый отчёт."
            )

        return response.strip()
