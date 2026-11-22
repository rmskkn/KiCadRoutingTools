#!/usr/bin/env python3
"""A `.kicad_dru` length / skew rule's condition may be more than an ||-list.

The grader read a condition only as an ||-list of net terms, optionally ANDed with
`A.NetName != 'x'` (tests/test_length_rule_name_exclusions.py). Every other
shape was "not modelled" and its rule NOT GRADED. Each term is a fact about the
net, so KiCad's `&&` and `!` of them bind a net as they read. Held to kicad-cli
10.0.8 on tests/fixtures/1846/time_rule with net P in class TIMED and Q in class
OTHER, a `length (min 1000mm)` rule per condition, the nets it reports being
`KICAD`:

  1. now read: another negated factor (`A.NetClass != 'x'`, `!A.hasNetclass('x')`,
     `!A.inDiffPair('x')`), several ||-lists ANDed, and a property or function
     name in any case (`A.netname`, `A.hasnetclass`);
  2. `!A.hasNetclass('x')` is case-sensitive, as hasNetclass() is, and
     `A.NetClass != 'x'` is not;
  3. still not modelled: an item type (a rule per item, not per net), a
     negated group, hasExactNetclass(), a lower-case item `a.`, and time-domain
     and (layer ...) rules as before.

    python3 tests/test_length_rule_condition_shapes.py
"""
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS)
sys.path.insert(0, os.path.join(REPO, 'py_router'))

from kicad_dru import length_rule_matches, parse_dru_length_rules  # noqa: E402

try:
    from kicad_oracle import find_kicad_cli  # noqa: E402
    CLI = find_kicad_cli(warn=False)
except Exception:                                              # noqa: BLE001
    CLI = None

FIXTURE = os.path.join(TESTS, 'fixtures', '1846', 'time_rule')
#: net -> its classes
NETS = {'P': ['TIMED'], 'Q': ['OTHER']}
#: condition -> the nets kicad-cli 10.0.8 reports under `length (min 1000mm)`
KICAD = {
    "A.NetClass == 'TIMED'": ['P'],
    "A.NetClass != 'TIMED'": ['Q'],
    "!A.hasNetclass('TIMED')": ['Q'],
    "!A.inDiffPair('*')": ['P', 'Q'],
    "A.hasNetclass('TIMED') && !A.hasNetclass('OTHER')": ['P'],
    "A.NetClass != 'Default'": ['P', 'Q'],
    "!A.hasNetclass('Default')": ['P', 'Q'],
    "A.NetClass == 'Default'": [],
    "A.NetClass == 'TIMED' && A.NetName == 'P'": ['P'],
    "A.NetClass == 'TIMED' && A.NetClass == 'OTHER'": [],
    "A.NetName == 'P' || A.NetClass == 'OTHER'": ['P', 'Q'],
    "(A.NetName == 'P' || A.NetName == 'Q') && A.NetClass != 'TIMED'": ['Q'],
    "A.netname == 'P'": ['P'],
    "A.NETNAME == 'P'": ['P'],
    "A.Netclass == 'TIMED'": ['P'],
    "A.hasnetclass('TIMED') && !A.hasnetclass('OTHER')": ['P'],
    "A.NetClass != 'timed'": ['Q'],
    "!A.hasNetclass('timed')": ['P', 'Q'],
}
#: what stays unsupported although KiCad reads it (`!(...)`, an item type, ...)
NOT_MODELLED = (
    "!(A.NetName == 'P')", "A.NetName == 'P' && A.Type == 'Track'", "A.hasExactNetclass('TIMED')",
    "a.NetName == 'P'", "A.NetName == 'P' || A.NetClass != 'x'",
    "(A.NetName == 'P' && A.NetClass == 'TIMED') || A.NetName == 'Q'")


def rule(cond):
    return parse_dru_length_rules(
        f'(version 1)\n(rule "r" (constraint length (min 1000mm))\n  (condition "{cond}"))\n')[0][0]


def bound(r):
    return sorted(n for n, cls in NETS.items() if length_rule_matches(r, n, cls, set(NETS)))


