# Спецификация 004: реализация и проверка — 11.11.2026

## Завершённая работа

- Устранены все пять шаблонных заглушек в OpenSpec proposal/design и SDD plan.
  Зафиксированы реальные владельцы, исходники, границы, риск, откат и тесты.
- Текущий read-only queue_monitor расширен каталогом причин v3. Три наблюдавшихся
  user_message_not_confirmed больше не скрываются как other; отдельно различается
  assistant_timeout. Новая причина не разрешает повтор Send.
- Миграция v1/v2 учитывает версии каталога. Уточнение старого other не создаёт
  ложный новый инцидент, но новые записи и переходы состояний не подавляются.
- Исправлен отдельный воспроизведённый случай web_waiter_timeout: собственный
  префикс известной причины сохраняется, внешний web_ нормализуется один раз.
- Применение монитора выполнено с SHA/CAS и native-byte backup. Gateway, Relay,
  Web Companion, пользовательский browser profile и live baseline не изменялись
  этим запуском. Browser helper/cache/bootstrap проверены как существующие владельцы.

## Доказательства

| Проверка | Результат | Job / команда |
| --- | --- | --- |
| Начальное воспроизведение | Три uncertain:user_message_not_confirmed были other в старом мониторе | 850d7552915b43b786281a9d8e34511b, исторический read-only отчёт |
| Подтверждение кода источником и metadata | Content script содержит user_message_not_confirmed; в SQLite три таких отказа | 7a286c3a8ed641d1b3f4eb2b4c66a4df, rc 0 |
| Linux final observer tests | 64 PASS, 0 SKIP | python -m unittest discover -s response-quality-completion-20261111 -p 'test_*.py' -q |
| Windows installed final components | 113 PASS / 1 SKIP, 0 errors, rc 0 | 2a0195ff9fd94316979f8789b427cc2b |
| OpenSpec strict + SDD crosswalk | PASS; 9 FR, 18 сценариев, 9 mappings, rc 0 | 2b67b399b8d24600a4e2afe1d2cae3fc |
| Live read-only observer | ATTENTION, rc 1; точные причины, baseline не продвинут | 4e79bee3b82048a8aab067b96045fd1f |

Windows SKIP: OS does not allow symlinks. Его нельзя считать успешной проверкой
symlink. Финальный runner импортировал текущие production-модули по проверенным
путям, а не старую копию candidate. Тесты используют временные fixtures.
Модельных запросов, реальных Telegram-отправок и перезапусков в этом прогоне — 0.

Перед последним исправлением отдельный тест
test_native_web_prefix_is_not_stripped_from_a_known_reason завершился ожидаемым
FAIL для web_waiter_timeout. После исправления этот тест и весь final suite прошли.

## FR → существующий код → проверки

| FR | Владелец | Проверки и статус |
| --- | --- | --- |
| FR-001 | hermes_windows_browser.select_native_browser | test_selects_native_as_one_path_with_spaces, package/PE/architecture negatives, literal arguments — source/unit подтверждено |
| FR-002 | hermes_windows_browser.find_existing_browser | existing Edge, explicit unavailable setting, missing browser, relative path negatives — source/unit подтверждено |
| FR-003 | gateway_bootstrap.run, browser_preflight.observe_browser | receipt PID/hash и failed receipt проверены в fixtures; свежая полная живая приёмка остаётся открытой |
| FR-004 | model_news_prefetch._remember_turn / _reuse_turn | test_count_is_bounded, test_expiry_does_not_extend_on_reads, test_clock_rollback_discards_cache, defensive-copy tests — PASS |
| FR-005 | model_news_prefetch.maybe_prefetch_news | test_followup_uses_initial_evidence_without_fetch, test_no_cached_evidence_means_no_fetch, test_failed_initial_fetch_does_not_invent_evidence — PASS |
| FR-006 | model_news_prefetch / model-only view | test_latest_user_is_the_only_key, test_different_news_request_does_not_reuse, test_canonical_tool_messages_remain_exact — PASS |
| FR-007 | monitoring.queue_monitor.reason_code / baseline_records | Новый test_confirmation_observation: 13 тестов на классификацию, миграцию, приватность и отсутствие auto-ack — PASS |
| FR-008 | monitoring.queue_monitor.observe | test_latency_target_does_not_claim_stale_state_or_phase_time, test_completed_request_is_not_an_active_latency_warning — PASS |
| FR-009 | monitoring.queue_monitor.observe | test_window_uses_creation_time_and_counts_terminal_outcomes, test_empty_or_active_only_window_has_unknown_ratio — PASS |

Checkboxes реализации отмечены только для FR-001–002 и FR-004–009 по этим
доказательствам. FR-003 и final E2E / independent release approval не закрыты.

## SHA текущих проверенных исходников

| Компонент | SHA-256 |
| --- | --- |
| monitoring/queue_monitor.py | da46996cb2efb582cdad5f5155d408c8b61f07da4f0fb3b2cdef43db8a88f87f |
| model_news_prefetch.py | c469f276f093ecd60c866de90d29d1f47a93f929d015d916a1eeeedb5933de2c |
| hermes_windows_browser.py | 5a42001e3436a867ea785c604d0ff6e9c004c48573b8c916492788bac60d73ee |
| gateway_bootstrap.py | b12bd72a5576a23b208c6f3ecd6a87473e8daeb3a84f5cf3bd3ed7c959579513 |

Backup исходного монитора f1a2… находится локально в
planning/response-quality-completion-20261111/queue_monitor.before.py.
Каталоги, cache и bootstrap в этом запуске не переустанавливались.

## Живое состояние и следующий шаг

Наблюдение в 03:43 Asia/Amman обнаружило intent возрастом 321.7 секунды и queued
возрастом 243.3 секунды; это возраст запроса, не время фазы. Среди новых отказов
видны три user_message_not_confirmed и один assistant_timeout. Это снимок,
не утверждение о текущем состоянии спустя время. Монитор сохранил ATTENTION,
telegram_delivery_verified=false и automatic_retry_authorized=false.

Следующий законный шаг для транспорта: связать fresh request с точной стадией
подтверждения user message и проверить восстановление без второго Send; текущие
DOM/concurrency/plugin участки уже меняются параллельно. Не перезаписывать эти
файлы и не переотправлять неизвестные попытки. FR-003 требует отдельного
контролируемого живого окна и Telegram ACK.

## Качество и границы

Безопасность: фиксированные коды и read-only SQLite, нет произвольных строк.
Надёжность: новая ошибка сохраняет тревогу, миграция не переименовывает её в успех.
Архитектура: расширен действующий владелец; второй ledger/транспорт не создан.
Масштабирование: каталог мал и фиксирован, прежние row/time budgets сохранены.
Корректность: версии, переходы, новые identity, префиксы и legacy tests проверены.
Поддерживаемость: stdlib и совместимый CLI. Производительность: дополнительные
проверки — операции со множествами без сети/LLM; ускорение Telegram этим патчем
не измерялось. Наблюдаемость: точная причина и honest unknown для доставки.
Трассируемость: 9 FR и ссылки на реальные tests/jobs выше.
