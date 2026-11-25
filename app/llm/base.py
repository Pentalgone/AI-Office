
from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """
    Базовый интерфейс для всех LLM-провайдеров AI-Office.
    """

    @abstractmethod
    async def chat(
        self,
        message: str,
        model: str,
    ) -> str:
        """
        Отправляет сообщение языковой модели
        и возвращает текстовый ответ.
        """
        pass
