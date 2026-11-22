"""Builds status.json for the dashboard (index.html renders it).

Data comes from the n8n API only: the latest runs of each workflow already read the Google Sheets
(PS5 Videos/Shorts, Shadow Legion Reels/Jobs/Episodes) and the Issues data table, so this script
reads those node outputs instead of calling Google. YouTube view counts are read from the public
video pages; a failed read keeps the last known number (never a made-up one).

Env: N8N_BASE_URL, N8N_API_KEY (GitHub secrets). Locally it falls back to ~/.config/n8n/api.env.
"""
import concurrent.futures, json, os, re, sys, urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

BASE_URL = os.environ.get('N8N_BASE_URL', 'https://n8n.srv992629.hstgr.cloud').rstrip('/')
API_KEY = os.environ.get('N8N_API_KEY')
if not API_KEY:
    try:
        API_KEY = next(l.split('=', 1)[1].strip() for l in open(os.path.expanduser('~/.config/n8n/api.env')) if l.startswith('N8N_API_KEY='))
    except Exception:
        sys.exit('N8N_API_KEY is required')

PS5_WF, RESOLVER_WF, SL_WF = 'NyqkzRSDWYbzusW8', 'WnWMAtnb8XPD91yd', 'rhOGUhnDmE2iaQgX'
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime.now(timezone.utc)
PS5_SLOTS_MIN = [180, 360, 570, 780, 1020, 1200]          # 03:00 06:00 09:30 13:00 17:00 20:00 IST (build.py SLOT_JS)
SL_SLOTS = {0: (10, 0), 1: (18, 0), 2: (10, 0), 3: (18, 0), 4: (10, 0)}  # Mon/Wed/Fri 10:00, Tue/Thu 18:00 IST
SL_BUDGET = 20                                            # Metricool free plan, per month from the 14th
OUT = 'status.json'


