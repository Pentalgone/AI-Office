import re

from app.llm.manager import create_llm_manager
from app.services.web_search import search_web, read_urls


class AgentExecutor:
    def __init__(self):
        self.llm = create_llm_manager()

    async def execute(
        self,
        agent_role: str,
        agent_name: str,
        task_title: str,
        task_description: str | None,
        instructions: str | None,
        related_reports: str | None = None,
    ) -> str:

        search_results = []
        direct_results = []

        if agent_role.lower() == "analyst":

            # 1. Извлекаем ссылки из отчётов других сотрудников
            urls = re.findall(r"https?://\S+", related_reports or "")

            urls = list(dict.fromkeys(url.rstrip(".,;:!?)]}") for url in urls))

            # 2. Читаем найденные ссылки напрямую
            if urls:
                direct_results = await read_urls(urls)

            # 3. Дополнительно ищем информацию в интернете
            query_parts = [task_title or "", task_description or "", instructions or ""]

            query = " ".join(" ".join(query_parts).split())
            query = query[:150]

            if query:
                search_results = await search_web(query=query, limit=10)

        # 4. Объединяем результаты прямого чтения и поиска
        all_results = []

        for item in direct_results:
            all_results.append({**item, "source_origin": "Ссылка из отчёта сотрудника"})

        for item in search_results:
            all_results.append(
                {**item, "source_origin": "Результат дополнительного поиска"}
            )

        # 5. Формируем контекст источников
        search_context = ""

        if all_results:
            search_context = "\n\nИСТОЧНИКИ ДЛЯ АНАЛИЗА:\n"

            for index, item in enumerate(all_results, 1):
                search_context += (
                    f"\n--- ИСТОЧНИК №{index} ---\n"
                    f"Тип источника: {item.get('source_origin', '')}\n"
                    f"Заголовок: {item.get('title', '')}\n"
                    f"Заголовок страницы: {item.get('page_title', '')}\n"
                    f"Ссылка: {item.get('final_url') or item.get('url', '')}\n"
                    f"Статус чтения: {item.get('fetch_status', 'unknown')}\n"
                    f"Содержимое страницы:\n"
                    f"{(item.get('content') or 'Содержимое не получено')[:7000]}\n"
                )
        else:
            search_context = "\n\nИсточники для анализа не найдены."

        prompt = f"""
Ты — сотрудник AI-Office.

Имя: {agent_name}
Должность: {agent_role}

ЗАДАЧА:
{task_title}

ОПИСАНИЕ:
{task_description or "Не указано"}

ИНСТРУКЦИИ РУКОВОДИТЕЛЯ:
{instructions or "Выполни задачу самостоятельно."}

{search_context}

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ДОСТОВЕРНОСТИ:

1. Для аналитических задач используй переданные источники.
2. Не добавляй компании, публикации и продукты,
   которых нет в источниках.
3. Не используй собственные знания модели как подтверждение фактов.
4. Не приписывай источникам сведения, которых в них нет.
5. Не утверждай, что изучил страницу, если её содержимое не получено.
6. Если страница недоступна, прямо укажи это.
7. Каждое фактическое утверждение сопровождай ссылкой на источник.
8. Не выдавай предположения за подтверждённые сведения.
9. Если источники противоречат друг другу, укажи противоречие.
10. Лучше указать мало подтверждённых сведений,
    чем заполнить отчёт предположениями.

ФОРМАТ ОТЧЁТА:

## 1. Выполненная работа

Кратко опиши фактически выполненные действия.

## 2. Сводная таблица

| Название | Ссылка | Подтверждённая информация | Результат проверки |

## 3. Подтверждённые сведения

Перечисли конкретные факты, подтверждённые содержимым источников.

## 4. Что не удалось установить

Укажи отсутствующие сведения и недоступные страницы.

## 5. Выводы

Сделай выводы только на основании подтверждённых фактов.

Не добавляй вымышленные компании, публикации, ссылки и сведения.

КОНТЕКСТ И РЕЗУЛЬТАТЫ РАБОТЫ ДРУГИХ СОТРУДНИКОВ:
{related_reports or "Другие сотрудники пока не предоставили результаты."}
"""

        response = await self.llm.chat(agent_role=agent_role.lower(), message=prompt)

        return response
