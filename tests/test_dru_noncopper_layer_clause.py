#!/usr/bin/env python3
"""A rule on a non-copper `(layer ...)` is read, when KiCad binds no copper to it.

Such a clause was always "unsupported" and its rule NOT GRADED. KiCad tests a
clearance, track_width, via_diameter, annular_width, hole_clearance or via_count
rule's layer clause against the COPPER layer it checks (a via's against F.Cu), so a
rule on F.SilkS, Edge.Cuts, F.Mask or User.1 binds no track, via or pad: there is
nothing to grade, and saying NOT GRADED was noise. Measured on kicad-cli 10.0.8,
one rule per kind on each layer, F.Cu as the control (rows of `MEASURED`).

It is not true of every kind, and those stay unsupported, because a rule read as
binding nothing there would claim coverage the checker lacks:
  * hole_size, hole_to_hole and edge_clearance bind whatever the clause says;
  * `disallow` of pad / text / graphic / hole binds items ON non-copper layers
    (a pad on F.Mask, a text on F.SilkS);
  * a rule that mixes a read kind with one of those, a coupled-pair rule (the pair
    step reads those itself), a layer name KiCad does not spell so ('F.Silkscreen'
    is a board's own alias, '*.SilkS' a wildcard).

    python3 tests/test_dru_noncopper_layer_clause.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS)
sys.path.insert(0, os.path.join(REPO, 'py_router'))

from design_rules import DesignRules, parse_dru  # noqa: E402
from kicad_dru import _parse_dru_full  # noqa: E402

try:
    from kicad_oracle import find_kicad_cli  # noqa: E402
    CLI = find_kicad_cli(warn=False)
except Exception:                                              # noqa: BLE001
    CLI = None

CU = ['F.Cu', 'B.Cu']
READ = {'clearance': '(min 1mm)', 'track_width': '(min 1mm)', 'via_diameter': '(min 2mm)',
        'annular_width': '(min 1mm)', 'hole_clearance': '(min 2mm)', 'via_count': '(max 0)',
        'disallow': 'track via'}
NOT_READ = (('hole_size', '(min 1mm)'), ('hole_to_hole', '(min 3mm)'),
            ('edge_clearance', '(min 3mm)'), ('silk_clearance', '(min 2mm)'),
            ('physical_clearance', '(min 2mm)'), ('disallow', 'pad'), ('disallow', 'text'),
            ('disallow', 'graphic'), ('disallow', 'hole'), ('disallow', 'track text'))


def rule_text(kind, value, layer, condition='', name='r'):
    cond = f'(condition "{condition}")' if condition else ''
    return (f'(version 1)\n(rule "{name}" {f"(layer {chr(34)}{layer}{chr(34)})" if layer else ""} '
            f'{cond} (constraint {kind} {value}))\n')


class Parse(unittest.TestCase):
    def test_kinds_kicad_tests_are_read(self):
        for layer in ('F.SilkS', 'Edge.Cuts', 'F.Mask', 'User.1', 'B.CrtYd', 'Margin'):
            for kind, value in READ.items():
                with self.subTest(layer=layer, kind=kind):
                    rule = parse_dru(rule_text(kind, value, layer))[0][0]
                    self.assertIsNone(rule.unsupported)
                    self.assertEqual(rule.layers_for(CU), [])

    def test_the_rest_stays_unsupported(self):
        for kind, value in NOT_READ:
            with self.subTest(kind=kind, value=value):
                rule = parse_dru(rule_text(kind, value, 'F.SilkS'))[0][0]
                self.assertIn('(non-copper)', rule.unsupported)

    def test_a_mixed_rule_stays_unsupported(self):
        text = ('(version 1)\n(rule "r" (layer "F.SilkS") (constraint clearance (min 1mm))'
                ' (constraint hole_size (min 1mm)))\n')
        self.assertIn('(non-copper)', parse_dru(text)[0][0].unsupported)

    def test_names_kicad_does_not_spell_so(self):
        for layer in ('F.Silkscreen', '*.SilkS', 'foo', 'f.silks', 'F.SilkS '):
            with self.subTest(layer=layer):
                rule = parse_dru(rule_text('clearance', '(min 1mm)', layer))[0][0]
                self.assertIn('(non-copper)', rule.unsupported)

    def test_a_coupled_pair_rule_stays_unsupported(self):
        rule = parse_dru(rule_text('clearance', '(min 0.12mm)', 'F.SilkS',
                                   "A.hasNetclass('HS') && AB.isCoupledDiffPair()"))[0][0]
        self.assertIn('(non-copper)', rule.unsupported)

    def test_copper_clauses_are_as_they_were(self):
        for layer, want in (('F.Cu', ['F.Cu']), ('outer', CU), ('inner', []), ('In1.Cu', [])):
            rule = parse_dru(rule_text('clearance', '(min 1mm)', layer))[0][0]
            self.assertIsNone(rule.unsupported)
            self.assertEqual(rule.layers_for(CU), want)

    def test_an_unreadable_condition_keeps_its_own_reason(self):
        rule = parse_dru(rule_text('clearance', '(min 1mm)', 'F.SilkS',
                                   "A.hasComponentClass('x')"))[0][0]
        self.assertIn('hasComponentClass', rule.unsupported)


class Resolve(unittest.TestCase):
    """The resolver: the rule on F.SilkS leaves every item at what it was, the
    same rule on F.Cu (the control) does not."""

    def resolved(self, kind, value, layer):
        dr = DesignRules(copper_layers=CU)
        dr.rules = parse_dru(rule_text(kind, value, layer))[0]
        dr.net_names = {1: 'A'}
        track = dr.item_for_net(1, 'track', 'F.Cu')
        via = dr.item_for_net(1, 'via')
        item = via if kind in ('via_diameter', 'annular_width') else track
        other = dr.item_for_net(2, 'track', 'F.Cu') if kind == 'clearance' else None
        return dr.resolve(kind, item, other, 'F.Cu').min

    def test_bound_on_copper_not_on_silk(self):
        for kind, value in (('clearance', '(min 1mm)'), ('track_width', '(min 1mm)'),
                            ('via_diameter', '(min 2mm)')):
            with self.subTest(kind=kind):
                self.assertEqual(self.resolved(kind, value, 'F.Cu'), float(value[5:-3]))
                self.assertNotEqual(self.resolved(kind, value, 'F.SilkS'), float(value[5:-3]))


class Channels(unittest.TestCase):
    """kicad_dru's own clearance reader says nothing of such a rule: not skipped,
    so check_drc has no unmodelled rule to name."""

    def test_clearance_rule_is_not_skipped(self):
        _res, _tracks, notes, skipped, _orders = _parse_dru_full(
            rule_text('clearance', '(min 1mm)', 'F.SilkS', "A.NetClass == 'HS'"), CU)
        self.assertEqual(skipped, [])
        self.assertTrue(any('non-copper layer' in n for n in notes), notes)

    def test_the_same_rule_on_copper_is_scoped(self):
        _res, _tracks, notes, skipped, _orders = _parse_dru_full(
            rule_text('clearance', '(min 1mm)', 'F.Cu', "A.NetClass == 'HS'"), CU)
        self.assertEqual(skipped, [])
        self.assertTrue(any('scoped' in n for n in notes), notes)

    def test_what_stays_unsupported_stays_skipped(self):
        for text in (
                rule_text('clearance', '(min 1mm)', 'F.SilkS', "A.NetClass == 'HS'")
                + '(rule "m" (layer "F.SilkS") (constraint clearance (min 1mm))'
                  ' (constraint hole_size (min 1mm)))\n',
                rule_text('clearance', '(min 0.12mm)', 'F.SilkS',
                          "A.hasNetclass('HS') && AB.isCoupledDiffPair()"),
                rule_text('clearance', '(min 1mm)', 'F.Silkscreen', "A.NetClass == 'HS'")):
            skipped = _parse_dru_full(text, CU)[3]
            with self.subTest(text=text):
                self.assertTrue(skipped and skipped[-1][0] in ('r', 'm'), skipped)


BOARD = '''(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0")
 (general (thickness 1.6)) (paper "A4")
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user "F.Silkscreen")
  (39 "F.Mask" user) (44 "Edge.Cuts" user) (50 "User.1" user))
 (setup (pad_to_mask_clearance 0))
 (net 0 "") (net 1 "A") (net 2 "B")
 (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts")
  (uuid "e0000000-0000-0000-0000-000000000001"))
 (footprint "x:P1" (layer "F.Cu") (uuid "f0000000-0000-0000-0000-000000000001") (at 10 10)
  (property "Reference" "P1" (at 0 -2 0) (layer "F.SilkS") (uuid "f1000000-0000-0000-0000-000000000001") (effects (font (size 1 1) (thickness 0.15))))
  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask") (net 1 "A") (uuid "a0000000-0000-0000-0000-000000000001")))
 (footprint "x:P2" (layer "F.Cu") (uuid "f0000000-0000-0000-0000-000000000002") (at 10.8 10)
  (property "Reference" "P2" (at 0 -2 0) (layer "F.SilkS") (uuid "f1000000-0000-0000-0000-000000000002") (effects (font (size 1 1) (thickness 0.15))))
  (pad "1" thru_hole circle (at 0 0) (size 1.0 1.0) (drill 0.4) (layers "*.Cu" "*.Mask") (net 2 "B") (uuid "a0000000-0000-0000-0000-000000000002")))
 (segment (start 20 20) (end 30 20) (width 0.2) (layer "F.Cu") (net 1) (uuid "b0000000-0000-0000-0000-000000000001"))
 (segment (start 20 20.4) (end 30 20.4) (width 0.2) (layer "F.Cu") (net 2) (uuid "b0000000-0000-0000-0000-000000000002"))
 (via (at 40 20) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "c0000000-0000-0000-0000-000000000001"))
 (via (at 45 20) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "c0000000-0000-0000-0000-000000000002"))
 (via (at 50 30) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "c0000000-0000-0000-0000-000000000003"))
 (segment (start 50.5 25) (end 50.5 35) (width 0.2) (layer "F.Cu") (net 2) (uuid "b0000000-0000-0000-0000-000000000003"))
)
'''
#: kicad-cli 10.0.8: does a rule of the kind still report a violation when its
#: layer clause is non-copper? (A rule on F.Cu is the control and always does.)
MEASURED = {'clearance': False, 'track_width': False, 'via_diameter': False,
            'annular_width': False, 'via_count': False, 'hole_size': True}


#: the rules of the oracle (hole_clearance is not among them: this board does
#: not make KiCad report one. It was measured on a board that does, as the
#: others, and reads as they do)
GROUPS = ((('clearance', '(min 1mm)'), ('track_width', '(min 1mm)'),
           ('via_diameter', '(min 2mm)'), ('annular_width', '(min 1mm)'),
           ('via_count', '(max 1)'), ('hole_size', '(min 1mm)')),)


@unittest.skipUnless(CLI, 'no kicad-cli')
class Oracle(unittest.TestCase):
    def reported(self, td, layer, group):
        tag = f'b_{layer.replace(".", "_")}_{group[0][0]}'
        path = os.path.join(td, tag + '.kicad_pcb')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(BOARD)
        with open(os.path.join(td, tag + '.kicad_pro'), 'w', encoding='utf-8') as fh:
            json.dump({'meta': {'filename': 'b.kicad_pro', 'version': 3}}, fh)
        with open(os.path.join(td, tag + '.kicad_dru'), 'w', encoding='utf-8') as fh:
            fh.write('(version 1)\n' + ''.join(
                f'(rule "r_{k}" (layer "{layer}") (constraint {k} {v}))\n' for k, v in group))
        out = path + '.json'
        subprocess.run([CLI, 'pcb', 'drc', '--format', 'json', '--severity-all', '-o', out, path],
                       capture_output=True, text=True, timeout=300)
        with open(out, encoding='utf-8') as fh:
            found = json.load(fh)['violations']
        return {k: sum(f"'r_{k}'" in v['description'] for v in found) for k, _v in group}

    def test_table(self):
        with tempfile.TemporaryDirectory() as td:
            for group in GROUPS:
                on_copper = self.reported(td, 'F.Cu', group)
                for layer in ('F.SilkS', 'Edge.Cuts'):
                    off = self.reported(td, layer, group)
                    for kind, _v in group:
                        with self.subTest(layer=layer, kind=kind):
                            self.assertGreater(on_copper[kind], 0, f'control: {on_copper}')
                            self.assertEqual(off[kind] > 0, MEASURED[kind], f'{off}')


if __name__ == '__main__':
    unittest.main()
