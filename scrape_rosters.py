#!/usr/bin/env python3
"""Scrape the official league sites and write roster files for Captioner.

    python scrape_rosters.py            # all leagues
    python scrape_rosters.py del2

Every league has its own parser. The team list comes from the league site, so
promoted or relegated teams need no manual work. Codes come from teams_<league>.md;
a team missing there gets a generated code (listed in the change report).

A roster file is only replaced when every team was read and the result looks
plausible (see check_plausibility). Otherwise the old file stays and the exit
code is non-zero.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
USER_AGENT = "Mozilla/5.0 (compatible; roster-scraper/2.0)"
REQUEST_DELAY_SECONDS = 1.0
RENDER_DELAY_SECONDS = 2.0  # between pages opened in the browser (each loads several data requests)
MIN_PLAYERS = 5            # fewer players for a team: the page was not read correctly
MAX_SHRINK = 0.6           # a team with >= SHRINK_BASE players may not drop below 60 %
SHRINK_BASE = 15
COACH_NUMBER = 100         # head coach 100, assistants 101, 102, ...

TEAM_LINE = re.compile(r"^-\s+`(?P<code>[a-z]+)`:\s+(?P<url>https://\S+)")
SECTION = re.compile(r"^-\s*(?P<team>.+?)\s*-$")
SPACE = re.compile(r"\s+")


# --- Model ------------------------------------------------------------------------

@dataclass(frozen=True)
class Person:
    name: str
    number: int | None = None
    role: str | None = None  # None = player, 'Coach', 'Assistant Coach'


@dataclass
class TeamRoster:
    key: str
    name: str
    url: str
    players: list[Person] = field(default_factory=list)
    staff: list[Person] = field(default_factory=list)


class ScrapeError(Exception):
    pass


def clean(text: str | None) -> str:
    return SPACE.sub(" ", text or "").strip()


def ascii_slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def parse_number(text: str) -> int | None:
    match = re.search(r"\d{1,3}", text or "")
    return int(match.group()) if match else None


def text_of(node) -> str:
    return clean(node.get_text(" ")) if node else ""


def person_name(text: str) -> str:
    """Without markers like 'Colin Schlenker *' (DEL: development licence)."""
    return clean(re.sub(r"[*†]+", " ", text))


# --- HTTP -------------------------------------------------------------------------

class Http:
    def __init__(self, delay: float = REQUEST_DELAY_SECONDS):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.delay = delay
        self._last = 0.0

    def get(self, url: str) -> BeautifulSoup:
        for attempt in range(3):
            wait = self._last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                return BeautifulSoup(response.text, "html.parser")
            except requests.RequestException as error:
                if attempt == 2:
                    raise ScrapeError(f"{url}: {error}") from error
                time.sleep(5 * (attempt + 1))
        raise AssertionError("unreachable")

    def render(self, url: str, selector: str) -> BeautifulSoup:
        """Opens the page in headless Chromium like a visitor and returns the DOM once `selector` exists."""
        if not hasattr(self, "_browser"):
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
            self._page = self._browser.new_page()
            # Only what the roster needs: no images, fonts, media.
            self._page.route("**/*", lambda route: route.abort()
                             if route.request.resource_type in ("image", "font", "media") else route.continue_())
        for attempt in range(3):
            wait = self._last + RENDER_DELAY_SECONDS - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                self._page.wait_for_selector(selector, state="attached", timeout=45_000)
                self._page.wait_for_timeout(500)
                return BeautifulSoup(self._page.content(), "html.parser")
            except Exception as error:  # playwright.TimeoutError and navigation errors
                if attempt == 2:
                    raise ScrapeError(f"{url}: {error}".splitlines()[0]) from error
                time.sleep(5 * (attempt + 1))
        raise AssertionError("unreachable")

    def close(self):
        if hasattr(self, "_browser"):
            self._browser.close()
            self._playwright.stop()
            del self._browser


# --- DEL (penny-del.org) ----------------------------------------------------------

DEL_BASE = "https://www.penny-del.org"


def del_key(url: str) -> str:
    match = re.search(r"/teams/([a-z0-9-]+)", url)
    if not match:
        raise ValueError(f"no DEL team URL: {url}")
    return match.group(1)


def del_team_urls(page: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for link in page.select('a[href^="/teams/"]'):
        match = re.fullmatch(r"/teams/([a-z0-9-]+)/uebersicht", link.get("href", ""))
        if match and f"{DEL_BASE}/teams/{match.group(1)}" not in urls:
            urls.append(f"{DEL_BASE}/teams/{match.group(1)}")
    return urls


def del_players(page: BeautifulSoup) -> list[Person]:
    """Kader page: one table per position (header th.alc-nbr), columns #, picture, name, ..."""
    players = []
    rows = [row for table in page.select("table") if table.select_one("thead th.alc-nbr")
            for row in table.select("tbody tr")]
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        image = cells[1].find("img")
        name = text_of(cells[2].find("a") or cells[2]) or clean(image.get("alt") if image else "")
        if person_name(name):
            players.append(Person(person_name(name), parse_number(cells[0].get_text())))
    return players


