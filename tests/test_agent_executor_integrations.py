import asyncio

import app.services.agent_executor as executor_module
from app.services.agent_executor import AgentExecutor


def test_developer_role_uses_github_developer(monkeypatch):
    captured = {}

    class FakeDeveloper:
        def __init__(self, llm):
            captured["llm"] = llm

        async def execute(self, **kwargs):
            captured["kwargs"] = kwargs
            return "Draft PR создан."

    monkeypatch.setattr(
        "app.services.github_developer.GitHubDeveloper",
        FakeDeveloper,
    )

    executor = AgentExecutor.__new__(AgentExecutor)
    executor.llm = object()

    result = asyncio.run(
        executor.execute(
            agent_role="Developer",
            agent_name="Developer",
            task_title="Добавить функцию",
            task_description="Описание",
            instructions="Реализовать функцию",
            task_id=10,
            assignment_id=20,
        )
    )

    assert result == "Draft PR создан."
    assert captured["kwargs"]["task_id"] == 10
    assert captured["kwargs"]["assignment_id"] == 20


def test_tester_role_reads_github_checks_when_pr_is_provided(monkeypatch):
    captured = {}

    class FakeTester:
        @staticmethod
        def extract_pull_request_number(text):
            return 42 if "pull/42" in text else None

        async def inspect_pull_request(self, number):
            captured["number"] = number
            return "CI checks passed."

    monkeypatch.setattr(
        "app.services.github_tester.GitHubTester",
        FakeTester,
    )

    executor = AgentExecutor.__new__(AgentExecutor)
    executor.llm = object()

    result = asyncio.run(
        executor.execute(
            agent_role="tester",
            agent_name="Tester",
            task_title="Проверить PR",
            task_description="https://github.com/Pentalgone/AI-Office/pull/42",
            instructions=None,
        )
    )

    assert result == "CI checks passed."
    assert captured["number"] == 42
