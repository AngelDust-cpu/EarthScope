# Специализированная маршрутизация — 2026-11-14

Текущий Frontend использует `openai/gpt-6.1-sol#medium`. Вариант medium присутствует в каталоге; проба вернула `FRONTEND-LIVE`. GPT-6 Luna в предыдущих записях о Frontend относится к истории.

Research временно переведён с Google Gemini 3.8 Flash на `openai/gpt-6-luna` после ошибок HTTP 503. Короткая проба через OpenAI вернула `RESEARCH-LIVE`. Работа веб-инструментов этой пробой не проверялась.

Frontend и Pitch переведены на `openai/gpt-6-luna` через подключение OpenAI в OpenCode. Пробы `ses_e27010416ffedcn9H79EfP9JyH` и `ses_e27010415ffethbwYmMc1fbfnq` завершились с ожидаемыми ответами. OpenCode оценил их в 0 USD; расход лимита аккаунта не измерялся. Назначения OpenRouter ниже относятся к истории.

Science переведён на `nvidia/nvidia/nemotron-3-ultra-550b-a55b` через
существующее подключение NVIDIA. В пробе `ses_e2774de6effeR2RpoaZtCIBPpp`
модель вернула 5,0 м/с и стандартную погрешность 0,27 м/с.
OpenCode оценил вызов в 0 USD. Это оценка, условия тарифа не проверялись.
После смены настройки штатный вызов Science вернул 2,5 км для 2500 м
в сессии `ses_e27746e94ffelBLCzufPFMks6u` с оценкой 0 USD.
Проверка всех закреплённых маршрутов прошла.

Последняя проверка: Data переведён на `opencode/glm-5.3-flash-free`.
GLM и DeepSeek дали одинаково верный результат на синтетической задаче
с UTC, дубликатами, пропусками и единицами. Обычный запуск Data через
`Invoke-Specialist.ps1` подтвердил новый маршрут: 125 см = 1,25 м,
сессия `ses_e2777f7e7ffeMjYHCZ3mENBf8S`, оценка OpenCode 0 USD.
DeepSeek закреплён только за Backend. Проверка конфигурации прошла.

Для Science GLM и `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`
успешно ответили на пробу распространения погрешности. Оба получили
0,269 м/с, верно указали знак поправки на ковариацию и условие средней
скорости. Для этих проб использованы бесплатные маршруты; стоимость
по событиям OpenCode не измерялась. На момент этих проб Science
использовал GPT-OSS 120B.

Запуск из Codex CLI описан в [README](README.md). Проверка обёртки
`Invoke-Specialist.ps1` показала семь доступных маршрутов в реестре.
Живой вызов QA вернул `QA-LIVE` через `opencode/inkling-2-small-free`
в сессии `ses_e277c6ef6ffeqR5ux6lOIUEk1R` с оценкой 0 USD.
Research теперь закреплён за `google/gemini-3.8-flash`. Проба из таблицы
Preflight завершилась с `finish: stop` и оценкой 0.00868425 USD.
Встроенный Orchestrator по-прежнему UNCONFIGURED, поскольку действующий
каталог OpenAI OAuth не показал Codex.

Этот отчёт заменяет предыдущий [MODEL-AUDIT.md](MODEL-AUDIT.md) в части
актуального распределения. Старый отчёт сохранён как история.
Область работ: только среда разработки; NASA challenge не выбран.

## Проверка реестра и правил

- Встроенный `opencode.models({all:true})`: просмотрены все пять страниц,
  466 записей (offset 0, 100, 200, 300, 400).
- Независимо проверены CLI `models --standalone` и API `GET /api/model`.
- Сохранены существующие подключения Google, Groq, NVIDIA, OpenAI,
  OpenCode Console и OpenRouter. Никакие подключения не добавлялись.
- В метаданных OpenAI connection: `method: oauth`; 18 моделей OpenAI
  в текущем реестре, **ни одной Codex**. Значения токенов не читались.
- `opencode/gpt-5.3-codex` и `openrouter/openai/gpt-5.3-codex`
  существуют, но это не требуемый ChatGPT OAuth маршрут. Они не вызывались.
- Использованы только существующие каталожные ID. Плагины, credentials,
  accounts, billing и глобальная конфигурация не изменялись.

## Решения по ролям

