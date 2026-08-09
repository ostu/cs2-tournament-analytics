/* CS2 Tournament Report — vanilla JS. Все данные — из window.APP_DATA
   (инлайнится build_dashboard.py из data/public/*.json). Не содержит
   приватных идентификаторов игроков — только public_player_id/public_nickname.
   Калибровочные (временные) команды исключены на уровне данных (см.
   pipeline/build_dimensions.py::classify_team_types) — DATA.teams,
   DATA.team_maps, DATA.team_map_matrix, DATA.team_rating_comparison и
   metadata.teams содержат только постоянные турнирные команды. */

(function () {
  "use strict";

  var DATA = window.APP_DATA;
  var CFG = window.APP_CONFIG;

  var dashboardState = {
    tournament: null,
    team: null,
    player: null,
    map: null,
    heatmapMetric: "elo_change",
  };

  var charts = {};

  // ------------------------------------------------------------------
  // форматирование
  // ------------------------------------------------------------------

  function isNil(v) { return v === null || v === undefined; }

  function fmtInt(v) {
    if (isNil(v)) return naSpan();
    return Math.round(v).toLocaleString("ru-RU");
  }

  function fmtSignedInt(v) {
    if (isNil(v)) return naSpan();
    var r = Math.round(v);
    var cls = r > 0 ? "positive" : (r < 0 ? "negative" : "");
    var sign = r > 0 ? "+" : "";
    return '<span class="' + cls + '">' + sign + r.toLocaleString("ru-RU") + "</span>";
  }

  function fmtRating(v) { return isNil(v) ? naSpan() : Math.round(v).toLocaleString("ru-RU"); }

  function fmtRatio(v) { return isNil(v) ? naSpan() : v.toFixed(2); }

  function fmtAvg(v) { return isNil(v) ? naSpan() : v.toFixed(1); }

  function fmtPercent(v) {
    if (isNil(v)) return naSpan();
    var pct = v * 100;
    var cls = pct > 0 ? "positive" : (pct < 0 ? "negative" : "");
    var sign = pct > 0 ? "+" : "";
    return '<span class="' + cls + '">' + sign + pct.toFixed(1) + "%</span>";
  }

  function fmtPercentPlain(v) { return isNil(v) ? naSpan() : (v * 100).toFixed(1) + "%"; }

  function naSpan() { return '<span class="na">—</span>'; }

  function escapeHtml(s) {
    if (isNil(s)) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function sumField(rows, field) {
    return rows.reduce(function (s, r) { return s + (isNil(r[field]) ? 0 : r[field]); }, 0);
  }

  function avgField(rows, field) {
    var vals = rows.map(function (r) { return r[field]; }).filter(function (v) { return !isNil(v); });
    if (!vals.length) return null;
    return vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
  }

  // ------------------------------------------------------------------
  // доступ к данным
  // ------------------------------------------------------------------

  function teamAllTimeRows() {
    return DATA.teams.filter(function (t) { return t.tournament_number === null; });
  }

  function teamTournamentRows(tournamentNumber) {
    return DATA.teams.filter(function (t) { return t.tournament_number === tournamentNumber; });
  }

  function playerRatingHistoryOf(pubId) {
    return DATA.player_rating_history
      .filter(function (r) { return r.public_player_id === pubId; })
      .sort(function (a, b) {
        return a.tournament_number - b.tournament_number ||
          a.rating_stage_order - b.rating_stage_order || a.event_sequence - b.event_sequence;
      });
  }

  function playerMapsOf(pubId) {
    return DATA.player_maps.filter(function (r) { return r.public_player_id === pubId; });
  }

  function playerByPubId(pubId) {
    return DATA.players.find(function (p) { return p.public_player_id === pubId; });
  }

  function teamMapsOf(teamId) {
    return DATA.team_maps.filter(function (r) { return r.team_id === teamId; });
  }

  function teamRatingHistoryOf(teamId) {
    return DATA.team_rating_history
      .filter(function (r) { return r.team_id === teamId; })
      .sort(function (a, b) { return a.tournament_number - b.tournament_number || a.rating_stage_order - b.rating_stage_order; });
  }

  function ratingForTournament(pubId, tournamentNumber, eventType) {
    var row = DATA.player_rating_history.find(function (r) {
      return r.public_player_id === pubId && r.tournament_number === tournamentNumber && r.event_type === eventType;
    });
    return row ? row.rating_after : null;
  }

  // ------------------------------------------------------------------
  // каскадные фильтры: tournament -> team -> player
  // ------------------------------------------------------------------

  function availableTeamIds() {
    if (dashboardState.tournament !== null) {
      return DATA.metadata.teams_by_tournament[String(dashboardState.tournament)] || [];
    }
    return DATA.metadata.teams.slice();
  }

  function participatingPlayerRows() {
    return DATA.player_maps.filter(function (r) {
      if (dashboardState.tournament !== null && r.tournament_number !== dashboardState.tournament) return false;
      if (dashboardState.team !== null && r.team_id !== dashboardState.team) return false;
      return true;
    });
  }

  function availablePlayerIds() {
    if (dashboardState.tournament === null && dashboardState.team === null) {
      return DATA.metadata.players.map(function (p) { return p.public_player_id; });
    }
    var seen = {};
    participatingPlayerRows().forEach(function (r) { seen[r.public_player_id] = true; });
    return Object.keys(seen);
  }

  function rebuildTeamOptions() {
    var sel = document.getElementById("filter-team");
    var available = availableTeamIds().slice().sort();
    var current = dashboardState.team;
    sel.innerHTML = '<option value="">Все команды</option>';
    available.forEach(function (name) {
      var opt = document.createElement("option");
      opt.value = name; opt.textContent = name;
      sel.appendChild(opt);
    });
    if (current !== null && available.indexOf(current) === -1) {
      dashboardState.team = null;
    }
    sel.value = dashboardState.team || "";
  }

  function rebuildPlayerOptions() {
    var sel = document.getElementById("filter-player");
    var availableIds = availablePlayerIds();
    var idSet = {};
    availableIds.forEach(function (id) { idSet[id] = true; });
    var current = dashboardState.player;

    var options = DATA.metadata.players
      .filter(function (p) { return idSet[p.public_player_id]; })
      .slice()
      .sort(function (a, b) { return a.public_nickname.localeCompare(b.public_nickname); });

    sel.innerHTML = '<option value="">Все игроки</option>';
    options.forEach(function (p) {
      var opt = document.createElement("option");
      opt.value = p.public_player_id; opt.textContent = p.public_nickname;
      sel.appendChild(opt);
    });
    if (current !== null && !idSet[current]) {
      dashboardState.player = null;
    }
    sel.value = dashboardState.player || "";
  }

  function initFilterBar() {
    var tSel = document.getElementById("filter-tournament");
    DATA.metadata.tournaments.forEach(function (t) {
      var opt = document.createElement("option");
      opt.value = t.tournament_number;
      opt.textContent = t.tournament_label;
      tSel.appendChild(opt);
    });

    var mapSel = document.getElementById("filter-map");
    DATA.metadata.maps.forEach(function (m) {
      var opt = document.createElement("option");
      opt.value = m; opt.textContent = m;
      mapSel.appendChild(opt);
    });

    rebuildTeamOptions();
    rebuildPlayerOptions();

    tSel.addEventListener("change", function () {
      dashboardState.tournament = tSel.value ? parseInt(tSel.value, 10) : null;
      // 1) команда: сброс, если недоступна в новом турнире
      rebuildTeamOptions();
      // 2) игрок: сброс, если недоступен при новом (турнир, команда)
      rebuildPlayerOptions();
      renderCurrentTab();
    });

    document.getElementById("filter-team").addEventListener("change", function (e) {
      dashboardState.team = e.target.value || null;
      rebuildPlayerOptions();
      renderCurrentTab();
    });

    document.getElementById("filter-player").addEventListener("change", function (e) {
      dashboardState.player = e.target.value || null;
      renderCurrentTab();
    });

    mapSel.addEventListener("change", function () { dashboardState.map = mapSel.value || null; renderCurrentTab(); });

    document.getElementById("btn-reset-filters").addEventListener("click", function () {
      dashboardState.tournament = null; dashboardState.team = null; dashboardState.player = null; dashboardState.map = null;
      tSel.value = ""; mapSel.value = "";
      rebuildTeamOptions();
      rebuildPlayerOptions();
      renderCurrentTab();
    });
  }

  function setPlayerFilter(pubId) {
    dashboardState.player = pubId;
    var sel = document.getElementById("filter-player");
    if (pubId && !Array.prototype.some.call(sel.options, function (o) { return o.value === pubId; })) {
      // выбранный игрок не входит в текущий отфильтрованный список — не
      // трогаем список опций, просто снимаем визуальное выделение
      sel.value = "";
      return;
    }
    sel.value = pubId || "";
  }

  function setTeamFilter(teamId) {
    dashboardState.team = teamId;
    rebuildPlayerOptions();
    document.getElementById("filter-team").value = teamId || "";
  }

  // ------------------------------------------------------------------
  // ECharts helper
  // ------------------------------------------------------------------

  function ensureChart(elId) {
    var el = document.getElementById(elId);
    if (!el) return null;
    if (!window.echarts) {
      el.innerHTML = '<div class="empty-state">График недоступен: библиотека ECharts не загрузилась ' +
        '(нет подключения к CDN). Для офлайн-режима см. README.md — «известные ограничения».</div>';
      return null;
    }
    if (charts[elId]) { charts[elId].dispose(); }
    charts[elId] = window.echarts.init(el);
    return charts[elId];
  }

  function emptyChartMessage(elId, message) {
    var el = document.getElementById(elId);
    if (el) el.innerHTML = '<div class="empty-state">' + escapeHtml(message) + "</div>";
  }

  var CHART_COLORS = ["#2f5fd0", "#1f9254", "#c23b3b", "#b8860b", "#7a5ad1", "#0f9aa5"];

  // ------------------------------------------------------------------
  // tabs
  // ------------------------------------------------------------------

  function setActiveTab(tab) {
    document.querySelectorAll(".tab-btn").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === tab);
    });
    document.querySelectorAll(".tab-page").forEach(function (p) {
      p.classList.toggle("active", p.id === "page-" + tab);
    });
    renderCurrentTab();
  }

  function currentTab() {
    var active = document.querySelector(".tab-btn.active");
    return active ? active.dataset.tab : "overview";
  }

  function renderCurrentTab() {
    var tab = currentTab();
    if (tab === "overview") renderOverview();
    else if (tab === "players") renderPlayers();
    else if (tab === "teams") renderTeams();
    else if (tab === "teammap") renderTeamMap();
  }

  // ==================================================================
  // OVERVIEW
  // ==================================================================

  function renderOverview() {
    var root = document.getElementById("page-overview");
    var totalMapsPlayed = new Set(DATA.team_maps.map(function (r) {
      return r.tournament_number + "-" + r.tournament_phase + "-" + r.map_number;
    })).size;
    var adrTracked = DATA.player_maps.filter(function (r) { return r.adr_tracked; }).length;
    var adrCoverage = DATA.player_maps.length ? adrTracked / DATA.player_maps.length : null;

    root.innerHTML =
      '<div class="grid grid-kpi" style="margin-bottom:20px;">' +
        kpiTile(DATA.metadata.tournament_count, "Турниров") +
        kpiTile(DATA.metadata.public_player_count, "Игроков") +
        kpiTile(DATA.metadata.team_count, "Постоянных команд") +
        kpiTile(DATA.metadata.map_count, "Карт (типов)") +
        kpiTile(totalMapsPlayed, "Карт сыграно") +
        kpiTile(fmtPercentPlain(adrCoverage), "Покрытие ADR", true) +
      '</div>' +
      '<div class="card card-primary" style="margin-bottom:16px;">' +
        '<h3 id="calib-final-title">Калибровочный (Nc) vs итоговый (Nt) рейтинг команд</h3>' +
        '<div id="chart-calib-final" class="chart tall"></div>' +
      '</div>' +
      '<div class="card"><h3>Сыгранные карты по типам</h3><div id="chart-map-dist" class="chart"></div></div>';

    renderCalibFinalChart();
    renderMapDistChart();
  }

  function kpiTile(value, label, isHtml) {
    return '<div class="card kpi-tile"><div class="value">' + (isHtml ? value : escapeHtml(String(value))) +
      '</div><div class="label">' + label + "</div></div>";
  }

  function renderCalibFinalChart() {
    var titleEl = document.getElementById("calib-final-title");
    var comparison = DATA.team_rating_comparison || [];
    var tournamentToShow = dashboardState.tournament;
    var subtitleSuffix = "";

    if (tournamentToShow === null) {
      var maxT = comparison.reduce(function (m, r) { return Math.max(m, r.tournament_number); }, 0);
      tournamentToShow = maxT || null;
      subtitleSuffix = " — показан последний доступный турнир: " + tournamentToShow;
    } else {
      subtitleSuffix = " — турнир " + tournamentToShow;
    }
    if (titleEl) titleEl.textContent = "Калибровочный (Nc) vs итоговый (Nt) рейтинг команд" + subtitleSuffix;

    var chart = ensureChart("chart-calib-final");
    if (!chart) return;

    var rows = comparison.filter(function (r) { return r.tournament_number === tournamentToShow; });
    if (dashboardState.team !== null) rows = rows.filter(function (r) { return r.team_id === dashboardState.team; });
    rows = rows.filter(function (r) { return r.team_nc !== null && r.team_nt !== null; })
      .sort(function (a, b) { return b.team_nt - a.team_nt; }).slice(0, 10);

    if (!rows.length) {
      emptyChartMessage("chart-calib-final", "Нет команд с полным покрытием Nc/Nt для этого турнира.");
      return;
    }
    chart.setOption({
      grid: { left: 110, right: 20, top: 30, bottom: 20 },
      legend: { top: 0, data: ["Nc (сумма состава)", "Nt (сумма состава)"] },
      xAxis: { type: "value" },
      yAxis: { type: "category", data: rows.map(function (r) { return r.team_id; }).reverse(), axisLabel: { fontSize: 11 } },
      tooltip: {
        trigger: "axis", axisPointer: { type: "shadow" },
        formatter: function (params) {
          var idx = rows.length - 1 - params[0].dataIndex;
          var r = rows[idx];
          return r.team_id + "<br/>Nc: " + Math.round(r.team_nc) + " (" + r.nc_players_count + " игр.)<br/>" +
            "Nt: " + Math.round(r.team_nt) + " (" + r.nt_players_count + " игр.)<br/>" +
            "Состав: " + r.roster_players_count + " игр.";
        },
      },
      series: [
        { name: "Nc (сумма состава)", type: "bar", data: rows.map(function (r) { return Math.round(r.team_nc); }).reverse(), itemStyle: { color: CHART_COLORS[3] } },
        { name: "Nt (сумма состава)", type: "bar", data: rows.map(function (r) { return Math.round(r.team_nt); }).reverse(), itemStyle: { color: CHART_COLORS[0] } },
      ],
    });
  }

  function renderMapDistChart() {
    var chart = ensureChart("chart-map-dist");
    if (!chart) return;
    var rows = DATA.team_maps;
    if (dashboardState.tournament !== null) rows = rows.filter(function (r) { return r.tournament_number === dashboardState.tournament; });
    var byMap = {};
    var seen = {};
    rows.forEach(function (r) {
      var key = r.tournament_number + "-" + r.tournament_phase + "-" + r.map_number;
      if (seen[key]) return;
      seen[key] = true;
      byMap[r.map_key] = (byMap[r.map_key] || 0) + 1;
    });
    var entries = Object.keys(byMap).map(function (k) { return [k, byMap[k]]; }).sort(function (a, b) { return b[1] - a[1]; });
    if (!entries.length) {
      emptyChartMessage("chart-map-dist", "Нет данных для текущих фильтров.");
      return;
    }
    chart.setOption({
      grid: { left: 100, right: 20, top: 10, bottom: 20 },
      xAxis: { type: "value" },
      yAxis: { type: "category", data: entries.map(function (e) { return e[0]; }).reverse(), axisLabel: { fontSize: 11 } },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      series: [{ type: "bar", data: entries.map(function (e) { return e[1]; }).reverse(), itemStyle: { color: CHART_COLORS[4] } }],
    });
  }

  // ==================================================================
  // PLAYERS
  // ==================================================================

  var playerSort = { col: "rating_final", dir: -1 };

  function computePlayerRowForContext(pubId) {
    var rows = DATA.player_maps.filter(function (r) { return r.public_player_id === pubId; });
    if (dashboardState.tournament !== null) rows = rows.filter(function (r) { return r.tournament_number === dashboardState.tournament; });
    if (dashboardState.team !== null) rows = rows.filter(function (r) { return r.team_id === dashboardState.team; });
    if (!rows.length) return null;

    var base = playerByPubId(pubId) || {};
    var mapsPlayed = rows.length;
    var kills = sumField(rows, "kills"), assists = sumField(rows, "assists"), deaths = sumField(rows, "deaths");
    var roundsPlayed = sumField(rows, "rounds_played");
    var kd = deaths ? kills / deaths : null;
    var kr = roundsPlayed ? kills / roundsPlayed : null;

    var canonicalMapsSet = {};
    rows.forEach(function (r) { canonicalMapsSet[r.tournament_number + "-" + r.tournament_phase + "-" + r.map_number] = true; });
    var canonicalMaps = Object.keys(canonicalMapsSet).length;
    var avgKillsPerMap = canonicalMaps ? kills / canonicalMaps : null;

    var adrRows = rows.filter(function (r) { return r.adr_tracked; });
    var adr = adrRows.length ? avgField(adrRows, "adr") : null;
    var adrCoverage = mapsPlayed ? adrRows.length / mapsPlayed : null;

    var mvpRows = rows.filter(function (r) { return r.mvp_tracked; });
    var mvps = mvpRows.length ? sumField(mvpRows, "mvps") : null;
    var mvpCoverage = mapsPlayed ? mvpRows.length / mapsPlayed : null;

    var tournamentsPlayedSet = {};
    rows.filter(function (r) { return r.tournament_phase === "t"; }).forEach(function (r) { tournamentsPlayedSet[r.tournament_number] = true; });

    var teamShown = dashboardState.team;
    if (!teamShown) {
      var counts = {};
      rows.forEach(function (r) { counts[r.team_id] = (counts[r.team_id] || 0) + 1; });
      teamShown = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; })[0] || null;
    }

    var ratingStart = base.rating_start, ratingFinal = base.rating_final;
    if (dashboardState.tournament !== null) {
      ratingStart = ratingForTournament(pubId, dashboardState.tournament, "normalized_entry");
      ratingFinal = ratingForTournament(pubId, dashboardState.tournament, "tournament_final");
    }
    var ratingChange = (!isNil(ratingStart) && !isNil(ratingFinal)) ? ratingFinal - ratingStart : null;
    var ratingChangePercent = (!isNil(ratingChange) && ratingStart) ? ratingChange / ratingStart : null;

    return {
      public_player_id: pubId,
      public_nickname: base.public_nickname || pubId,
      latest_team: teamShown,
      tournaments_played: Object.keys(tournamentsPlayedSet).length,
      maps_played: mapsPlayed,
      kills: kills, assists: assists, deaths: deaths, kd: kd, kr: kr,
      average_kills_per_map: avgKillsPerMap,
      adr: adr, adr_coverage: adrCoverage, mvps: mvps, mvp_coverage: mvpCoverage,
      rating_start: ratingStart, rating_final: ratingFinal,
      rating_change: ratingChange, rating_change_percent: ratingChangePercent,
      data_quality_flags: base.data_quality_flags || [],
    };
  }

  function playersForCurrentContext() {
    if (dashboardState.tournament === null && dashboardState.team === null) {
      return DATA.players.slice();
    }
    var ids = availablePlayerIdsFromContext();
    return ids.map(computePlayerRowForContext).filter(function (r) { return r !== null; });
  }

  function availablePlayerIdsFromContext() {
    var seen = {};
    participatingPlayerRows().forEach(function (r) { seen[r.public_player_id] = true; });
    return Object.keys(seen);
  }

  function renderPlayers() {
    var root = document.getElementById("page-players");
    var rows = playersForCurrentContext();

    root.innerHTML =
      '<div class="table-wrap" style="margin-bottom:20px;"><table class="data-table" id="players-table"><thead><tr>' +
      playerHeaderCells() +
      '</tr></thead><tbody></tbody></table></div>' +
      '<div id="player-profile"></div>';

    document.querySelectorAll("#players-table thead th").forEach(function (th) {
      th.addEventListener("click", function () {
        var col = th.dataset.col;
        if (playerSort.col === col) playerSort.dir *= -1; else { playerSort.col = col; playerSort.dir = -1; }
        renderPlayersTableBody(rows);
      });
    });

    renderPlayersTableBody(rows);

    if (dashboardState.player) {
      renderPlayerProfile(dashboardState.player);
    } else {
      document.getElementById("player-profile").innerHTML = playerSelectionCta();
    }
  }

  function playerSelectionCta() {
    return '<div class="cta-empty-state">' +
      '<div class="cta-icon">' + PLAYER_ICON_SVG + '</div>' +
      '<h3>Выберите игрока</h3>' +
      '<p>Выберите игрока в таблице ниже или воспользуйтесь фильтром сверху, чтобы открыть подробный профиль, историю рейтинга и статистику по картам.</p>' +
    '</div>';
  }

  var PLAYER_ICON_SVG = '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
    '<circle cx="12" cy="8" r="4" stroke="currentColor" stroke-width="1.6"/>' +
    '<path d="M4 20c0-4.4 3.6-7 8-7s8 2.6 8 7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>' +
    '</svg>';

  var PLAYER_COLUMNS = [
    { col: "public_nickname", label: "Игрок" },
    { col: "latest_team", label: "Команда" },
    { col: "tournaments_played", label: "Турниров", fmt: fmtInt },
    { col: "maps_played", label: "Карт", fmt: fmtInt },
    { col: "kills", label: "K", fmt: fmtInt },
    { col: "assists", label: "A", fmt: fmtInt },
    { col: "deaths", label: "D", fmt: fmtInt },
    { col: "kd", label: "K/D", fmt: fmtRatio },
    { col: "kr", label: "K/R", fmt: fmtRatio },
    { col: "average_kills_per_map", label: "Убийств/карту", fmt: fmtAvg },
    { col: "adr", label: "ADR", fmt: fmtAvg },
    { col: "rating_start", label: "Elo начало", fmt: fmtRating },
    { col: "rating_final", label: "Elo итог", fmt: fmtRating },
    { col: "rating_change", label: "Изм. Elo", fmt: fmtSignedInt },
    { col: "rating_change_percent", label: "Изм. Elo, %", fmt: fmtPercent },
  ];

  function playerHeaderCells() {
    return PLAYER_COLUMNS.map(function (c) {
      var cls = playerSort.col === c.col ? (playerSort.dir === 1 ? "sorted-asc" : "sorted-desc") : "";
      return '<th data-col="' + c.col + '" class="' + cls + '">' + c.label + "</th>";
    }).join("");
  }

  function renderPlayersTableBody(rows) {
    var sorted = rows.slice().sort(function (a, b) {
      var av = a[playerSort.col], bv = b[playerSort.col];
      if (isNil(av) && isNil(bv)) return 0;
      if (isNil(av)) return 1;
      if (isNil(bv)) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * playerSort.dir;
      return (av - bv) * playerSort.dir;
    });
    var tbody = document.querySelector("#players-table tbody");
    if (!sorted.length) {
      tbody.innerHTML = '<tr><td colspan="' + PLAYER_COLUMNS.length + '"><div class="empty-state">Нет игроков для текущих фильтров.</div></td></tr>';
      return;
    }
    tbody.innerHTML = sorted.map(function (p) {
      var cells = PLAYER_COLUMNS.map(function (c) {
        var raw = p[c.col];
        var html = c.fmt ? c.fmt(raw) : (isNil(raw) ? naSpan() : escapeHtml(raw));
        var cls = c.fmt ? "num" : "";
        return '<td class="' + cls + '">' + html + "</td>";
      }).join("");
      return '<tr data-player="' + p.public_player_id + '">' + cells + "</tr>";
    }).join("");

    tbody.querySelectorAll("tr[data-player]").forEach(function (tr) {
      tr.addEventListener("click", function () {
        setPlayerFilter(tr.dataset.player);
        renderPlayerProfile(tr.dataset.player);
      });
    });
  }

  function renderPlayerProfile(pubId) {
    var player = playerByPubId(pubId);
    var container = document.getElementById("player-profile");
    if (!player) { container.innerHTML = '<div class="empty-state">Нет данных по игроку.</div>'; return; }

    var history = playerRatingHistoryOf(pubId);
    var maps = playerMapsOf(pubId);
    var flagsHtml = renderFlagsBanner(player.data_quality_flags);

    container.innerHTML =
      '<div class="profile-header"><h2>' + escapeHtml(player.public_nickname) + "</h2><span class=\"badge badge-muted\">" +
      escapeHtml(player.latest_team || "без команды") + "</span></div>" +
      flagsHtml +
      '<div class="grid grid-2" style="margin-bottom:16px;">' +
        '<div class="card"><h3>Динамика Elo по турнирам</h3><div id="chart-player-timeline" class="chart"></div></div>' +
        '<div class="card"><h3>Стадии рейтингового цикла (a → c → n → t), последний турнир</h3>' +
          ratingStageLegend() +
          '<div id="chart-player-waterfall" class="chart"></div></div>' +
      '</div>' +
      '<div class="card"><h3>Статистика по картам</h3><div class="table-wrap">' +
      renderPlayerMapsTable(maps) + "</div></div>";

    renderPlayerTimelineChart(history);
    renderPlayerWaterfallChart(history, player.latest_tournament);
  }

  var FLAG_LABELS = {
    roster_size_not_five: "Состав команды в данных содержит 4/6 игроков; возможна замена или неполная запись.",
  };

  function renderFlagsBanner(flags) {
    if (!flags || !flags.length) return "";
    var parts = flags.map(function (f) { return FLAG_LABELS[f] || ("Особенности данных: " + f); });
    return '<div class="warning-banner">' + parts.map(escapeHtml).join(" ") + "</div>";
  }

  function ratingStageLegend() {
    return '<div class="stage-legend">' +
      '<span class="stage-badge"><b>A</b> — старт после нормализации</span>' +
      '<span class="stage-badge"><b>C</b> — после калибровочных игр</span>' +
      '<span class="stage-badge"><b>N</b> — рейтинг для формирования постоянных команд</span>' +
      '<span class="stage-badge"><b>T</b> — итог после турнира</span>' +
    '</div>';
  }

  function formatMapEventLabel(mapName, mapNumber) {
    var num = isNil(mapNumber) ? "" : String(Math.round(mapNumber));
    if (isNil(mapName) || mapName === "") return "Карта " + num;
    var short = String(mapName).replace(/^de_/i, "");
    short = short.charAt(0).toUpperCase() + short.slice(1);
    return short + " (" + num + ")";
  }

  function renderPlayerTimelineChart(history) {
    var chart = ensureChart("chart-player-timeline");
    if (!chart) return;
    var finals = history.filter(function (h) { return h.event_type === "tournament_final"; });
    if (!finals.length) { emptyChartMessage("chart-player-timeline", "Нет данных."); return; }
    chart.setOption({
      grid: { left: 50, right: 20, top: 20, bottom: 40 },
      xAxis: { type: "category", data: finals.map(function (h) { return h.tournament_label; }) },
      yAxis: { type: "value", name: "Elo" },
      tooltip: { trigger: "axis" },
      series: [{ type: "line", data: finals.map(function (h) { return Math.round(h.rating_after); }), smooth: true, itemStyle: { color: CHART_COLORS[0] }, areaStyle: { opacity: 0.08 } }],
    });
  }

  function renderPlayerWaterfallChart(history, tournamentNumber) {
    var chart = ensureChart("chart-player-waterfall");
    if (!chart) return;
    var rows = history.filter(function (h) { return h.tournament_number === tournamentNumber; });
    if (!rows.length) {
      emptyChartMessage("chart-player-waterfall", "Нет данных за этот турнир.");
      return;
    }
    var labels = rows.map(function (h) {
      if (h.event_type === "tournament_map_change" || h.event_type === "calibration_game") {
        return formatMapEventLabel(h.map_name, h.map_number_canonical);
      }
      return h.rating_stage ? h.rating_stage.toUpperCase() : h.event_type;
    });
    var base = rows.map(function (h) { return Math.min(h.rating_before === null ? h.rating_after : h.rating_before, h.rating_after); });
    var height = rows.map(function (h) { return Math.abs((h.rating_change === null ? 0 : h.rating_change)); });
    chart.setOption({
      grid: { left: 50, right: 20, top: 20, bottom: 60 },
      xAxis: { type: "category", data: labels, axisLabel: { rotate: 45, fontSize: 10 } },
      yAxis: { type: "value", name: "Elo" },
      tooltip: { trigger: "axis" },
      series: [
        { type: "bar", stack: "wf", data: base, itemStyle: { color: "transparent" } },
        { type: "bar", stack: "wf", data: height, itemStyle: { color: CHART_COLORS[0] } },
      ],
    });
  }

  function renderPlayerMapsTable(maps) {
    if (!maps.length) return '<div class="empty-state">Нет сыгранных карт.</div>';
    var rows = maps.slice().sort(function (a, b) { return b.tournament_number - a.tournament_number || b.map_number - a.map_number; });
    var head = "<tr><th>Турнир</th><th>Стадия</th><th>Карта</th><th>Команда</th><th>W/L</th>" +
      "<th>K</th><th>A</th><th>D</th><th>ADR</th><th>MVP</th><th>Изм. Elo</th></tr>";
    var body = rows.map(function (r) {
      return "<tr><td>" + r.tournament_number + "</td><td>" + escapeHtml(r.tournament_phase) + "</td><td>" +
        escapeHtml(r.map_key) + "</td><td>" + escapeHtml(r.team_id) + "</td><td>" + escapeHtml(r.win_loss) + "</td>" +
        '<td class="num">' + fmtInt(r.kills) + '</td><td class="num">' + fmtInt(r.assists) + '</td><td class="num">' + fmtInt(r.deaths) + "</td>" +
        '<td class="num">' + (r.adr_tracked ? fmtAvg(r.adr) : '<span class="na" title="ADR не трекался на этой карте">н/д</span>') + "</td>" +
        '<td class="num">' + (r.mvp_tracked ? fmtInt(r.mvps) : '<span class="na" title="MVP не трекался на этой карте">н/д</span>') + "</td>" +
        '<td class="num">' + fmtSignedInt(r.elo_change) + "</td></tr>";
    }).join("");
    return '<table class="data-table"><thead>' + head + "</thead><tbody>" + body + "</tbody></table>";
  }

  // ==================================================================
  // TEAMS
  // ==================================================================

  var teamSort = { col: "rating_final", dir: -1 };

  function renderTeams() {
    var root = document.getElementById("page-teams");
    var rows = dashboardState.tournament !== null ? teamTournamentRows(dashboardState.tournament) : teamAllTimeRows();
    if (dashboardState.team !== null) rows = rows.filter(function (r) { return r.team_id === dashboardState.team; });

    root.innerHTML =
      '<div class="table-wrap" style="margin-bottom:20px;"><table class="data-table" id="teams-table"><thead><tr>' +
      teamHeaderCells() + '</tr></thead><tbody></tbody></table></div>' +
      '<div id="team-profile"></div>';

    document.querySelectorAll("#teams-table thead th").forEach(function (th) {
      th.addEventListener("click", function () {
        var col = th.dataset.col;
        if (teamSort.col === col) teamSort.dir *= -1; else { teamSort.col = col; teamSort.dir = -1; }
        renderTeamsTableBody(rows);
      });
    });

    renderTeamsTableBody(rows);

    if (dashboardState.team) renderTeamProfile(dashboardState.team);
    else document.getElementById("team-profile").innerHTML = '<div class="empty-state">Выберите команду в таблице или в фильтре.</div>';
  }

  var TEAM_COLUMNS = [
    { col: "team_id", label: "Команда" },
    { col: "tournament_place", label: "Место", fmt: fmtInt },
    { col: "maps_played", label: "Карт", fmt: fmtInt },
    { col: "wins", label: "Победы", fmt: fmtInt },
    { col: "losses", label: "Поражения", fmt: fmtInt },
    { col: "win_rate", label: "Win rate", fmt: fmtPercentPlain },
    { col: "round_diff", label: "Раунд. дифф.", fmt: fmtSignedInt },
    { col: "players_count", label: "Состав", fmt: fmtInt },
    { col: "rating_start", label: "Elo начало", fmt: fmtRating },
    { col: "rating_final", label: "Elo итог", fmt: fmtRating },
    { col: "rating_change", label: "Изм. Elo", fmt: fmtSignedInt },
  ];

  function teamHeaderCells() {
    return TEAM_COLUMNS.map(function (c) {
      var cls = teamSort.col === c.col ? (teamSort.dir === 1 ? "sorted-asc" : "sorted-desc") : "";
      return '<th data-col="' + c.col + '" class="' + cls + '">' + c.label + "</th>";
    }).join("");
  }

  function renderTeamsTableBody(rows) {
    var sorted = rows.slice().sort(function (a, b) {
      var av = a[teamSort.col], bv = b[teamSort.col];
      if (isNil(av) && isNil(bv)) return 0;
      if (isNil(av)) return 1;
      if (isNil(bv)) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * teamSort.dir;
      return (av - bv) * teamSort.dir;
    });
    var tbody = document.querySelector("#teams-table tbody");
    if (!sorted.length) {
      tbody.innerHTML = '<tr><td colspan="' + TEAM_COLUMNS.length + '"><div class="empty-state">Нет команд для текущих фильтров.</div></td></tr>';
      return;
    }
    tbody.innerHTML = sorted.map(function (t) {
      var cells = TEAM_COLUMNS.map(function (c) {
        var raw = t[c.col];
        var html = c.fmt ? c.fmt(raw) : (isNil(raw) ? naSpan() : escapeHtml(raw));
        return '<td class="' + (c.fmt ? "num" : "") + '">' + html + "</td>";
      }).join("");
      return '<tr data-team="' + t.team_id + '">' + cells + "</tr>";
    }).join("");
    tbody.querySelectorAll("tr[data-team]").forEach(function (tr) {
      tr.addEventListener("click", function () { setTeamFilter(tr.dataset.team); renderCurrentTab(); });
    });
  }

  function renderTeamProfile(teamId) {
    var container = document.getElementById("team-profile");
    var allTime = teamAllTimeRows().find(function (t) { return t.team_id === teamId; });
    var history = teamRatingHistoryOf(teamId);
    var maps = teamMapsOf(teamId);
    var roster = new Set();
    DATA.player_maps.forEach(function (r) { if (r.team_id === teamId) roster.add(r.public_player_id); });
    var rosterNicknames = Array.from(roster).map(function (id) { var p = playerByPubId(id); return p ? p.public_nickname : id; });

    if (!allTime) { container.innerHTML = '<div class="empty-state">Нет данных по команде.</div>'; return; }

    container.innerHTML =
      '<div class="profile-header"><h2>' + escapeHtml(teamId) + "</h2></div>" +
      renderFlagsBanner(allTime.data_quality_flags) +
      '<div class="grid grid-2" style="margin-bottom:16px;">' +
        '<div class="card"><h3>Динамика Elo по турнирам (сумма состава)</h3><div id="chart-team-timeline" class="chart"></div></div>' +
        '<div class="card"><h3>Вклад карт в изменение Elo</h3><div id="chart-team-contribution" class="chart"></div></div>' +
      '</div>' +
      '<div class="grid grid-2">' +
        '<div class="card"><h3>Состав (публичные псевдонимы)</h3><p>' + rosterNicknames.map(escapeHtml).join(", ") + '</p></div>' +
        '<div class="card"><h3>Результаты по картам</h3><div class="table-wrap">' + renderTeamMapsTable(maps) + "</div></div>" +
      "</div>";

    renderTeamTimelineChart(teamId, history);
    renderTeamContributionChart(maps);
  }

  function renderTeamTimelineChart(teamId, history) {
    var chart = ensureChart("chart-team-timeline");
    if (!chart) return;
    var comparison = (DATA.team_rating_comparison || []).filter(function (r) { return r.team_id === teamId && r.team_nt !== null; })
      .sort(function (a, b) { return a.tournament_number - b.tournament_number; });
    if (comparison.length) {
      chart.setOption({
        grid: { left: 50, right: 20, top: 20, bottom: 20 },
        xAxis: { type: "category", data: comparison.map(function (h) { return "Турнир " + h.tournament_number; }) },
        yAxis: { type: "value", name: "Elo (сумма состава)" },
        tooltip: { trigger: "axis" },
        series: [{ type: "line", data: comparison.map(function (h) { return Math.round(h.team_nt); }), smooth: true, itemStyle: { color: CHART_COLORS[0] } }],
      });
      return;
    }
    var finals = history.filter(function (h) { return h.event_type === "tournament_final"; });
    if (!finals.length) { emptyChartMessage("chart-team-timeline", "Недостаточно данных."); return; }
    chart.setOption({
      grid: { left: 50, right: 20, top: 20, bottom: 20 },
      xAxis: { type: "category", data: finals.map(function (h) { return "Турнир " + h.tournament_number; }) },
      yAxis: { type: "value", name: "Elo" },
      tooltip: { trigger: "axis" },
      series: [{ type: "line", data: finals.map(function (h) { return Math.round(h.rating_after_avg); }), smooth: true, itemStyle: { color: CHART_COLORS[0] } }],
    });
  }

  function renderTeamContributionChart(maps) {
    var chart = ensureChart("chart-team-contribution");
    if (!chart) return;
    var byMap = {};
    maps.forEach(function (m) {
      if (m.elo_change_total === null) return;
      byMap[m.map_key] = (byMap[m.map_key] || 0) + m.elo_change_total;
    });
    var entries = Object.keys(byMap).map(function (k) { return [k, byMap[k]]; });
    if (!entries.length) { emptyChartMessage("chart-team-contribution", "Нет данных."); return; }
    chart.setOption({
      grid: { left: 100, right: 20, top: 10, bottom: 20 },
      xAxis: { type: "value" },
      yAxis: { type: "category", data: entries.map(function (e) { return e[0]; }) },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      series: [{ type: "bar", data: entries.map(function (e) { return Math.round(e[1]); }), itemStyle: { color: CHART_COLORS[0] } }],
    });
  }

  function renderTeamMapsTable(maps) {
    if (!maps.length) return '<div class="empty-state">Нет сыгранных карт.</div>';
    var rows = maps.slice().sort(function (a, b) { return b.tournament_number - a.tournament_number || b.map_number - a.map_number; });
    var head = "<tr><th>Турнир</th><th>Карта</th><th>Соперник</th><th>Счёт</th><th>W/L</th><th>Изм. Elo</th></tr>";
    var body = rows.map(function (r) {
      var score = r.result_parse_status === "ok" ? (r.team_score + "-" + r.opponent_score) : '<span class="na" title="счёт не распознан">н/д</span>';
      return "<tr><td>" + r.tournament_number + "</td><td>" + escapeHtml(r.map_key) + "</td><td>" + escapeHtml(r.opponent_team_id) + "</td>" +
        '<td class="num">' + score + "</td><td>" + escapeHtml(r.win_loss) + '</td><td class="num">' + fmtSignedInt(r.elo_change_total) + "</td></tr>";
    }).join("");
    return '<table class="data-table"><thead>' + head + "</thead><tbody>" + body + "</tbody></table>";
  }

  // ==================================================================
  // TEAM × MAP
  // ==================================================================

  var teamMapSort = "total";

  function computeHeatmapCells() {
    var rows = DATA.team_maps;
    if (dashboardState.tournament !== null) rows = rows.filter(function (r) { return r.tournament_number === dashboardState.tournament; });

    var groups = {};
    rows.forEach(function (r) {
      var key = r.team_id + "|" + r.map_key;
      if (!groups[key]) {
        groups[key] = {
          team_id: r.team_id, map_key: r.map_key, maps_played: 0, wins: 0, losses: 0,
          round_diff_sum: 0, round_diff_valid: 0, elo_change_sum: 0, elo_change_valid: 0,
        };
      }
      var g = groups[key];
      g.maps_played += 1;
      if (r.win_loss === "W") g.wins += 1;
      if (r.win_loss === "L") g.losses += 1;
      if (r.result_parse_status === "ok" && !isNil(r.round_diff)) { g.round_diff_sum += r.round_diff; g.round_diff_valid += 1; }
      if (!isNil(r.elo_change_total)) { g.elo_change_sum += r.elo_change_total; g.elo_change_valid += 1; }
    });

    return Object.keys(groups).map(function (key) {
      var g = groups[key];
      return {
        team_id: g.team_id, map_key: g.map_key, maps_played: g.maps_played, wins: g.wins, losses: g.losses,
        win_rate: g.maps_played ? g.wins / g.maps_played : null,
        round_diff: g.round_diff_valid ? g.round_diff_sum : null,
        elo_change: g.elo_change_valid ? g.elo_change_sum : null,
        sample_size: g.maps_played,
        low_sample_flag: g.maps_played < CFG.low_sample_threshold,
      };
    });
  }

  function renderTeamMap() {
    var root = document.getElementById("page-teammap");
    var subtitle = dashboardState.tournament !== null ? (" — турнир " + dashboardState.tournament) : " — все турниры";
    root.innerHTML =
      '<div class="heatmap-controls">' +
        '<div class="filter-field"><label>Метрика</label><select id="heatmap-metric">' +
          '<option value="elo_change">Изменение Elo</option>' +
          '<option value="round_diff">Раундовый дифференциал</option>' +
          '<option value="win_rate">Win rate</option>' +
          '<option value="maps_played">Сыграно карт</option>' +
        '</select></div>' +
        '<div class="filter-field"><label>Сортировка</label><select id="heatmap-sort">' +
          '<option value="total">По итогу</option><option value="alpha">По алфавиту</option>' +
        '</select></div>' +
        '<span class="heatmap-legend">Ячейки с &lt; ' + CFG.low_sample_threshold + ' картами приглушены (маленькая выборка)' + escapeHtml(subtitle) + '</span>' +
      '</div>' +
      '<div id="heatmap-chart" class="chart tall"></div>';

    document.getElementById("heatmap-metric").value = dashboardState.heatmapMetric;
    document.getElementById("heatmap-metric").addEventListener("change", function (e) { dashboardState.heatmapMetric = e.target.value; renderHeatmap(); });
    document.getElementById("heatmap-sort").addEventListener("change", function (e) { teamMapSort = e.target.value; renderHeatmap(); });

    renderHeatmap();
  }

  function renderHeatmap() {
    var chart = ensureChart("heatmap-chart");
    if (!chart) return;

    var rows = computeHeatmapCells();
    if (dashboardState.team !== null) rows = rows.filter(function (r) { return r.team_id === dashboardState.team; });
    if (dashboardState.map !== null) rows = rows.filter(function (r) { return r.map_key === dashboardState.map; });

    if (!rows.length) {
      emptyChartMessage("heatmap-chart", "Нет данных для текущих фильтров.");
      return;
    }

    var teams = Array.from(new Set(rows.map(function (r) { return r.team_id; })));
    var maps = DATA.metadata.maps;

    var totals = {};
    teams.forEach(function (t) {
      totals[t] = rows.filter(function (r) { return r.team_id === t; })
        .reduce(function (s, r) { return s + (r[dashboardState.heatmapMetric] || 0); }, 0);
    });
    teams.sort(function (a, b) {
      if (teamMapSort === "alpha") return a.localeCompare(b);
      return totals[b] - totals[a];
    });

    var cellByKey = {};
    rows.forEach(function (r) { cellByKey[r.team_id + "|" + r.map_key] = r; });

    var data = [];
    teams.forEach(function (team, ti) {
      maps.forEach(function (map, mi) {
        var cell = cellByKey[team + "|" + map];
        if (!cell) return;
        var v = cell[dashboardState.heatmapMetric];
        data.push({ value: [mi, ti, v === null ? null : Math.round(v * (dashboardState.heatmapMetric === "win_rate" ? 1000 : 1)) / (dashboardState.heatmapMetric === "win_rate" ? 10 : 1)],
          sample: cell.sample_size, low: cell.low_sample_flag });
      });
    });

    var values = data.map(function (d) { return d.value[2]; }).filter(function (v) { return v !== null; });
    var maxAbs = values.length ? Math.max.apply(null, values.map(Math.abs)) : 1;

    chart.setOption({
      grid: { left: 140, right: 20, top: 10, bottom: 60 },
      tooltip: {
        formatter: function (p) {
          if (!p.data) return "";
          var d = p.data;
          var warn = d.low ? "<br/><b>Маленькая выборка</b>" : "";
          return maps[d.value[0]] + " × " + teams[d.value[1]] + "<br/>Значение: " + d.value[2] +
            "<br/>Карт: " + d.sample + warn;
        },
      },
      xAxis: { type: "category", data: maps, splitArea: { show: true }, axisLabel: { fontSize: 10, rotate: 30 } },
      yAxis: { type: "category", data: teams, splitArea: { show: true }, axisLabel: { fontSize: 10 } },
      visualMap: {
        min: -maxAbs, max: maxAbs, calculable: true, orient: "horizontal", left: "center", bottom: 0,
        inRange: { color: ["#c23b3b", "#f5f6f8", "#1f9254"] },
      },
      series: [{
        type: "heatmap",
        data: data.map(function (d) { return { value: d.value, sample: d.sample, low: d.low, itemStyle: d.low ? { opacity: 0.35 } : {} }; }),
        label: { show: true, fontSize: 10, formatter: function (p) { return p.data.value[2] === null ? "" : p.data.value[2]; } },
      }],
    });
  }

  // ==================================================================
  // init
  // ==================================================================

  function init() {
    document.getElementById("meta-title").textContent = DATA.metadata.report_title;
    document.getElementById("meta-updated").textContent = DATA.metadata.data_as_of ?
      "Данные по " + DATA.metadata.data_as_of.slice(0, 10) : "—";
    document.getElementById("meta-tournaments").textContent = DATA.metadata.tournament_count;
    document.getElementById("meta-players").textContent = DATA.metadata.public_player_count;
    document.getElementById("meta-teams").textContent = DATA.metadata.team_count;
    document.getElementById("meta-maps").textContent = DATA.metadata.map_count;

    initFilterBar();

    document.querySelectorAll(".tab-btn").forEach(function (b) {
      b.addEventListener("click", function () { setActiveTab(b.dataset.tab); });
    });

    setActiveTab("overview");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
