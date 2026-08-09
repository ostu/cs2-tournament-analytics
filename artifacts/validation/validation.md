# Отчёт валидации pipeline

Всего проверок: 78. Критических провалов: **0**. Warning: **1**.

## source_integrity

- [PASS] `source_integrity` — cs_farpost_elo_changes.xlsx: совпадает
- [PASS] `source_integrity` — cs_farpost_elo_norm.xlsx: совпадает
- [PASS] `source_integrity` — cs_farpost_players.xlsx: совпадает
- [PASS] `source_integrity` — cs_farpost_tournament_results.xlsx: совпадает
- [PASS] `source_integrity` — cs_farpost_tournament_score.xlsx: совпадает

## dimension_key_unique

- [PASS] `dimension_key_unique:dim_tournament` — дублей по ['tournament_number']: 0
- [PASS] `dimension_key_unique:dim_team` — дублей по ['team_key']: 0
- [PASS] `dimension_key_unique:dim_map` — дублей по ['map_key']: 0
- [PASS] `dimension_key_unique:dim_player_public` — дублей по ['public_player_id']: 0

## fact_grain_unique

- [PASS] `fact_grain_unique:fact_player_map` — дублей по гранулярному ключу ['private_player_key', 'tournament_number', 'tournament_phase', 'map_number']: 0
- [PASS] `fact_grain_unique:fact_team_map` — дублей по гранулярному ключу ['tournament_number', 'tournament_phase', 'map_number', 'team_key']: 0
- [PASS] `fact_grain_unique:fact_player_rating_event` — дублей по гранулярному ключу ['private_player_key', 'tournament_number', 'rating_stage', 'event_type', 'event_sequence']: 0

## no_unexpected_many_to_many

- [PASS] `no_unexpected_many_to_many:team_per_map` — карт с числом команд != 2: 0 из 224

## na_sum_invariant

- [PASS] `na_sum_invariant` — турнир 1: Na сумма 35000.0 vs целевая 35000
- [PASS] `na_sum_invariant` — турнир 2: Na сумма 47000.0 vs целевая 47000
- [PASS] `na_sum_invariant` — турнир 3: Na сумма 20000.0 vs целевая 20000
- [PASS] `na_sum_invariant` — турнир 4: Na сумма 27000.0 vs целевая 27000
- [PASS] `na_sum_invariant` — турнир 5: Na сумма 25000.0 vs целевая 25000
- [PASS] `na_sum_invariant` — турнир 6: Na сумма 40000.0 vs целевая 40000
- [PASS] `na_sum_invariant` — турнир 7: Na сумма 36000.0 vs целевая 36000

## nn_sum_invariant

- [PASS] `nn_sum_invariant` — турнир 7: Nn сумма 36000.0 vs целевая 36000

## rating_after_equals_before_plus_change

- [PASS] `rating_after_equals_before_plus_change` — несоответствий: 0 из 2875

## known_final_reconstruction_mismatches

- [PASS] `known_final_reconstruction_mismatches` — известных расхождений calibration_final/tournament_final с реконструкцией по картам: 35 (ожидаемо для турнира 7, см. docs/rating_semantics.md §3)

## mapid_offset_join_coverage

- [PASS] `mapid_offset_join_coverage` — coverage=99.9553%, порог=99.50%

## kills_nonnegative

- [WARN] `kills_nonnegative` — строк с kills<0: 1 (см. data_quality_flags=negative_stat_value_in_raw_source)

## deaths_nonnegative

- [PASS] `deaths_nonnegative` — строк с deaths<0: 0

## rounds_positive

- [PASS] `rounds_positive` — строк с rounds_played<=0: 0

## kd_no_infinite

- [PASS] `kd_no_infinite` — строк с deaths=0 (KD не определён, обрабатывается как null): 0

## adr_null_when_not_tracked

- [PASS] `adr_null_when_not_tracked` — строк с adr_tracked=false, но adr не null: 0

## mvp_tracked_consistent_per_tournament

- [PASS] `mvp_tracked_consistent_per_tournament` — турниров/стадий с неоднородным mvp_tracked внутри себя: 0

## team_map_two_sided_complete

- [PASS] `team_map_two_sided_complete` — 224/224 карт с ровно 2 командами

## team_map_mirror_reconciliation

- [PASS] `team_map_mirror_reconciliation` — зеркальность (W/L противоположны, same match, round_diff зеркален, opponent корректен): 224/224

## team_type_values_valid

- [PASS] `team_type_values_valid` — все team_type — из допустимого набора

## team_type_no_unresolved_unknown

- [PASS] `team_type_no_unresolved_unknown` — команд с team_type=unknown (не публикуются в team dashboards до подтверждения): 0

## team_type_no_overlap

- [PASS] `team_type_no_overlap` — команд одновременно в permanent и calibration: 0

## no_calibration_team_in_public

- [PASS] `no_calibration_team_in_public:teams.json` — teams.json: калибровочных команд нет
- [PASS] `no_calibration_team_in_public:team_maps.json` — team_maps.json: калибровочных команд нет
- [PASS] `no_calibration_team_in_public:team_map_matrix.json` — team_map_matrix.json: калибровочных команд нет
- [PASS] `no_calibration_team_in_public:team_rating_history.json` — team_rating_history.json: калибровочных команд нет
- [PASS] `no_calibration_team_in_public:team_rating_comparison.json` — team_rating_comparison.json: калибровочных команд нет

