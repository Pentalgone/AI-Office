import pytest

from app.services.github_repository import (
    GitHubRepositoryClient,
    GitHubRepositoryError,
)


def test_github_repository_client_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GitHubRepositoryError, match="GITHUB_TOKEN"):
        GitHubRepositoryClient()
