/* Native Node runner for the exact same socket-free UI cases, no npm packages. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const context = vm.createContext({});
for (const name of ['engine.js', 'progress.js']) {
  vm.runInContext(fs.readFileSync(path.join(root, 'crm/static/learning', name), 'utf8'), context, {filename: name});
}
context.TEST_CATALOG = JSON.parse(fs.readFileSync(path.join(root, 'crm/learning_lessons.json'), 'utf8'));
context.TEST_CLOCK = {today: '2026-11-04', timezone: 'Europe/Moscow', week_start: '2026-10-26', week_end: '2026-11-01',
  callback: {due_date: '2026-11-05', due_time: '15:00', moscow_date: '2026-11-05'},
  move: {due_date: '2026-11-06', due_time: '11:30', moscow_date: '2026-11-06'}};
context.TEST_PRACTICE_SOURCE = fs.readFileSync(path.join(root, 'crm/static/learning/practice.js'), 'utf8');
const result = vm.runInContext(fs.readFileSync(path.join(root, 'tests/js/learning_test.js'), 'utf8'), context,
  {filename: 'learning_test.js', timeout: 15000});
console.log(JSON.stringify({engine: process.version, testMode: 'DOM model, not browser', results: JSON.parse(result)}, null, 2));
