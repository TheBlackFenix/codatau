const test = require('node:test');
const assert = require('node:assert/strict');
const ui = require('../app/static/js/chat-presentation.js');

test('formats amounts without assuming currency or parsing identifiers', () => {
    assert.equal(ui.number(2227700), '2.227.700');
    assert.equal(ui.number(1234.56), '1.234,56');
    assert.equal(ui.number(-1234.5), '-1.234,5');
    assert.equal(ui.number(0), '0');
    assert.equal(ui.number(null), 'Sin dato');
    assert.equal(ui.number('00123'), '00123');
});
test('date groups omit midnight but preserve actual times and fractions', () => {
    assert.equal(ui.label('2026-02-13 00:00:00', true), '13/02/2026');
    assert.equal(ui.label('2026-02-13 14:30:00', true), '13/02/2026 14:30:00');
    assert.equal(ui.label('2026-02-13 00:00:00.125', true), '13/02/2026 00:00:00.125');
    assert.equal(ui.label('2026-02-13', true), '13/02/2026');
});
test('category labels, period names and unrecognized formats are unchanged', () => {
    assert.equal(ui.label('2026-02-13', false), '2026-02-13');
    assert.equal(ui.label('Enero a marzo 2026', true), 'Enero a marzo 2026');
    assert.equal(ui.label('<script>alert(1)</script>'), '<script>alert(1)</script>');
    assert.equal(ui.label(null), 'Sin dato');
    assert.equal(ui.label('2026-02-13T00:00:00Z', true), '2026-02-13T00:00:00Z');
});
