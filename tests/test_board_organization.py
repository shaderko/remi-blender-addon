"""Nested board groups and viewport placement without Blender."""

import importlib.util
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi_test_organization", ROOT / "features/board/organization.py")
org = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = org
spec.loader.exec_module(org)


def board(zoom=1, pan=(-10000, 6000), region=(1440, 900)):
    return SimpleNamespace(zoom=zoom, pan=list(pan), region=region, positions={}, scales={},
                           catalog=SimpleNamespace(assets={}), organization=org.Organization())


def overlaps(a, b):
    x, y, w, h = a
    xx, yy, ww, hh = b
    return x < xx+ww and x+w > xx and y < yy+hh and y+h > yy


class Groups(unittest.TestCase):
    def test_nested_group_round_trip(self):
        model = org.Organization()
        child = model.create({"a", "b"}, title="Small parts")
        parent = model.create({"a", "b", "c"}, {child.id}, "Assembly")
        self.assertEqual(child.parent, parent.id)
        self.assertEqual(model.members(parent.id), {"a", "b", "c"})
        restored = org.Organization(model.documents(), model.membership)
        self.assertEqual(restored.documents(), model.documents())
        self.assertEqual(restored.path(child.id), "Assembly / Small parts")

    def test_group_inside_common_parent(self):
        model = org.Organization()
        parent = model.create({"a", "b", "c"})
        child = model.create({"a", "b"})
        self.assertEqual(child.parent, parent.id)
        self.assertEqual(model.membership["c"], parent.id)
        self.assertEqual(model.roots({child.id, parent.id}), {parent.id})

    def test_cycle_prevention_is_atomic(self):
        model = org.Organization()
        child = model.create({"a"})
        parent = model.create({"b"}, {child.id})
        before = (model.documents(), dict(model.membership))
        for target in (child.id, parent.id):
            with self.assertRaises(ValueError):
                model.move_into({"b"}, {parent.id}, target)
            self.assertEqual((model.documents(), model.membership), before)

    def test_ungroup_keeps_cards_and_children(self):
        model = org.Organization()
        child = model.create({"a"})
        parent = model.create({"b"}, {child.id})
        outer = model.create({"c"}, {parent.id})
        model.ungroup({parent.id})
        self.assertEqual(child.parent, outer.id)
        self.assertEqual(model.membership["b"], outer.id)
        self.assertEqual(model.members(outer.id), {"a", "b", "c"})
        model.ungroup({outer.id})
        self.assertEqual(child.parent, "")
        self.assertEqual(model.membership, {"a": child.id})

    def test_move_out_and_empty_anchor(self):
        model = org.Organization()
        group = model.create({"a"})
        group.anchor = [600, -700]
        model.move_into({"a"}, (), "")
        self.assertEqual(model.bounds({})[group.id][:2], (600, -700))
        self.assertEqual(model.membership, {})

    def test_padding_recursively_includes_children_and_captions(self):
        model = org.Organization()
        child = model.create({"a"})
        parent = model.create(set(), {child.id})
        rects = {"a": (500, 600, 300, 300)}
        for zoom in (.12, 1, 3):
            bounds = model.bounds(rects, zoom=zoom)
            inner, outer = bounds[child.id], bounds[parent.id]
            self.assertEqual(inner[0], 500-org.PADDING)
            self.assertEqual(inner[1], 600-max(org.CAPTION, 18/zoom)-org.PADDING)
            self.assertEqual(outer[0], inner[0]-org.PADDING)
            self.assertEqual(outer[1], inner[1]-org.PADDING)
            self.assertAlmostEqual(outer[3], inner[3]+2*org.PADDING+max(org.HEADER, 18/zoom))
        self.assertEqual(rects["a"], (500, 600, 300, 300))

    def test_malformed_documents_and_cycles_are_repaired(self):
        documents = [None, [], "bad", {"id": "group:bad", "title": "Bad", "anchor": "ab"},
                     {"id": "group:nan", "title": "Bad", "anchor": [float("nan"), 0]},
                     {"id": "group:a", "title": "A", "parent": "group:b"},
                     {"id": "group:b", "title": "B", "parent": "group:a"}]
        model = org.Organization(documents, {"a": "group:a", "b": [], "c": {}, "d": None, "e": "missing"})
        self.assertEqual(set(model.groups), {"group:a", "group:b"})
        self.assertEqual(model.membership, {"a": "group:a"})
        self.assertLessEqual(len(list(model.ancestors("group:a"))), 2)
        self.assertEqual(len(model.bounds({})), 2)

    def test_names_and_empty_selection(self):
        self.assertEqual(org.clean_name("  My\n  group  "), "My group")
        with self.assertRaises(ValueError):
            org.clean_name(" \t ")
        model = org.Organization()
        with self.assertRaises(ValueError):
            model.create(set())
        self.assertEqual(model.create({"a"}).title, "Group")
        self.assertEqual(model.create({"b"}).title, "Group 2")


class Placement(unittest.TestCase):
    def test_distant_pan_and_zoom_stay_in_view(self):
        for zoom in (.12, .7, 1, 3):
            value = board(zoom=zoom)
            before = list(value.pan)
            org.place_in_view(value, "new")
            x, y, w, h = org.card_rects(value)["new"]
            self.assertGreaterEqual(x*zoom+value.pan[0], 80)
            self.assertLessEqual((x+w)*zoom+value.pan[0], value.region[0]-24)
            self.assertGreaterEqual(y*zoom+value.pan[1], 150)
            self.assertLessEqual((y+h)*zoom+value.pan[1], value.region[1]-28)
            self.assertEqual(value.pan, before)

    def test_multiple_cards_and_varied_scales_never_overlap(self):
        rng = random.Random(80)
        value = board(zoom=.6)
        for index in range(30):
            key = str(index)
            org.place_in_view(value, key)
            rectangles = org.card_rects(value)
            for other in rectangles:
                if other != key:
                    self.assertFalse(overlaps(rectangles[key], rectangles[other]), (key, other))
            value.scales[key] *= rng.uniform(.5, 1)

    def test_empty_and_huge_group_frames_are_obstacles(self):
        value = board(pan=(0, 0))
        group = value.organization.create({"a"})
        value.positions["a"] = [-100000, -100000]
        value.scales["a"] = 1000
        org.place_in_view(value, "new")
        self.assertFalse(overlaps(org.card_rects(value)["new"], org.group_bounds(value)[group.id]))
        value = board(pan=(0, 0))
        group = value.organization.create({"missing"})
        group.anchor = [550, 350]
        org.place_in_view(value, "new")
        self.assertFalse(overlaps(org.card_rects(value)["new"], org.group_bounds(value)[group.id]))


if __name__ == "__main__":
    unittest.main()
