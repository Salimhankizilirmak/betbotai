import asyncio
import logging

# Mock objects to mimic the structure and test the filter
def mock_filter(recommendations):
    # This is a copy of the logic in nba_player_props.py
    recommendations.sort(key=lambda x: -x["confidence"])
    final_selections = []
    used_players = set()
    used_stats = set()
    
    for rec in recommendations:
        if len(final_selections) >= 3:
            break
        player = rec["player"]
        stat = rec["stat"]
        if player in used_players:
            continue
        if stat in used_stats:
            continue
        final_selections.append(rec)
        used_players.add(player)
        used_stats.add(stat)
    return final_selections

def test_logic():
    # Scenario: 5 recommendations for the same match
    recs = [
        {"player": "Player A", "stat": "PTS", "confidence": 90}, 
        {"player": "Player A", "stat": "REB", "confidence": 85}, # Should be skipped (Player A used)
        {"player": "Player B", "stat": "PTS", "confidence": 80}, # Should be skipped (PTS used)
        {"player": "Player B", "stat": "REB", "confidence": 75}, # Should be selected
        {"player": "Player C", "stat": "AST", "confidence": 70}, # Should be selected
        {"player": "Player D", "stat": "AST", "confidence": 60}, # Should be skipped (Max 3 reached)
    ]
    
    selected = mock_filter(recs)
    print("Selected Props:")
    for s in selected:
        print(f"- {s['player']} | {s['stat']} ({s['confidence']})")
    
    # Assertions
    assert len(selected) == 3
    assert selected[0]["player"] == "Player A" and selected[0]["stat"] == "PTS"
    assert selected[1]["player"] == "Player B" and selected[1]["stat"] == "REB"
    assert selected[2]["player"] == "Player C" and selected[2]["stat"] == "AST"
    print("\n✅ Verification SUCCESS: Rules applied correctly.")

if __name__ == "__main__":
    test_logic()
