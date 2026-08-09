# GitHub Publication Audit — `cs2-tournament-analytics`

Date: 2026-08-09
Scope: privacy/security review of the repository ahead of publishing it as an open-source
portfolio project. This audit covers content only — no `git commit`, `git push`, GitHub repo
creation, git-history rewrite, or change to `data/raw/` was performed.

Tooling used: the project's own `pipeline/check_publication_leaks.py` (run against `docs/`,
`config/`, `dax.txt`, `powerquery.txt`, both notebooks, `artifacts/`, and finally against the
**exact** 63-file snapshot that `.gitignore` currently allows into git — see §6), plus manual
`grep`-based scans for secrets/credentials, absolute local paths, and a Windows username, plus
direct reading of every `docs/*.md` file and every config/data file listed as a candidate for
publication.

---

## 1. Git history findings — CRITICAL check, result: no history exists

```
git rev-list --all --count   → 0
git log --all                → (empty)
git branch -a                → (no branches)
```

The repository currently has **zero commits**. There is no git history to leak from, and no
history-rewrite/cleanup is needed. This removes the main risk this section of the audit is meant
to catch (previously-committed secrets that `.gitignore` can no longer protect) — but it also
means **everything below this point is a pre-first-commit review**, not a retroactive cleanup.

Two `.idea/*` files (`cs2-tournament.iml`, `workspace.xml`, plus three more) were already staged
(`git add`ed) before this audit. No local path or username leaked through their content (checked
directly), but per standard practice `.idea/` is IDE metadata, not project content, so it was
**unstaged** (`git reset -- .idea`, non-destructive — files remain on disk) and added to
`.gitignore`.

**Recommendation:** see §9. Because there is no history to clean, **Option B vs. Option A from
the original task is moot** — there is nothing to choose between. The only decision left is
whether to make the *first* commit from the current (now-audited) working tree, which is safe to
do.

---

## 2. Secrets scan

Grepped `docs/`, `config/`, `*.txt`, `*.ipynb`, `pipeline/`, `dashboard/`, `tests/`, `.idea/` for:
password/passwd, API keys, `secret`/`credentials` patterns, private-key headers, AWS-style keys,
`ftp://`/`smtp://`, and for absolute Windows/Unix paths and the local Windows username.

**Result: no credentials, API keys, passwords, or FTP/SMTP endpoints found.** No absolute local
paths or username leaked in any tracked or candidate text file. (`.pyc` bytecode files under
`__pycache__/` do embed absolute build paths as a general Python behavior, but that directory is
already excluded via `.gitignore` and was never a candidate for publication.)

**One real finding, since fixed:** `powerquery.txt` contained six `Web.Contents("https://docs.
google.com/spreadsheets/...pub?...output=csv")` links — the live Power Query source URLs,
including the one for the `players` sheet (the sheet with `fio`/`discord_name`/`faceit_link`/
`steam_account`/`city`/`department`). `check_publication_leaks.py` does **not** catch this class of
issue (it only matches literal PII values, not arbitrary secrets/URLs — a limitation the project's
own `docs/privacy_and_publication.md` already documents). All six URLs have been replaced with
`<REDACTED_GOOGLE_SHEETS_PUBLISH_URL_*>` placeholders; the M-query logic itself is unchanged and
still fully readable as documentation.

---

## 3. PII scan

Ran `check_publication_leaks.py`'s own token collector (432 real tokens: every `id`/`player_id`/
`steam_name`/`steam_account`/`fio`/`city`/`department`/`discord_name`/`faceit_name`/`faceit_link`/
`another_names` value from `data/raw/`, plus `private_player_key` from `data/private/`) against
every directory/file being considered for publication.

### 3.1 Confirmed real PII found (all fixed in this session)

Real identifiers are **not reproduced in this report** — writing out the exact value that leaked
would just recreate the same leak inside the audit document itself (this happened once already
during this session, in an earlier draft of this file and of `.gitignore`; both were corrected).
Anonymized labels below (`Player-Alpha`, etc.) match the ones used in the fixed `docs/*.md` files,
where applicable.

