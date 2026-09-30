import tempfile
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from app.core import Store
from app.display_time import display_datetime
from app.templates import parse_datetime, render
from app.variables import build_variables
from test_app import rule


class Examples(HTMLParser):
    def __init__(self):
        super().__init__()
        self.examples, self.active = [], False

    def handle_starttag(self, tag, attrs):
        if tag == 'pre':
            self.active = True
            self.examples.append('')

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.examples[-1] += data


class GuideTests(unittest.TestCase):
    def test_all_copyable_examples_render_with_real_variables(self):
        parser = Examples()
        parser.feed(Path('app/static/help.html').read_text(encoding='utf-8'))
        self.assertEqual(len(parser.examples), 5)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory + '/db')
            try:
                rules = [rule(variable_name='listener1',name='研发'),
                         rule(id='second',variable_name='listener2',name='行政',enabled=False)]
                store.set('rules',rules)
                store.set('snapshot',dict(semantics_version=2,stale=False,
                    last_success='2026-09-24T10:50:00Z',last_attempt='2026-09-24T10:49:00Z',
                    records=[{'person':'张三'}],rule_people={rules[0]['id']:['张三']}))
                catalog = build_variables(store)
                output = [render(example,catalog) for example in parser.examples]
                self.assertIn('2026-09-24 18:49:00',output[1])
                self.assertIn('• 张三',output[2])
                self.assertIn('研发：1 人未填',output[3])
                self.assertNotIn('行政',output[3])
                self.assertIn('研发：张三',output[4])
            finally:
                store.close()

    def test_fixed_schedule_accepts_display_format_without_changing_instant(self):
        self.assertEqual(parse_datetime('2026-09-24 18:50:00'),parse_datetime('2026-09-24T10:50:00Z'))
        with self.assertRaises(ValueError):
            parse_datetime('2026-02-30 09:00:00')
        self.assertEqual(display_datetime(datetime(2026,12,31,16,tzinfo=timezone.utc)),'2027-01-01 00:00:00')
        self.assertEqual(display_datetime('2026-09-24T18:50:00'),'2026-09-24 18:50:00')
