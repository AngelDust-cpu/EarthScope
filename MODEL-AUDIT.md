# Аудит моделей и маршрутизации

> Исторический отчёт. Актуальное перераспределение и новые проверки:
> [MODEL-ROUTING.md](MODEL-ROUTING.md).

Дата: 2026-11-14. Область: среда OpenCode, без выбора/реализации NASA challenge.

## Что было до аудита

Найдены `opencode.json`, `AGENTS.md` и восемь файлов
`.opencode/agents/{orchestrator,backend,data,frontend,research,science,qa,pitch}.md`.
Глобальный project default: `opencode/deepseek-v4.1-flash-free`.
Только `qa.md` задавал `model: opencode/inkling-2-small-free`.
Остальные семь файлов не задавали модель. Для специалистов это означало
наследование модели родительской сессии, а НЕ гарантированный DeepSeek.
Предпочтения Codex/Gemini/Qwen/GPT-OSS/Particle из AGENTS.md не были настройками.
Текущая сессия аудита работает на GPT-6.1 Sol Fast; изменение файлов не переключает её.

## Каталог и подключения

Проверены встроенный каталог `opencode.models` с `all: true`, CLI 2.2.7
`models --standalone`, `auth list --standalone`, `debug agents --standalone`.
CLI показывает сохранённые подключения OpenAI, Google (две записи),
Groq, NVIDIA, OpenCode Console и OpenRouter. Авторизации не изменялись.
Наличие сохранённого подключения или модели в каталоге не гарантирует успешный вызов.

Старое утверждение README о 12 моделях и отсутствии подключений устарело.
Каталог встроенных инструментов и набор маршрутов локального CLI отличаются:
например, `openai/gpt-5.3-codex` есть во встроенном каталоге, но безопасный
CLI-тест вернул `provider.no-route: Model unavailable` до получения ответа модели.

## Маршрутизация после аудита

Сокращение D = `opencode/deepseek-v4.1-flash-free`.
Fallback здесь — явно закреплённая временная замена или ручной резерв,
а не выдуманный параметр автоматического переключения OpenCode.

| Агент / роль | До | Желаемая модель (реальный ID) | Текущая настройка | Доступность цели / fallback |
|---|---|---|---|---|
| Orchestrator / координация | Без model | `openai/gpt-5.3-codex` | D | CLI: no-route. Другой каталоговый маршрут `opencode/gpt-5.3-codex` платный, не вызывался. Временно D |
| Backend / API | Наследование | D | D | Успешный бесплатный вызов; ручной резерв Inkling |
| Data / pipelines | Наследование | D | D | Успешный бесплатный вызов; ручной резерв Inkling |
| Frontend / UI | Наследование | `groq/qwen/qwen3.8-27b` | D | Каталог: есть, ненулевой тариф; бесплатная квота не подтверждена, вызов пропущен. Временно D |
| Research / источники | Наследование | `google/gemini-3.8-flash` | D | Каталог: есть, ненулевой тариф; бесплатная квота не подтверждена, вызов пропущен. Временно D |
| Science / методы | Наследование | `groq/openai/gpt-oss-120b` | `nvidia/openai/gpt-oss-20b` | NVIDIA 20B: успешный бесплатный вызов; Groq 120B: бесплатная квота не подтверждена. Ручной резерв D |
| QA / проверка | Inkling | `opencode/inkling-2-small-free` | `opencode/inkling-2-small-free` | Успешный бесплатный вызов; ручной резерв D |
| Pitch / документация | Наследование | `openrouter/particle-ai/particle-1:free` | `openrouter/particle-ai/particle-1:free` | Успешный бесплатный вызов; ручной резерв D |

Все бесплатные резервы ограничены текущей доступностью провайдера.
Отказ резерва не разрешает автоматически переходить на платный маршрут.

## Тесты и фактическая модель

Модель взята из `model.providerID` и `model.id` assistant-сообщений API,
а не из самоописания агента. Стоимость — поле `cost` OpenCode,
не независимая проверка счёта провайдера.

