
import os
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from app.llm.base import BaseLLMProvider


# Загружаем .env из корня проекта
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://api.giga.chat/v1"


class GigaChatError(Exception):
    """Ошибка взаимодействия с GigaChat API."""


class GigaChatClient(BaseLLMProvider):
    def __init__(self):
        self.credentials = os.getenv("GIGACHAT_CREDENTIALS")

        if not self.credentials:
            raise GigaChatError(
                "Не найдена переменная GIGACHAT_CREDENTIALS в .env"
            )

        self.access_token = None
        self.token_expires_at = 0

    async def _get_access_token(self):
        # Используем действующий токен, если он ещё не истекает
        if (
            self.access_token
            and time.time() < self.token_expires_at - 60
        ):
            return self.access_token

        headers = {
            "Authorization": f"Basic {self.credentials}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        data = {
            "scope": "GIGACHAT_API_PERS"
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    OAUTH_URL,
                    headers=headers,
                    data=data,
                )

                response.raise_for_status()
                result = response.json()

        except httpx.HTTPError as error:
            raise GigaChatError(
                f"Ошибка получения токена GigaChat: {type(error).__name__}: {error}"
            ) from None

        except ValueError:
            raise GigaChatError(
                "GigaChat вернул некорректный ответ при авторизации."
            ) from None

        self.access_token = result["access_token"]
        self.token_expires_at = result["expires_at"] / 1000

        return self.access_token

    async def chat(self, message: str, model: str):
        if not message.strip():
            raise ValueError("Сообщение не должно быть пустым.")

        token = await self._get_access_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": message,
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{API_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                )

                response.raise_for_status()
                result = response.json()

        except httpx.HTTPStatusError as error:
            raise GigaChatError(
                f"GigaChat API вернул HTTP {error.response.status_code}."
            ) from None

        except httpx.HTTPError as error:
            raise GigaChatError(
                f"Ошибка запроса к GigaChat: {type(error).__name__}"
            ) from None

        except ValueError:
            raise GigaChatError(
                "GigaChat вернул некорректный JSON."
            ) from None

        try:
            return result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise GigaChatError(
                "Не удалось получить текст ответа GigaChat."
            ) from None
