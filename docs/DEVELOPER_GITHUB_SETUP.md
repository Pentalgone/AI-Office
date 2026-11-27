# Подключение AI-Developer к GitHub

Этот шаг понадобится после установки версии AI-Office с GitHub Developer integration. Пока не нужно отправлять токен в ChatGPT или вставлять его в GitHub.

## 1. Создать fine-grained token

В GitHub:
1. Открой Settings → Developer settings.
2. Открой Personal access tokens → Fine-grained tokens.
3. Нажми Generate new token.
4. Resource owner: `Pentalgone`.
5. Repository access: Only select repositories → `AI-Office`.
6. Установи минимальные разрешения:

| Permission | Access |
|---|---|
| Contents | Read and write |
| Pull requests | Read and write |
| Checks | Read-only |
| Actions | Read-only |
| Metadata | Read-only (обязательное базовое разрешение) |

Рекомендуется задать ограниченный срок действия токена. Не включай доступ ко всем репозиториям.

## 2. Добавить токен в локальный .env

Открой существующий файл `C:\AI-Office\.env` и добавь в конец:

```dotenv
GITHUB_TOKEN=твой_токен
GITHUB_REPOSITORY=Pentalgone/AI-Office
GITHUB_BASE_BRANCH=ai-office-upgrade
```

Не заменяй весь файл: в нём уже находятся настройки GigaChat и Yandex Search. Не загружай `.env` в GitHub и не отправляй токен в чат.

## 3. Что сможет делать Developer

- читать только разрешённые исходники, тесты и документацию;
- создавать отдельную ветку;
- записывать подготовленные изменения в GitHub;
- открывать Draft Pull Request в рабочую ветку;
- получать результаты GitHub Checks.

Developer не может автоматически объединить PR в рабочую или основную ветку. Секреты, локальная база и произвольные shell-команды ему недоступны.

## 4. Что проверим после подключения

1. Проверим, что токен имеет нужные права.
2. Поставим Developer небольшую тестовую задачу.
3. Проверим созданную ветку, commit и Draft PR.
4. Дождёмся GitHub Actions.
5. Поручим Tester проверить реальные статусы CI.
6. Только после проверки будем считать GitHub Developer подключённым.
