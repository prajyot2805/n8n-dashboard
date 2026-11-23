"""Outside alarm for the PS5 pipeline: tells you on Telegram when n8n cannot (n8n down, timers stopped, nothing posted).

Runs in GitHub Actions after every dashboard refresh (GitHub starts those only every 3 to 9 h) and on the Mac every 30 min
while it is on (ps5-shorts automation/tips/tips.py). Each place keeps its own state, one message per problem and one when it ends.
Env: TIPS_BOT_TOKEN, TELEGRAM_CHAT_ID. Usage: python3 alarm.py <state file> <checks in a row that must fail>
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

TEXT = {'down': 'n8n does not answer. Nothing posts and your bots are silent until it is back.',
        'off': 'The PS5 pipeline is switched off in n8n, so nothing posts.',
        'stopped': 'The PS5 pipeline has not run for over 45 minutes. Its timers may have stopped.',
        'nopost': 'No Short has been posted for {h:.0f} hours although {q} are queued.'}

DONE = {'down': 'n8n answers again.', 'off': 'The PS5 pipeline is on again.', 'stopped': 'The PS5 pipeline is running again.', 'nopost': 'Shorts are posting again.'}


def problems(now, up, wf, last_run, shorts):
    """{key: text} of what is wrong right now."""
    if not up:
        return {'down': TEXT['down']}
    if not wf.get('active'):
        return {'off': TEXT['off']}
    out = {}
    if last_run and now - last_run > timedelta(minutes=45):
        out['stopped'] = TEXT['stopped']
    ts = lambda v: datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    posted = [ts(s['posted_at']) for s in shorts if s.get('status') == 'posted' and s.get('posted_at') and s.get('layout') != 'T']
    q = sum(1 for s in shorts if s.get('status') == 'queued' and s.get('drive_file_id'))
    daily = ((wf.get('staticData') or {}).get('global') or {}).get('daily') or 6
    slow = q < 18 or str(daily) != '6'  # 1 to 5 a day (/dailycount or a short queue): gaps of up to a day are normal
    if posted and q and now - max(posted) > timedelta(hours=30 if slow else 14):
        out['nopost'] = TEXT['nopost'].format(h=(now - max(posted)).total_seconds() / 3600, q=q)
    return out


def step(state, bad, need):
    """New state and the messages to send. A problem is told after `need` checks in a row and closed once it is gone."""
    new, msgs = {}, []
    for k, text in bad.items():
        n = state.get(k, {}).get('n', 0) + 1
        told = state.get(k, {}).get('told', False)
        if n >= need and not told:
            msgs.append('🚨 ' + text)
            told = True
        new[k] = {'n': min(n, need), 'told': told}
    msgs += ['✅ ' + DONE[k] for k, v in state.items() if v.get('told') and k not in bad and not ('down' in bad and k != 'down')]
    for k, v in state.items():  # while n8n is down the other checks say nothing, so an open problem stays open
        if 'down' in bad and k != 'down':
            new[k] = v
    return new, msgs


def send(text):
    body = json.dumps({'chat_id': os.environ['TELEGRAM_CHAT_ID'], 'text': text}).encode()
    req = urllib.request.Request('https://api.telegram.org/bot%s/sendMessage' % os.environ['TIPS_BOT_TOKEN'], data=body, headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        print('telegram failed', type(e).__name__)  # never the URL: it carries the token
        return False


def main(path, need):
    import update_dashboard as d
    try:
        state = json.load(open(path))
    except (OSError, ValueError):
        state = {}
    wf = {}
    for k in range(3):
        wf = d.api('workflows/' + d.PS5_WF)
        if wf.get('id'):
            break
        time.sleep(60 if k < 2 else 0)
    up, last, shorts = bool(wf.get('id')), None, []
    if up:
        ex = d.api(f'executions?workflowId={d.PS5_WF}&status=success&limit=1').get('data') or []
        last = datetime.fromisoformat(ex[0]['startedAt'].replace('Z', '+00:00')) if ex else None
        shorts = d.node_output(d.PS5_WF, ['Read Shorts L1', 'Read Shorts NA', 'Read Shorts']) or []
    new, msgs = step(state, problems(datetime.now(timezone.utc), up, wf, last, shorts), need)
    if all([send(m) for m in msgs]):  # not sent: the old state stays, so it is tried again next time
        if new != state:
            json.dump(new, open(path, 'w'), indent=1, sort_keys=True)
    print('alarm:', sorted(new) or 'all good', len(msgs), 'messages')


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]))
