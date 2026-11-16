import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scrape_rosters as sr  # noqa: E402
from scrape_rosters import Person, TeamRoster  # noqa: E402


def soup(html):
    return BeautifulSoup(html, "html.parser")


def roster_table(rows, marked=False):
    body = "".join(
        f'<tr><td>{number}</td><td><img alt="{name}"></td><td><a href="/x">\n {name}\n </a></td><td>GER</td></tr>'
        for number, name in rows)
    css = "team-roster " if marked else ""
    return (f'<table class="table {css}table-dt2"><thead><tr><th class="alc-nbr">#</th><th>Spieler</th></tr></thead>'
            f"<tbody>{body}</tbody></table>")


# --- DEL ----------------------------------------------------------------------------

def test_del_reads_every_position_table_not_only_the_marked_one():
    page = soup(roster_table([("11", "Kristian Reichel")], marked=True)
                + roster_table([("4", "Dan Renouf")]) + roster_table([("", "Colin Schlenker *")])
                + '<table class="team-leader"><tbody><tr><td>1</td><td></td><td>Nope</td></tr></tbody></table>')
    assert sr.del_players(page) == [Person("Kristian Reichel", 11), Person("Dan Renouf", 4), Person("Colin Schlenker")]


def test_del_staff_from_infos_page():
    page = soup("<dl><dt>Trainer:</dt><dd>Dallas Eakins</dd><dt>Co-Trainer:</dt><dd>Curt Fraser, Marcel Goc</dd>"
                "<dt>Sportlicher Leiter:</dt><dd>Dallas Eakins</dd></dl>")
    assert sr.del_staff(page) == [Person("Dallas Eakins", role="Coach"),
                                  Person("Curt Fraser", role="Assistant Coach"),
                                  Person("Marcel Goc", role="Assistant Coach")]


def test_del_team_list():
    page = soup('<a href="/teams/adler-mannheim/uebersicht">A</a><a href="/teams/adler-mannheim/uebersicht">A</a>'
                '<a href="/teams/koelner-haie/uebersicht">K</a><a href="/teams/koelner-haie/kader">K</a>')
    assert sr.del_team_urls(page) == ["https://www.penny-del.org/teams/adler-mannheim",
                                      "https://www.penny-del.org/teams/koelner-haie"]
    assert sr.del_key("https://www.penny-del.org/teams/koelner-haie/uebersicht") == "koelner-haie"


# --- DEL2 ---------------------------------------------------------------------------

DEL2_CLUB = """
<section class="teamHeader"><h1>EC Bad Nauheim</h1></section>
<div class="kader-list"><table><tr><td>Luca Ganz</td></tr></table></div>
<div class="kader-cards">
  <div class="player-card"><div class="player-card-avatar"><span class="player-card-jersey">#33</span></div>
    <div class="player-card-name"><a href="/spieler/luca-ganz_5305">Luca <b>Ganz</b></a></div></div>
  <div class="player-card"><div class="player-card-name"><a>Julius <b>Schulte</b></a></div></div>
</div>
<div id="club-officials">
  <div class="player-card"><div class="player-card-name">Christopher <b>Fiori</b></div>
    <div class="player-card-meta">Geschäftsführer</div></div>
  <div class="player-card"><div class="player-card-name">Kevin <b>Gaudet</b></div>
    <div class="player-card-meta"><img alt="CAN"><span>Headcoach</span></div></div>
  <div class="player-card"><div class="player-card-name">Dominic <b>Auger</b></div>
    <div class="player-card-meta"><span>Assistant-Coach</span></div></div>
  <div class="player-card"><div class="player-card-name">Tom <b>Goalie</b></div>
    <div class="player-card-meta"><span>Torwarttrainer</span></div></div>
</div>
"""


def test_del2_players_and_staff_from_club_page():
    page = soup(DEL2_CLUB)
    assert sr.del2_players(page) == [Person("Luca Ganz", 33), Person("Julius Schulte")]
    assert sr.del2_staff(page) == [Person("Kevin Gaudet", role="Coach"), Person("Dominic Auger", role="Assistant Coach")]


