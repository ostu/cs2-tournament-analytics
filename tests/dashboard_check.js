"use strict";
/*
 * Смоук-тест dashboard/js/app.js под реальным (хоть и минимальным) DOM —
 * без браузера, без сборки dashboard.html. Строит статическую разметку из
 * pipeline/dashboard_template.html (та же разметка, что попадёт в
 * dashboard.html), подставляет синтетические APP_DATA/APP_CONFIG и фейковый
 * echarts (перехватывает setOption, чтобы проверить реально переданные
 * данные графиков), затем гоняет init()/переключение вкладок/выбор игрока и
 * проверяет соответствие требованиям задания (см. docs/dashboard_spec.md и
 * секцию 7 задания "Обновить тесты").
 *
 * Печатает построчно "PASS <name>" / "FAIL <name>: <detail>" и завершается
 * ненулевым кодом, если хоть одна проверка провалена.
 *
 * Запуск: node tests/dashboard_check.js
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { createDocumentFromHtml } = require("./dom_shim");

const ROOT = path.resolve(__dirname, "..");
const results = [];

function check(name, condition, detail) {
  results.push({ name, passed: !!condition, detail: detail || "" });
}

// ---------------------------------------------------------------------------
// 1. Fixture DOM из реального шаблона (та же разметка, что в dashboard.html)
// ---------------------------------------------------------------------------

const templateHtml = fs.readFileSync(path.join(ROOT, "pipeline", "dashboard_template.html"), "utf-8");
const bodyStart = templateHtml.indexOf("<body>") + "<body>".length;
const bodyEnd = templateHtml.indexOf("<script", bodyStart);
const staticBodyHtml = templateHtml.slice(bodyStart, bodyEnd);

const document = createDocumentFromHtml(staticBodyHtml);

// ---------------------------------------------------------------------------
// 2. Синтетические данные — минимальный, но реалистичный набор
// ---------------------------------------------------------------------------

const APP_DATA = {
  metadata: {
    report_title: "Test Report",
    data_as_of: "2026-01-01T00:00:00",
    tournament_count: 2,
    public_player_count: 2,
    team_count: 2,
    map_count: 2,
    tournaments: [
      { tournament_number: 1, tournament_label: "Турнир 1" },
      { tournament_number: 2, tournament_label: "Турнир 2" },
    ],
    teams: ["TeamA", "TeamFlagged"],
    teams_by_tournament: { "1": ["TeamA", "TeamFlagged"], "2": ["TeamA", "TeamFlagged"] },
    maps: ["de_dust2", "de_mirage"],
    players: [
      { public_player_id: "pub1", public_nickname: "Alpha" },
      { public_player_id: "pub2", public_nickname: "Bravo" },
    ],
  },
  players: [
    {
      public_player_id: "pub1", public_nickname: "Alpha", latest_team: "TeamA", latest_tournament: 2,
      tournaments_played: 2, maps_played: 4, kills: 40, assists: 10, deaths: 30, kd: 1.33, kr: 0.8,
      average_kills_per_map: 10, adr: 75.5, adr_coverage: 1.0, mvps: 3, mvp_coverage: 1.0,
      rating_start: 1000, rating_final: 1200, rating_change: 200, rating_change_percent: 0.2,
      data_quality_flags: [],
    },
    {
      public_player_id: "pub2", public_nickname: "Bravo", latest_team: "TeamFlagged", latest_tournament: 2,
      tournaments_played: 1, maps_played: 2, kills: 20, assists: 5, deaths: 18, kd: 1.11, kr: 0.7,
      average_kills_per_map: 10, adr: null, adr_coverage: null, mvps: null, mvp_coverage: null,
      rating_start: 1000, rating_final: 1050, rating_change: 50, rating_change_percent: 0.05,
      data_quality_flags: [],
    },
  ],
  player_rating_history: [
    { public_player_id: "pub1", tournament_number: 1, rating_stage: "a", rating_stage_order: 10, event_type: "normalized_entry", event_sequence: 0, rating_before: null, rating_change: 1000, rating_after: 1000, tournament_label: "Турнир 1", map_name: null, map_number_canonical: null },
    { public_player_id: "pub1", tournament_number: 1, rating_stage: "t", rating_stage_order: 40, event_type: "tournament_final", event_sequence: 9000, rating_before: 1000, rating_change: 50, rating_after: 1050, tournament_label: "Турнир 1", map_name: null, map_number_canonical: null },
    { public_player_id: "pub1", tournament_number: 2, rating_stage: "a", rating_stage_order: 10, event_type: "normalized_entry", event_sequence: 0, rating_before: 1050, rating_change: 0, rating_after: 1050, tournament_label: "Турнир 2", map_name: null, map_number_canonical: null },
    { public_player_id: "pub1", tournament_number: 2, rating_stage: "c", rating_stage_order: 20, event_type: "calibration_final", event_sequence: 9000, rating_before: 1050, rating_change: 0, rating_after: 1050, tournament_label: "Турнир 2", map_name: null, map_number_canonical: null },
    { public_player_id: "pub1", tournament_number: 2, rating_stage: "t", rating_stage_order: 40, event_type: "tournament_map_change", event_sequence: 13, rating_before: 1050, rating_change: 50, rating_after: 1100, tournament_label: "Турнир 2", map_name: "de_dust2", map_number_canonical: 13 },
    { public_player_id: "pub1", tournament_number: 2, rating_stage: "t", rating_stage_order: 40, event_type: "tournament_map_change", event_sequence: 14, rating_before: 1100, rating_change: 100, rating_after: 1200, tournament_label: "Турнир 2", map_name: null, map_number_canonical: 14 },
    { public_player_id: "pub1", tournament_number: 2, rating_stage: "t", rating_stage_order: 40, event_type: "tournament_final", event_sequence: 9000, rating_before: 1050, rating_change: 150, rating_after: 1200, tournament_label: "Турнир 2", map_name: null, map_number_canonical: null },
  ],
  player_maps: [],
  teams: [
    { team_id: "TeamA", tournament_number: null, rating_final: 5100, rating_start: 5000, rating_change: 100, tournament_place: null, maps_played: 8, wins: 5, losses: 3, win_rate: 0.625, round_diff: 12, players_count: 5, data_quality_flags: [] },
    { team_id: "TeamA", tournament_number: 2, rating_final: 5100, rating_start: 5000, rating_change: 100, tournament_place: 1, maps_played: 8, wins: 5, losses: 3, win_rate: 0.625, round_diff: 12, players_count: 5, data_quality_flags: [] },
    { team_id: "TeamFlagged", tournament_number: null, rating_final: 4900, rating_start: 4800, rating_change: 100, tournament_place: null, maps_played: 6, wins: 2, losses: 4, win_rate: 0.33, round_diff: -6, players_count: 6, data_quality_flags: ["roster_size_not_five"] },
    { team_id: "TeamFlagged", tournament_number: 2, rating_final: 4900, rating_start: 4800, rating_change: 100, tournament_place: 2, maps_played: 6, wins: 2, losses: 4, win_rate: 0.33, round_diff: -6, players_count: 6, data_quality_flags: ["roster_size_not_five"] },
  ],
  team_rating_history: [],
  team_maps: [
    { team_id: "TeamA", opponent_team_id: "TeamFlagged", tournament_number: 2, tournament_phase: "t", map_number: 1, map_key: "de_dust2", win_loss: "W", result_parse_status: "ok", team_score: 16, opponent_score: 10, round_diff: 6, elo_change_total: 50 },
    { team_id: "TeamFlagged", opponent_team_id: "TeamA", tournament_number: 2, tournament_phase: "t", map_number: 1, map_key: "de_dust2", win_loss: "L", result_parse_status: "ok", team_score: 10, opponent_score: 16, round_diff: -6, elo_change_total: -50 },
  ],
  team_map_matrix: [],
  team_rating_comparison: [
    { team_id: "TeamA", tournament_number: 2, team_nc: 5000, team_nt: 5100, nc_players_count: 5, nt_players_count: 5, roster_players_count: 5 },
    { team_id: "TeamFlagged", tournament_number: 2, team_nc: 4800, team_nt: 4900, nc_players_count: 6, nt_players_count: 6, roster_players_count: 6 },
  ],
};

const APP_CONFIG = { low_sample_threshold: 3, report_title: "Test Report", locale: "ru-RU" };

// ---------------------------------------------------------------------------
// 3. Загрузка app.js в изолированный контекст с фейковым echarts
// ---------------------------------------------------------------------------

const echartsCalls = [];
const fakeEcharts = {
  init(el) {
    const chart = {
      setOption(opts) { this.lastOptions = opts; },
      dispose() {},
    };
    el._echartsInstance = chart;
    echartsCalls.push(el.id);
    return chart;
  },
};

const sandbox = {
  window: { APP_DATA: APP_DATA, APP_CONFIG: APP_CONFIG, echarts: fakeEcharts },
  document: document,
  console: console,
  Set: Set,
  Array: Array,
  Object: Object,
  Math: Math,
  JSON: JSON,
  parseInt: parseInt,
  String: String,
  Number: Number,
};
vm.createContext(sandbox);

const appJsSource = fs.readFileSync(path.join(ROOT, "dashboard", "js", "app.js"), "utf-8");

let runtimeError = null;
try {
  vm.runInContext(appJsSource, sandbox, { filename: "app.js" });
  document.fireReady();
} catch (err) {
  runtimeError = err;
}

check("app_js_executes_without_exceptions", !runtimeError, runtimeError ? String(runtimeError.stack || runtimeError) : "");

if (runtimeError) {
  report();
  process.exit(1);
}

function chartOptionsOf(elId) {
  const el = document.getElementById(elId);
  return el && el._echartsInstance ? el._echartsInstance.lastOptions : null;
}

function elementOrderIds(root) {
  const ids = [];
  (function walk(n) {
    if (n.nodeType === 1) {
      if (n.id) ids.push(n.id);
      (n.childNodes || []).forEach(walk);
    }
  })(root);
  return ids;
}

// ---------------------------------------------------------------------------
// 4. Overview
// ---------------------------------------------------------------------------

const REMOVED_OVERVIEW_IDS = ["chart-team-rating", "chart-player-rating", "chart-gainers", "chart-losers"];
REMOVED_OVERVIEW_IDS.forEach(function (id) {
  check(`overview_dom_container_absent:${id}`, document.getElementById(id) === null,
    `#${id} должен отсутствовать после удаления графика`);
  check(`overview_renderer_not_invoked:${id}`, echartsCalls.indexOf(id) === -1,
    `echarts.init не должен вызываться для #${id}`);
});

check("overview_calib_final_present", document.getElementById("chart-calib-final") !== null,
  "#chart-calib-final (Nc vs Nt) должен присутствовать на Overview");
check("overview_calib_final_invoked", echartsCalls.indexOf("chart-calib-final") !== -1,
  "renderCalibFinalChart должен инициализировать #chart-calib-final");

const overviewOrder = elementOrderIds(document.getElementById("page-overview"));
const calibIdx = overviewOrder.indexOf("chart-calib-final");
const mapDistIdx = overviewOrder.indexOf("chart-map-dist");
check("overview_calib_final_is_first_chart", calibIdx !== -1 && (mapDistIdx === -1 || calibIdx < mapDistIdx),
  `порядок DOM: ${overviewOrder.join(", ")}`);

const calibTitleEl = document.getElementById("calib-final-title");
check("overview_calib_final_subtitle_shows_latest_tournament",
  !!calibTitleEl && /показан последний доступный турнир: 2/.test(calibTitleEl.textContent),
  `фактический заголовок: "${calibTitleEl ? calibTitleEl.textContent : null}"`);

const calibOptions = chartOptionsOf("chart-calib-final");
check("overview_calib_final_has_source_rows", !!calibOptions && calibOptions.series[0].data.length > 0,
  "Nc/Nt чарт должен получить хотя бы одну строку данных");

// ---------------------------------------------------------------------------
// 5. Players table — без CTA
// ---------------------------------------------------------------------------

document.querySelector('.tab-btn[data-tab="players"]').dispatch("click");

const headerCells = document.querySelectorAll("#players-table thead th").map(function (th) { return th.textContent; });
check("players_table_no_adr_coverage_header", headerCells.indexOf("Покрытие ADR") === -1,
  `заголовки: ${headerCells.join(" | ")}`);
check("players_table_has_adr_header", headerCells.indexOf("ADR") !== -1,
  `заголовки: ${headerCells.join(" | ")}`);

const rows = document.querySelectorAll("#players-table tbody tr[data-player]");
const adrColIndex = headerCells.indexOf("ADR");
const bravoRow = rows.filter(function (tr) { return tr.dataset.player === "pub2"; })[0];
const bravoAdrCell = bravoRow ? bravoRow.childNodes.filter(function (c) { return c.tagName === "TD"; })[adrColIndex] : null;
check("players_table_missing_adr_shows_dash", !!bravoAdrCell && bravoAdrCell.textContent.trim() === "—",
  `ADR-ячейка игрока без трекинга: "${bravoAdrCell ? bravoAdrCell.textContent : null}"`);

// ---------------------------------------------------------------------------
// 6. Player selection CTA
// ---------------------------------------------------------------------------

const profileBeforeSelect = document.getElementById("player-profile");
check("player_cta_visible_before_selection",
  profileBeforeSelect.querySelectorAll(".cta-empty-state").length === 1,
  "до выбора игрока должен быть виден CTA-блок");
check("player_cta_has_heading",
  profileBeforeSelect.querySelectorAll(".cta-empty-state h3").filter(function (h) { return h.textContent === "Выберите игрока"; }).length === 1,
  "CTA должен содержать заголовок 'Выберите игрока'");

const alphaRow = rows.filter(function (tr) { return tr.dataset.player === "pub1"; })[0];
alphaRow.dispatch("click");

const profileAfterSelect = document.getElementById("player-profile");
check("player_cta_hidden_after_selection",
  profileAfterSelect.querySelectorAll(".cta-empty-state").length === 0,
  "после выбора игрока CTA должен исчезнуть");
check("player_profile_shown_after_selection",
  profileAfterSelect.querySelectorAll(".profile-header h2").filter(function (h) { return h.textContent === "Alpha"; }).length === 1,
  "после выбора игрока должен отобразиться профиль выбранного игрока");

// ---------------------------------------------------------------------------
// 7. Rating-cycle chart: legend + map labels
// ---------------------------------------------------------------------------

const legendEls = profileAfterSelect.querySelectorAll(".stage-legend");
check("rating_stage_legend_present", legendEls.length === 1, "легенда A/C/N/T должна быть рядом с графиком стадий");
if (legendEls.length) {
  const legendText = legendEls[0].textContent;
  ["старт после нормализации", "после калибровочных игр", "формирования постоянных команд", "итог после турнира"].forEach(function (fragment) {
    check(`rating_stage_legend_mentions:${fragment}`, legendText.indexOf(fragment) !== -1, `легенда: "${legendText}"`);
  });
}

const waterfallOptions = chartOptionsOf("chart-player-waterfall");
check("waterfall_chart_rendered", !!waterfallOptions, "waterfall для последнего турнира игрока должен отрисоваться");
if (waterfallOptions) {
  const labels = waterfallOptions.xAxis.data;
  check("waterfall_known_map_label_formatted", labels.indexOf("Dust2 (13)") !== -1,
    `labels: ${JSON.stringify(labels)}`);
  check("waterfall_unknown_map_label_fallback", labels.indexOf("Карта 14") !== -1,
    `labels: ${JSON.stringify(labels)}`);
  check("waterfall_stage_label_still_uppercase", labels.indexOf("A") !== -1 && labels.indexOf("C") !== -1,
    `labels: ${JSON.stringify(labels)}`);
}

// ---------------------------------------------------------------------------
// 8. Teams tab — friendly roster_size_not_five banner
// ---------------------------------------------------------------------------

document.querySelector('.tab-btn[data-tab="teams"]').dispatch("click");
const teamRows = document.querySelectorAll("#teams-table tbody tr[data-team]");
const flaggedTeamRow = teamRows.filter(function (tr) { return tr.dataset.team === "TeamFlagged"; })[0];
check("teams_table_has_flagged_team_row", !!flaggedTeamRow, "строка TeamFlagged должна быть в таблице команд");
if (flaggedTeamRow) flaggedTeamRow.dispatch("click");

const teamProfile = document.getElementById("team-profile");
const banner = teamProfile.querySelectorAll(".warning-banner");
check("team_profile_shows_friendly_roster_banner",
  banner.length > 0 && banner[0].textContent.indexOf("Состав команды в данных содержит 4/6 игроков") !== -1,
  `банер: ${banner.length ? banner[0].textContent : "(нет)"}`);

// ---------------------------------------------------------------------------
// 9. Overview <-> Teams <-> Players round-trip does not throw again
// ---------------------------------------------------------------------------

try {
  document.querySelector('.tab-btn[data-tab="overview"]').dispatch("click");
  document.querySelector('.tab-btn[data-tab="teammap"]').dispatch("click");
  check("tab_roundtrip_no_exceptions", true, "");
} catch (err) {
  check("tab_roundtrip_no_exceptions", false, String(err.stack || err));
}

// ---------------------------------------------------------------------------
report();

function report() {
  let failed = 0;
  results.forEach(function (r) {
    if (r.passed) {
      console.log(`PASS ${r.name}`);
    } else {
      failed++;
      console.log(`FAIL ${r.name}: ${r.detail}`);
    }
  });
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  if (failed > 0) process.exit(1);
}