| Location | What leaked | Fix applied |
|---|---|---|
| `docs/legacy_vs_canonical_results.md`, `docs/metric_catalog.md` | 2 real player ids used as worked examples | replaced with `Player-Alpha`/`Player-Beta` |
| `docs/metric_catalog.md`, `docs/powerbi_logic_audit.md`, `docs/unresolved_questions.md` (×4) | 1 real player id (the one unmatched `elo_changes` row) | replaced with `Player-Gamma` |
| `docs/rating_semantics.md`, `docs/source_audit_v2.md`, `docs/unresolved_questions.md` (×2) | 1 real player id (the `elo_start=500` anomaly) | replaced with `Player-Delta` |
| `docs/source_audit.md` | 2 real player ids used only to illustrate the `id` slug format | replaced with fabricated example slugs `player-slug-1`/`player-slug-2` |
| `docs/source_audit.md` | 2 more real player ids | replaced with a generic description ("2 players", no ids) |
| `docs/privacy_and_publication.md`, `docs/data_model.md` | a real historical team name **and** the real player id it was named after — i.e. the *exact* deanonymization case the redaction logic exists to prevent, written out in the doc that explains that logic | reworded to describe the scenario without naming the team or the player |
| `config/team_aliases_review.csv` | the same team-name/player-id pair, as raw pipeline data | **file excluded from git** (see §4 — this file is a functional pipeline input keyed to exact raw team-name strings; hand-editing the one sensitive row risked breaking the alias-resolution join for anyone who *does* have `data/raw/`, so exclusion was safer than redaction here) |
| `cs_farpost_tournament.ipynb` | 21 distinct real player ids/nicknames in cached cell outputs | **notebook excluded from git** (see §4) |
| `cs_farpost_tournament_calib.ipynb` | 61 distinct real player ids/nicknames **and 10 real full names (ФИО) in Cyrillic** in cached cell outputs | **notebook excluded from git** (see §4) |
| `artifacts/data_profile/columns_profile.csv`, `cs_farpost_players.profile.json` | direct sample dump of the raw PII columns: real ФИО, city names, department names, Discord handles with discriminators (`user#1234`), Faceit profile URLs, Steam profile URLs including numeric SteamID64s | **whole `artifacts/data_profile/` directory excluded from git** (see §4) |
| `artifacts/audit_v2/*.json` (4 files) | real player ids used as worked examples in the legacy-vs-canonical comparisons | **whole `artifacts/audit_v2/` directory excluded from git** (see §4) |
| `artifacts/validation/team_roster_anomalies.{json,md}` | real player ids listed per anomalous roster | **excluded from git** (see §4); the sibling `validation.md`/`validation.json` in the same folder are clean and are kept |

### 3.2 Reviewed and cleared as false positives

`check_publication_leaks.py` has a documented short-token limitation (tokens ≥3 characters, no
context awareness). Two collisions were manually reviewed and confirmed harmless (deliberately not
quoting the exact matched strings here, for the same reason as §3.1 — one of them is a common
3-letter English word that happens to also be a real player id, the other is an ordinary Russian
technical term that happens to also be a real `department` value):

- A common short English word — every hit in `docs/*.md` and `dax.txt` is a code/DAX-formula
  keyword (`Math.max`-style usage, `MIN`/`MAX(...)`), not a reference to the player.
- A common Russian technical term for "parsing" — every hit in `docs/*.md` is in a sentence about
  parsing the `result`/score column, not a reference to anyone's department.

### 3.3 Not caught by the tool, found by direct reading

Reading `data/raw/cs_farpost_players.xlsx`'s profiled sample values directly (via
`artifacts/data_profile/columns_profile.csv`) surfaced that the `department` column's real values
include the name of the organization the tournament was run inside of. This isn't a token the
leak-checker tracks (it's not in the forbidden-fields list, and no repository content besides the
now-excluded `artifacts/data_profile/` references it) but it's the reason `data/raw/`,
`data/private/`, and `artifacts/data_profile/` are excluded outright rather than partially
redacted — there is no practical way to hand-scrub an org name out of free-text department labels
with confidence, and no file that is actually needed for the public dashboard depends on it.

---

## 4. What's excluded from the public repository (and why)

Encoded in the updated `.gitignore`:

