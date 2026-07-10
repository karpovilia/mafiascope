# 001: панель BELIEVED («как я думаю, обо мне думают») отображается не всегда
status: verified
severity: high
game: 0dc71656-2b83-4ce8-ada2-e8fddd804187  step: (1, 16) idx=22  player: Finley  (репрезентативный из 543 случаев)

## Симптом
В impersonate mode правая верхняя панель BELIEVED (how I think they see me) иногда пустая / без связей, хотя игра идёт и пробы собраны.

## Repro (зафиксировано тестировщиком 2026-07-07 на до-фиксовой версии 7fcd8a6)
1. Открыть вьюер; `switchGame('0dc71656-2b83-4ce8-ada2-e8fddd804187')`.
2. `enterImpersonate('Finley')`; `setStep(22)` (шаг R1.16).
3. Панель BELIEVED: все 5 линий с `stroke-opacity="0"` — связи полностью невидимы, все узлы серые «neutral 0%». Панель выглядит пустой. Скриншот «до»: `docs/bugs/img/001-before.png`.

Проверка одной строкой: `[...document.querySelectorAll('#imp-believed-graph line')].map(l=>l.getAttribute('stroke-opacity'))` → `["0","0","0","0","0"]`.

Масштаб на до-фиксовой версии (полный перебор 32 игры × все агенты × все шаги, 4914 состояний): 543 с линиями opacity < 0.3; из них 316 с opacity ≤ 0.15 и 16 с opacity = 0. Случаев «0 элементов `line`» — 0: панель всегда рендерила линии, они именно невидимы. Симптом «не всегда» = гипотеза №1 подтверждена как основная.

## Расследование (2026-07-07, слой данных чист)
Проверено скриптами по `src/all_games.json` (33 игры):
- forward-fill НЕ теряет social_map: 0 нарушений инварианта «однажды появившись, social_map присутствует на всех последующих шагах»;
- формы данных чистые: 7973 social_map, все `{toward_me: [...]}`, attitude ∈ {trusts, neutral, suspects} (4 None на 48922), confidence всегда > 0, имена игроков совпадают;
- «фактически пустых» шагов (нет ни social_map, ни role_assessment у живого агента) — 0 из 6702;
- viewBox всех impersonate-панелей и fixedPositions согласованы (300×200);
- `switchGame` сбрасывает `impersonating` → крэш от несовпадения имён между играми исключён.

## Suspected root cause (по убыванию вероятности)
1. **Невидимые связи при низкой confidence** — `src/viewer.html:776`: `stroke-opacity = min(0.9, confidence/100)`, `stroke-width = max(1.5, confidence/25)`. При confidence 10–25 (типично для ранних шагов) линии на тёмной теме практически неразличимы; узлы серые «neutral N%». Панель выглядит пустой, хотя технически рендерится. Фикс: минимальная opacity ~0.35 + различимый стиль для «низкая уверенность».
2. **Игра без social_map** — 1 игра из 33 записана со старой батареей проб (без social_map): молчаливый fallback на role_assessment (`viewer.html:744-771`); если и он не распарсен, всё серое с нулевой оценкой. Фикс: явное состояние «no social_map probe in this game» вместо тихого fallback.
3. **Нет guard-состояния** — `renderBelievedEgoGraph` (viewer.html:730), в отличие от `renderMyWorldGraph` (:683), не имеет ветки «No data»: при `beliefs` undefined/has_data=false рисует серые узлы с opacity-0 линиями. Фикс: тот же guard с текстом.
4. Runtime-исключения при специфических данных — тестировщику всегда слушать `pageerror`.

## Hardening (не подтверждено на текущих данных, но хрупко — чинить попутно)
- `src/prepare_viewer.py:221-230`: forward-fill заменяет entry целиком; при неполной батарее на шаге потеряет social_map. Мерджить по probe_id.
- `src/prepare_viewer.py:171`: parse-fail пишет `probe_id: None` — ключ-None перекроет старое значение при будущем мердже. Не писать ключ вовсе.

## Expected
При наличии social_map у агента панель всегда показывает читаемые связи; при отсутствии данных — явный текст о причине («no data yet» / «probe not recorded in this game»), не пустота.

## Fix (2026-07-07, кодер)
Корень подтверждён презентационный — гипотеза №1 (невидимые линии при низкой confidence) + №3 (нет guard-состояний).

Коммиты:
- `afed7f0` fix viewer — `src/viewer.html`, `renderBelievedEgoGraph` / `renderBelievedEgoLog`:
  - `stroke-opacity` линий зажата снизу: `max(0.35, min(0.9, confidence/100))`; при confidence < 30 линия рисуется пунктиром `4 3` (различимый стиль «низкая уверенность»);
  - guard «No data» при `!beliefs || !beliefs.has_data` (симметрично `renderMyWorldGraph`);
  - guard «No social_map probe recorded», если нет ни social_map, ни role_assessment;
  - fallback по role_assessment теперь явный: подпись «inferred from role assessment (no social_map)» в графе и строчка-пометка в логе.
- `550c7f3` fix prepare_viewer — hardening по разделу выше: forward-fill мерджит по probe_id (неполная батарея не затирает старые пробы), parse-fail не пишет ключ `probe_id: None`.

Самопроверка (headless chromium + puppeteer-core, рецепт тестировщика):
- полный свип 32 игры × все агенты × каждый 3-й шаг (2723 состояния): 0 пустых панелей у живых агентов с social_map, min stroke-opacity = 0.35, pageerror нет;
- синтетический прогон всех guard-веток (`has_data:false`, `beliefs undefined`, ни одной пробы, только role_assessment, social_map с confidence 10): все явные состояния и пунктир рендерятся;
- data-инварианты на пересобранном `all_games.json`: 0 значений `probe_id: None`, 0 потерь forward-fill;
- скриншот «после»: `docs/bugs/img/001-after-believed.png` (ранний шаг R0.5, confidence 55–70 — связи читаемы).

Примечание: реальные данные ни разу не попадают в guard-/fallback-ветки (во всех 32 играх social_map есть на каждом шаге с данными), т.е. видимая «пустота» из симптома почти наверняка была невидимыми линиями (№1). НЕ закрыт — ждёт verified от тестировщика по шагам воспроизведения.

## Verified (2026-07-07, тестировщик)
Верифицировано на закоммиченном состоянии `a4098d1` (включает `afed7f0` + `550c7f3`), стенд: headless chromium + puppeteer-core, перегенерированный `all_games.json` (32 игры).

- Репро «до» подтверждено на 7fcd8a6: на точке (0dc71656, Finley, R1.16) все линии BELIEVED имели opacity 0 — см. Repro выше и `docs/bugs/img/001-before.png`.
- «После» на той же точке: opacity линий 0.55–0.9, связи читаемы, у узлов подписи trusts/suspects/neutral с процентами — `docs/bugs/img/001-after.png`.
- Полный перебор fixed-версии (32 игры × все агенты × **каждый** шаг, 7973 состояния, 6702 у живых агентов): 0 панелей без линий при наличии данных; min stroke-opacity по всем линиям = 0.35; guard «ELIMINATED» ровно у мёртвых (1271), «ELIMINATED у живого» — 0; `pageerror` — 0.
- Data-инварианты пересобранного `all_games.json` (forward-fill по 4 пробам, монотонность alive, сортировка шагов, консистентность carried_forward): 0 нарушений на 1139 шагах.
