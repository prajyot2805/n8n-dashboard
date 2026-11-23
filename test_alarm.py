"""Self-check for alarm.py. No network."""
from datetime import datetime, timedelta, timezone
import alarm as a
now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
iso = lambda h: (now - timedelta(hours=h)).isoformat().replace('+00:00', 'Z')
ON, run = {'active': True, 'id': 'x'}, now - timedelta(minutes=5)
Q = [{'status': 'queued', 'drive_file_id': 'd'}] * 20
P = lambda h, **k: {'status': 'posted', 'posted_at': iso(h), **k}
keys = lambda *x: sorted(a.problems(now, *x))
assert keys(True, ON, run, Q + [P(7)]) == [] and keys(True, ON, run, Q + [P(13.9)]) == []
assert keys(True, ON, run, Q + [P(15)]) == ['nopost'] and keys(True, ON, run, [P(99)]) == []             # empty queue is not a fault
assert keys(True, ON, run, Q[:5] + [P(20)]) == [] and keys(True, ON, run, Q[:5] + [P(31)]) == ['nopost']  # slow days
aw = {**ON, 'staticData': {'global': {'daily': 3}}}
assert keys(True, aw, run, Q + [P(20)]) == [] and keys(True, {**ON, 'staticData': None}, run, Q + [P(20)]) == ['nopost']
assert keys(True, ON, run, Q + [P(20), P(1, layout='T')]) == ['nopost']                                   # a commentary Short hides nothing
assert keys(False, {}, None, []) == ['down'] and keys(True, {'active': False}, run, Q) == ['off']
assert keys(True, ON, now - timedelta(minutes=50), Q + [P(1)]) == ['stopped'] and keys(True, ON, None, []) == []
s, m = a.step({}, {'down': 'D'}, 2); assert m == []
s, m = a.step(s, {'down': 'D'}, 2); assert m == ['🚨 D']
s, m = a.step(s, {'down': 'D'}, 2); assert m == []                                                        # once
s, m = a.step(s, {}, 2); assert len(m) == 1 and m[0].startswith('✅') and s == {}
s, m = a.step({'down': {'n': 1, 'told': False}}, {}, 2); assert m == [] and s == {}                        # a hiccup stays quiet
s, m = a.step({'nopost': {'n': 1, 'told': True}}, {'down': 'D'}, 1); assert m == ['🚨 D'] and s['nopost']['told']   # not "fixed" by an outage
s, m = a.step(s, {'nopost': 'N'}, 1); assert len(m) == 1 and '✅' in m[0] and s['nopost']['told']
assert all('{' not in x for x in a.step({k: {'n': 1, 'told': True} for k in a.TEXT}, {}, 1)[1])
print('ok')
