from app.services.github_tester import GitHubTester


def test_tester_extracts_github_pull_request_number():
    assert GitHubTester.extract_pull_request_number(
        "Проверь https://github.com/Pentalgone/AI-Office/pull/123"
    ) == 123


def test_tester_ignores_non_github_links():
    assert GitHubTester.extract_pull_request_number(
        "https://example.com/pull/123"
    ) is None
