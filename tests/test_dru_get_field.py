#!/usr/bin/env python3
"""`A.getField('x') == 'y'` in a `.kicad_dru` condition is read, not unsupported.

Measured on kicad-cli 10.0.8 (an `assertion` rule per row, on a board of
footprints with fields, their pads, a track and a via):

  * getField() reads the FOOTPRINT item's fields. On a pad, a track, a via, or a
    `B` that is not there it is '', so `A.getField('MPN') == 'x'` binds a part
    and none of its pads, and `A.getField('MPN') != 'x'` binds every pad;
  * a field name is case-sensitive ('mpn' is not 'MPN'); Reference, Value,
    Datasheet and Description are fields; a field the part lacks is '';
  * the text is the file's, no `${VARIABLE}` expanded;
  * `==` is the NetName compare: case-insensitive, and a literal on the right is
    a wildcard ('AB*' binds 'abc'), on the left it is not;
  * a getField() alone is false, and so is a field with no text.

No grader measures a footprint against a copper rule, so a rule on getField()
binds, on the items they grade, what `== ''` / `!= 'x'` say of '' -- the rows
below. `Footprint.fields` carries the fields from both parse paths.

    python3 tests/test_dru_get_field.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zlib

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS)
sys.path.insert(0, os.path.join(REPO, 'py_router'))

from design_rules import (DesignRules, RuleItem, _eval, parse_condition,  # noqa: E402
                          parse_dru)
from kicad_parser import parse_kicad_pcb  # noqa: E402

try:
    from kicad_oracle import find_kicad_cli  # noqa: E402
    CLI = find_kicad_cli(warn=False)
except Exception:                                              # noqa: BLE001
    CLI = None

FIELDS = (('MPN', 'ABC-1'), ('Other', 'a b'), ('Reference', 'U1'), ('mpn', 'xyz'))
PART = RuleItem(type='footprint', footprint_ref='U1', fields=FIELDS)
PAD = RuleItem(type='pad', footprint_ref='U1')
TRACK = RuleItem(type='track')

#: condition -> (holds on the footprint, on a pad / track / via), measured
ROWS = {
    "A.getField('MPN') == 'ABC-1'": (True, False),
    "A.getField('MPN') == 'abc-1'": (True, False),
    "A.getField('MPN') == 'ABC*'": (True, False),
    "A.getField('MPN') == 'abc?1'": (True, False),
    "A.getField('MPN') == '*-1'": (True, False),
    "A.getField('MPN') == 'ABC'": (False, False),
    "'ABC-1' == A.getField('MPN')": (True, False),
    "'ABC*' == A.getField('MPN')": (False, False),
    "A.getField('MPN') != 'ABC-1'": (False, True),
    "A.getField('MPN') != 'x'": (True, True),
    "A.getField('mpn') == 'xyz'": (True, False),
    "A.getField('MPN') == 'xyz'": (False, False),
    "A.getField('Reference') == 'u1'": (True, False),
    "A.getField('nope') == ''": (True, True),
    "A.getField('nope') != ''": (False, False),
    "A.getField('nope') != 'x'": (True, True),
    "A.getField('MPN') == ''": (False, True),
    "A.getField('MPN') != ''": (True, False),
    "A.getField('MPN') == A.getField('mpn')": (False, True),
    "A.getField('MPN') != A.getField('Other')": (True, False),
    "A.getField('MPN')": (False, False),
    "!A.getField('MPN')": (True, True),
    "!(A.getField('MPN') == 'ABC-1')": (False, True),
    "A.getField('MPN') == 'ABC-1' || A.getField('Other') == 'a b'": (True, False),
    "A.getField('MPN') == 'ABC-1' && A.getField('Other') == 'a b'": (True, False),
    "A.getField('MPN') == 'ABC-1' && A.getField('Other') == 'zz'": (False, False),
    # a B that is not there reads '' as well
    "B.getField('MPN') == 'ABC-1'": (False, False),
    "B.getField('MPN') != 'ABC-1'": (True, True),
    "B.getField('MPN') == ''": (True, True),
}


def holds(cond, item, other=None):
    return bool(_eval(parse_condition(cond), item, other, None))


class Model(unittest.TestCase):
    def test_rows(self):
        for cond, (part, copper) in ROWS.items():
            with self.subTest(cond=cond):
                self.assertEqual(holds(cond, PART), part, 'footprint')
                self.assertEqual(holds(cond, PAD), copper, 'pad')
                self.assertEqual(holds(cond, TRACK), copper, 'track')

    def test_matching_and_negation_on_a_rule(self):
        # the three cases a rule is written for, through parse_dru
        for cond, on_part, on_pad in (("A.getField('MPN') == 'ABC-1'", True, False),
                                      ("A.getField('MPN') == 'nope'", False, False),
                                      ("A.getField('MPN') != 'ABC-1'", False, True)):
            rules, _ = parse_dru(f'(version 1)\n(rule "r" (condition "{cond}")\n'
                                 f'  (constraint track_width (min 0.5mm)))\n')
            self.assertIsNone(rules[0].unsupported, cond)
            with self.subTest(cond=cond):
                self.assertEqual((holds(cond, PART), holds(cond, PAD)), (on_part, on_pad))

    def test_it_is_not_in_the_unsupported_list(self):
        rules, _ = parse_dru('(version 1)\n(rule "r" (condition "A.hasComponentClass(\'x\')")\n'
                             '  (constraint track_width (min 0.5mm)))\n')
        self.assertIn('hasComponentClass', rules[0].unsupported)


BOARD = '''(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0")
 (general (thickness 1.6)) (paper "A4")
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user "F.Silkscreen")
  (39 "F.Mask" user) (44 "Edge.Cuts" user) (47 "F.CrtYd" user "F.Courtyard") (49 "F.Fab" user))
 (setup (pad_to_mask_clearance 0))
 (net 0 "") (net 1 "A")
 (gr_rect (start 0 0) (end 40 30) (stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts")
  (uuid "e0000000-0000-0000-0000-000000000001"))
{parts}
)
'''
PART_TXT = ('(footprint "x:{name}" (layer "F.Cu") (uuid "f0000000-0000-0000-0000-00000000000{i}") '
            '(at {x} 10)\n'
            '  (property "Reference" "{name}" (at 0 -2 0) (layer "F.SilkS") '
            '(uuid "f1000000-0000-0000-0000-00000000000{i}") (effects (font (size 1 1))))\n'
            '{props}'
            '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask") (net 1 "A") '
            '(uuid "a0000000-0000-0000-0000-00000000000{i}")))\n')


def prop(k, v):
    return (f'  (property "{k}" "{v}" (at 0 0 0) (layer "F.Fab") (hide yes) '
            f'(uuid "f2000000-0000-0000-0000-0000000000{zlib.crc32(k.encode()) % 90 + 10}") '
            f'(effects (font (size 1 1))))\n')


class Parser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        parts = ''.join(PART_TXT.format(name=n, i=i + 1, x=10 + 8 * i, props=p) for i, (n, p) in enumerate((
            ('U1', prop('MPN', 'ABC-1') + prop('Other', 'a \\"q\\" b') + prop('Empty', '')),
            ('U2', ''))))
        cls.path = os.path.join(cls.td.name, 'b.kicad_pcb')
        with open(cls.path, 'w', encoding='utf-8') as fh:
            fh.write(BOARD.format(parts=parts))

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_text_parser_fields(self):
        pcb = parse_kicad_pcb(self.path)
        self.assertEqual(pcb.footprints['U1'].fields,
                         {'Reference': 'U1', 'MPN': 'ABC-1', 'Other': 'a "q" b'})
        self.assertEqual(pcb.footprints['U2'].fields, {'Reference': 'U2'})

    def test_pcbnew_fields_agree(self):
        try:
            import pcbnew
            from kicad_parser import build_pcb_data_from_board
        except Exception:                                      # noqa: BLE001
            self.skipTest('no pcbnew')
        live = build_pcb_data_from_board(pcbnew.LoadBoard(self.path))
        for key, fp in parse_kicad_pcb(self.path).footprints.items():
            self.assertEqual(live.footprints[key].fields, fp.fields, key)

    def test_item_for_footprint(self):
        pcb = parse_kicad_pcb(self.path)
        dr = DesignRules.from_project(pcb, self.path, copper_layers=['F.Cu', 'B.Cu'])
        part = dr.item_for_footprint('U1', pcb.footprints['U1'])
        pad = dr.item_for_pad(pcb.footprints['U1'].pads[0])
        self.assertTrue(holds("A.getField('MPN') == 'abc-1'", part))
        self.assertFalse(holds("A.getField('MPN') == 'abc-1'", pad))
        self.assertTrue(holds("A.getField('MPN') != 'abc-1'", pad))
        self.assertFalse(holds("A.getField('MPN') == 'ABC-1'", dr.item_for_footprint(
            'U2', pcb.footprints['U2'])))


@unittest.skipUnless(CLI, 'no kicad-cli')
class Oracle(unittest.TestCase):
    """A sample of ROWS held to kicad-cli: the items each assertion holds on."""
    SAMPLE = ("A.getField('MPN') == 'abc*'", "'ABC*' == A.getField('MPN')",
              "A.getField('MPN') != 'ABC-1'", "A.getField('mpn') == 'ABC-1'",
              "A.getField('nope') == ''", "A.getField('MPN')", "B.getField('MPN') != 'x'")

    def test_sample(self):
        fps = ''.join(PART_TXT.format(name=n, i=i + 1, x=10 + 8 * i, props=p) for i, (n, p) in enumerate((
            ('U1', prop('MPN', 'ABC-1')), ('U2', prop('MPN', 'XYZ')))))
        track = ('(segment (start 5 20) (end 15 20) (width 0.2) (layer "F.Cu") (net 1) '
                 '(uuid "b0000000-0000-0000-0000-000000000001"))\n')
        scope = "A.Type == 'Footprint' || A.Type == 'Pad' || A.Type == 'Track'"
        names = {'f0000000-0000-0000-0000-000000000001': 'U1',
                 'f0000000-0000-0000-0000-000000000002': 'U2',
                 'a0000000-0000-0000-0000-000000000001': 'pad1',
                 'a0000000-0000-0000-0000-000000000002': 'pad2',
                 'b0000000-0000-0000-0000-000000000001': 'track'}
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'o.kicad_pcb')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(BOARD.format(parts=fps + track))
            with open(os.path.join(td, 'o.kicad_pro'), 'w', encoding='utf-8') as fh:
                json.dump({'meta': {'filename': 'o.kicad_pro', 'version': 3}}, fh)
            with open(os.path.join(td, 'o.kicad_dru'), 'w', encoding='utf-8') as fh:
                fh.write('(version 1)\n' + ''.join(
                    f'(rule "row{i}" (condition "{scope}") (constraint assertion "{c}"))\n'
                    for i, c in enumerate(self.SAMPLE)))
            out = os.path.join(td, 'o.json')
            subprocess.run([CLI, 'pcb', 'drc', '--format', 'json', '--severity-all', '-o', out, path],
                           capture_output=True, text=True, timeout=300)
            with open(out, encoding='utf-8') as fh:
                found = json.load(fh)['violations']
        failed = {c: set() for c in self.SAMPLE}
        for v in found:
            if v['type'] == 'assertion_failure':
                c = self.SAMPLE[int(re.search(r'row(\d+)', v['description']).group(1))]
                failed[c] |= {names.get(i['uuid']) for i in v['items']}
        items = {'U1': RuleItem(type='footprint', footprint_ref='U1', fields=(('MPN', 'ABC-1'),)),
                 'U2': RuleItem(type='footprint', footprint_ref='U2', fields=(('MPN', 'XYZ'),)),
                 'pad1': RuleItem(type='pad'), 'pad2': RuleItem(type='pad'),
                 'track': RuleItem(type='track')}
        for c in self.SAMPLE:
            with self.subTest(cond=c):
                model = {n for n, it in items.items() if not holds(c, it)}
                self.assertEqual(model, failed[c] - {None})


if __name__ == '__main__':
    unittest.main()