| Агент | Провайдер | Точный model ID (без provider prefix) | Причина |
|---|---|---|---|
| Orchestrator | — | **UNCONFIGURED** | Codex отсутствует у существующего OpenAI OAuth подключения. Не заменяется DeepSeek или другой моделью |
| Backend | opencode | `deepseek-v4.1-flash-free` | Стабильно отвечает на проверку API; специализация реализации |
| Data | opencode | `deepseek-v4.1-flash-free` | Проверена обработка единиц; сохранён implementation-маршрут |
| Frontend | openrouter | `baidu/ernie-5.5-flash-preview:free` | Успешная React/TypeScript проба; Qwen/Groq не вмещает обычный контекст агента в текущую квоту |
| Research | — | **PENDING** | Ожидается результат Gemini Flash, модель ещё не менялась |
| Science | groq | `openai/gpt-oss-120b` | Успешно воспроизведены значение и распространение погрешности; это 120B, не прежняя 20B |
| QA | opencode | `inkling-2-small-free` | Сохранена явная настройка; проверено отклонение неверного утверждения |
| Pitch | openrouter | `particle-ai/particle-1:free` | Сохранена явная настройка; проверен статус DRAFT |

Это небольшие функциональные пробы, не сравнительный benchmark качества.
ERNIE — preview-модель; стабильность в длинных UI-задачах ещё не измерена.
GPT-OSS корректно посчитал 5 ± 0.269 m/s на синтетических данных при
независимых стандартных неопределённостях 2 m и 1 s.
В его пояснении скорость названа требующей равномерного движения:
поправка ревьюера — d/t даёт среднюю скорость и без равномерности.
Нормальность ошибок не обязательна для линейной формулы дисперсии.

## Preflight перед изменениями

| Кандидат | Сессия | Наблюдение |
|---|---|---|
| Qwen `groq/qwen/qwen3.8-27b` | `ses_e2797e0a4ffel3a4it1ePAFpGX` | Ошибка квоты: ITPM 7000, запрос 7594. Не назначена; тариф не повышался |
| ERNIE free | `ses_e2797910effe1ZehstxVgmkHFP` | Stable item IDs, типизированная onClick, native button; finish stop, cost 0 |
| GPT-OSS 120B | `ses_e2797e090ffe8GUNYAmF1MPihf` | Верный расчёт 5 ± 0.27 m/s; finish stop, оценка cost 0.0019197 |
| Gemini Flash | `ses_e2797e094ffeOswgiQtJg6OvCr` | Результат ещё не получен |

## Тесты после сохранения model

Эти вызовы выполнены **без model override**. Фактическая модель взята из
`model.providerID` / `model.id` assistant-сообщений API, а не из ответа модели.

| Агент | Сессия | Фактический provider/model | Итог |
|---|---|---|---|
| Backend | `ses_e2796bbbdfferNABYrTOK6WFHX` | `opencode/deepseek-v4.1-flash-free` | `BACKEND-CONFIGURED 400` |
| Data | `ses_e2796bbb1ffdL2NqNxvuy7KP5x` | `opencode/deepseek-v4.1-flash-free` | `DATA-CONFIGURED 2000 m` |
| Frontend | `ses_e2796bbd9ffeuX93zKpVu1MqZG` | `openrouter/baidu/ernie-5.5-flash-preview:free` | Ответ получен, но пропущено требуемое слово button |
| Science | `ses_e2796bbc8ffdWPGk0dH768unfN` | `groq/openai/gpt-oss-120b` | Верные 5 m/s, finish stop; cost 0.00110055 |
| QA | `ses_e2796bba8ffe2XiL1MhLSdM4ND` | `opencode/inkling-2-small-free` | `QA-CONFIGURED FAIL` — ожидаемый ответ |
| Pitch | `ses_e2796bba5ffetSgN3OkoawmI6y` | `openrouter/particle-ai/particle-1:free` | DRAFT; вызвал read-only инструменты вопреки требованию не использовать их |

У всех перечисленных итоговых ответов `finish: stop`. Cost равен 0,
кроме явно указанного Science. Pitch дополнительно сделал два
assistant-шага с `finish: tool-calls` на той же модели, cost 0.
Это нарушение инструкции теста, а не доказательство идеальной дисциплины агента.

**Бесплатность:** поле cost — оценка OpenCode, не выписка провайдера.
Groq GPT-OSS 120B имеет ненулевую оценку; бесплатная квота/реальное списание
не подтверждены. Работающий существующий API использован по запросу пользователя,
но новые платные API и тарифы не подключались.

## Источники и доступ

Официальные страницы прочитаны 2026-11-14:
- https://opencode.ai/docs/agents — model, наследование, disabled, primary session.
- https://opencode.ai/docs/providers — ChatGPT OAuth и динамическая доступность.
- Живой реестр и метаданные сообщений локального OpenCode 2.2.7.

Документация публична; её лицензия отдельно не проверялась.
Для inference действуют авторизация, квоты и условия каждого провайдера.
Registry limits — заявленные значения, а не измеренная полезная ёмкость.