def del_staff(page: BeautifulSoup) -> list[Person]:
    """Infos page: 'Trainer:' and 'Co-Trainer:', each followed by names (comma separated)."""
    lines = [clean(line) for line in page.get_text("\n").split("\n") if clean(line)]
    staff: list[Person] = []
    for label, value in zip(lines, lines[1:]):
        role = {"Trainer:": "Coach", "Co-Trainer:": "Assistant Coach"}.get(label)
        if not role or value.endswith(":"):
            continue
        for name in re.split(r",|\s+und\s+|\s*/\s*", value):
            if clean(name):
                staff.append(Person(clean(name), role=role))
    return staff


def scrape_del(http: Http) -> list[TeamRoster]:
    teams = []
    for url in del_team_urls(http.get(f"{DEL_BASE}/teams")):
        page = http.get(f"{url}/kader")
        name = text_of(page.select_one("h1")) or del_key(url)
        teams.append(TeamRoster(del_key(url), name, url, del_players(page), del_staff(http.get(f"{url}/infos"))))
    return teams


# --- DEL2 (del-2.org) -------------------------------------------------------------

DEL2_BASE = "https://www.del-2.org"


def del2_key(url: str) -> str:
    match = re.search(r"/clubs/[a-z0-9-]+_(\d+)", url)
    if not match:
        raise ValueError(f"no DEL2 club URL: {url}")
    return match.group(1)


