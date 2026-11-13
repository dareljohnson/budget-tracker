from budget_tracker.statements.lines import collapsed, group_lines
from budget_tracker.statements.model import Word

def test_group_lines_splits_on_two_point_gap_and_sorts_by_x():
    words = [Word("b", 40, 10), Word("a", 10, 10.5), Word("c", 10, 20)]
    lines = group_lines(words)
    assert [[w.text for w in line] for line in lines] == [["a", "b"], ["c"]]
    assert collapsed(lines[0]) == "a b"
