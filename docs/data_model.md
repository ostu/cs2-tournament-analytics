# Модель данных — grain, ключи, lineage

Канонический слой данных: `data/raw/` → `pipeline/normalize_sources.py` → `data/interim/` →
`pipeline/build_dimensions.py` + `build_rating_events.py` + `build_fact_player_map.py` +
`build_fact_team_map.py` → `data/processed/` (dim_*/fact_*) → `pipeline/build_marts.py` →
`data/processed/mart_*` → `pipeline/build_public_data.py` → `data/public/*.json` →
`pipeline/build_dashboard.py` → `dashboard.html`.

Формат хранения: parquet (+ CSV-preview рядом, `*.preview.csv`, для ручной проверки).

---

## 1. Interim (`data/interim/`, `normalize_sources.py`)

| Таблица | Grain | Источник |
|---|---|---|
| `elo_changes_normalized` | игрок × турнир × стадия × карта (0-based, до коррекции) | `cs_farpost_elo_changes.xlsx` |
| `elo_norm_normalized` | игрок × турнир × стадия (`a`/`n`) | `cs_farpost_elo_norm.xlsx` |
| `tournament_results_normalized` | игрок × турнир × стадия × карта | `cs_farpost_tournament_results.xlsx` |
| `tournament_score_normalized` | команда × турнир | `cs_farpost_tournament_score.xlsx` |
| `players_wide_normalized` | игрок (широкий формат, с PII) | `cs_farpost_players.xlsx` |
| `player_tournament_elo` | игрок × турнир (elo_start/calib/final, длинный формат) | `cs_farpost_players.xlsx` (unpivot) |

Все таблицы несут `source_file`, `source_row`, `data_quality_flags` (список строк).

---

## 2. Dimensions (`data/processed/`, `build_dimensions.py`)

### `dim_tournament`
- **Grain:** один турнир (`tournament_number`).
- **Ключ:** `tournament_number`.
- **Поля:** `tournament_key, tournament_label, tournament_start_date, tournament_end_date, player_count, team_count, maps_count, rating_stages_present, rating_cycle_available, adr_tracked_any, mvp_tracked_any, source_coverage_flags`.
- **Источники:** `tournament_results_normalized`, `elo_norm_normalized`.

### `dim_rating_stage`
- **Grain:** одна стадия рейтингового цикла (`a`/`c`/`n`/`t`).
- **Ключ:** `rating_stage`.
- **Поля:** `stage_name, stage_order, stage_description`.
- **Источник:** статическая справочная таблица (`config.py::RATING_STAGE_INFO`, зафиксировано пользователем в Audit v2/v3).

### `dim_team`
- **Grain:** одна каноническая команда.
- **Ключ:** `team_key` (= `canonical_team_name`).
- **Поля:** `display_team_name, alias_status, raw_name_variants, first_tournament, last_tournament`.
- **Источники:** `tournament_results_normalized.canonical_team_name`, `tournament_score_normalized.canonical_team_name` (после alias-резолюции, `config/team_aliases_review.csv`).

### `dim_map`
- **Grain:** одна карта CS2.
- **Ключ:** `map_key` (= `canonical_map_name`).
- **Поля:** `raw_map_name, map_order, active_flag` (true, если карта встречалась в основном турнире с наибольшим `tournament_number`).
- **Источник:** `tournament_results_normalized.map`.

### `dim_player_private` (`data/private/`, НЕ публикуется)
- **Grain:** один игрок.
- **Ключ:** `private_player_key`.
- **Поля:** все PII-поля (`fio, city, department, discord_name, faceit_name, faceit_link, steam_account, steam_name, player_id, another_names`) + `public_player_id, public_nickname, mapping_provenance`.
- **Источники:** `players_wide_normalized` + `data/private/player_aliases.csv`.

