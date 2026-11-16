# Roster files for Captioner

Rosters for **DEL**, **DEL2** and (pending) **Oberliga Nord/Süd** in Captioner's code-replacement format,
scraped daily from the official league sites by GitHub Actions.

| File | Source |
|---|---|
| `roster_del.txt` | penny-del.org: team list `/teams`, players `/teams/<team>/kader`, coaches `/teams/<team>/infos` |
| `roster_del2.txt` | del-2.org: team list `/clubs`, players and officials on `/clubs/<team>_<id>` |
| `roster_oberliga.txt` | deb-online.live, **not automated yet** (roster is rendered by a JavaScript widget) |

## Using it in Captioner

Settings → roster sources → URL, as shown in the browser:

```
https://github.com/katzefudder/del2_roster/blob/main/roster_del.txt
https://github.com/katzefudder/del2_roster/blob/main/roster_del2.txt
https://github.com/katzefudder/del2_roster/blob/main/roster_oberliga.txt
```

Captioner reloads them every 60 minutes and falls back to the last good version offline.

## Format

```
- EC Bad Nauheim -

bn	EC Bad Nauheim
bn2	-2- Joel Keussen (EC Bad Nauheim)
bn_schulte	Julius Schulte (EC Bad Nauheim)
bn100	Kevin Gaudet (Coach EC Bad Nauheim)
bn101	Dominic Auger (Assistant Coach EC Bad Nauheim)
```

- `<code>` alone: team name (for headlines like `DEL 2 - #bn# gegen #ka#`).
- `<code><number>`: player. Without a number, or with a number taken twice: `<code>_<lastname>`.
- `<code>100`: head coach, `101`, `102`, …: assistant coaches.
- Teams sorted by name, players by number.

## Team codes

`teams_<league>.md` maps each team to a fixed code (`- \`bn\`: <team URL>`). The team list itself comes from
the league site, so promoted or relegated teams need no edit. A team missing from the file gets a generated
code; the change report says so and shows the line to add. Codes must be letters only and unique across all
leagues (`fr` = EHC Freiburg, DEL2; Löwen Frankfurt is `ffm`). The tests check both.

## Daily run

`.github/workflows/update-rosters.yml` runs daily at 04:30 UTC, after every push to the scraper or the team
files, and on demand (Actions → Update rosters → Run workflow; optional leagues and `force`).

- A roster file is only replaced when every team was read and the result is plausible: at least 10 teams,
  at least 5 players per team, and no team with 15+ players shrinking below 60 %. Otherwise the old file stays,
  the run fails and GitHub sends a mail. If a change is genuine (e.g. rosters cleared at the start of a season),
  run the workflow manually with `force`.
- The changes (new, gone, number changed) go to `reports/<league>-changes.md`, the commit message and the
  run summary.

## Local

```sh
poetry install
poetry run pytest
poetry run python scrape_rosters.py          # all leagues
poetry run python scrape_rosters.py del2     # one league
poetry run python scrape_rosters.py --force  # skip the plausibility check
```

Requests are sequential with one second between them (about 45 requests per run).
