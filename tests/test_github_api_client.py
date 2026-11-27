import asyncio

import httpx

from app.services.github_repository import GitHubRepositoryClient


def test_github_client_creates_branch_commit_and_draft_pr():
    requests = []

    async def handler(request):
        requests.append((request.method, request.url.path, request.headers.get("Authorization")))

        if request.method == "GET" and request.url.path.endswith(
            "/git/ref/heads/ai-office-upgrade"
        ):
            return httpx.Response(
                200,
                json={"object": {"sha": "base-commit"}},
            )

        if request.method == "GET" and request.url.path.endswith(
            "/git/commits/base-commit"
        ):
            return httpx.Response(
                200,
                json={"tree": {"sha": "base-tree"}},
            )

        if request.method == "POST" and request.url.path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob-sha"})

        if request.method == "POST" and request.url.path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree-sha"})

        if request.method == "POST" and request.url.path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit"})

        if request.method == "POST" and request.url.path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": "refs/heads/ai-office/task-test"})

        if request.method == "POST" and request.url.path.endswith("/pulls"):
            return httpx.Response(
                201,
                json={
                    "number": 77,
                    "html_url": "https://github.com/Pentalgone/AI-Office/pull/77",
                },
            )

        return httpx.Response(404, json={"message": "unexpected request"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with GitHubRepositoryClient(
            token="test-token",
            transport=transport,
        ) as client:
            return await client.create_draft_change(
                branch_name="ai-office/task-1-assignment-2-abcd1234",
                commit_message="Add feature",
                pull_request_title="Add feature",
                pull_request_body="Draft PR",
                changes=[
                    {
                        "path": "tests/test_feature.py",
                        "content": "def test_feature():\n    assert True\n",
                    }
                ],
            )

    result = asyncio.run(run())

    assert result["pull_request_number"] == 77
    assert result["branch"] == "ai-office/task-1-assignment-2-abcd1234"
    assert result["files"] == ["tests/test_feature.py"]
    assert any(path.endswith("/git/refs") for _, path, _ in requests)
    assert any(path.endswith("/pulls") for _, path, _ in requests)
    assert all(auth == "Bearer test-token" for _, _, auth in requests)
