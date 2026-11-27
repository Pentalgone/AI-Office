import base64
import os
import re
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

load_dotenv()


class GitHubRepositoryError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


ALLOWED_PREFIXES = ("app/", "tests/", "docs/")
ALLOWED_ROOT_FILES = {
    "requirements.txt",
    "requirements-dev.txt",
    "README.md",
}
BLOCKED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "data",
    "certificates",
}
BLOCKED_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
}
BLOCKED_NAME_FRAGMENTS = (
    "secret",
    "credential",
    "password",
    "private_key",
)


def validate_repository_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Путь к файлу не должен быть пустым.")

    normalized = path.strip().replace("\\", "/")
    pure_path = PurePosixPath(normalized)
    raw_parts = normalized.split("/")

    if (
        len(normalized) > 240
        or any(ord(character) < 32 for character in normalized)
        or pure_path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ValueError("Недопустимый путь к файлу проекта.")

    if any(part.casefold() in BLOCKED_PARTS for part in pure_path.parts):
        raise ValueError("Доступ к служебным каталогам запрещён.")

    filename = pure_path.name.casefold()
    if (
        filename in {".env", ".envrc"}
        or filename.startswith(".env.")
        or any(fragment in filename for fragment in BLOCKED_NAME_FRAGMENTS)
    ):
        raise ValueError("Developer не имеет доступа к файлам окружения и секретам.")
    if any(filename.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        raise ValueError("Доступ к бинарным данным и ключам запрещён.")

    allowed = (
        normalized.startswith(ALLOWED_PREFIXES)
        or normalized in ALLOWED_ROOT_FILES
    )
    if not allowed:
        raise ValueError("Файл находится вне разрешённой области проекта.")

    return normalized


class GitHubRepositoryClient:
    """Минимальный GitHub API-клиент для безопасной работы Developer."""

    def __init__(
        self,
        token: str | None = None,
        repository: str | None = None,
        base_branch: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.repository = repository or os.getenv(
            "GITHUB_REPOSITORY", "Pentalgone/AI-Office"
        )
        self.base_branch = base_branch or os.getenv(
            "GITHUB_BASE_BRANCH", "ai-office-upgrade"
        )

        if not self.token:
            raise GitHubRepositoryError(
                "Не настроен GITHUB_TOKEN. Добавьте токен GitHub с доступом "
                "к содержимому репозитория и pull requests в локальный .env."
            )

        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise GitHubRepositoryError("Некорректное значение GITHUB_REPOSITORY.")

        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "AI-Office-Developer",
            },
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self._client.aclose()

    async def _request(self, method: str, endpoint: str, **kwargs):
        response = await self._client.request(method, endpoint, **kwargs)
        if response.status_code >= 400:
            detail = response.text[:500]
            raise GitHubRepositoryError(
                f"GitHub API вернул HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        return response.json()

    async def get_branch_ref(self, branch: str) -> dict:
        return await self._request(
            "GET",
            f"/repos/{self.repository}/git/ref/heads/{quote(branch, safe='/')}",
        )

    async def list_files(self, ref: str | None = None) -> list[str]:
        branch_ref = await self.get_branch_ref(ref or self.base_branch)
        commit = await self._request(
            "GET",
            f"/repos/{self.repository}/git/commits/{branch_ref['object']['sha']}",
        )
        tree = await self._request(
            "GET",
            f"/repos/{self.repository}/git/trees/{commit['tree']['sha']}",
            params={"recursive": "1"},
        )

        paths = []
        for item in tree.get("tree", []):
            if item.get("type") != "blob" or item.get("mode") not in {"100644", "100755"}:
                continue
            try:
                paths.append(validate_repository_path(item.get("path", "")))
            except ValueError:
                continue
        return sorted(set(paths))

    async def read_file(self, path: str, ref: str | None = None) -> str:
        safe_path = validate_repository_path(path)
        result = await self._request(
            "GET",
            f"/repos/{self.repository}/contents/{quote(safe_path, safe='/')}",
            params={"ref": ref or self.base_branch},
        )

        if result.get("type") != "file" or result.get("encoding") != "base64":
            raise GitHubRepositoryError(
                f"GitHub не вернул текстовый файл: {safe_path}"
            )

        try:
            content = base64.b64decode(result.get("content", "")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise GitHubRepositoryError(
                f"Файл не является корректным UTF-8 текстом: {safe_path}"
            ) from None

        if len(content) > 60000:
            raise GitHubRepositoryError(
                f"Файл слишком большой для безопасной передачи модели: {safe_path}"
            )
        return content

    async def get_pull_request(self, number: int) -> dict:
        return await self._request(
            "GET",
            f"/repos/{self.repository}/pulls/{number}",
        )

    async def get_commit_checks(self, commit_sha: str) -> list[dict]:
        result = await self._request(
            "GET",
            f"/repos/{self.repository}/commits/{commit_sha}/check-runs",
            params={"per_page": 100},
        )
        return result.get("check_runs", [])

    async def create_draft_change(
        self,
        branch_name: str,
        commit_message: str,
        pull_request_title: str,
        pull_request_body: str,
        changes: list[dict],
    ) -> dict:
        if not re.fullmatch(r"ai-office/[a-zA-Z0-9._/-]{1,90}", branch_name):
            raise ValueError("Недопустимое имя рабочей ветки Developer.")

        if not changes or len(changes) > 10:
            raise ValueError("Пакет изменений должен содержать от 1 до 10 файлов.")

        validated = []
        total_size = 0
        seen_paths = set()

        for item in changes:
            path = validate_repository_path(item.get("path"))
            content = item.get("content")
            if not isinstance(content, str):
                raise ValueError(f"Содержимое файла должно быть строкой: {path}")
            if len(content) > 60000:
                raise ValueError(f"Файл превышает лимит размера: {path}")
            if path in seen_paths:
                raise ValueError(f"Повторяющийся путь в изменениях: {path}")
            seen_paths.add(path)
            total_size += len(content)
            validated.append((path, content))

        if total_size > 200000:
            raise ValueError("Общий размер изменений превышает лимит 200 000 символов.")

        base_ref = await self.get_branch_ref(self.base_branch)
        base_sha = base_ref["object"]["sha"]
        base_commit = await self._request(
            "GET",
            f"/repos/{self.repository}/git/commits/{base_sha}",
        )
        base_tree_sha = base_commit["tree"]["sha"]

        tree_items = []
        for path, content in validated:
            blob = await self._request(
                "POST",
                f"/repos/{self.repository}/git/blobs",
                json={"content": content, "encoding": "utf-8"},
            )
            tree_items.append({
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            })

        tree = await self._request(
            "POST",
            f"/repos/{self.repository}/git/trees",
            json={
                "base_tree": base_tree_sha,
                "tree": tree_items,
            },
        )
        commit = await self._request(
            "POST",
            f"/repos/{self.repository}/git/commits",
            json={
                "message": commit_message[:200],
                "tree": tree["sha"],
                "parents": [base_sha],
            },
        )
        await self._request(
            "POST",
            f"/repos/{self.repository}/git/refs",
            json={
                "ref": f"refs/heads/{branch_name}",
                "sha": commit["sha"],
            },
        )

        pull_request = await self._request(
            "POST",
            f"/repos/{self.repository}/pulls",
            json={
                "title": pull_request_title[:200],
                "head": branch_name,
                "base": self.base_branch,
                "body": pull_request_body,
                "draft": True,
            },
        )

        return {
            "branch": branch_name,
            "commit_sha": commit["sha"],
            "pull_request_number": pull_request["number"],
            "pull_request_url": pull_request["html_url"],
            "files": [path for path, _ in validated],
        }
