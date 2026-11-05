"""Display-only regression checks. No database, providers or real customer records."""
import hashlib
import re
import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE='a042710'

def git_file(name):
    return subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT)


def luminance(value):
    value=value.lstrip('#')
    if len(value)==3:value=''.join(c*2 for c in value)
    values=[int(value[i:i+2],16)/255 for i in (0,2,4)]
    return sum((v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4)*weight for v,weight in zip(values,(.2126,.7152,.0722)))


class ReadabilityTests(unittest.TestCase):
    def test_text_pairs_contrast_in_explicit_and_automatic_themes(self):
        css=(ROOT/'crm/static/app.css').read_text()
        light=css.split(':root{',1)[1].split('}',1)[0]
        dark=css.split(':root[data-theme="dark"]{',1)[1].split('}',1)[0]
        auto=css.split('@media (prefers-color-scheme: dark)',1)[1].split('}',1)[0]
        pairs=[('fg','surface'),('muted','surface-2'),('accent-text','accent-soft'),('heat-text','heat-soft'),('good-text','good-soft'),('warn-text','warn-soft'),('bad-text','bad-soft'),('accent-ink','accent')]
        for mode,block in [('light',light),('auto-light',light),('dark',dark),('auto-dark',auto)]:
            values=dict(re.findall(r'--([\w-]+):\s*([^;\n}]+)',block))
            def resolve(key):
                value=values[key]
                if value.startswith('var('):return resolve(re.search(r'--([\w-]+)',value)[1])
                return value
            for fg,bg in pairs:
                low,high=sorted([luminance(resolve(fg)),luminance(resolve(bg))])
                self.assertGreaterEqual((high+.05)/(low+.05),4.5,(mode,fg,bg))

    def test_all_original_gif_posters_and_manifest_are_byte_identical(self):
        files=subprocess.check_output(['git','ls-tree','-r','--name-only',BASE,'crm/static/learning/examples'],cwd=ROOT,text=True).splitlines()
        self.assertEqual(len(files),74)
        for name in files+['crm/learning_examples.json','scripts/build_learning_examples.py']:
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).digest(),hashlib.sha256(git_file(name)).digest(),name)

    def test_pause_drag_and_success_animation_implementations_unchanged(self):
        before=git_file('crm/static/learning/learning.js').decode()
        after=(ROOT/'crm/static/learning/learning.js').read_text()
        names=[('exampleKey','exampleMarkup'),('exampleMarkup','bindExample'),('bindExample','endSuccess'),('endSuccess','showSuccess'),('showSuccess','successText'),('successText','stepPositionKey'),('beginDrag','dragMove'),('dragMove','finishDrag'),('finishDrag','moveWithKeys'),('moveWithKeys','queuePosition')]
        for name,next_name in names:
            def part(text):return text.split('  function '+name+'(',1)[1].split('  function '+next_name+'(',1)[0]
            self.assertEqual(part(before),part(after),name)
        css_before=git_file('crm/static/learning/learning.css').decode()
        css_after=(ROOT/'crm/static/learning/learning.css').read_text()
        self.assertEqual(css_before.split('.learn-success{',1)[1],css_after.split('.learn-success{',1)[1])

    def test_server_time_storage_permissions_and_provider_code_unchanged(self):
        for name in ['crm/web.py','crm/store.py','crm/company.py','crm/learning.py','crm/gpt.py','crm/robot_runner.py','crm/robot_store.py','crm/automations.py']:
            self.assertEqual((ROOT/name).read_bytes(),git_file(name),name)
        before=git_file('crm/static/app.js').decode();after=(ROOT/'crm/static/app.js').read_text()
        for first,last in [('function isoToday(', 'function addDays('),('function addDays(', 'function nextWorkday('),('function nextWorkday(', 'function ddmm(')]:
            def part(text):return text.split(first,1)[1].split(last,1)[0]
            self.assertEqual(part(before),part(after),first)

    def test_component_colours_are_paired_and_instructions_are_not_muted(self):
        css=(ROOT/'crm/static/learning/learning.css').read_text()
        self.assertIn('p.learn-explanation{font-size:13px;line-height:1.5;color:var(--fg)',css)
        app=(ROOT/'crm/static/app.css').read_text()
        self.assertIn('::placeholder',app);self.assertIn('opacity:1',app)
        self.assertIn('select.inp option',app)
        self.assertIn('overflow-wrap:anywhere',app)
        self.assertNotIn('color:white !important',app)
        help_css=(ROOT/'crm/static/helper.css').read_text()
        self.assertIn('.page-help{position:fixed',help_css)
        self.assertIn('background:var(--surface);color:var(--fg)',help_css)

    def test_help_uses_no_gpt_calls_navigation_or_lesson_state_writes(self):
        script=(ROOT/'crm/static/help.js').read_text()
        for text in ['api(', 'fetch(', 'gptAsk(', 'go(', 'W.action(', 'W.pause(', 'state.attempts', 'sessionStorage', 'localStorage']:
            self.assertNotIn(text,script)
        self.assertIn("['manager','director'].includes",script)


if __name__=='__main__':unittest.main()