def del2_team_urls(page: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for link in page.select('a[href^="/clubs/"]'):
        href = link.get("href", "")
        if re.fullmatch(r"/clubs/[a-z0-9-]+_\d+", href) and urljoin(DEL2_BASE, href) not in urls:
            urls.append(urljoin(DEL2_BASE, href))
    return urls


def del2_players(page: BeautifulSoup) -> list[Person]:
    """Club page: player cards ('Vorname <b>Nachname</b>', '#33') below .kader-cards."""
    players = []
    for card in page.select(".kader-cards .player-card"):
        name = person_name(text_of(card.select_one(".player-card-name")))
        if name:
            players.append(Person(name, parse_number(text_of(card.select_one(".player-card-jersey")))))
    return players


def staff_role(text: str) -> str | None:
    text = text.casefold()
    if re.search(r"torwart|goalie|athlet|video|skills", text):
        return None
    if re.search(r"assistant|co-?trainer|co-?coach", text):
        return "Assistant Coach"
    if re.search(r"head-?coach|cheftrainer|^trainer$|^coach$", text):
        return "Coach"
    return None


def del2_staff(page: BeautifulSoup) -> list[Person]:
    staff = []
    for card in page.select("#club-officials .player-card"):
        role = staff_role(text_of(card.select_one(".player-card-meta")))
        name = text_of(card.select_one(".player-card-name"))
        if name and role:
            staff.append(Person(name, role=role))
    return staff


def scrape_del2(http: Http) -> list[TeamRoster]:
    teams = []
    for url in del2_team_urls(http.get(f"{DEL2_BASE}/clubs")):
        page = http.get(url)
        name = text_of(page.select_one(".teamHeader h1") or page.select_one("h1")) or del2_key(url)
        teams.append(TeamRoster(del2_key(url), name, url, del2_players(page), del2_staff(page)))
    return teams


# --- Oberliga (deb-online.live) ---------------------------------------------------
#
# Standings and rosters are rendered in the browser by a hockeydata widget, so these pages
# are opened in headless Chromium. The league page without ?divisionId shows the current
# season; its id is read from the page and passed on to the team pages.

OBERLIGA_BASE = "https://deb-online.live"
OBERLIGA_DIVISIONS = {
    "Oberliga Nord": f"{OBERLIGA_BASE}/liga/herren/oberliga-nord/",
    "Oberliga Süd": f"{OBERLIGA_BASE}/liga/herren/oberliga-sued/",
}
STANDINGS = '[data-hd-widget="hockeydata.los.Standings"]'
ROSTER_TABLE = ".-hd-los-team-full-page-roster-details-data table"
NAME_PARTICLES = {"van", "von", "der", "den", "de", "di", "da", "du", "la", "le", "ten", "ter", "zu"}


def oberliga_key(url: str) -> str:
    match = re.search(r"teamId=(\d+)", url)
    if not match:
        raise ValueError(f"no Oberliga team URL: {url}")
    return match.group(1)


def division_id(page: BeautifulSoup) -> str:
    match = re.search(r'"divisionId":"(\d+)"', str(page.select_one(STANDINGS) or page))
    if not match:
        raise ScrapeError("keine divisionId auf der Ligaseite")
    return match.group(1)


def oberliga_teams(standings: BeautifulSoup) -> list[tuple[str, str]]:
    """Rendered standings -> [(teamId, long name)]; rows carry class -hd-los-standings-row-<teamId>."""
    teams = []
    for row in standings.select(f"{STANDINGS} tr.-hd-los-standings-row"):
        team_id = next((c.rsplit("-", 1)[1] for c in row.get("class", [])
                        if re.fullmatch(r"-hd-los-standings-row-\d+", c)), None)
        name = text_of(row.select_one(".-hd-los-standings-team-name-long"))
        if team_id and name and team_id not in [t for t, _ in teams]:
            teams.append((team_id, name))
    return teams


def capitalize_name(word: str) -> str:
    """'O'BRIEN' -> 'O'Brien', 'MÜLLER-THURGAU' -> 'Müller-Thurgau', 'MCLEOD' -> 'McLeod'."""
    word = re.sub(r"[^\W\d_]+", lambda m: m.group().capitalize(), word.lower())
    return re.sub(r"^Mc(\w)", lambda m: "Mc" + m.group(1).upper(), word)


def hockeydata_name(text: str) -> str:
    """'VAN DER LINDE Leon' -> 'Leon van der Linde' (surname in capitals first)."""
    words = clean(text.replace("_", "-")).split()
    surname = []
    while words and words[0].upper() == words[0] and any(ch.isalpha() for ch in words[0]):
        surname.append(words.pop(0))
    if not words or not surname:
        return clean(text)
    surname = [w.lower() if w.lower() in NAME_PARTICLES and i < len(surname) - 1 else capitalize_name(w)
               for i, w in enumerate(surname)]
    return " ".join(words + surname)


def oberliga_players(page: BeautifulSoup) -> list[Person]:
    players = []
    table = page.select_one(ROSTER_TABLE)
    for row in table.select("tbody tr") if table else []:
        cells = row.find_all("td")
        if len(cells) >= 2 and text_of(cells[0]):
            players.append(Person(hockeydata_name(text_of(cells[0])), parse_number(text_of(cells[1]))))
    return players


def oberliga_staff(page: BeautifulSoup) -> list[Person]:
    """Team facts: rows like 'Trainer | Casey Fratkin'."""
    staff = []
    for row in page.select(".-hd-los-team-full-page-team-facts-table-row"):
        cells = row.find_all("td")
        if len(cells) != 2:
            continue
        role = staff_role(text_of(cells[0]))
        for name in re.split(r",|\s+und\s+|\s*/\s*", text_of(cells[1])) if role else []:
            if clean(name):
                staff.append(Person(clean(name), role=role))
    return staff


def scrape_oberliga(http: Http) -> list[TeamRoster]:
    teams = []
    for title, url in OBERLIGA_DIVISIONS.items():
        standings = http.render(url, f"{STANDINGS} tr.-hd-los-standings-row")
        division = division_id(standings)
        found = oberliga_teams(standings)
        if not found:
            raise ScrapeError(f"{title}: keine Teams in der Tabelle ({url})")
        for team_id, name in found:
            team_url = f"{OBERLIGA_BASE}/team/?teamId={team_id}&divisionId={division}"
            page = http.render(team_url, f"{ROSTER_TABLE} tbody tr")
            teams.append(TeamRoster(team_id, name, team_url, oberliga_players(page), oberliga_staff(page)))
    return teams


# --- Leagues ----------------------------------------------------------------------

@dataclass(frozen=True)
class League:
    name: str
    title: str
    scrape: Callable[[Http], list[TeamRoster]]
    key: Callable[[str], str]
    min_teams: int

    @property
    def teams_file(self) -> Path:
        return ROOT / f"teams_{self.name}.md"

    @property
    def roster_file(self) -> Path:
        return ROOT / f"roster_{self.name}.txt"

    @property
    def report_file(self) -> Path:
        return ROOT / "reports" / f"{self.name}-changes.md"


LEAGUES = {
    "del": League("del", "DEL", scrape_del, del_key, min_teams=10),
    "del2": League("del2", "DEL2", scrape_del2, del2_key, min_teams=10),
    "oberliga": League("oberliga", "Oberliga", scrape_oberliga, oberliga_key, min_teams=20),
}


# --- Codes ------------------------------------------------------------------------

def read_team_lines(path: Path) -> list[tuple[str, str]]:
    """teams_<league>.md: lines '- `code`: url' -> [(code, url)]."""
    if not path.exists():
        return []
    return [(m.group("code"), m.group("url")) for line in path.read_text(encoding="utf-8").splitlines()
            if (m := TEAM_LINE.match(line.strip()))]


def load_codes(league: League) -> dict[str, str]:
    return {league.key(url): code for code, url in read_team_lines(league.teams_file)}


def codes_of_other_leagues(league: League) -> set[str]:
    return {code for path in ROOT.glob("teams_*.md") if path != league.teams_file
            for code, _ in read_team_lines(path)}


def generated_code(name: str, taken: set[str]) -> str:
    words = [word for word in (ascii_slug(word) for word in name.split()) if word]
    candidates = ["".join(word[0] for word in words)]
    candidates += [word[:length] for word in reversed(words) for length in (2, 3, 4)]
    for candidate in candidates:
        if len(candidate) >= 2 and candidate.isalpha() and candidate not in taken:
            return candidate
    base, suffix = (re.sub(r"[^a-z]", "", "".join(words)) or "team")[:3], "x"
    while base + suffix in taken:
        suffix += "x"
    return base + suffix


def assign_codes(league: League, teams: list[TeamRoster]) -> tuple[dict[str, str], list[str]]:
    known = load_codes(league)
    taken = codes_of_other_leagues(league) | set(known.values())
    codes, notes = {}, []
    for team in teams:
        code = known.get(team.key)
        if not code:
            code = generated_code(team.name, taken)
            taken.add(code)
            notes.append(f"Neues Team ohne festes Kürzel: {team.name} → `{code}`. "
                         f"Zum Festschreiben in {league.teams_file.name}: - `{code}`: {team.url}")
        codes[team.key] = code
    return codes, notes


# --- Output -----------------------------------------------------------------------

def player_codes(code: str, players: list[Person]) -> list[tuple[str, Person]]:
    """<code><number>; without number or with a number taken twice <code>_<lastname>."""
    unique: dict[str, Person] = {}
    for player in players:
        previous = unique.get(player.name.casefold())
        if previous is None or (previous.number is None and player.number is not None):
            unique[player.name.casefold()] = player
    ordered = sorted(unique.values(), key=lambda p: (p.number is None, p.number or 0, p.name.casefold()))
    result, used = [], set()
    for player in ordered:
        candidate = f"{code}{player.number}" if player.number is not None else ""
        if not candidate or candidate in used:
            candidate = f"{code}_{ascii_slug(player.name.split()[-1]) or 'x'}"
            if candidate in used:
                candidate = f"{code}_{ascii_slug(player.name)}"
        used.add(candidate)
        result.append((candidate, player))
    return result


def staff_codes(code: str, staff: list[Person]) -> list[tuple[str, Person]]:
    seen, ordered = set(), []
    for person in sorted(staff, key=lambda p: p.role != "Coach"):
        if person.name.casefold() not in seen:
            seen.add(person.name.casefold())
            ordered.append(person)
    return [(f"{code}{COACH_NUMBER + index}", person) for index, person in enumerate(ordered)]


def render_team(code: str, team: TeamRoster) -> str:
    lines = [f"- {team.name} -", "", f"{code}\t{team.name}"]
    for player_code, player in player_codes(code, team.players):
        number = f"-{player.number}- " if player.number is not None else ""
        lines.append(f"{player_code}\t{number}{player.name} ({team.name})")
    for staff_code, person in staff_codes(code, team.staff):
        lines.append(f"{staff_code}\t{person.name} ({person.role} {team.name})")
    return "\n".join(lines)


def render(teams: list[TeamRoster], codes: dict[str, str]) -> str:
    ordered = sorted(teams, key=lambda team: team.name.casefold())
    return "\n\n".join(render_team(codes[team.key], team) for team in ordered) + "\n"


def parse_roster(text: str) -> dict[str, dict[str, int | None]]:
    """Roster file -> {team: {player: number}}; players only (staff reads '(Coach Team)')."""
    teams: dict[str, dict[str, int | None]] = {}
    team = None
    for line in text.splitlines():
        if "\t" not in line:
            section = SECTION.match(line.strip())
            if section:
                team = section.group("team")
                teams.setdefault(team, {})
            continue
        match = re.match(r"^(?:-(\d+|\?)-\s*)?(.+?)\s*\((.+)\)\s*$", line.split("\t", 1)[1])
        if team and match and match.group(3) == team:
            number = match.group(1)
            teams[team][match.group(2)] = int(number) if number and number.isdigit() else None
    return teams


def adopt_spelling(teams: list[TeamRoster], previous: dict[str, dict]) -> None:
    """Keeps the previous spelling of a name that differs only in case (e.g. 'MacDonald' vs 'Macdonald')."""
    known = {name.casefold(): name for players in previous.values() for name in players}
    for team in teams:
        team.players = [Person(known.get(p.name.casefold(), p.name), p.number, p.role) for p in team.players]


def check_plausibility(league: League, teams: list[TeamRoster], previous: dict[str, dict]) -> list[str]:
    problems = []
    if len(teams) < league.min_teams:
        problems.append(f"nur {len(teams)} Teams gefunden (mindestens {league.min_teams} erwartet)")
    for team in teams:
        count = len({player.name.casefold() for player in team.players})
        before = len(previous.get(team.name, {}))
        if count < MIN_PLAYERS:
            problems.append(f"{team.name}: nur {count} Spieler ({team.url})")
        elif before >= SHRINK_BASE and count < before * MAX_SHRINK:
            problems.append(f"{team.name}: {count} statt bisher {before} Spieler ({team.url})")
    return problems


def render_changes(previous: dict[str, dict], current: dict[str, dict]) -> list[str]:
    lines = []
    for team in sorted(current.keys() | previous.keys()):
        old, new = previous.get(team, {}), current.get(team, {})
        if old and not new:
            lines.append(f"- **{team}**: nicht mehr in der Liga")
            continue
        if previous and not old:
            lines.append(f"- **{team}**: neu in der Liga")
            continue
        added = [f"{name} ({number or '?'})" for name, number in new.items() if name not in old]
        removed = [name for name in old if name not in new]
        changed = [f"{name} ({old[name] or '?'} → {new[name] or '?'})"
                   for name in new if name in old and old[name] != new[name]]
        parts = [f"neu: {', '.join(added)}" if added else "",
                 f"weg: {', '.join(removed)}" if removed else "",
                 f"Nummer: {', '.join(changed)}" if changed else ""]
        if any(parts):
            lines.append(f"- **{team}**: " + "; ".join(part for part in parts if part))
    return lines


# --- Main -------------------------------------------------------------------------

def update(league: League, http: Http, force: bool = False) -> bool:
    """Scrapes one league, writes roster and report. False if the roster stayed as it was."""
    print(f"== {league.title}", flush=True)
    previous_text = league.roster_file.read_text(encoding="utf-8") if league.roster_file.exists() else ""
    previous = parse_roster(previous_text)
    try:
        teams = league.scrape(http)
    except ScrapeError as error:
        print(f"Abbruch {league.title}: {error}", file=sys.stderr)
        return False
    adopt_spelling(teams, previous)
    codes, notes = assign_codes(league, teams)
    text = render(teams, codes)
    problems = check_plausibility(league, teams, previous)
    if problems and not force:
        print(f"Abbruch {league.title}, {league.roster_file.name} bleibt unverändert:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return False
    changes = render_changes(previous, parse_roster(text))
    report = [f"## {league.title}", "",
              *(f"- {note}" for note in notes),
              *(f"- Trotz Prüfung geschrieben (--force): {problem}" for problem in problems),
              *(changes or ["Keine Kaderänderungen."]), ""]
    league.report_file.parent.mkdir(exist_ok=True)
    league.report_file.write_text("\n".join(report), encoding="utf-8")
    if text != previous_text:
        league.roster_file.write_text(text, encoding="utf-8")
    print("\n".join(report))
    print(f"{len(teams)} Teams, {sum(len(team.players) for team in teams)} Spieler → {league.roster_file.name}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("leagues", nargs="*", metavar="LIGA", help=f"{', '.join(LEAGUES)} (Standard: alle)")
    parser.add_argument("--force", action="store_true", help="auch bei unplausiblem Ergebnis schreiben")
    args = parser.parse_args(argv)
    unknown = [name for name in args.leagues if name not in LEAGUES]
    if unknown:
        parser.error(f"unbekannte Liga: {', '.join(unknown)}")
    http = Http()
    try:
        results = [update(LEAGUES[name], http, args.force) for name in (args.leagues or LEAGUES)]
    finally:
        http.close()
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
