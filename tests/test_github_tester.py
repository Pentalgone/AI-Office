from app.services.github_tester import GitHubTester


def test_tester_extracts_github_pull_request_number():
    assert GitHubTester.extract_pull_request_number(
        "Проверь https://github.com/Pentalgone/AI-Office/pull/123"
    ) == 123


def test_tester_ignores_non_github_links():
    assert GitHubTester.extract_pull_request_number(
        "https://example.com/pull/123"
    ) is None


def test_tester_reports_actual_completed_ci_checks(monkeypatch):
    import asyncio

    import app.services.github_tester as tester_module

    class FakeRepository:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_pull_request(self, number):
            return {
                "state": "open",
                "head": {
                    "sha": "abcdef1234567890",
                    "ref": "ai-office/task-1",
                },
            }

        async def get_commit_checks(self, commit_sha):
            return [
                {
                    "name": "tests",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.com/Pentalgone/AI-Office/actions/runs/1",
                }
            ]

    monkeypatch.setattr(
        tester_module,
        "GitHubRepositoryClient",
        FakeRepository,
    )

    result = asyncio.run(
        tester_module.GitHubTester().inspect_pull_request(77)
    )

    assert "ВСЕ АВТОМАТИЧЕСКИЕ ПРОВЕРКИ ПРОШЛИ" in result
    assert "ручная проверка поведения приложения не выполнялась" in result
