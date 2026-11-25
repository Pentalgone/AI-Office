
from app.llm.base import BaseLLMProvider
from app.llm.gigachat import GigaChatClient


class LLMManager:
    """
    Управляет провайдерами и моделями AI-Office.
    """

    def __init__(self):
        self.providers: dict[str, BaseLLMProvider] = {}
        self.agent_models: dict[str, dict[str, str]] = {}

    def register_provider(
        self,
        name: str,
        provider: BaseLLMProvider,
    ):
        self.providers[name] = provider

    def assign_model(
        self,
        agent_role: str,
        provider_name: str,
        model_name: str,
    ):
        if provider_name not in self.providers:
            raise ValueError(
                f"Провайдер '{provider_name}' не зарегистрирован."
            )

        self.agent_models[agent_role] = {
            "provider": provider_name,
            "model": model_name,
        }

    async def chat(
        self,
        agent_role: str,
        message: str,
    ) -> str:
        configuration = self.agent_models.get(agent_role)

        if not configuration:
            raise ValueError(
                f"Для роли '{agent_role}' не назначена модель."
            )

        provider_name = configuration["provider"]
        model_name = configuration["model"]

        provider = self.providers[provider_name]

        return await provider.chat(
            message=message,
            model=model_name,
        )


def create_llm_manager() -> LLMManager:
    manager = LLMManager()
    manager.register_provider(name="gigachat", provider=GigaChatClient())

    roles = [
        "director",
        "analyst",
        "secretary",
        "developer",
        "tester"
    ]

    for role in roles:
        manager.assign_model(
            agent_role=role,
            provider_name="gigachat",
            model_name="GigaChat-3-Pro"
        )

    return manager
