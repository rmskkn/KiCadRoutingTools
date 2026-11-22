#!/usr/bin/env python3
"""`A.memberOfSheet('x')` and `A.memberOfSheetOrChildren('x')` are read, not unsupported.

KiCad compares a part's schematic SHEET NAME (`Footprint.sheet_name`, as the file
spells it: '/Power/', '/Power/Buck/', '' for the root), not the uuid path in
`(path ...)`. `MEASURED` is what kicad-cli 10.0.8 does: for each list of sheet
names, the pattern -> the parts it binds, one character per sheet ('1' binds),
from an `assertion` rule per row on the parts' pads. What it shows:

  * the compare is case-sensitive, with `*` and `?` wildcards, and both sides
    read as a path ending in '/': '/Power', '/Power/' and a sheet spelled '/Power'
    all agree, and the root ('' or '/') is '/';
  * memberOfSheet() matches the whole path, a `*` reaching across '/': '/Power/*'
    binds '/Power/Buck/' and not '/Power/';
  * memberOfSheetOrChildren() matches as many leading components of the sheet as
    the pattern has, so `*` stays inside one component, '/Power/' binds
    '/Power/Buck/' too, '/' binds every part, and the root binds no other pattern;
  * an empty pattern binds nothing; a track or a via, on no part, binds nothing;
  * a second argument is a compile error in KiCad (`kicad_condition_refusal`).

Not reproduced: the pattern '//', which memberOfSheet() reads as the root sheet.

    python3 tests/test_dru_member_of_sheet.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS)
sys.path.insert(0, os.path.join(REPO, 'py_router'))

from design_rules import (DesignRules, RuleItem, _eval, _sheet_matches,  # noqa: E402
                          parse_condition, parse_dru)
from kicad_parser import parse_kicad_pcb  # noqa: E402

try:
    from kicad_oracle import find_kicad_cli  # noqa: E402
    CLI = find_kicad_cli(warn=False)
except Exception:                                              # noqa: BLE001
    CLI = None

#: (sheet names, {function: {pattern: bits}}); a None sheet is a part with no sheet
MEASURED = (
    (['/Power/', '/Power/Buck/', '/Power/Buck/Fb/', '/PowerX/', '/Pow/', 'Power', '/', None, '/Power/Buck'],
     {
      'memberOfSheet': {
          '/Power/': '100000000',
          '/Power': '100000000',
          '/Power/*': '011000001',
          '/Power/Buck/': '010000001',
          '*Buck*': '011000001',
          '*Buck': '010000001',
          'Buck*': '000000000',
          '*Power*': '111101001',
          '*Power/*': '011000001',
          '/Power/*/*': '001000000',
          '*/': '111111111',
          '/*/': '111110001',
          '/*/*': '011000001',
          '/Power/Buck/*': '001000000',
          '*': '111111111',
          '/': '000000110',
          '/Pow*': '111110001',
          'Power*': '000001000',
          'Power': '000001000',
          '*r': '100001000',
          '': '000000000',
          '/Pow?r/': '100000000',
          '/P*r': '100000000',
          'power': '000000000',
          '/Power//': '000000000',
      },
      'memberOfSheetOrChildren': {
          '/Power/': '111000001',
          '/Power': '111000001',
          '/Power/*': '011000001',
          '/Power/Buck/': '011000001',
          '*Buck*': '000000000',
          '*Buck': '000000000',
          'Buck*': '000000000',
          '*Power*': '000001000',
          '*Power/*': '000000000',
          '/Power/*/*': '001000000',
          '*/': '111111001',
          '/*/': '111110001',
          '/*/*': '011000001',
          '/Power/Buck/*': '001000000',
          '*': '111111001',
          '/': '111111111',
          '/Pow*': '111110001',
          'Power*': '000001000',
          'Power': '000001000',
          '*r': '000001000',
          '': '000000000',
          '/Pow?r/': '111000001',
          '/P*r': '111000001',
          'power': '000000000',
          '/Power//': '000000000',
      },
     }),
    (['/Power/', '/Power/Buck/', '/Power/Buck/Fb/', '/PowerX/', 'Power/Buck/', 'Power', '/', None, '//'],
     {
      'memberOfSheet': {
          '///': '000000000',
          '//': '000000111',
          '/*Buck*': '011000000',
          '/Po*/Buck': '010000000',
          '/Power/Buck*': '011000000',
          '/Power/Bu*': '011000000',
          '/P*r': '100000000',
          '/Power*/': '111100000',
          '/*/Buck': '010000000',
          '/*/Buck/Fb': '001000000',
          '/**': '111100001',
          '/Power/**': '011000000',
          '/Power/Buck/Fb/*': '000000000',
          'Power/*': '000010000',
          'Power/': '000001000',
          'Power/Buck': '000010000',
          '/Power*Buck*': '011000000',
          '/': '000000110',
          '*': '111111111',
          '*/': '111111111',
      },
      'memberOfSheetOrChildren': {
          '///': '000000000',
          '//': '000000001',
          '/*Buck*': '000000000',
          '/Po*/Buck': '011000000',
          '/Power/Buck*': '011000000',
          '/Power/Bu*': '011000000',
          '/P*r': '111000000',
          '/Power*/': '111100000',
          '/*/Buck': '011000000',
          '/*/Buck/Fb': '001000000',
          '/**': '111100001',
          '/Power/**': '011000000',
          '/Power/Buck/Fb/*': '000000000',
          'Power/*': '000010000',
          'Power/': '000011000',
          'Power/Buck': '000010000',
          '/Power*Buck*': '000000000',
          '/': '111111111',
          '*': '111111001',
          '*/': '111111001',
      },
     }),
)
#: patterns the model does not reproduce (see the docstring)
NOT_REPRODUCED = {'//'}


def holds(cond, item, other=None):
    return bool(_eval(parse_condition(cond), item, other, None))


class Model(unittest.TestCase):
    def test_measured_tables(self):
        for sheets, funcs in MEASURED:
            for fn, rows in funcs.items():
                children = fn == 'memberOfSheetOrChildren'
                for pattern, bits in rows.items():
                    if pattern in NOT_REPRODUCED:
                        continue
                    got = ''.join('1' if _sheet_matches(pattern, s or '', children) else '0'
                                  for s in sheets)
                    with self.subTest(fn=fn, pattern=pattern, sheets=sheets):
                        self.assertEqual(got, bits)

    def test_through_the_evaluator(self):
        power = RuleItem(type='pad', sheet_name='/Power/')
        buck = RuleItem(type='pad', sheet_name='/Power/Buck/')
        root = RuleItem(type='pad', sheet_name='')
        track = RuleItem(type='track')
        for cond, items, want in (
                ("A.memberOfSheet('/Power/')", (power, buck, root, track), (1, 0, 0, 0)),
                ("A.memberOfSheetOrChildren('/Power/')", (power, buck, root, track), (1, 1, 0, 0)),
                ("!A.memberOfSheet('/Power/')", (power, buck, root, track), (0, 1, 1, 1)),
                ("!A.memberOfSheetOrChildren('/Power')", (power, buck, root, track), (0, 0, 1, 1)),
                ("A.memberOfSheet('/Power/') || A.memberOfSheet('/')", (power, buck, root),
                 (1, 0, 1)),
                ("A.memberOfSheet('/Power/') && A.Type == 'Pad'", (power, track), (1, 0))):
            for item, w in zip(items, want):
                with self.subTest(cond=cond, item=item.sheet_name):
                    self.assertEqual(holds(cond, item), bool(w))
        # a B that is not there, and a second argument
        self.assertFalse(holds("B.memberOfSheet('/')", power))
        self.assertFalse(holds("A.memberOfSheet('/Power/', 'x')", power))

    def test_rule_is_supported(self):
        for fn in ('memberOfSheet', 'memberOfSheetOrChildren'):
            rules, _ = parse_dru(f'(version 1)\n(rule "r" (condition "A.{fn}(\'/Power/\')")\n'
                                 f'  (constraint clearance (min 1mm)))\n')
            self.assertIsNone(rules[0].unsupported, fn)
            # it scopes by part, which no obstacle stamp prices
            self.assertIn(f'A.{fn}()', rules[0].router_unhonoured)


BOARD = '''(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0")
 (general (thickness 1.6)) (paper "A4")
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user "F.Silkscreen")
  (39 "F.Mask" user) (44 "Edge.Cuts" user))
 (setup (pad_to_mask_clearance 0))
 (net 0 "") (net 1 "A") (net 2 "B")
 (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts")
  (uuid "e0000000-0000-0000-0000-000000000001"))
{parts}
)
'''
PART = ('(footprint "x:{name}" (layer "F.Cu") (uuid "f0000000-0000-0000-0000-00000000000{i}") '
        '(at {x} 10)\n'
        '  (property "Reference" "{name}" (at 0 -2 0) (layer "F.SilkS") '
        '(uuid "f1000000-0000-0000-0000-00000000000{i}") (effects (font (size 1 1))))\n'
        '  {sheet}\n'
        '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask") (net {net} "{nn}") '
        '(uuid "a0000000-0000-0000-0000-00000000000{i}")))\n')
SHEETS = (('R1', '(path "/aaaa0000-0000-0000-0000-000000000001/bbbb0000-0000-0000-0000-000000000001")'
                 ' (sheetname "/Power/") (sheetfile "power.kicad_sch")', 1, 'A'),
          ('R2', '(path "/aaaa0000-0000-0000-0000-000000000002/bbbb0000-0000-0000-0000-000000000002")'
                 ' (sheetname "/Power/Buck/")', 2, 'B'),
          ('R3', '(path "/aaaa0000-0000-0000-0000-000000000003")', 1, 'A'))


class FromBoard(unittest.TestCase):
    """The sheet reaches the pad's RuleItem, and the resolver binds by it."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        parts = ''.join(PART.format(name=n, i=i + 1, x=10 + 10 * i, sheet=s, net=net, nn=nn)
                        for i, (n, s, net, nn) in enumerate(SHEETS))
        cls.path = os.path.join(cls.td.name, 'b.kicad_pcb')
        with open(cls.path, 'w', encoding='utf-8') as fh:
            fh.write(BOARD.format(parts=parts))

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def rules(self, cond, kind='track_width'):
        with open(self.path[:-10] + '.kicad_dru', 'w', encoding='utf-8') as fh:
            fh.write(f'(version 1)\n(rule "r" (condition "{cond}")\n'
                     f'  (constraint {kind} (min 0.9mm)))\n')
        pcb = parse_kicad_pcb(self.path)
        return pcb, DesignRules.from_project(pcb, self.path, copper_layers=['F.Cu', 'B.Cu'])

    def test_sheet_names_are_read(self):
        pcb = parse_kicad_pcb(self.path)
        self.assertEqual([pcb.footprints[r].sheet_name for r in ('R1', 'R2', 'R3')],
                         ['/Power/', '/Power/Buck/', ''])

    def test_binding(self):
        for cond, want in (("A.memberOfSheet('/Power/')", (0.9, None, None)),
                           ("A.memberOfSheetOrChildren('/Power/')", (0.9, 0.9, None)),
                           ("!A.memberOfSheetOrChildren('/Power/')", (None, None, 0.9)),
                           ("A.memberOfSheet('/Elsewhere/')", (None, None, None))):
            pcb, dr = self.rules(cond)
            got = tuple(dr.resolve('track_width', dr.item_for_pad(pcb.footprints[r].pads[0]),
                                   None, 'F.Cu').min for r in ('R1', 'R2', 'R3'))
            with self.subTest(cond=cond):
                self.assertIsNone(dr.rules[0].unsupported)
                self.assertEqual(tuple(g if g == 0.9 else None for g in got), want)

    def test_a_track_is_on_no_sheet(self):
        pcb, dr = self.rules("A.memberOfSheet('/') || A.memberOfSheetOrChildren('/')")
        item = dr.item_for_net(1, 'track', 'F.Cu')
        self.assertNotEqual(dr.resolve('track_width', item, None, 'F.Cu').min, 0.9)