def test_del2_team_list_and_key():
    page = soup('<a href="/clubs/ec-bad-nauheim_45">B</a><a href="/clubs">alle</a><a href="/clubs/ev-landshut_27">L</a>')
    assert sr.del2_team_urls(page) == ["https://www.del-2.org/clubs/ec-bad-nauheim_45",
                                       "https://www.del-2.org/clubs/ev-landshut_27"]
    assert sr.del2_key("https://www.del-2.org/clubs/ec-bad-nauheim_45") == "45"


# --- Output -------------------------------------------------------------------------

def test_render_in_captioner_format():
    team = TeamRoster("45", "EC Bad Nauheim", "u",
                      players=[Person("Joel Keussen", 2), Person("Julius Schulte"), Person("Joel Keussen", 2)],
                      staff=[Person("Dominic Auger", role="Assistant Coach"), Person("Kevin Gaudet", role="Coach"),
                             Person("Second Assistant", role="Assistant Coach")])
    assert sr.render([team], {"45": "bn"}) == (
        "- EC Bad Nauheim -\n\n"
        "bn\tEC Bad Nauheim\n"
        "bn2\t-2- Joel Keussen (EC Bad Nauheim)\n"
        "bn_schulte\tJulius Schulte (EC Bad Nauheim)\n"
        "bn100\tKevin Gaudet (Coach EC Bad Nauheim)\n"
        "bn101\tDominic Auger (Assistant Coach EC Bad Nauheim)\n"
        "bn102\tSecond Assistant (Assistant Coach EC Bad Nauheim)\n")


def test_duplicate_number_gets_name_code():
    codes = sr.player_codes("bn", [Person("Anna Erste", 9), Person("Bert Zweite", 9)])
    assert [code for code, _ in codes] == ["bn9", "bn_zweite"]


def test_parse_roster_ignores_staff_and_team_code():
    text = sr.render([TeamRoster("1", "T", "u", [Person("Ada Alt", 4), Person("Ohne Nummer")],
                                 [Person("Cee Coach", role="Coach")])], {"1": "tt"})
    assert sr.parse_roster(text) == {"T": {"Ada Alt": 4, "Ohne Nummer": None}}


def test_change_report():
    previous = {"T": {"Ada Alt": 4, "Bea Weg": 9}, "Old": {"X Y": 1}}
    current = {"T": {"Ada Alt": 7, "Chris Neu": 3}, "New": {"Z Z": 2}}
    lines = sr.render_changes(previous, current)
    assert "- **T**: neu: Chris Neu (3); weg: Bea Weg; Nummer: Ada Alt (4 → 7)" in lines
    assert "- **Old**: nicht mehr in der Liga" in lines
    assert "- **New**: neu in der Liga" in lines


def test_plausibility_rejects_shrunken_or_empty_teams():
    league = sr.LEAGUES["del2"]
    previous = {"Big": {f"P{i}": i for i in range(30)}}
    teams = [TeamRoster("1", "Big", "u", [Person(f"P{i}", i) for i in range(15)]),
             TeamRoster("2", "Tiny", "u", [Person("Solo", 1)])]
    problems = sr.check_plausibility(league, teams, previous)
    assert any("Big: 15 statt bisher 30" in p for p in problems)
    assert any("Tiny: nur 1 Spieler" in p for p in problems)
    assert any("nur 2 Teams" in p for p in problems)


# --- Codes --------------------------------------------------------------------------

def test_team_codes_are_unique_across_leagues():
    seen = {}
    for path in sorted(sr.ROOT.glob("teams_*.md")):
        for code, url in sr.read_team_lines(path):
            assert code not in seen, f"{code} in {path.name} und {seen[code]}"
            seen[code] = path.name
    assert seen


def test_no_team_code_is_a_prefix_digit_clash():
    # 'ab' + '12' must never equal another team's code + number: codes are letters only.
    for path in sr.ROOT.glob("teams_*.md"):
        assert all(code.isalpha() for code, _ in sr.read_team_lines(path))


def test_generated_code_avoids_taken_codes():
    assert sr.generated_code("Krefeld Pinguine", set()) == "kp"
    assert sr.generated_code("Krefeld Pinguine", {"kp"}) == "pi"
    assert sr.generated_code("Höchstadt Alligators", set()) == "ha"


