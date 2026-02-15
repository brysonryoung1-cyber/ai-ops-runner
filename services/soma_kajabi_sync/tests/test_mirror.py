"""Hermetic tests for Soma mirror module.

Tests diff computation between Home and Practitioner snapshots.
No network, no credentials, no side effects.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from soma_kajabi_sync.mirror import _diff_snapshots


class TestDiffSnapshots:
    def test_identical_snapshots(self):
        snapshot = {
            "categories": [
                {
                    "name": "Module 1",
                    "items": [
                        {"title": "Intro", "position": 0},
                        {"title": "Deep Dive", "position": 1},
                    ],
                },
            ],
        }
        actions = _diff_snapshots(snapshot, snapshot)
        assert len(actions) == 0

    def test_missing_category(self):
        home = {
            "categories": [
                {"name": "Module 1", "items": [{"title": "Intro", "position": 0}]},
                {"name": "Module 2", "items": [{"title": "Overview", "position": 0}]},
            ],
        }
        practitioner = {
            "categories": [
                {"name": "Module 1", "items": [{"title": "Intro", "position": 0}]},
            ],
        }
        actions = _diff_snapshots(home, practitioner)
        category_adds = [a for a in actions if a["action"] == "add_category"]
        item_adds = [a for a in actions if a["action"] == "add_item"]
        assert len(category_adds) == 1
        assert category_adds[0]["title"] == "Module 2"
        assert len(item_adds) == 1
        assert item_adds[0]["title"] == "Overview"

    def test_missing_item(self):
        home = {
            "categories": [
                {
                    "name": "Module 1",
                    "items": [
                        {"title": "Intro", "position": 0},
                        {"title": "New Content", "position": 1},
                    ],
                },
            ],
        }
        practitioner = {
            "categories": [
                {
                    "name": "Module 1",
                    "items": [{"title": "Intro", "position": 0}],
                },
            ],
        }
        actions = _diff_snapshots(home, practitioner)
        assert len(actions) == 1
        assert actions[0]["action"] == "add_item"
        assert actions[0]["title"] == "New Content"

    def test_position_mismatch(self):
        home = {
            "categories": [
                {
                    "name": "Module 1",
                    "items": [{"title": "Intro", "position": 0}],
                },
            ],
        }
        practitioner = {
            "categories": [
                {
                    "name": "Module 1",
                    "items": [{"title": "Intro", "position": 5}],
                },
            ],
        }
        actions = _diff_snapshots(home, practitioner)
        assert len(actions) == 1
        assert actions[0]["action"] == "reorder"

    def test_empty_snapshots(self):
        actions = _diff_snapshots({"categories": []}, {"categories": []})
        assert len(actions) == 0

    def test_complex_diff(self):
        home = {
            "categories": [
                {
                    "name": "A",
                    "items": [
                        {"title": "A1", "position": 0},
                        {"title": "A2", "position": 1},
                        {"title": "A3", "position": 2},
                    ],
                },
                {
                    "name": "B",
                    "items": [{"title": "B1", "position": 0}],
                },
            ],
        }
        practitioner = {
            "categories": [
                {
                    "name": "A",
                    "items": [
                        {"title": "A1", "position": 0},
                    ],
                },
            ],
        }
        actions = _diff_snapshots(home, practitioner)
        # Should have: add_item A2, add_item A3, add_category B, add_item B1
        assert len(actions) == 4
        action_types = {a["action"] for a in actions}
        assert "add_item" in action_types
        assert "add_category" in action_types