def api(path):
    req = urllib.request.Request(f'{BASE_URL}/api/v1/{path}', headers={'X-N8N-API-KEY': API_KEY, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        print('n8n API error', path, e, file=sys.stderr)
        return {}


def node_output(wf, names, scan=20):
    """Items of the first of `names` found in the latest successful runs of workflow `wf`."""
    for e in (api(f'executions?workflowId={wf}&status=success&limit={scan}').get('data') or []):
        rd = (((api(f"executions/{e['id']}?includeData=true").get('data') or {}).get('resultData') or {}).get('runData') or {})
        for n in names:
            try:
                return [i['json'] for i in rd[n][0]['data']['main'][0]]
            except (KeyError, IndexError, TypeError):
                pass
    return None


def ts(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=IST)
    except ValueError:
        return None


def iso(d):
    return d.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if d else None


def ist_day(d):
    return d.astimezone(IST).date() if d else None


def is_gta(game):
    return bool(re.search(r'\b(gta\s*(6|vi)|grand\s+theft\s+auto\s*(6|vi))\b', str(game or ''), re.I))


def views_of(item):
    vid, short = item
    url = f'https://www.youtube.com/shorts/{vid}' if short else f'https://www.youtube.com/watch?v={vid}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 'Accept-Language': 'en'})
        html = urllib.request.urlopen(req, timeout=8).read().decode('utf-8', 'ignore')
        m = re.search(r'"viewCount":\s*"(\d+)"', html)
        return vid, int(m.group(1)) if m else None
    except Exception:
        return vid, None


def youtube_views(ids, old):
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        got = dict(ex.map(views_of, ids))
    return {v: (got.get(v) if got.get(v) is not None else old.get(v)) for v, _ in ids}


# ---------------------------------------------------------------- PS5 Shorts
def ps5(old_views):
    videos = node_output(PS5_WF, ['Read Videos', 'Read Videos NA', 'Read Videos A']) or []
    shorts = node_output(PS5_WF, ['Read Shorts L1', 'Read Shorts NA', 'Read Shorts']) or []
    issues = node_output(RESOLVER_WF, ['Read Issues V', 'Read Issues R']) or []
    videos = [v for v in videos if v.get('video_id')]
    shorts = [s for s in shorts if s.get('short_id')]
    vtitle = {str(v['video_id']): v.get('new_title') or v.get('old_title') or '' for v in videos}
    today = NOW.astimezone(IST).date()

    posted = [s for s in shorts if s.get('status') == 'posted' and s.get('short_video_id')]
    views = youtube_views([(s['short_video_id'], True) for s in posted] + [(str(v['video_id']), False) for v in videos], old_views)

    def short_card(s, at=None):
        return {'id': s['short_id'], 'title': s.get('short_title') or '', 'game': s.get('game') or '', 'at': iso(at or ts(s.get('posted_at'))),
                'url': s.get('short_url') or '', 'views': views.get(s.get('short_video_id')), 'score': s.get('score') or '',
                'duration': round(float(s.get('duration_sec') or 0)), 'from': vtitle.get(str(s.get('source_video_id')), ''),
                'ig': s.get('ig_status') == 'done', 'fb_url': 'https://www.facebook.com/reel/%s' % s['fb_id'] if s.get('fb_status') == 'done' and s.get('fb_id') else ''}

    # Up next: the poster's own rules (build.py Pick Short and QUEUE_JS pickOrder, 22 Nov): the Short booked for a slot posts;
    # open slots take two GTA 6 Shorts, then one of another game, at most 2 of one source video a day, best score first.
    queued = [s for s in shorts if s.get('status') == 'queued' and s.get('drive_file_id')]
    missed = lambda s: ts(s.get('slot_at')) is not None and ts(s['slot_at']) < NOW - timedelta(hours=1)
    booked = {iso(ts(s['slot_at'])): s for s in queued if ts(s.get('slot_at')) and not missed(s)}
    pool = sorted([s for s in queued if not ts(s.get('slot_at')) or missed(s)],
                  key=lambda s: (-float(s.get('score') or 0), str(s.get('queued_at') or '')))
    line = [(ts(s['posted_at']), s) for s in posted if s.get('layout') != 'T' and ts(s.get('posted_at'))] + [(ts(s['slot_at']), s) for s in booked.values()]

    def next_short(t):
        before = [s for at, s in sorted(((a, s) for a, s in line if a < t), key=lambda x: x[0], reverse=True)]
        used = Counter(s.get('source_video_id') for at, s in line if ist_day(at) == ist_day(t))
        base = [s for s in pool if used[s.get('source_video_id')] < 2] or pool
        gta, rest = [s for s in base if is_gta(s.get('game'))], [s for s in base if not is_gta(s.get('game'))]
        games = [s.get('game') for s in before]
        rest.sort(key=lambda s: -(games.index(s.get('game')) if s.get('game') in games else 10 ** 9))
        turn = len(before) >= 2 and all(is_gta(s.get('game')) for s in before[:2])
        return (rest + gta if turn else gta + rest)[0]

    slots, day0 = [], datetime.combine(today, datetime.min.time(), IST)
    for k in range(3):
        for m in PS5_SLOTS_MIN:
            t = day0 + timedelta(days=k, minutes=m)
            if t > NOW and len(slots) < 12:
                slots.append(t)
    up_next = []
    for t in slots:
        b, note, guess = booked.get(iso(t)), '', False
        if b:
            s = b
        elif pool:
            s, guess = next_short(t), True
            pool.remove(s)
            line.append((t, s))
        else:
            up_next.append({'at': iso(t), 'empty': True})
            continue
        rs = s.get('review_status')
        title_state = 'yours' if rs == 'approved' else 'check_sent' if rs == 'sent' else 'claude'
        up_next.append({**short_card(s, t), 'guess': guess, 'note': note, 'title_state': title_state})

    # What's happening now
    now = []
    for v in videos:
        st = v.get('clip_status')
        if st == 'clipping':
            now.append({'kind': 'work', 'text': 'Mac is clipping “%s”' % vtitle[str(v['video_id'])]})
        elif st == 'awaiting_brief':
            now.append({'kind': 'you', 'text': 'Waiting for your brief on “%s”' % vtitle[str(v['video_id'])]})
    n_appr = sum(1 for v in videos if v.get('clip_status') == 'approved')
    if n_appr:
        now.append({'kind': 'wait', 'text': '%d video%s approved, waiting for the Mac to clip' % (n_appr, '' if n_appr == 1 else 's')})
    n_brief = sum(1 for v in videos if v.get('clip_status') == 'brief_queued')
    if n_brief:
        now.append({'kind': 'wait', 'text': '%d more brief request%s queued' % (n_brief, '' if n_brief == 1 else 's')})
    for s in queued:
        if s.get('review_status') == 'sent':
            now.append({'kind': 'you', 'text': 'Title check waiting for you: “%s” (posts %s)' % (s.get('short_title'), ts(s.get('slot_at')).astimezone(IST).strftime('%a %H:%M') if ts(s.get('slot_at')) else 'soon')})
    open_issues = [{'id': i.get('issue_id'), 'title': i.get('title') or i.get('node') or 'Problem', 'status': i.get('status'), 'at': i.get('logged_at')}
                   for i in issues if i.get('issue_id') and i.get('status') not in ('resolved', None, '')]
    for i in open_issues:
        now.append({'kind': 'you' if i['status'] in ('needs_you', 'needs_claude') else 'work',
                    'text': 'Issue: %s (%s)' % (i['title'], i['status'].replace('_', ' '))})

    # Today
    uploads = sorted([v for v in videos if ist_day(ts(v.get('published_at'))) == today], key=lambda v: v.get('published_at') or '')
    made_today = [s for s in shorts if ist_day(ts(s.get('queued_at'))) == today]
    posted_today = sorted([s for s in posted if ist_day(ts(s.get('posted_at'))) == today], key=lambda s: s.get('posted_at') or '')

    # Performance
    week = NOW - timedelta(days=7)
    per_day = Counter(ist_day(ts(s.get('posted_at'))).isoformat() for s in posted if ts(s.get('posted_at')))
    days14 = [(today - timedelta(days=13 - k)).isoformat() for k in range(14)]
    top = sorted([s for s in posted if (ts(s.get('posted_at')) or NOW) >= week], key=lambda s: -(views.get(s['short_video_id']) or 0))[:5]
    pipeline = sorted(videos, key=lambda v: v.get('published_at') or '', reverse=True)[:8]

    return {
        'now': now,
        'today': {
            'posted': [short_card(s) for s in posted_today],
            'uploads': [{'title': vtitle[str(v['video_id'])], 'game': v.get('game') or '', 'url': v.get('video_url') or '', 'at': iso(ts(v.get('published_at'))),
                         'status': v.get('clip_status') or ''} for v in uploads],
            'shorts_made': len(made_today),
            'clipped': sorted({vtitle.get(str(s.get('source_video_id')), '') for s in made_today}),
        },
        'up_next': up_next,
        'queue': {'total': len(queued), 'by_game': dict(Counter(s.get('game') or 'Other' for s in queued).most_common()),
                  'days_left': round(len(queued) / len(PS5_SLOTS_MIN), 1)},
        'videos': [{'title': vtitle[str(v['video_id'])], 'game': v.get('game') or '', 'url': v.get('video_url') or '', 'at': iso(ts(v.get('published_at'))),
                    'status': v.get('clip_status') or '', 'shorts_made': v.get('shorts_made') or '', 'shorts_posted': v.get('shorts_posted') or '',
                    'views': views.get(str(v['video_id']))} for v in pipeline],
        'issues': open_issues,
        'totals': {'shorts_posted': len(posted), 'shorts_views': sum(views.get(s['short_video_id']) or 0 for s in posted),
                   'videos': len(videos), 'video_views': sum(views.get(str(v['video_id'])) or 0 for v in videos)},
        'per_day': [{'day': d, 'posted': per_day.get(d, 0)} for d in days14],
        # Cross-poster workflow (22 Nov): Instagram/Facebook status per Short, from the Shorts tab. No Meta views here
        # (that would need a Meta token in GitHub); counts only.
        'platforms': {
            'youtube': {'posted': len(posted), 'views': sum(views.get(s['short_video_id']) or 0 for s in posted), 'url': 'https://www.youtube.com/@ps5gamesonly95'},
            'instagram': {'posted': sum(s.get('ig_status') == 'done' for s in posted), 'waiting': sum(s.get('ig_status') not in ('done', 'failed') for s in posted),
                          'failed': sum(s.get('ig_status') == 'failed' for s in posted), 'url': 'https://www.instagram.com/ps5games_only/'},
            'facebook': {'posted': sum(s.get('fb_status') == 'done' for s in posted), 'waiting': sum(s.get('fb_status') not in ('done', 'failed', 'skipped') for s in posted),
                         'failed': sum(s.get('fb_status') == 'failed' for s in posted), 'url': 'https://www.facebook.com/ps5gamesonly'},
        },
        'top_week': [short_card(s) for s in top],
        'ok': bool(videos and shorts),
    }, views


# ---------------------------------------------------------------- Shadow Legion
def table(value_range):
    rows = value_range.get('values') or []
    if not rows:
        return []
    h = rows[0]
    return [{h[i]: (r[i] if i < len(r) else '') for i in range(len(h))} for r in rows[1:]]


def shadow_legion():
    out = node_output(SL_WF, ['Read Sheet', 'Read Sheet 2'])
    if not out:
        return {'ok': False}
    tabs = {vr['range'].split('!')[0].strip("'"): table(vr) for vr in out[0].get('valueRanges', [])}
    reels, jobs, eps = tabs.get('Reels', []), tabs.get('Jobs', []), tabs.get('Episodes', [])
    lessons = tabs.get('Lessons', [])
    today = NOW.astimezone(IST).date()
    num = lambda v: float(v) if str(v).replace('.', '', 1).isdigit() else None

    def reel(r):
        return {'id': r.get('id'), 'title': r.get('title') or r.get('theme') or '', 'at': iso(ts(r.get('slot'))), 'status': r.get('status') or '',
                'url': r.get('ig_url') or '', 'views': num(r.get('views')), 'reach': num(r.get('reach')), 'likes': num(r.get('likes')),
                'comments': num(r.get('comments')), 'shares': num(r.get('shares')), 'skip_rate': num(r.get('skip_rate')),
                'avg_watch': num(r.get('avg_watch')), 'score': num(r.get('score'))}

    # Metricool month runs from the 14th
    loc = NOW.astimezone(IST)
    start = loc.replace(day=14, hour=0, minute=0, second=0, microsecond=0)
    if loc.day < 14:
        start = (start - timedelta(days=20)).replace(day=14)
    end = (start + timedelta(days=32)).replace(day=14)
    used = [r for r in reels if r.get('status') in ('posted', 'scheduled') and ts(r.get('slot')) and start <= ts(r['slot']) < end]

    sched = {iso(ts(r['slot'])): r for r in reels if r.get('status') == 'scheduled' and ts(r.get('slot'))}
    pending = {iso(ts(j['slot'])): j for j in jobs if j.get('status') in ('pending', 'rendering') and ts(j.get('slot'))}
    queued_eps = [e for e in eps if e.get('status') == 'queued']
    up_next, k, d = [], 0, NOW.astimezone(IST).date()
    while len(up_next) < 5 and k < 21:
        day = d + timedelta(days=k)
        k += 1
        if day.weekday() not in SL_SLOTS:
            continue
        h, m = SL_SLOTS[day.weekday()]
        t = datetime(day.year, day.month, day.day, h, m, tzinfo=IST)
        if t <= NOW:
            continue
        key = iso(t)
        if key in sched:
            up_next.append({**reel(sched[key]), 'state': 'scheduled'})
        elif key in pending:
            j = pending[key]
            up_next.append({'at': key, 'title': j.get('theme_label') or j.get('theme') or '', 'state': j.get('status'), 'stage': j.get('stage') or ''})
        else:
            e = queued_eps.pop(0) if queued_eps else None
            up_next.append({'at': key, 'title': ('%s · Part %s/%s: %s' % (e['series'], e['part'], e['of'], e['title'])) if e else 'Next theme', 'state': 'planned'})
    # Scheduled reels on other times (slot overrides) still count
    for key, r in sched.items():
        if not any(u.get('at') == key for u in up_next):
            up_next.append({**reel(r), 'state': 'scheduled'})
    up_next.sort(key=lambda u: u['at'] or '')

    now = []
    for j in jobs:
        if j.get('status') == 'rendering':
            now.append({'kind': 'work', 'text': 'Laptop is making reel #%s (%s)' % (j.get('n'), (j.get('stage') or 'rendering').replace('_', ' '))})
        elif j.get('status') == 'pending':
            t = ts(j.get('slot'))
            now.append({'kind': 'you', 'text': 'Reel #%s is due %s: turn on the laptop (SL → Turn ON)' % (j.get('n'), t.astimezone(IST).strftime('%a %d %b, %H:%M') if t else 'soon')})

    posted = [r for r in reels if r.get('status') == 'posted']
    return {
        'ok': True,
        'now': now,
        'today': [reel(r) for r in reels if ist_day(ts(r.get('slot'))) == today],
        'up_next': up_next,
        'budget': {'used': len(used), 'limit': SL_BUDGET, 'from': start.date().isoformat(), 'to': (end - timedelta(days=1)).date().isoformat()},
        'reels': [reel(r) for r in sorted(reels, key=lambda r: r.get('slot') or '', reverse=True) if r.get('status') in ('posted', 'scheduled')],
        'totals': {'posted': len(posted), 'views': sum(num(r.get('views')) or 0 for r in posted), 'reach': sum(num(r.get('reach')) or 0 for r in posted),
                   'likes': sum(num(r.get('likes')) or 0 for r in posted)},
        'episodes_left': len([e for e in eps if e.get('status') == 'queued']),
        'lessons': [l.get('lesson') for l in lessons[-3:] if l.get('lesson')],
    }


def main():
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT)).get('views', {})
        except Exception:
            pass
    p, views = ps5(old)
    data = {'generated_at': iso(NOW), 'ps5': p, 'sl': shadow_legion(), 'views': {k: v for k, v in views.items() if v is not None}}
    if not p['ok']:
        print('PS5 data missing from recent n8n runs', file=sys.stderr)
    json.dump(data, open(OUT, 'w'), indent=1, ensure_ascii=False)
    print('wrote', OUT, 'ps5 ok:', p['ok'], 'sl ok:', data['sl']['ok'], 'up next:', len(p['up_next']), 'queue:', p['queue']['total'])


if __name__ == '__main__':
    main()