@pytest.mark.parametrize("league", list(sr.LEAGUES.values()), ids=lambda league: league.name)
def test_teams_files_parse(league):
    codes = sr.load_codes(league)
    assert len(codes) >= league.min_teams


# --- Oberliga -----------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("ASSAVOLYUK Daniel", "Daniel Assavolyuk"),
    ("ELSBERGER Julian-Maximilian", "Julian-Maximilian Elsberger"),
    ("VAN DER LINDE Leon", "Leon van der Linde"),
    ("O'BRIEN Sean", "Sean O'Brien"),
    ("MCLEOD Conner", "Conner McLeod"),
    ("METZLER_WAGNER Tobias", "Tobias Metzler-Wagner"),
    ("PFÄNDER Marcel", "Marcel Pfänder"),
    ("Already Normal", "Already Normal"),
])
def test_hockeydata_name(raw, expected):
    assert sr.hockeydata_name(raw) == expected


OBERLIGA_STANDINGS = """
<div data-hd-widget="hockeydata.los.Standings" data-hd-widget-options='{"apiKey":"x","divisionId":"21614"}'>
<table><tbody>
<tr class="-hd-los-standings-row -hd-los-standings-row-20912 -hd-clickable"><td>1</td>
  <td class="-hd-los-standings-team-name-short">DSC</td><td class="-hd-los-standings-team-name-long">Deggendorfer SC</td></tr>
<tr class="-hd-los-standings-row -hd-los-standings-row-70199 -hd-clickable"><td>2</td>
  <td class="-hd-los-standings-team-name-long">ESV Kaufbeuren</td></tr>
</tbody></table></div>
"""

OBERLIGA_TEAM = """
<div class="-hd-los -hd-los-team-full-page">
<table><tbody>
<tr class="-hd-los-team-full-page-team-facts-table-row"><td>Vereinsname</td><td>Deggendorfer SC</td></tr>
<tr class="-hd-los-team-full-page-team-facts-table-row"><td>Trainer</td><td>Casey Fratkin</td></tr>
<tr class="-hd-los-team-full-page-team-facts-table-row"><td>Co-Trainer</td><td>A Eins, B Zwei</td></tr>
<tr class="-hd-los-team-full-page-team-facts-table-row"><td>Torwarttrainer</td><td>Not Me</td></tr>
</tbody></table>
<div class="-hd-los-team-full-page-container-data -hd-los-team-full-page-roster-details-data"><table>
<thead><tr><th>Spieler</th><th>#</th><th>Pos</th></tr></thead>
<tbody><tr class="-hd-clickable"><td value="GRAF Sebastian">GRAF Sebastian</td><td>1</td><td>G</td></tr>
<tr class="-hd-clickable"><td>HEISS Silvan</td><td></td><td>D</td></tr></tbody></table></div>
</div>
"""


def test_oberliga_standings():
    page = soup(OBERLIGA_STANDINGS)
    assert sr.division_id(page) == "21614"
    assert sr.oberliga_teams(page) == [("20912", "Deggendorfer SC"), ("70199", "ESV Kaufbeuren")]
    assert sr.oberliga_key("https://deb-online.live/team/?teamId=20912&divisionId=21614") == "20912"


def test_oberliga_team_page():
    page = soup(OBERLIGA_TEAM)
    assert sr.oberliga_players(page) == [Person("Sebastian Graf", 1), Person("Silvan Heiss")]
    assert sr.oberliga_staff(page) == [Person("Casey Fratkin", role="Coach"), Person("A Eins", role="Assistant Coach"),
                                       Person("B Zwei", role="Assistant Coach")]


def test_previous_spelling_wins_when_only_case_or_sharp_s_differs():
    teams = [TeamRoster("1", "T", "u", [Person("Silvan Heiss", 4), Person("Conner Mcleod", 47), Person("New Guy", 9)])]
    sr.adopt_spelling(teams, {"T": {"Silvan Heiß": 4, "Conner McLeod": 47}})
    assert [p.name for p in teams[0].players] == ["Silvan Heiß", "Conner McLeod", "New Guy"]
