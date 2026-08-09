"""
audit_team_rosters.py
========================

Диагностика аномалий состава постоянных команд, где размер состава,
используемого для Nc/Nt и профиля команды (`build_marts.resolve_final_roster()`),
не равен 5 (см. секцию 2 задания "Команды с 4 или 6 игроками").

НИЧЕГО не исправляет: `resolve_final_roster()` не меняется, рейтинг не
пересчитывается, состав не дополняется и не урезается. Скрипт только
собирает сигналы (кто ещё играл за команду, переходы между командами,
пропуски в player-map, расхождения между разными версиями состава) и
классифицирует вероятную причину по фиксированному перечню — окончательное
решение, какой вариант состава использовать, принимает пользователь.

Результат:
    artifacts/validation/team_roster_anomalies.json
    artifacts/validation/team_roster_anomalies.md

Диагностический скрипт — всегда возвращает 0, не блокирует сборку pipeline
(см. pipeline/validate_pipeline.py, где наличие отчёта проверяется как warning).

Запуск (из корня репозитория):

    python pipeline/audit_team_rosters.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import EXPECTED_ROSTER_SIZE, INTERIM_DIR, PROCESSED_DIR, VALIDATION_DIR  # noqa: E402
from io_utils import load_team_alias_map, read_table, write_json  # noqa: E402
from build_marts import permanent_team_keys, resolve_final_roster  # noqa: E402

CAUSE_TAXONOMY = [
    "real_four_player_map",
    "substitution",
    "mid_tournament_transfer",
    "missing_player_map_rows",
    "conflicting_source_roster",
    "final_map_roster_artifact",
    "unknown",
]


def _source_roster_table() -> pd.DataFrame:
    """Официальный состав по `cs_farpost_players.xlsx` (`t{n}_team`),
    альяс-нормализованный тем же способом, что и остальные team-справочники
    (см. io_utils.load_team_alias_map) — Вариант A из секции 2.2 задания."""
    alias_map, _pending = load_team_alias_map()
    elo = read_table(INTERIM_DIR / "player_tournament_elo.parquet")
    elo = elo.dropna(subset=["team_raw"]).copy()
    elo["canonical_team"] = elo["team_raw"].map(alias_map).fillna(elo["team_raw"])
    return elo[["id", "tournament_number", "canonical_team"]]


def _modal_roster(team_rows: pd.DataFrame) -> tuple[set, int]:
    """Вариант B: пятёрка игроков, чаще всего встречавшаяся ВМЕСТЕ на одной
    карте команды в этом турнире (мода по frozenset(игроков на карте))."""
    per_map = team_rows.groupby("map_number")["private_player_key"].apply(lambda s: frozenset(s))
    if not len(per_map):
        return set(), 0
    counts = per_map.value_counts()
    top = counts.index[0]
    return set(top), int(counts.iloc[0])


def _has_internal_gap(team_maps_sorted: list[int], player_maps: list[int]) -> bool:
    """`map_number` — сквозная нумерация карт ВСЕГО турнира (не только карт
    этой команды), поэтому пропуск нужно искать относительно СОБСТВЕННЫХ карт
    команды, а не относительно абсолютных номеров: игрок пропустил внутри
    своего диапазона участия карту, которую команда играла (и другие
    игроки на ней есть), а он — нет. Пропуск ДО первой/ПОСЛЕ последней своей
    карты — это переход/замена на границе, не внутренний разрыв."""
    positions = sorted(team_maps_sorted.index(m) for m in player_maps)
    if len(positions) < 2:
        return False
    expected = set(range(positions[0], positions[-1] + 1))
    return bool(expected - set(positions))


def _classify(
    *, final_size: int, last_map_size: int, modal_size: int, source_size: int,
    has_transfer: bool, has_internal_gap: bool, has_missing_flag: bool,
    per_map_sizes_set: set, final_roster_pids: set, source_roster: set,
) -> str:
    if has_internal_gap or has_missing_flag:
        return "missing_player_map_rows"

    if last_map_size == EXPECTED_ROSTER_SIZE and final_size != EXPECTED_ROSTER_SIZE:
        # Фактический последний матч команды сыгран впятером — расхождение
        # только в том, что resolve_final_roster() берёт "свой последний
        # матч" ПО ИГРОКУ, а не "последний матч команды".
        return "mid_tournament_transfer" if has_transfer else "final_map_roster_artifact"

    if last_map_size != EXPECTED_ROSTER_SIZE:
        if per_map_sizes_set == {last_map_size}:
            # Такой размер состава был на КАЖДОЙ карте команды в турнире —
            # не артефакт склейки составов, а фактический размер состава.
            return "real_four_player_map" if last_map_size == 4 else "unknown"
        if modal_size == EXPECTED_ROSTER_SIZE and has_transfer:
            return "substitution"

    if source_roster and len(final_roster_pids ^ source_roster) >= 2:
        return "conflicting_source_roster"

    return "unknown"


def analyze() -> tuple[list[dict], dict]:
    player_map = read_table(PROCESSED_DIR / "fact_player_map.parquet")
    dim_team = read_table(PROCESSED_DIR / "dim_team.parquet")
    permanent = permanent_team_keys(dim_team)
    source_roster_table = _source_roster_table()

    t_only = player_map[(player_map["tournament_phase"] == "t") & (player_map["team_key"].isin(permanent))]
    roster = resolve_final_roster(player_map)
    roster = roster[roster["team_key"].isin(permanent)]

    roster_counts = roster.groupby(["team_key", "tournament_number"])["private_player_key"].nunique()
    anomaly_keys = roster_counts[roster_counts != EXPECTED_ROSTER_SIZE].index.tolist()

    # Переходы игрока между командами В ТЕЧЕНИЕ турнира (тот же грейн, что
    # tests/test_team_classification.py::TestFinalRosterResolution).
    transfers = t_only.groupby(["private_player_key", "tournament_number"])["team_key"].nunique()
    transferred_pids = set(transfers[transfers > 1].index)

    reports = []
    for team_key, tournament_number in anomaly_keys:
        team_rows = t_only[(t_only["team_key"] == team_key) & (t_only["tournament_number"] == tournament_number)]
        final_roster_pids = set(
            roster[(roster["team_key"] == team_key) & (roster["tournament_number"] == tournament_number)][
                "private_player_key"
            ]
        )
        all_pids = set(team_rows["private_player_key"].unique())

        last_map_number = int(team_rows["map_number"].max())
        last_map_roster = set(team_rows[team_rows["map_number"] == last_map_number]["private_player_key"])

        modal_roster, modal_count = _modal_roster(team_rows)
        source_roster = set(
            source_roster_table[
                (source_roster_table["tournament_number"] == tournament_number)
                & (source_roster_table["canonical_team"] == team_key)
            ]["id"]
        )

        per_map_sizes = team_rows.groupby("map_number")["private_player_key"].nunique().to_dict()
        team_maps_sorted = sorted(int(m) for m in team_rows["map_number"].unique())

        players_detail = []
        any_internal_gap = False
        any_missing_flag = False
        for pid in sorted(all_pids):
            prows = team_rows[team_rows["private_player_key"] == pid]
            maps_played = sorted(int(m) for m in prows["map_number"].unique())
            gap = _has_internal_gap(team_maps_sorted, maps_played)
            any_internal_gap = any_internal_gap or gap
            player_teams_this_tournament = sorted(
                player_map[
                    (player_map["private_player_key"] == pid)
                    & (player_map["tournament_number"] == tournament_number)
                    & (player_map["tournament_phase"] == "t")
                ]["team_key"].unique()
            )
            missing_flag_hit = bool(
                prows["data_quality_flags"].apply(lambda fl: "missing_elo_change_for_map" in fl).any()
            )
            any_missing_flag = any_missing_flag or missing_flag_hit
            players_detail.append({
                "private_player_key": pid,
                "maps_played": len(maps_played),
                "first_map": maps_played[0],
                "last_map": maps_played[-1],
                "has_internal_gap": gap,
                "is_transfer_case": (pid, tournament_number) in transferred_pids,
                "teams_this_tournament": player_teams_this_tournament,
                "in_final_roster": pid in final_roster_pids,
                "in_last_physical_map_roster": pid in last_map_roster,
                "missing_elo_change_flag_on_any_map": missing_flag_hit,
            })

        has_transfer = any(p["is_transfer_case"] for p in players_detail)
        last_map_size = len(last_map_roster)
        final_size = len(final_roster_pids)

        cause = _classify(
            final_size=final_size, last_map_size=last_map_size, modal_size=len(modal_roster),
            source_size=len(source_roster), has_transfer=has_transfer,
            has_internal_gap=any_internal_gap, has_missing_flag=any_missing_flag,
            per_map_sizes_set=set(per_map_sizes.values()), final_roster_pids=final_roster_pids,
            source_roster=source_roster,
        )

        reports.append({
            "tournament_number": int(tournament_number),
            "team": team_key,
            "roster_size": final_size,
            "final_roster_used_for_nc_nt": sorted(final_roster_pids),
            "all_players_this_tournament": sorted(all_pids),
            "last_physical_map_number": last_map_number,
            "last_physical_map_roster": sorted(last_map_roster),
            "modal_roster": sorted(modal_roster),
            "modal_roster_map_count": modal_count,
            "source_roster_players_xlsx": sorted(source_roster),
            "per_map_roster_sizes": {str(int(k)): int(v) for k, v in sorted(per_map_sizes.items())},
            "has_mid_tournament_transfer": has_transfer,
            "has_internal_gap_in_player_map": any_internal_gap,
            "has_missing_elo_change_flag": any_missing_flag,
            "players": players_detail,
            "probable_cause": cause,
        })

    summary = {
        "expected_roster_size": EXPECTED_ROSTER_SIZE,
        "anomaly_count": len(reports),
        "by_cause": {c: sum(1 for r in reports if r["probable_cause"] == c) for c in CAUSE_TAXONOMY},
        "by_roster_size": _count_by(reports, "roster_size"),
    }
    return reports, summary


def _count_by(reports: list[dict], field: str) -> dict:
    out: dict[str, int] = {}
    for r in reports:
        out[str(r[field])] = out.get(str(r[field]), 0) + 1
    return out


def render_markdown(reports: list[dict], summary: dict) -> str:
    lines = ["# Аномалии состава постоянных команд (roster_size != 5)", ""]
    lines.append(
        f"Всего аномалий (пара команда×турнир, где размер состава для Nc/Nt != "
        f"{summary['expected_roster_size']}): **{summary['anomaly_count']}**."
    )
    lines.append("")
    lines.append(
        "Это ДИАГНОСТИКА, не исправление — `resolve_final_roster()` не менялся, рейтинг "
        "не пересчитывался, состав не дополнялся и не урезался. Какой вариант состава "
        "считать окончательным — решает пользователь (см. `docs/unresolved_questions.md`)."
    )
    lines.append("")
    lines.append("## По вероятной причине")
    lines.append("")
    for cause, count in summary["by_cause"].items():
        lines.append(f"- `{cause}`: {count}")
    lines.append("")
    lines.append("## По размеру состава")
    lines.append("")
    for size, count in sorted(summary["by_roster_size"].items(), key=lambda kv: int(kv[0])):
        lines.append(f"- {size} игрок(ов): {count}")
    lines.append("")
    lines.append("## Детали по каждой аномалии")
    lines.append("")
    for r in sorted(reports, key=lambda x: (x["tournament_number"], x["team"])):
        lines.append(f"### Турнир {r['tournament_number']} — {r['team']}")
        lines.append("")
        lines.append(f"- Размер состава (используется для Nc/Nt, `resolve_final_roster()`): **{r['roster_size']}**")
        lines.append(f"- Вероятная причина: `{r['probable_cause']}`")
        lines.append(f"- Состав для Nc/Nt: {', '.join(r['final_roster_used_for_nc_nt']) or '—'}")
        lines.append(f"- Все игроки, сыгравшие за команду в этом турнире: {', '.join(r['all_players_this_tournament'])}")
        lines.append(
            f"- Состав на фактической последней карте команды (карта {r['last_physical_map_number']}): "
            f"{', '.join(r['last_physical_map_roster']) or '—'}"
        )
        lines.append(
            f"- Наиболее частый состав на карте (mode, {r['modal_roster_map_count']} карт(ы)): "
            f"{', '.join(r['modal_roster']) or '—'}"
        )
        lines.append(
            f"- Состав по источнику `cs_farpost_players.xlsx` (t{r['tournament_number']}_team): "
            f"{', '.join(r['source_roster_players_xlsx']) or '—'}"
        )
        lines.append(f"- Переход игрока между командами в течение турнира: {'да' if r['has_mid_tournament_transfer'] else 'нет'}")
        lines.append(f"- Разрыв в player-map данных: {'да' if r['has_internal_gap_in_player_map'] else 'нет'}")
        lines.append(f"- Пропущенный elo_change на карте: {'да' if r['has_missing_elo_change_flag'] else 'нет'}")
        lines.append("")
        lines.append("| Игрок | Карт сыграно | Первая карта | Последняя карта | Переход | В финальном составе | В составе последней карты |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in r["players"]:
            lines.append(
                f"| {p['private_player_key']} | {p['maps_played']} | {p['first_map']} | {p['last_map']} | "
                f"{'да' if p['is_transfer_case'] else 'нет'} | {'да' if p['in_final_roster'] else 'нет'} | "
                f"{'да' if p['in_last_physical_map_roster'] else 'нет'} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    reports, summary = analyze()

    write_json({"summary": summary, "anomalies": reports}, VALIDATION_DIR / "team_roster_anomalies.json")
    (VALIDATION_DIR / "team_roster_anomalies.md").write_text(render_markdown(reports, summary), encoding="utf-8")

    print(f"[audit_team_rosters] аномалий состава (roster_size != 5): {summary['anomaly_count']}")
    for cause, count in summary["by_cause"].items():
        if count:
            print(f"    {cause}: {count}")
    print("[audit_team_rosters] отчёт: artifacts/validation/team_roster_anomalies.{json,md}")
    print("[audit_team_rosters] диагностика информационная — состав НЕ изменён, сборка не блокируется.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
