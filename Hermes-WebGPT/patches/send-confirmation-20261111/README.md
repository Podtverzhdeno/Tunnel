# Hermes: подтверждение сообщения, показанного вложением

## Исправленный дефект

Рабочий content script содержал `boundAnswer`, но fallback для большой вставки
его не вызывал. Одна user bubble могла получить `message_saved` без ответа,
с чужим request_id, ещё идущей генерацией или старым transcript. При этом
вставка меньше 12000 символов не имела этого fallback даже при завершённом
правильно привязанном ответе. Это воспроизведённые дефекты кода; они не
доказывают причину каждого исторического `user_message_not_confirmed`.

Использован существующий `hermes_relay_content.js`, без второго sender/транспорта.
Для точного полного текста пользователя прежний путь сохранён. Когда UI
показывает вложение вместо полного текста, подтверждение требует одновременно:

1. Пустой transcript при назначении и непосредственно перед единственным Send.
2. Сохранённый `/c/…`, ровно один новый authored user turn, пустой composer.
3. Отсутствие доверенного ручного вмешательства.
4. Последний assistant после этой user bubble, без generating indicator.
5. Полный JSON-конверт с точным request_id, допустимым content и массивом tool_calls.

Порог длины убран: сильные доказательства одинаковы для короткого и длинного
вложения. Отсутствующий/чужой/незавершённый ответ не создаёт saved receipt.
Final result по-прежнему ждёт стабильности 5 секунд и проверяется Relay.
Тайм-ауты, единственный click, durable intent, noReplay и внешний ACK lifecycle
completion mode не ослаблены. Код не исполняет инструменты из JSON.

## Реальное применение на ПК

Обе копии `chat-launch-smoke-desktop-test/web-companion/hermes_relay_content.js`
и `web-extension/hermes_relay_content.js` заменены по точному SHA/CAS после
native-byte backup. Остальные 12 файлов кода проверены по снимку до и после теста,
чтобы не опубликовать параллельные изменения другого чата.

Штатный `finish_verified_webcompanion_release.py` завершился rc 0:
`PUBLISHED`, `MATCHED`, 14 файлов, версия расширения 0.9.7. Проверены 27 сценариев
установленного content script, 13 auto-reload regression cases и 48 core assertions.
Self-reload предоставлен существующему механизму idle/uncertain guards.
Фактический новый boot/revision рабочего браузера пока не подтверждён.
Публикация маркера не означает успешный Telegram E2E.

## Проверки

| Среда | Результат | Реальный job |
| --- | --- | --- |
| Старый исходник, Node VM, synthetic DOM | 18 PASS / 9 ожидаемых FAIL — воспроизведение дефекта | локальная команда того же harness до правки |
| Новый код, Node VM, Linux | 27/27 PASS, синтаксис PASS | `node test_confirmation.cjs` |
| Новый код, Node VM, Windows | 27/27 PASS, rc 0 | `403663f54ee44230b1ca06411c88c577` |
| Настоящий headless Edge, изолированный DOM fixture | 7/7 PASS, rc 0 | `a4c13f3e78354093ba51c5880fc906e4` |
| Установленный source + обычный release workflow | PASS, rc 0, source/destination MATCHED | `157bf60ac61c4d1b8c8d2049f80cfc51` |
| OpenSpec strict + SDD 001 crosswalk | PASS, 31 FR / 64 сценария / 31 mappings | `3b2cf015b4534ebab2921b5a5d4e5b16` |

Edge действительно использовал DOM `innerText`, selectors и document order.
Все navigation requests перехвачены и обслужены synthetic HTML, остальные
запросы блокировались. Это тест настоящего браузерного движка без настоящего
ChatGPT backend, пользовательского профиля, модели, Telegram или Relay HTTP.

Негативные случаи: неверный ID/конверт, отсутствие ответа, generation,
старые/множественные user turns, неправильный document order, detached DOM,
новый ручной draft, trusted user event, потеря intent/saved ACK, ошибка click,
несохранённый route, late answer, subframe, внешний completion ACK.

## Связь со спецификацией

Действующий SDD `001-add-playwright-cdp-web-transport`: FR-011 (saved user turn),
FR-012 (final response evidence), FR-028 (offline browser fixture). Два сценария
добавлены к существующему требованию Saved user turn verification в
`openspec/changes/add-playwright-cdp-web-transport/specs/durable-web-turn-provenance/spec.md`.
Новые permissions, ledger или операция повторного Send не вводятся.

## Идентичность и откат

Исходный SHA-256:
`358f5e5536d8fbe2321b2f3e6d913d9905a2738b35ab4016ed5aa20193b0725f`.
Новый SHA-256:
`cc30b85981ae376b52a39953fd4f03d0fb14bd9e5d4e8d0d37d95fda3b217604`.
Native backups и receipt находятся локально в
`Hermes-WebGPT/planning/send-confirmation-fix-20261111/`.

При откате сначала сверить текущие SHA и чужие изменения, восстановить обе копии
кода только своим backup и выполнить тот же verified release workflow. Не менять
private config, approval/Relay SQLite или noReplay записи и не повторять запросы.
Файлы bundle — проверенный patch; это не полный архив пользовательской установки.

## Воспроизведение

```text
node --check hermes_relay_content.js
node test_confirmation.cjs
```

`edge_fixture.py` использует уже установленный Playwright и Edge. Не устанавливает
пакетов. Весь браузерный контекст закрывается после теста.

## Остаётся открытым

Подтвердить загрузку нового revision в активном расширении и один новый
Telegram → ChatGPT → Telegram проход с request_id и реальным Telegram ACK.
Старые uncertain запросы не повторялись. В этом запуске новых сообщений в
ChatGPT/Telegram не отправляли и процессы Gateway/Relay не перезапускали.
