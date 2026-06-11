import json


class EloPredictor:
    def __init__(self, ratings_path):
        with open(ratings_path) as f:
            self.ratings = json.load(f)

    def expected_score(self, r_a, r_b, home=0):
        return 1 / (1 + 10 ** ((r_b - r_a + home) / 400))

    def predict(self, team_a, team_b, neutral=False):
        r_a = self.ratings.get(team_a)
        r_b = self.ratings.get(team_b)
        if r_a is None or r_b is None:
            missing = [t for t in (team_a, team_b) if t not in self.ratings]
            return {"error": f"Team(s) not found: {missing}. Available: {sorted(self.ratings.keys())}"}

        hosts = {"Mexico", "United States", "Canada"}
        ha = 70 if (not neutral and team_a in hosts) else 0

        e_a = self.expected_score(r_a, r_b, ha)
        gap = abs((r_a + ha) - r_b)
        p_draw = max(0.08, min(0.35, 0.25 - 0.05 * (gap / 200)))
        p_win_a = e_a * (1 - p_draw)
        p_win_b = (1 - e_a) * (1 - p_draw)

        return {
            "team_a": {"name": team_a, "rating": r_a},
            "team_b": {"name": team_b, "rating": r_b},
            "home_advantage": ha,
            "probabilities": {
                team_a: round(p_win_a * 100, 1),
                "draw": round(p_draw * 100, 1),
                team_b: round(p_win_b * 100, 1),
            },
        }
