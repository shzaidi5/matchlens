"""Fictional clubs and players. Nothing here refers to a real club or person.

Base positions are in the team's own attacking frame: x runs 0 (own goal) to
105 (opponent goal), y runs 0 to 68.
"""

from __future__ import annotations

from ..models import Player, Team


def _squad(team: str, rows: list[tuple]) -> tuple[Player, ...]:
    return tuple(
        Player(
            id=f"{team}-{num}",
            name=name,
            number=num,
            position=pos,
            team=team,
            base_x=x,
            base_y=y,
            pace=pace,
            passing=passing,
            finishing=finishing,
            tackling=tackling,
        )
        for (num, name, pos, x, y, pace, passing, finishing, tackling) in rows
    )


HOME = Team(
    id="NBR",
    name="Northbridge Rovers",
    short="Northbridge",
    color="#E5484D",
    formation="4-3-3",
    side="home",
    players=_squad(
        "NBR",
        [
            (1, "Tomas Hale", "GK", 6, 34, 27.0, 0.72, 0.10, 0.40),
            (2, "Dario Ferreyra", "RB", 30, 9, 33.4, 0.76, 0.35, 0.68),
            (5, "Callum Otieno", "CB", 23, 25, 31.0, 0.80, 0.30, 0.78),
            (6, "Mikkel Strand", "CB", 23, 43, 30.2, 0.82, 0.30, 0.76),
            (3, "Jun-ho Baek", "LB", 30, 59, 33.0, 0.77, 0.35, 0.66),
            (4, "Idris Mbaye", "DM", 41, 34, 31.2, 0.86, 0.40, 0.74),
            (8, "Leon Harwick", "CM", 52, 22, 31.8, 0.84, 0.55, 0.62),
            (10, "Rafael Quintero", "CM", 55, 46, 31.5, 0.89, 0.66, 0.52),
            (7, "Kofi Adjaye", "RW", 70, 10, 34.6, 0.76, 0.66, 0.42),
            (9, "Viktor Sandahl", "ST", 77, 34, 32.9, 0.72, 0.78, 0.38),
            (11, "Emeka Nwosu", "LW", 70, 58, 34.9, 0.75, 0.68, 0.40),
        ],
    ),
    bench=_squad(
        "NBR",
        [
            (14, "Oscar Lindqvist", "CM", 52, 22, 32.0, 0.82, 0.50, 0.66),
            (19, "Tariq Benali", "ST", 77, 34, 33.6, 0.70, 0.74, 0.36),
        ],
    ),
)

AWAY = Team(
    id="CAL",
    name="Calderwick United",
    short="Calderwick",
    color="#3E9BD8",
    formation="4-2-3-1",
    side="away",
    players=_squad(
        "CAL",
        [
            (1, "Marek Dvorsky", "GK", 6, 34, 26.5, 0.70, 0.10, 0.40),
            (22, "Aiden Prowse", "RB", 30, 9, 33.8, 0.74, 0.35, 0.70),
            (4, "Nuno Seabra", "CB", 23, 25, 30.6, 0.78, 0.30, 0.80),
            (15, "Elias Brandt", "CB", 23, 43, 31.4, 0.77, 0.30, 0.78),
            (3, "Kwame Asante-Boyd", "LB", 30, 59, 33.5, 0.75, 0.35, 0.68),
            (6, "Stefan Ilic", "DM", 40, 26, 31.0, 0.82, 0.40, 0.78),
            (8, "Hamza El Idrissi", "DM", 42, 42, 32.2, 0.83, 0.48, 0.72),
            (10, "Luca Moretto", "AM", 60, 34, 31.6, 0.87, 0.68, 0.48),
            (17, "Jaden Whitlock", "RW", 68, 10, 35.2, 0.74, 0.64, 0.44),
            (11, "Mateo Arrieta", "LW", 68, 58, 34.4, 0.77, 0.70, 0.42),
            (9, "Olu Adeyemi-Ross", "ST", 78, 34, 32.4, 0.70, 0.76, 0.40),
        ],
    ),
    bench=_squad(
        "CAL",
        [
            (20, "Finn Gallagher-Reyes", "ST", 78, 34, 35.4, 0.72, 0.77, 0.46),
            (16, "Tomasz Wrona", "CM", 42, 42, 32.0, 0.80, 0.45, 0.74),
        ],
    ),
)
