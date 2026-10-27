# NexaCRM Support AI

Публичное демо RAG-помощника по 15 Markdown-документам из `source/`. Чат отвечает со ссылками на фрагменты документации; пользователь может подтвердить создание заявки. Диалог хранится в Streamlit-сессии, заявки — в SQLite. Индекс знаний — локальный Qdrant. Не вводите реальные персональные данные и секреты в демо.

## Локальный запуск

Нужен Python 3.12 и [uv](https://docs.astral.sh/uv/getting-started/installation/). Из корня проекта:

```powershell
uv sync --locked
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Заполните `OPENAI_API_KEY` в `.env`, затем:

```powershell
uv run python ingest.py --dry-run
uv run python ingest.py
uv run streamlit run app.py --server.address=127.0.0.1
```

Публичный интерфейс откроется на `http://127.0.0.1:8501`. Для локальной проверки заявок отдельно запустите `uv run streamlit run admin.py --server.address=127.0.0.1 --server.port=8502 --server.baseUrlPath=admin`. На сервере административный интерфейс доступен только через Caddy с HTTP Basic Auth.

Перед повторной индексацией остановите публичный Streamlit-процесс: встроенный Qdrant блокирует каталог открытого индекса. При деплое новый индекс строится в отдельном каталоге выпуска, пока прежняя версия работает.

## Проверки

```powershell
uv run python -m pytest -q
uv run python ingest.py --dry-run
uv run python -m nexacrm.eval_cases
```

`--dry-run` не вызывает OpenAI и не меняет индекс. Индексация передаёт тексты документации в OpenAI Embeddings API. Публичный чат передаёт вопрос, историю и найденные фрагменты в OpenAI. Повторные попытки после ошибки тоже входят в общий лимит 100 принятых вопросов за календарные сутки по московскому времени: это ограничивает возможные расходы.

## Production: uai-dbezz.ru

- `https://uai-dbezz.ru/` — публичный Streamlit-процесс на `127.0.0.1:8501`.
- `https://uai-dbezz.ru/admin/` — отдельный процесс на `127.0.0.1:8502`; Caddy требует пароль на всём маршруте, включая WebSocket.
- `/srv/nexacrm/releases/<SHA>` — неизменяемые выпуски с отдельным индексом Qdrant; `/srv/nexacrm/current` указывает на активный выпуск.
- `/srv/nexacrm/shared/tickets.sqlite3` — общая SQLite-база заявок и суточного лимита; `/srv/nexacrm/backups` — семь ежедневных копий.
- `/etc/nexacrm/production.env` содержит API-ключ и настройки, `/etc/nexacrm/admin.hash` — хеш пароля. Эти файлы не попадают в Git.

### Первый запуск сервера

1. Через консоль Timeweb проверьте SSH-отпечаток командой `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. Сравните с отпечатком, полученным с локальной машины, **до** передачи root-пароля. Проверьте `cat /etc/os-release`, `free -h`, `df -h /`, наличие systemd и свободные порты 80/443.
2. Загрузите каталог `deploy/` на сервер и запустите `bootstrap.sh --prepare` от root из корня проекта. Он установит uv и Caddy из официальных источников, создаст пользователя `nexacrm` и каталоги данных.
3. Установите на сервере `/etc/nexacrm/production.env` с содержимым `OPENAI_API_KEY=<текущий ключ>`, `TICKETS_PATH=/srv/nexacrm/shared/tickets.sqlite3`, `DAILY_QUESTION_LIMIT=100`. Создайте отдельный сильный пароль администратора, получите его хеш через `caddy hash-password` и сохраните хеш в `/etc/nexacrm/admin.hash`. Запустите `bootstrap.sh` без аргументов: он установит сервисы и выставит права на секреты. Пароль передайте владельцу отдельно от Git. Существующий вход root по паролю сохранится.
4. Создайте отдельный ключ Ed25519 для GitHub Actions и добавьте его открытую часть в `/home/nexacrm/.ssh/authorized_keys`. В репозитории GitHub → Settings → Secrets and variables → Actions добавьте три repository secrets: `DEPLOY_HOST` (IPv4 сервера), `DEPLOY_SSH_KEY` (закрытый ключ), `DEPLOY_KNOWN_HOSTS` (проверенная строка host key). Не копируйте локальный `.env` или `ssh_creds.txt` в репозиторий.
5. После настройки DNS и секретов влейте подготовленный PR в `master`. Workflow `.github/workflows/deploy.yml` проверит тесты и сухую индексацию, затем передаст серверу SHA коммита. Скрипт `deploy/deploy.sh` скачает именно его, создаст индекс, переключит выпуск и проверит оба процесса. При неуспехе он восстановит предыдущий выпуск и пометит workflow ошибкой.

### DNS в Timeweb Cloud

Откройте «Домены и SSL» → `uai-dbezz.ru`. Если NS уже принадлежат Timeweb, переходите сразу во вкладку DNS. Если домен не делегирован на Timeweb, в настройках NS укажите `ns1.timeweb.ru`, `ns2.timeweb.ru`, `ns3.timeweb.org`, `ns4.timeweb.org` и дождитесь делегирования. Во вкладке DNS добавьте или измените **A-запись основного домена** на IPv4 сервера. Запись AAAA добавляйте только при наличии настроенного IPv6. Отдельная запись для `admin` не требуется. Проверяйте `NS` и `A` до запуска автоматического HTTPS. [Инструкция Timeweb](https://timeweb.cloud/docs/domains/dns-records-management).

### Эксплуатация

- Сверить выпуск: `git -C /srv/nexacrm/current rev-parse HEAD` на сервере и SHA удалённой `master` в GitHub.
- Состояние: `systemctl status nexacrm-public nexacrm-admin caddy nexacrm-backup.timer` и `journalctl -u nexacrm-public -u nexacrm-admin -n 100`.
- Ручное восстановление: направить `/srv/nexacrm/current` на прежний каталог в `releases/`, затем перезапустить оба сервиса. SQLite при переключении выпуска не меняется.
- Копии SQLite создаются ежедневно в 03:00 по Москве. Они находятся на этом же сервере; при потере машины эти копии тоже будут потеряны.

Это демо, а не полноценная система поддержки с реальными персональными данными. Веб-сервер публикует только порты 80/443; оба Streamlit-процесса слушают loopback. Вход root по паролю сохранён по решению владельца сервера.
