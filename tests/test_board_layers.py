"""Layer hierarchy and search are independent of Blender and asset files."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi_test_layer_tree", ROOT / "features/board/organization.py")
org = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = org
spec.loader.exec_module(org)


def board():
    assets = {key: SimpleNamespace(id=key, kind="IMAGE", title=title, label="", hidden=False, state="READY")
              for key, title in (("a", "Ocean"), ("b", "Hull"), ("c", "Deck"), ("d", "Loose reference"))}
    result = SimpleNamespace(catalog=SimpleNamespace(assets=assets), order=list(assets),
                             pending=[], organization=org.Organization())
    child = result.organization.create({"a", "b"}, title="References")
    parent = result.organization.create({"c"}, {child.id}, "Ship")
    return result, child.id, parent.id


class Layers(unittest.TestCase):
    def test_tree_is_nested_and_cards_appear_once(self):
        value, child, parent = board()
        rows = org.layer_rows(value)
        self.assertEqual([(row.id, row.depth) for row in rows],
                         [(parent, 0), (child, 1), ("a", 2), ("b", 2), ("c", 1), ("d", 0)])
        self.assertEqual(len({row.id for row in rows}), len(rows))

    def test_collapsing_changes_only_the_list(self):
        value, child, parent = board()
        self.assertEqual([row.id for row in org.layer_rows(value, collapsed={child})], [parent, child, "c", "d"])
        self.assertEqual([row.id for row in org.layer_rows(value, collapsed={parent})], [parent, "d"])
        self.assertEqual(value.organization.members(parent), {"a", "b", "c"})

    def test_search_reveals_matches_in_collapsed_groups(self):
        value, child, parent = board()
        rows = org.layer_rows(value, query="  oCeAn ", collapsed={parent, child})
        self.assertEqual([row.id for row in rows], [parent, child, "a"])
        self.assertEqual(org.layer_rows(value, query="missing"), [])

    def test_searching_group_includes_its_contents_and_ancestors(self):
        value, child, parent = board()
        self.assertEqual([row.id for row in org.layer_rows(value, query="References")], [parent, child, "a", "b"])
        self.assertEqual([row.id for row in org.layer_rows(value, query="Ship")], [parent, child, "a", "b", "c"])

    def test_live_names_membership_and_hidden_cards(self):
        value, child, parent = board()
        value.catalog.assets["a"].label = "Named reference"
        value.catalog.assets["b"].hidden = True
        value.organization.move_into({"a"}, (), "")
        rows = org.layer_rows(value)
        self.assertNotIn("b", {row.id for row in rows})
        row = next(row for row in rows if row.id == "a")
        self.assertEqual((row.title, row.depth), ("Named reference", 0))
        self.assertEqual([row.id for row in org.layer_rows(value, query="Named")], ["a"])

    def test_queued_error_and_pending_entries(self):
        value, child, parent = board()
        value.catalog.assets["a"].state = "RUNNING"
        value.catalog.assets["b"].state = "ERROR"
        value.pending = [{"key": "pending:0", "prompt": "New image"}]
        value.order.append("pending:0")
        rows = {row.id: row for row in org.layer_rows(value)}
        self.assertEqual(rows["a"].state, "RUNNING")
        self.assertEqual(rows["b"].state, "ERROR")
        self.assertEqual((rows["pending:0"].title, rows["pending:0"].kind), ("New image", "PENDING"))

    def test_missing_layout_and_empty_groups_stay_findable(self):
        value, child, parent = board()
        value.order.remove("a")
        value.organization.move_into({"a", "b"}, (), "")
        rows = org.layer_rows(value)
        self.assertIn(child, {row.id for row in rows})
        self.assertIn("a", {row.id for row in rows})


if __name__ == "__main__":
    unittest.main()