| Агент | Сессия | Фактическая модель | Результат | cost |
|---|---|---|---|---|
| Orchestrator, целевой маршрут | `ses_e27db38b1ffeC49n2FEPBaed5z` | Ответа модели нет | `provider.no-route` для `openai/gpt-5.3-codex` | — |
| Orchestrator, fallback | `ses_e27db0202ffeln3r5wciHE5VsN` | D | `ORCHESTRATOR-LIVE OK` | 0 |
| Backend | `ses_e27db7a3cffe0rJ0jK5CdqTiXe` | D | `BACKEND-LIVE 400` | 0 |
| Data | `ses_e27db7a38ffeFaYFXMBz6c95XA` | D | `DATA-LIVE 2000 m` | 0 |
| Frontend | `ses_e27db060dfferCql6sm12mS1ih` | D | `FRONTEND-LIVE button` | 0 |
| Research | `ses_e27db060bffeXDFwDfGKp7joW5` | D | Верный ответ NO, но нарушен точный формат: добавлены пояснения | 0 |
| QA | `ses_e27db7a14ffeb8UKnvtSRjh0vM` | `opencode/inkling-2-small-free` | `QA-LIVE FAIL` — ожидаемая оценка неверного 2+2=5, не ошибка вызова | 0 |
| Pitch | `ses_e27db7a10ffeD2PXEETkAeKsuS` | `openrouter/particle-ai/particle-1:free` | `PITCH-LIVE DRAFT` | 0 |
| Science | `ses_e27db7a16ffeAdprpUAlS0O98s` | `nvidia/openai/gpt-oss-20b` | `SCIENCE-LIVE 5 m/s` | 0 |

Backend/Data/QA/Pitch сначала проверялись с явным override инструмента.
Frontend/Research вызваны без override после сохранения `model`.
Orchestrator fallback проверен CLI без `--model`.
Science проверен с явным override; после получения ответа и проверки API
этот же ID закреплён в science.md. Все семь специалистов завершили вызовы.
Тестовые задания не разрешали инструменты, изменения файлов и делегирование.

Финальная проверка: `scripts/Test-ModelRouting.ps1` проверяет восемь
разрешённых OpenCode назначений model, режимы и семь разрешений делегирования.
На этой машине запуск .ps1 напрямую заблокирован Execution Policy.
Запуск через отдельный процесс не меняет постоянную политику:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-ModelRouting.ps1
```

Результат: PASS для всех восьми агентов и семи разрешений.
`opencode.json` оставлен без изменений; обновлены семь файлов агентов
(QA уже имел нужную настройку), README, этот отчёт и проверочный скрипт.

## Что нужно для целевой схемы

- **Codex:** восстановить доступ к Codex в уже существующем OpenAI-подключении
  (без смены основного аккаунта) либо отдельно разрешить платный маршрут
  `opencode/gpt-5.3-codex`. Никакого нового аккаунта или плагина не требуется.
- **Qwen:** подтвердить бесплатную квоту уже подключённого Groq и успешный запрос
  к `groq/qwen/qwen3.8-27b`.
- **Gemini:** подтвердить бесплатную квоту уже подключённого Google и запрос
  к доступной Gemini Flash. Две сохранённые записи не переключались.
- **GPT-OSS 120B:** подтвердить бесплатную квоту Groq; бесплатный NVIDIA 20B —
  другая размерность модели, не эквивалентная проверка 120B.
- DeepSeek, Inkling и Particle free уже вернули ответы.

Не включались платные сервисы, плагины, мультиаккаунт, публичное размещение
или отправка заявки. Ключи и параметры авторизации не читались и не публиковались.

## Воспроизведение проверки

Из корня проекта, PowerShell:

```powershell
$oc = '.\.tools\opencode\opencode.exe'
& $oc auth list --standalone
& $oc models --standalone
(& $oc debug agents --standalone | ConvertFrom-Json) |
  Where-Object id -in @('orchestrator','backend','data','frontend','research','science','qa','pitch') |
  Select-Object id,mode,model

# Только метаданные уже завершённого вызова:
$id = 'ses_e27db7a3cffe0rJ0jK5CdqTiXe'
(& $oc api get "/api/session/$id/message" | ConvertFrom-Json).data |
  Where-Object type -eq assistant |
  Select-Object agent,model,finish,cost
```

Настройки обновляются без перезапуска OpenCode; модель существующей primary-сессии
хранится отдельно. Выбор агента сам по себе её не меняет.
Прежде чем переключать primary на Codex, необходим успешный тест маршрута.

## Первичные источники

Проверены 2026-11-14:
- https://opencode.ai/docs/agents — `model`, наследование, режимы, primary session model.
- https://opencode.ai/docs/config — project config и precedence.
- https://opencode.ai/docs/cli — CLI и private/shared server.
- Живой каталог и API локального OpenCode; тарифы и доступность могут измениться.
  Документация публична; условия лицензии документации отдельно не проверялись.