### `dim_player_public` (`data/processed/`)
- **Grain:** один игрок.
- **Ключ:** `public_player_id`.
- **Поля:** `public_nickname` — **без** `private_player_key** (сам мостик приватный, живёт только в `data/private/player_aliases.csv`, используется для JOIN внутри pipeline, но не копируется в эту таблицу).

---

## 3. Facts (`data/processed/`)

### `fact_player_rating_event` (`build_rating_events.py`)
- **Grain:** одно рейтинговое событие одного игрока.
- **Ключ:** `(private_player_key, tournament_number, rating_stage, event_type, event_sequence)`.
- **event_type:** `previous_tournament_final, normalized_entry, calibration_game, calibration_final, blended_normalized, tournament_map_change, tournament_final`.
- **Формулы:** см. `docs/rating_semantics.md` — `normalized_entry` (authoritative-формула пользователя), `calibration_game/tournament_map_change` (notebook, 3 прохода koef 0.2/0.15/0.1), `calibration_final/tournament_final` (сырая колонка `players.xlsx`, сверенная с реконструкцией по картам), `blended_normalized` (`calculate_elo_final`, notebook).
- **Инварианты** (проверены `validate_pipeline.py`): `Σ(normalized_entry.rating_after) = player_count×1000` на каждый турнир; то же для `blended_normalized`; `rating_after = rating_before + rating_change`, где применимо; отсутствие дублей по ключу.

### `fact_player_map` (`build_fact_player_map.py`)
- **Grain:** один игрок на одной сыгранной карте.
- **Ключ:** `(private_player_key, tournament_number, tournament_phase, map_number)`.
- **Поля:** статистика карты (`kills, assists, deaths, adr, adr_tracked, mvps, mvp_tracked, rounds_played`), `team_key, opponent_team_key, win_loss`, счёт (`result_raw, team_score, opponent_score, round_diff, result_parse_status`), `elo_change` (джойн с `elo_changes_normalized` после mapId-коррекции), `data_quality_flags`.
- **Источники:** `tournament_results_normalized` + `elo_changes_normalized`.

### `fact_team_map` (`build_fact_team_map.py`)
- **Grain:** одна команда на одной сыгранной карте.
- **Ключ:** `(tournament_number, tournament_phase, map_number, team_key)`.
- **Поля:** `opponent_team_key, team_score, opponent_score, round_diff` (посчитан напрямую, БЕЗ деления на фиксированное число игроков — см. `docs/legacy_vs_canonical_results.md` §3), `win_loss, players_count, kills_total, deaths_total, elo_change_total, result_parse_status`.
- **Источник:** агрегация `fact_player_map` по составу.
- **Reconciliation:** 2 строки на карту (команда/соперник), `win_loss` противоположны, `round_diff` зеркален, `opponent_team_key` взаимно корректен — проверено на 224/224 картах (100%).

---

## 4. Marts (`data/processed/mart_*`, `build_marts.py`)

| Mart | Grain | Назначение |
|---|---|---|
| `mart_player_summary` | игрок (всё время, с `latest_tournament`/`latest_team`) | таблица игроков, профиль (сводка) |
| `mart_player_rating_history` | игрок × рейтинговое событие | таймлайн Elo игрока (a→c→n→t) |
| `mart_player_map` | игрок × карта | детальная таблица карт в профиле игрока |
| `mart_team_summary` | команда × турнир (+ all-time, `tournament_number=null`) | таблица команд, профиль |
| `mart_team_rating_comparison` | команда × турнир | Nc vs Nt (сумма состава) — карточка Overview |
| `mart_team_rating_history` | команда × турнир × event_type (среднее по составу) | таймлайн Elo команды |
| `mart_team_map` | команда × карта × турнир | результаты по картам в профиле команды |
| `mart_team_map_matrix` | команда × карта (агрегат по всем турнирам) | heatmap Team×Map, с `sample_size`/`low_sample_flag` |

Формулы каждой метрики — `config/metric_definitions.json`.

**`map_name`** в `mart_player_rating_history`: для событий грейна "карта"
(`tournament_map_change`, `calibration_game`) добавлено через существующую
связь `fact_player_map.map_key → dim_map.canonical_map_name` (джойн по
`private_player_key, tournament_number, rating_stage=tournament_phase,
map_number_canonical=map_number`) — используется дашбордом для подписи
`MapName (N)` вместо технического `карта N`. Для событий без карты
(`a`/`c_final`/`n`/`t_final`) остаётся `null`.

**`roster_size_not_five`** — data-quality флаг в `mart_team_summary` и
`mart_team_rating_comparison`, проставляется, когда `players_count`/
`roster_players_count` (состав, полученный `resolve_final_roster()`) != 5.
Не меняет состав и не блокирует сборку — см. `docs/unresolved_questions.md`
§6 и `artifacts/validation/team_roster_anomalies.{json,md}`
(`pipeline/audit_team_rosters.py`).

---

## 5. Public layer (`data/public/*.json`, `build_public_data.py`)

Прямые производные marts с двумя обязательными преобразованиями:
1. `private_player_key` → `public_player_id`/`public_nickname` (джойн через `data/private/player_aliases.csv`, сам мостик не копируется).
2. Названия команд, содержащие приватный идентификатор игрока как подстроку (найден 1 такой исторический случай — см. `docs/privacy_and_publication.md`, §0, где пример анонимизирован), заменяются на детерминированный плейсхолдер (`build_team_redaction_map()`).

Проверяется `pipeline/check_publication_leaks.py` перед завершением сборки (нулевой код возврата — обязательное условие).

---

## 6. Dashboard (`dashboard.html`, `build_dashboard.py`)

Один self-contained HTML: инлайнит `dashboard/css/styles.css`, `dashboard/js/app.js`,
все 8 файлов `data/public/*.json` (как `window.APP_DATA`), `config/dashboard_config.json` (как
`window.APP_CONFIG`). Внешняя зависимость — только Apache ECharts (CDN, с документированным
офлайн-fallback). Проверяется leak checker'ом перед тем, как считаться готовым к публикации.

---

## 7. Полный lineage одной метрики (пример: `average_kills_per_map` игрока)

```
cs_farpost_tournament_results.xlsx (kills, mapId, tournament)
        │  normalize_sources.py: разбор tournament → tournament_number/rating_stage,
        │                        team alias, result-парсинг
        ▼
tournament_results_normalized.parquet
        │  build_fact_player_map.py: джойн с elo_changes (mapId+1), roster/opponent
        ▼
fact_player_map.parquet  (грейн: игрок × карта)
        │  build_marts.py: SUM(kills) / COUNT(DISTINCT tournament_number,tournament_phase,map_number)
        ▼
mart_player_summary.parquet  (поле average_kills_per_map)
        │  build_public_data.py: private_player_key → public_player_id/public_nickname
        ▼
data/public/players.json
        │  build_dashboard.py: инлайн в window.APP_DATA
        ▼
dashboard.html → таблица "Игроки", колонка "Убийств/карту"
```
