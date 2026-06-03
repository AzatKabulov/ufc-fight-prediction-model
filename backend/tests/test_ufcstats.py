from unittest import TestCase

from bs4 import BeautifulSoup

from app.services.ufcstats import (
    _height_to_cm,
    _parse_career_stats,
    _parse_event_fight_rows,
    _parse_fight_history,
    _parse_profile_facts,
    _parse_upcoming_event_fight_rows,
    _reach_to_cm,
)


class UfcStatsParserTests(TestCase):
    def test_profile_facts_parse_height_reach_stance_and_dob(self) -> None:
        soup = BeautifulSoup(
            """
            <ul>
              <li class="b-list__box-list-item">Height: 5' 7"</li>
              <li class="b-list__box-list-item">Reach: 69"</li>
              <li class="b-list__box-list-item">STANCE: Orthodox</li>
              <li class="b-list__box-list-item">DOB: Jan 21, 1997</li>
            </ul>
            """,
            "html.parser",
        )

        facts = _parse_profile_facts(soup)

        self.assertEqual(facts["height"], "5' 7\"")
        self.assertEqual(_height_to_cm(facts["height"]), 170.2)
        self.assertEqual(_reach_to_cm(facts["reach"]), 175.3)
        self.assertEqual(facts["stance"], "Orthodox")
        self.assertEqual(facts["dob"], "1997-01-21")

    def test_career_stats_parse_model_and_ui_fields(self) -> None:
        soup = BeautifulSoup(
            """
            <ul>
              <li class="b-list__box-list-item">SLpM: 4.69</li>
              <li class="b-list__box-list-item">Str. Acc.: 48%</li>
              <li class="b-list__box-list-item">SApM: 3.12</li>
              <li class="b-list__box-list-item">Str. Def: 63%</li>
              <li class="b-list__box-list-item">TD Avg.: 1.92</li>
              <li class="b-list__box-list-item">TD Acc.: 56%</li>
              <li class="b-list__box-list-item">TD Def.: 92%</li>
              <li class="b-list__box-list-item">Sub. Avg.: 1.10</li>
            </ul>
            """,
            "html.parser",
        )

        stats = _parse_career_stats(soup)

        self.assertEqual(stats["strikes_landed_per_min"], 4.69)
        self.assertEqual(stats["sig_str_acc"], 0.48)
        self.assertEqual(stats["strikes_absorbed_per_min"], 3.12)
        self.assertEqual(stats["sig_str_def"], 0.63)
        self.assertEqual(stats["td_avg_per_15"], 1.92)
        self.assertEqual(stats["td_acc"], 0.56)
        self.assertEqual(stats["td_def"], 0.92)
        self.assertEqual(stats["sub_avg_per_15"], 1.10)

    def test_fight_history_parse_opponent_and_stat_pairs(self) -> None:
        soup = BeautifulSoup(
            """
            <table>
              <tr class="b-fight-details__table-row b-fight-details__table-row__hover" data-link="http://ufcstats.com/fight-details/abc">
                <td>win</td>
                <td>
                  <a>Ilia Topuria</a>
                  <a>Josh Emmett</a>
                </td>
                <td>1 0</td>
                <td>152 87</td>
                <td>3 0</td>
                <td>0 0</td>
                <td>UFC Fight Night: Emmett vs. Topuria Jun. 24, 2023</td>
                <td>Decision - Unanimous</td>
                <td>5</td>
                <td>5:00</td>
              </tr>
            </table>
            """,
            "html.parser",
        )

        fights = _parse_fight_history(soup, "Ilia Topuria")

        self.assertEqual(fights[0]["opponent"], "Josh Emmett")
        self.assertEqual(fights[0]["event"], "UFC Fight Night: Emmett vs. Topuria")
        self.assertEqual(fights[0]["date"], "Jun. 24, 2023")
        self.assertEqual(fights[0]["method"], "Decision - Unanimous")
        self.assertEqual(fights[0]["round"], "5")
        self.assertEqual(fights[0]["knockdowns_for"], 1)
        self.assertEqual(fights[0]["sig_strikes_for"], 152)
        self.assertEqual(fights[0]["sig_strikes_against"], 87)
        self.assertEqual(fights[0]["takedowns_for"], 3)
        self.assertEqual(fights[0]["time"], "5:00")

    def test_event_fight_rows_parse_completed_fight_summary(self) -> None:
        soup = BeautifulSoup(
            """
            <table>
              <tr class="b-fight-details__table-row b-fight-details__table-row__hover" data-link="http://ufcstats.com/fight-details/fight1">
                <td>win</td>
                <td>
                  <a href="http://ufcstats.com/fighter-details/a">Arnold Allen</a>
                  <a href="http://ufcstats.com/fighter-details/b">Melquizael Costa</a>
                </td>
                <td>1 0</td>
                <td>98 100</td>
                <td>7 0</td>
                <td>0 0</td>
                <td>Featherweight</td>
                <td>U-DEC</td>
                <td>5</td>
                <td>5:00</td>
              </tr>
            </table>
            """,
            "html.parser",
        )

        fights = _parse_event_fight_rows(soup)

        self.assertEqual(fights[0]["winner_name"], "Arnold Allen")
        self.assertEqual(fights[0]["weight_class"], "Featherweight")
        self.assertEqual(fights[0]["method"], "U-DEC")
        self.assertEqual(fights[0]["round"], 5)
        self.assertEqual(fights[0]["fighter_stats"][0]["sig_strikes_landed"], 98)
        self.assertEqual(fights[0]["fighter_stats"][1]["takedowns_landed"], 0)

    def test_upcoming_event_rows_parse_scheduled_matchups(self) -> None:
        soup = BeautifulSoup(
            """
            <table>
              <tr class="b-fight-details__table-row b-fight-details__table-row__hover" data-link="http://ufcstats.com/fight-details/upcoming1">
                <td></td>
                <td>
                  <a href="http://ufcstats.com/fighter-details/a">Song Yadong</a>
                  <a href="http://ufcstats.com/fighter-details/b">Deiveson Figueiredo</a>
                </td>
                <td></td>
                <td></td>
                <td></td>
                <td></td>
                <td>Bantamweight</td>
                <td></td>
                <td></td>
                <td></td>
              </tr>
            </table>
            """,
            "html.parser",
        )

        fights = _parse_upcoming_event_fight_rows(soup)

        self.assertEqual(fights[0]["fighter_a"]["name"], "Song Yadong")
        self.assertEqual(fights[0]["fighter_b"]["name"], "Deiveson Figueiredo")
        self.assertEqual(fights[0]["weight_class"], "Bantamweight")
        self.assertEqual(fights[0]["status"], "scheduled")
