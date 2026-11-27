import pytest

from app.services.github_developer import GitHubDeveloper
from app.services.github_repository import validate_repository_path


@pytest.mark.parametrize(
    "path",
    [
        "app/services/example.py",
        "app/services/secretary.py",
        "tests/test_example.py",
        "docs/notes.md",
        "requirements-dev.txt",
    ],
)
def test_validate_repository_path_allows_project_files(path):
    assert validate_repository_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "../.env",
        ".env",
        ".env.example",
        ".envrc",
        "app/secrets.py",
        "app/private_key.py",
        "data/ai_office.db",
        ".venv/pyvenv.cfg",
        ".git/config",
        "certificates/root.pem",
        "C:/Windows/win.ini",
        "outside/secret.txt",
    ],
)
def test_validate_repository_path_rejects_sensitive_or_outside_files(path):
    with pytest.raises(ValueError):
        validate_repository_path(path)


def test_developer_validates_requested_files_against_repository_tree():
    plan = {
        "files_to_read": ["app/main.py", "tests/test_main.py"],
        "implementation_plan": "Добавить маршрут и тест.",
    }
    assert GitHubDeveloper._validate_file_plan(
        plan,
        ["app/main.py", "tests/test_main.py", "docs/notes.md"],
    ) == ["app/main.py", "tests/test_main.py"]


def test_developer_allows_new_file_but_not_unread_existing_file():
    patch = {
        "summary": "Добавлен тест.",
        "files": [
            {
                "path": "tests/test_new_feature.py",
                "content": "def test_new_feature():\n    assert True\n",
            },
            {
                "path": "app/main.py",
                "content": "print('updated')\n",
            },
        ],
    }
    result = GitHubDeveloper._validate_changes(
        patch,
        readable_paths=["app/main.py"],
        available_paths=["app/main.py", "app/models.py"],
    )
    assert [item["path"] for item in result] == [
        "tests/test_new_feature.py",
        "app/main.py",
    ]

    unread_patch = {
        "files": [
            {
                "path": "app/models.py",
                "content": "class Model: pass\n",
            }
        ]
    }
    with pytest.raises(ValueError, match="непрочитанный файл"):
        GitHubDeveloper._validate_changes(
            unread_patch,
            readable_paths=["app/main.py"],
            available_paths=["app/main.py", "app/models.py"],
        )


def test_developer_json_parser_accepts_fenced_json():
    assert GitHubDeveloper._parse_json(
        '```json\n{"files_to_read":["app/main.py"]}\n```'
    ) == {"files_to_read": ["app/main.py"]}