| Path | Reason |
|---|---|
| `data/raw/` | Full PII: fio, city, department, discord, faceit, steam. Never published under any circumstance. |
| `data/private/` | The `private_player_key -> public_nickname` bridge table. Publishing it would defeat the pseudonymization entirely. |
| `data/interim/`, `data/processed/` | Internal canonical layers, keyed by `private_player_key`. Not needed by the dashboard (which reads only `data/public/`) and not safe to publish as-is. |
| `config/team_aliases_review.csv` | One row (a historical team name) is a real player-id leak (§3.1); the file is also a functional pipeline input regenerated from `data/raw/`, so it's not useful without private data anyway. |
| `artifacts/audit_v2/`, `artifacts/data_profile/` | Built by directly profiling `data/raw/`; saturated with real ids, Steam/Discord/Faceit links, ФИО, city, department (§3.1, §3.3). |
| `artifacts/validation/team_roster_anomalies.*` | Lists real player ids per roster anomaly. `validation.md`/`validation.json` in the same directory are clean and are **kept**. |
| `cs_farpost_tournament.ipynb`, `cs_farpost_tournament_calib.ipynb` | Cached cell outputs contain real player ids and real full names (ФИО) — not just code, but executed results. Scrubbing 80+ output cells reliably was judged not worth the risk versus simply not publishing the notebooks; their logic is already fully described in `docs/source_audit.md`, `docs/rating_semantics.md`, `docs/powerbi_logic_audit.md`. |
| `.idea/`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/` | Standard IDE/build noise, no project value. |
| `.env`, `.env.*`, `*.pem`, `*.key`, `*credentials*`, `*secrets*` | Defensive patterns; none currently exist in the repo, added for future-proofing. |

`dax.txt` and `powerquery.txt` (after URL redaction, §2) are **kept** — pure formula/M-query text
with no PII once the URLs are gone, and they're direct source material for
`docs/powerbi_logic_audit.md`.

---

## 5. Documentation assessment

Every file in `docs/*.md` was read in full, not assumed safe. Nine of ten files required at least
one redaction (§3.1); all nine were fixed in place. After fixing, `docs/` was re-scanned and the
only remaining hits are the two reviewed false positives from §3.2. No other internal detail
(local paths, employer name, unredacted org structure) was found in the documentation text itself.

`README.md` was rewritten from scratch in English for public/portfolio framing (previous version
was an internal-facing Russian technical log). It does not name the source organization anywhere,
frames the project as "amateur CS2 tournaments" per the task brief, and every claim in its
"Data quality" section is backed by a specific test or validation check rather than being asserted.

---

## 6. Public data assessment (`data/public/`)

`data/public/*.json` — the only data source `dashboard.html` reads — was checked three ways:

1. **Read directly.** Sampled `teams.json` and `metadata.json`; confirmed the one known
   team-name redaction (the sensitive historical team name → `"Команда (имя скрыто #1)"`) is
   present and correct in the actual built output, not just described in docs.
2. **`check_publication_leaks.py data/public`** → `0` matches against all 432 known real tokens.
3. **`pytest tests/test_privacy.py`** → all pass, including
   `test_no_forbidden_columns_in_any_public_json`, `test_no_player_aliases_file_in_public`, and
   `test_public_data_passes_leak_check`.

**Verdict: `data/public/` is safe to publish and to keep in the repository** — it's the
pseudonymized layer the whole privacy design exists to produce, it's a reasonable size (9 JSON
files), and it's what makes the frontend demo in the README actually reproducible without private
data. Recommend keeping it. `dashboard.html` and `for_site/main_image.jpg` (a screenshot of the
Players tab, already fully pseudonymized) were checked the same way and are also safe; the
screenshot was copied to `docs/images/dashboard-players.jpg` for the README.

---

## 7. Final leak scan of the exact publishable snapshot

Rather than trust the per-directory checks above in isolation, the **exact file list `git`
would commit** under the now-updated `.gitignore` (63 files — `git ls-files --others
--exclude-standard --cached`) was copied into an isolated temp directory and scanned as a single
unit:

```
[check_publication_leaks] запрещённых токенов (реальные id/ники/PII): 432
TOTAL DISTINCT FINDINGS: 0
TOTAL RAW FINDINGS: 0
```

**Zero findings across the entire proposed public snapshot.**

---

## 8. Test / validation results (this session)

```
python -m pytest tests/ -v            → 68 passed
python pipeline/validate_pipeline.py  → 78 checks, 0 critical failures, 1 known warning
                                         (a single negative kills=-4 row in the raw source,
                                          preserved as-is and flagged, not a privacy issue)
python pipeline/check_publication_leaks.py data/public    → exit 0, 0 matches
python pipeline/check_publication_leaks.py dashboard.html → exit 0, 0 matches
python pipeline/check_publication_leaks.py --self-test    → OK
```

---

## 9. Final recommendation

1. **Content is now safe to publish.** All confirmed PII/secret findings (§3.1, §2) have been
   fixed or excluded; the exact 63-file snapshot that would be committed passes a full leak scan
   with zero findings (§7); tests and validation pass (§8).
2. **Git history is not a blocker** — there is none. This repository has never been committed, so
   there is nothing to rewrite or to choose between "clean history" (Option A) vs. "fresh repo
   from snapshot" (Option B) — they're the same action here. **Recommendation: make the first
   commit now, from this already-audited working tree**, rather than treating it as a separate
   future step.
3. Before that first commit, do one more `git status` review of what's staged (a broad `git add`
   can silently pick up something new since this audit ran) — routine hygiene, not a sign of a
   known problem.
4. Two things remain outside the scope of an automated/text audit and are worth a human glance
   before the repo goes fully public: (a) skim `docs/*.md` once more for tone/phrasing now that
   the redactions are in (an automated substring fix can read slightly awkward in context); (b)
   confirm you're comfortable with the level of internal-process detail in `docs/` (formulas,
   audit methodology, open questions) being public — none of it is privacy-sensitive, but it is
   more internal-engineering detail than a typical portfolio README, by design of this project's
   history.
