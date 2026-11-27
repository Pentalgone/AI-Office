import asyncio
import re

from app.services.github_repository import GitHubRepositoryClient


class GitHubTester:
    @staticmethod
    def extract_pull_request_number(text: str) -> int | None:
        if not isinstance(text, str):
            return None
        match = re.search(
            r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/(\d+)",
            text,
            flags=re.IGNORECASE,
        )
        return int(match.group(1)) if match else None

    async def inspect_pull_request(self, pull_request_number: int) -> str:
        async with GitHubRepositoryClient() as repository:
            pull_request = await repository.get_pull_request(pull_request_number)
            head = pull_request.get("head", {})
            commit_sha = head.get("sha")
            if not commit_sha:
                raise ValueError("У Pull Request не найден commit SHA.")

            checks = []
            for attempt in range(12):
                checks = await repository.get_commit_checks(commit_sha)
                if checks and all(
                    check.get("status") == "completed" for check in checks
                ):
                    break
                if attempt < 11:
                    await asyncio.sleep(5)

        if not checks:
            return (
                f"Pull Request #{pull_request_number}: проверки GitHub Actions "
                "пока не появились. Ручное тестирование не выполнялось."
            )

        lines = [
            f"## Проверка Pull Request #{pull_request_number}",
            f"**Состояние PR:** {pull_request.get('state', 'unknown')}",
            f"**Ветка:** {head.get('ref', 'unknown')}",
            f"**Commit:** {commit_sha[:12]}",
            "",
            "### Автоматические проверки",
        ]

        conclusions = []
        for check in checks:
            name = check.get("name", "Проверка")
            status = check.get("status", "unknown")
            conclusion = check.get("conclusion") or "ещё выполняется"
            conclusions.append(conclusion)
            url = check.get("html_url")
            line = f"- {name}: {status} / {conclusion}"
            if url:
                line += f" — {url}"
            lines.append(line)

        if any(value in {"failure", "cancelled", "timed_out", "action_required"} for value in conclusions):
            overall = "ОБНАРУЖЕНЫ ОШИБКИ"
        elif all(value == "success" for value in conclusions):
            overall = "ВСЕ АВТОМАТИЧЕСКИЕ ПРОВЕРКИ ПРОШЛИ"
        else:
            overall = "ПРОВЕРКИ ЕЩЁ НЕ ЗАВЕРШЕНЫ"

        lines.extend([
            "",
            f"**Итог:** {overall}.",
            "Это результат GitHub Actions; ручная проверка поведения приложения не выполнялась.",
        ])
        return "\n".join(lines)
