# 002: игра с 0 шагов обходит skip-guard через --game-id и даёт молча пустой вьюер
status: open
severity: low
game: 024d54fe-659f-4300-ada8-3b730e4e0d0c  step: n/a (шагов нет)  player: n/a

## Repro — точные шаги
1. `python serve_viewer.py --no-open --game-id 024d54fe-659f-4300-ada8-3b730e4e0d0c` (или `prepare_viewer.py --game-id ...`) — у игры пустой `introspection.jsonl`, `total_steps: 0`.
   Одиночный путь пишет игру в выдачу **без** проверки `if not data["steps"]`, которая есть только в `scan_game_dirs` (`src/prepare_viewer.py:298-300`).
2. Открыть вьюер (для headless-репро: стенд с этим `viewer_data.json` без `all_games.json`, чтобы сработал fallback-фетч).
3. Наблюдать: полностью пустые панели игроков и Ground Truth без какого-либо сообщения; таймлайн «-», step-info пустой.
4. В консоли (не `pageerror` — исключение проглочено): `renderStep error: TypeError: Cannot read properties of undefined (reading 'label')` — `renderStep(0)` берёт `DATA.steps[0]` = undefined, `updateTimeline` падает на `s.label` (`src/viewer.html`, `renderStep`/`updateTimeline`; try/catch в `renderStep` скрывает ошибку).

## Expected / Actual
- **Expected:** либо одиночный путь отказывается собирать игру без шагов с тем же сообщением `SKIP: no steps (aborted run?)`, либо вьюер показывает явное состояние «game has no recorded steps» вместо пустых панелей.
- **Actual:** молча пустой интерфейс + проглоченный TypeError в консоли. Скриншот: `docs/bugs/img/002-empty-game-blank.png`.

## Suspected root cause
- `src/prepare_viewer.py:294-300` — guard `if not data["steps"]: SKIP` есть только в `scan_game_dirs`; ветки `main()` с `--game-id` (`:314-321`) и `serve_viewer.py:32-39` (`--game-id`) его не имеют.
- `src/viewer.html` `renderStep`/`setStep`: при `total_steps == 0` `setStep(0)` кламп `Math.min(-1, ...)` → `renderStep(0)` на пустом массиве шагов; нет ветки «нет шагов».

## Замечания
- Проверено на закоммиченном состоянии `a4098d1`. «Вьюер не падает» из чек-листа формально выполняется (исключение перехвачено), поэтому severity low.
- Основной multi-game путь не затронут: `scan_game_dirs` корректно скипает игру (проверено прямым вызовом и составом пересобранного `all_games.json` — 32 игры без 024d54fe).