class Model(unittest.TestCase):
    def test_the_measured_table(self):
        for cond, want in KICAD.items():
            with self.subTest(cond=cond):
                r = rule(cond)
                self.assertIsNotNone(r.terms, r.unsupported)
                self.assertEqual(bound(r), want)

    def test_fields(self):
        r = rule("A.hasNetclass('DDR') && A.NetName == 'DQ*' && A.NetClass != 'x' "
                 "&& !A.inDiffPair('CLK') && A.NetName != 'DQS*'")
        self.assertEqual(r.terms, (('netclass', 'DDR'),))
        self.assertEqual(r.also, ((('netname', 'DQ*'),),))
        self.assertEqual(r.exclude_terms, (('netclass', 'x'), ('indiffpair', 'CLK')))
        self.assertEqual(r.exclude, ('DQS*',))

    def test_the_old_shapes_are_as_they_were(self):
        r = rule("A.hasNetclass('TIMED') && A.NetName != 'CS*' && A.NetName != 'RESET'")
        self.assertEqual((r.terms, r.exclude, r.exclude_terms, r.also),
                         ((('netclass', 'TIMED'),), ('CS*', 'RESET'), (), ()))
        r = rule("A.NetName != 'Q'")
        self.assertEqual((r.terms, r.exclude), ((), ('Q',)))

    def test_not_modelled_stays_so(self):
        for cond in NOT_MODELLED:
            with self.subTest(cond=cond):
                r = rule(cond)
                self.assertIsNone(r.terms)
                self.assertEqual((r.exclude, r.exclude_terms, r.also), ((), (), ()))
                self.assertIsNone(length_rule_matches(r, 'P', ['TIMED']))

    def test_layer_clause_and_time_domain_are_still_not_modelled(self):
        r = parse_dru_length_rules('(version 1)\n(rule "r" (layer "F.Cu") (constraint length '
                                   '(min 1mm)) (condition "A.NetClass != \'x\'"))\n')[0][0]
        self.assertIsNone(r.terms)
        self.assertIn('layer', r.unsupported)
        r = parse_dru_length_rules('(version 1)\n(rule "r" (constraint length (min 100ps)) '
                                   '(condition "A.NetClass != \'x\'"))\n')[0][0]
        self.assertIsNone(r.terms)
        self.assertIn('time-domain', r.unsupported)

    def test_message_for_a_shape_that_is_not_an_or_list(self):
        r = rule("A.NetName == 'P' && A.Type == 'Track'")
        self.assertIn('not an ||-list', r.unsupported)


@unittest.skipUnless(CLI, 'no kicad-cli')
class Oracle(unittest.TestCase):
    """KICAD held to kicad-cli: a sample of its rows, on the fixture with Q moved
    to class OTHER."""
    SAMPLE = ("A.NetClass != 'TIMED'", "!A.hasNetclass('timed')", "A.netname == 'P'",
              "A.hasNetclass('TIMED') && !A.hasNetclass('OTHER')",
              "(A.NetName == 'P' || A.NetName == 'Q') && A.NetClass != 'TIMED'",
              "!A.inDiffPair('*')")

    def kicad_nets(self, td, i, cond):
        d = os.path.join(td, f'c{i}')
        os.makedirs(d)
        shutil.copy(FIXTURE + '.kicad_pcb', os.path.join(d, 'b.kicad_pcb'))
        with open(FIXTURE + '.kicad_pro', encoding='utf-8') as fh:
            pro = json.load(fh)
        classes = pro['net_settings']['classes']
        other = dict(next(c for c in classes if c['name'] == 'TIMED'), name='OTHER', priority=1)
        classes.append(other)
        pro['net_settings']['netclass_patterns'] = [
            {'netclass': 'TIMED', 'pattern': 'P'}, {'netclass': 'OTHER', 'pattern': 'Q'}]
        with open(os.path.join(d, 'b.kicad_pro'), 'w', encoding='utf-8') as fh:
            json.dump(pro, fh)
        with open(os.path.join(d, 'b.kicad_dru'), 'w', encoding='utf-8') as fh:
            fh.write(f'(version 1)\n(rule "r" (constraint length (min 1000mm))\n'
                     f'  (condition "{cond}"))\n')
        out = os.path.join(d, 'o.json')
        subprocess.run([CLI, 'pcb', 'drc', '--format', 'json', '--severity-all', '-o', out,
                        os.path.join(d, 'b.kicad_pcb')], capture_output=True, text=True, timeout=300)
        with open(out, encoding='utf-8') as fh:
            found = json.load(fh)['violations']
        return sorted({m.group(1) for v in found if v['type'] == 'length_out_of_range'
                       for it in v['items'] for m in [re.search(r'\[(\w+)\]', it['description'])]
                       if m})

    def test_sample(self):
        with tempfile.TemporaryDirectory() as td:
            with concurrent.futures.ThreadPoolExecutor(3) as ex:
                got = list(ex.map(lambda ic: self.kicad_nets(td, *ic), enumerate(self.SAMPLE)))
        for cond, nets in zip(self.SAMPLE, got):
            with self.subTest(cond=cond):
                self.assertEqual(nets, KICAD[cond])
                self.assertEqual(bound(rule(cond)), nets)


if __name__ == '__main__':
    unittest.main()