@unittest.skipUnless(CLI, 'no kicad-cli')
class Oracle(unittest.TestCase):
    """The sheet-name rows of FromBoard held to kicad-cli: the same board, an
    assertion rule per condition, the pads each holds on."""
    CASES = ("A.memberOfSheet('/Power/')", "A.memberOfSheet('/Power')",
             "A.memberOfSheet('/Power/*')", "A.memberOfSheetOrChildren('/Power/')",
             "A.memberOfSheetOrChildren('/')", "!A.memberOfSheet('/Power/')",
             "A.memberOfSheet('/power/')")

    def test_rows(self):
        names = {f'a0000000-0000-0000-0000-00000000000{i + 1}': s[0] for i, s in enumerate(SHEETS)}
        scope = "A.Type == 'Pad'"
        with tempfile.TemporaryDirectory() as td:
            parts = ''.join(PART.format(name=n, i=i + 1, x=10 + 10 * i, sheet=s, net=net, nn=nn)
                            for i, (n, s, net, nn) in enumerate(SHEETS))
            path = os.path.join(td, 'o.kicad_pcb')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(BOARD.format(parts=parts))
            with open(os.path.join(td, 'o.kicad_pro'), 'w', encoding='utf-8') as fh:
                json.dump({'meta': {'filename': 'o.kicad_pro', 'version': 3}}, fh)
            with open(os.path.join(td, 'o.kicad_dru'), 'w', encoding='utf-8') as fh:
                fh.write('(version 1)\n' + ''.join(
                    f'(rule "row{i}" (condition "{scope}") (constraint assertion "{c}"))\n'
                    for i, c in enumerate(self.CASES)))
            out = os.path.join(td, 'o.json')
            subprocess.run([CLI, 'pcb', 'drc', '--format', 'json', '--severity-all', '-o', out, path],
                           capture_output=True, text=True, timeout=300)
            with open(out, encoding='utf-8') as fh:
                found = json.load(fh)['violations']
        failed = {c: set() for c in self.CASES}
        for v in found:
            if v['type'] == 'assertion_failure':
                c = self.CASES[int(re.search(r'row(\d+)', v['description']).group(1))]
                failed[c] |= {names.get(i['uuid']) for i in v['items']}
        sheets = {'R1': '/Power/', 'R2': '/Power/Buck/', 'R3': ''}
        for c in self.CASES:
            model = {r for r, s in sheets.items()
                     if not holds(c, RuleItem(type='pad', sheet_name=s))}
            with self.subTest(cond=c):
                self.assertEqual(model, failed[c] - {None})


if __name__ == '__main__':
    unittest.main()