## no_calibration_team_in_filter_dropdown

- [PASS] `no_calibration_team_in_filter_dropdown` — фильтр команд не содержит calibration_team

## no_calibration_team_in_teams_by_tournament

- [PASS] `no_calibration_team_in_teams_by_tournament` — ни один турнир в teams_by_tournament не содержит calibration_team

## calibration_rating_events_preserved

- [PASS] `calibration_rating_events_preserved` — calibration_game=590, calibration_final=230 (должны быть > 0)

## team_rating_final_not_all_null_all_time

- [PASS] `team_rating_final_not_all_null_all_time` — all-time строк с валидным rating_final: 40 из 40 (при 43 постоянных командах)

## team_rating_final_not_all_null_per_tournament

- [PASS] `team_rating_final_not_all_null_per_tournament` — per-tournament строк с валидным rating_final: 43 из 43

## team_rating_comparison_not_all_null

- [PASS] `team_rating_comparison_not_all_null` — строк с валидными team_nc и team_nt: 43 из 43

## privacy_leak_check

- [PASS] `privacy_leak_check` — public: чисто
- [PASS] `privacy_leak_check` — dashboard.html: чисто

## no_private_mapping_file_in_public

- [PASS] `no_private_mapping_file_in_public` — файлов player_aliases* в data/public/: 0

## no_private_player_key_field

- [PASS] `no_private_player_key_field:metadata.json` — поле private_player_key отсутствует
- [PASS] `no_private_player_key_field:players.json` — поле private_player_key отсутствует
- [PASS] `no_private_player_key_field:player_maps.json` — поле private_player_key отсутствует
- [PASS] `no_private_player_key_field:player_rating_history.json` — поле private_player_key отсутствует
- [PASS] `no_private_player_key_field:teams.json` — поле private_player_key отсутствует
- [PASS] `no_private_player_key_field:team_maps.json` — поле private_player_key отсутствует
- [PASS] `no_private_player_key_field:team_map_matrix.json` — поле private_player_key отсутствует
- [PASS] `no_private_player_key_field:team_rating_comparison.json` — поле private_player_key отсутствует
- [PASS] `no_private_player_key_field:team_rating_history.json` — поле private_player_key отсутствует

## dashboard_exists

- [PASS] `dashboard_exists` — dashboard.html существует, размер 3651 КБ

## dashboard_required_sections

- [PASS] `dashboard_required_sections` — все обязательные секции присутствуют

## dashboard_embedded_json_valid

- [PASS] `dashboard_embedded_json_valid` — embedded JSON валиден, все ключи присутствуют

## dashboard_no_missing_local_assets

- [PASS] `dashboard_no_missing_local_assets` — нет ссылок на отсутствующие локальные файлы (все ассеты инлайнены или через CDN)

## overview_removed_components_absent

- [PASS] `overview_removed_components_absent` — удалённые Overview-компоненты (рейтинг команд, 3 графика игроков) отсутствуют

## overview_calib_final_container_present

- [PASS] `overview_calib_final_container_present` — контейнер #chart-calib-final для Nc vs Nt найден

## overview_calib_final_is_first_chart

- [PASS] `overview_calib_final_is_first_chart` — Nc vs Nt расположен раньше остальных Overview-графиков в разметке

## players_table_no_adr_coverage_column

- [PASS] `players_table_no_adr_coverage_column` — PLAYER_COLUMNS не содержит видимую колонку 'Покрытие ADR'

## players_table_adr_column_present

- [PASS] `players_table_adr_column_present` — колонка ADR присутствует

## player_cta_present

- [PASS] `player_cta_present` — CTA выбора игрока найден

## rating_stage_legend_present

- [PASS] `rating_stage_legend_present` — легенда стадий A/C/N/T найдена

## rating_history_map_name_present

- [PASS] `rating_history_map_name_present` — map_name заполнен для 2234/2234 карточных рейтинговых событий

## calib_final_chart_source_rows_present

- [PASS] `calib_final_chart_source_rows_present` — mart_team_rating_comparison: 43 строк

## about_section_reader_friendly

- [PASS] `about_section_reader_friendly` — блок 'О данных' не содержит технических терминов

## about_section_mentions_pseudonyms_and_stages

- [PASS] `about_section_mentions_pseudonyms_and_stages` — упоминает псевдонимы и стадии рейтинга A/C/N/T

## roster_anomalies_report_exists

- [PASS] `roster_anomalies_report_exists` — team_roster_anomalies.json/.md созданы

## roster_anomalies_size_4_or_6_captured

- [PASS] `roster_anomalies_size_4_or_6_captured` — аномалий всего: 12, из них roster_size 4 или 6: 9

## roster_anomalies_causes_from_taxonomy

- [PASS] `roster_anomalies_causes_from_taxonomy` — все причины — из согласованной таксономии

## roster_size_not_five_flag_present

- [PASS] `roster_size_not_five_flag_present` — строк mart_team_summary с флагом roster_size_not_five: 12

## roster_anomalies_are_warning_not_critical

- [PASS] `roster_anomalies_are_warning_not_critical` — 12 аномалий(и) состава зафиксировано как warning, сборка не заблокирована
