#!/usr/bin/env python3
"""TNA 10X daily-send pipeline - every deterministic mechanic of the campaign in one file.

Built 2026-09-30 from the session that rebuilt 10X. The LLM run does the tool
calls (HigherGov, Airtable, Gmail), the human-grade review and the "why this is
yours" writing. This script does everything that must never drift:

  ingest    HigherGov search_opportunities result files -> hg/raw.jsonl (newest version per solicitation)
  gate      ingestion gates                              -> hg/clean.json
  match     opportunity-first matcher v3 (approved)      -> picks.json
  review    side-by-side sheet for the human-grade review -> review.txt
  draft     assemble emails from facts the LLM wrote     -> drafts.json
  schedule  Send After spacing, 4-9 minutes, top-up aware -> drafts.json (in place)
  payloads  Airtable batches: Outbox (10s), Sends (10s), Contacts (10s) -> payloads/*.json
  backfill  match HigherGov awardee lookups to contacts, State only    -> backfill_state.json

Every command prints one line of counts. Never paste the data into the conversation.

Run order for a day:  ingest -> gate -> match -> review -> (LLM: review + live check + facts)
                      -> draft -> schedule -> payloads -> (LLM: write batches to Airtable)
"""
import argparse, collections, datetime, glob, hashlib, json, math, os, random, re, sys

# --------------------------------------------------------------------------------------------
# Airtable field IDs (base appa68g31bjkD7QfA)
# --------------------------------------------------------------------------------------------
C = dict(email='fldOkkq9N5M23jmVe', company='fld71b1KMuOBNODfM', name='fldnVxaHdM1awj94C', state='fldASYNuAPvfMZ59b',
         revenue='fldQuVZqcRwf5hukW', certs='fldxANdsBQYrA5F6z', naics='fld75YMwnEpeBe6b4', cap='fld3mQLjpwMh0R2SY',
         status='fldXiMYlpYJdlhvaY', next_eligible='fldHpBQbFbIWmy78v', last_touched='fldkhMBfq18rpbeNS',
         times_touched='fldPAuDgajOaqHPL7', claimed_by='fldZcuXXf3IEJci42', claimed_at='fld7YN55f9mTfiHc5')
OUTBOX = dict(key='fldFjuHthu0ENUjXD', to='fldG9PhiK5BBn1KWa', subject='fldDydK7RjdxnX205', html='fldqnQuLICpcRxDEq',
              machine='fldYRONmYQyyAmTlR', send_after='fld5BABdLtPfapoiL', status='fldo3mwgxvFsTXwLE', contact_email='fldLq0B1D3WKrM3Fm')
SENDS = dict(key='fldIzJZ2v8Lbski86', email='fld5A9nM0VyWCiI5N', company='fldWsQOO3wQMjtQYs', machine='fldybWbCJbj7PR1KX',
             address='fld59yzYB2EKnajjI', sent_at='fldJsHKQ0szp62jc7', opp='fld3YyS0tCRLEhatw', score='flduulISthYp4FlhF',
             subject='fldjxkl3BOchzIoSE', snapshot='fldEGydo2FGMItAY5', variant='fldKUFVWdh9fRZDYF')

# --------------------------------------------------------------------------------------------
# Shared vocabulary
# --------------------------------------------------------------------------------------------
SA = {'SBA': 'SB', 'SBP': 'SB', '8A': '8(a)', '8AN': '8(a)', 'HZC': 'HUBZone', 'HZS': 'HUBZone', 'SDVOSBC': 'SDVOSB',
      'SDVOSBS': 'SDVOSB', 'VOSB': 'VOSB', 'VSA': 'VOSB', 'WOSB': 'WOSB', 'WOSBSS': 'WOSB', 'EDWOSB': 'EDWOSB', 'EDWOSBSS': 'EDWOSB'}
CERT = {'8(a)': ['8A'], 'HUBZone': ['HUB'], 'SDVOSB': ['SDVOSB'], 'VOSB': ['VOSB', 'SDVOSB'], 'WOSB': ['WOSB', 'EDWOSB'], 'EDWOSB': ['EDWOSB']}
PLACEHOLDER_REVENUE = {250000}          # 791 contacts carry exactly $250,000 - a default, not data
US = set('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR '
         'PA RI SC SD TN TX UT VT VA WA WV WI WY PR GU VI AS MP'.split())
STATE_WORDS = ('alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois '
               'indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana '
               'nebraska nevada hampshire jersey mexico york carolina dakota ohio oklahoma oregon pennsylvania rhode tennessee texas '
               'utah vermont virginia washington wisconsin wyoming')
STOP = set(('''a an and or the of for to in on at by with from as is are be this that these those we our you your their its it
into per via etc all any other others more most such not no nor than then also including include includes based related within
across over under new one two three services service solutions solution products product systems system business small owned
disabled veteran veterans woman women minority provide provides providing support supporting requirements requirement management
consulting engineering sdvosb vosb hubzone wosb edwosb certified concern socioeconomic contract contracts contractor
contractors government federal agency agencies offeror offerors solicitation response responses due deliver delivery ensure
perform performance work shall will may must specialize specialized specializing company firm llc inc corp corporation general
various full range quality high technical professional industry industries experience expertise years clients customer
customers commercial public private sector national have has had does did been being both state states offer offers offered
future meet meets used use uses using local line lines address area office offices latest effective critical innovative
commitment executive assist increase tools tool cost costs far procurement understand challenges designed promotes exercise
gap innovation human conference implement applications platform man risk card ssp heavy mills housing residential upgrades
aviation traffic intelligence educational exam cost-effective strategic master assessment configuration architecture efficient
solution-oriented manufacturing contractors construction building buildings repair repairs ''' + STATE_WORDS).split())
NAME_STOP = set('''technologies technology tech group associates enterprises enterprise international industries global partners
holdings services solutions systems consulting consultants company companies corporation incorporated limited federal american
national united advanced integrated applied strategic strategies innovations innovative resources resource works worldwide
defense sciences science engineering engineers construction contracting builders management logistics'''.split())
def toks(s): return {w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", (s or '').lower()) if w not in STOP}

# NAICS so broad that a focused registration in them is not evidence of fit on its own
CATCH_ALL = {'236220', '236210', '236118', '541611', '541330', '541990', '561210', '561990', '621111', '621999', '238990',
             '541618', '541690', '541519', '561110', '488190', '811310', '423990', '424990', '541511', '541512', '541712',
             '541713', '541714', '541715', '611430', '611710'}
# On-site work: the firm must be in the place-of-performance state (Daniel's rule, 2026-09-30)
ONSITE_PFX = ('23', '5617', '5616', '5621', '5622', '4842', '6211', '6214', '6219', '8113', '8111', '5413', '5629', '4931',
              '7223', '1133', '5311', '4812')
REMOTE_PFX = ('5415', '5416', '5412', '5411', '5418', '5182', '5419', '6114', '6117', '5112', '5191')

KEEP_TYPES = {'solicitation', 'presolicitation', 'synopsis solicitation', 'combined synopsis/solicitation', 'special notice', 'sources sought'}
INGEST_BAD = re.compile(r'security clearance|top secret|ts/sci|secret clearance|facility clearance|\bfcl\b|fedramp|cmmc level [23]|'
                        r'authorized distributor|authorized dealer|brand[- ]name only', re.I)
VEHICLE = re.compile(r'task order request|\btorp?\b|call order|under (the|its) (existing )?(bpa|idiq|gsa|mac|matoc|schedule)|'
                     r'(oasis|seaport|sewp|cio-sp|stars|alliant|polaris|gsa mas|gsa schedule)\b|only (current )?(holders|awardees)|'
                     r'fair opportunity|existing (idiq|matoc|macc|bpa) holders|awardees of|mac holders|limited to .{0,40}holders', re.I)
SOLE = re.compile(r'sole[- ]source|intent to (sole|award)|notice of intent|only one responsible|other than full and open|intends to '
                  r'negotiate|limited sources|justification and approval|\bj&a\b|6\.302|13\.106-1\(b\)|3304\(a\)\(1\)|8\(a\) direct award|'
                  r'brand[- ]name (only|justification)', re.I)
# "intends to award a MATOC / multiple contracts" with no named firm is NOT sole source - SOLE does not match it.
CANCEL = re.compile(r'\b(cancell?ed|withdrawn|no longer required|requirement has been removed)\b', re.I)
NOT_PROCUREMENT = re.compile(r'seed fund|webinar|industry day|workshop|outreach event|matchmaking|conference registration|'
                             r'available for licens|licensing opportunit|technology transfer|patent licens|crada', re.I)
ASK = re.compile(r'capabilit(y|ies) statement|respond|quote|proposal|\bbid\b', re.I)
RECOMP = re.compile(r'\b(incumbent|recompete|follow-on|bridge contract|extension of)\b', re.I)
SITE = re.compile(r'site visit|on[- ]site|at the (facility|installation|medical center|base)|badg', re.I)
PART = re.compile(r'^SPE[0-9A-Z]', re.I)
PART_TITLE = re.compile(r'^\d{2,4}--|\bNSN\b|\bP/N\b|part number', re.I)


def today_arg(a): return datetime.date.fromisoformat(a) if a else datetime.date.today()
def text_of(o): return (o.get('title') or '') + ' ' + (o.get('ai_summary') or '') + ' ' + (o.get('description_text') or '')[:5000]
def sid_of(o): return (o.get('source_id') or o.get('opp_key') or '').strip()


# --------------------------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------------------------
def _results(path):
    t = open(path, encoding='utf-8').read()
    if t.lstrip().startswith('['):
        try: t = ''.join(x.get('text', '') for x in json.loads(t))
        except Exception: pass
    if '{' not in t: return []
    try: return json.loads(t[t.index('{'):t.rindex('}') + 1]).get('results', [])
    except Exception: return []

def cmd_ingest(a):
    os.makedirs('hg', exist_ok=True)
    rows = {}
    if os.path.exists('hg/raw.jsonl'):
        for l in open('hg/raw.jsonl', encoding='utf-8'):
            r = json.loads(l); rows[sid_of(r)] = r
    n = 0
    for pat in a.files:
        for f in glob.glob(pat):
            for r in _results(f):
                n += 1; k = sid_of(r)
                if not k: continue
                old = rows.get(k)
                if not old or (r.get('posted_date') or '') >= (old.get('posted_date') or ''):   # newest version governs
                    rows[k] = r
    with open('hg/raw.jsonl', 'w', encoding='utf-8') as fh:
        for r in rows.values(): fh.write(json.dumps(r) + '\n')
    print(f'ingest: {n} result rows read, {len(rows)} unique solicitations in hg/raw.jsonl')


# --------------------------------------------------------------------------------------------
# gate (ingestion)
# --------------------------------------------------------------------------------------------
def ingest_gate(r, today):
    st = r.get('source_type')
    if st not in ('sam', 'sled'): return 'source'            # sled_forecast is never a bid
    typ = ((r.get('opp_type') or {}).get('description') or '').lower()
    if typ not in KEEP_TYPES: return 'type'
    due = (r.get('due_date') or '')[:10]
    if not due: return 'due'
    d = (datetime.date.fromisoformat(due) - today).days
    if d < 7 or d > 120: return 'due'
    if (r.get('posted_date') or '')[:10] < (today - datetime.timedelta(days=30)).isoformat(): return 'posted'
    if not (r.get('source_path') or '').startswith('http'): return 'nourl'   # `path` is HigherGov's page - never emailed
    if r.get('sole_source_flag'): return 'sole'
    t = text_of(r)
    if SOLE.search(t): return 'sole'
    if INGEST_BAD.search(t): return 'clearance/brand'
    if VEHICLE.search(t): return 'vehicle'
    if CANCEL.search(r.get('title') or ''): return 'cancelled'
    if len(r.get('ai_summary') or '') < 150 and len(r.get('description_text') or '') < 300: return 'thin'
    if st == 'sled' and re.search(r'\bgrant\b|nonprofit|non-profit', t, re.I): return 'grant'
    if (r.get('pop_country') or 'USA') not in ('USA', 'US', ''): return 'oconus'
    return None

def cmd_gate(a):
    today = today_arg(a.today); c = collections.Counter(); clean = []
    for l in open('hg/raw.jsonl', encoding='utf-8'):
        r = json.loads(l); g = ingest_gate(r, today); c[g or 'CLEAN'] += 1
        if not g: clean.append(r)
    json.dump(clean, open('hg/clean.json', 'w', encoding='utf-8'))
    print('gate:', dict(c.most_common()))


# --------------------------------------------------------------------------------------------
# match (v3, approved 2026-09-30)
# --------------------------------------------------------------------------------------------
def notice_gates(o, today):
    t = text_of(o)
    if o.get('sole_source_flag') or SOLE.search(t) or VEHICLE.search(t): return None
    if CANCEL.search(o.get('title') or ''): return None
    if NOT_PROCUREMENT.search(t) and not ASK.search(t): return None
    if re.search(r'available for licens|licensing opportunit|patent licens', t, re.I): return None
    typ = ((o.get('opp_type') or {}).get('description') or '').lower()
    try: days = (datetime.date.fromisoformat((o.get('due_date') or '')[:10]) - today).days
    except ValueError: return None
    need = 7 if any(k in typ for k in ('sources sought', 'special', 'presol')) else (10 if re.search(r'\brfq\b|quote|quotation', t, re.I) else 21)
    if days < need or days > 120: return None
    n = ((o.get('naics_code') or {}).get('naics_code') or '')[:6]
    if not n: return None
    sled = o.get('source_type') == 'sled'
    if n.startswith(REMOTE_PFX):
        onsite = sled or bool(re.search(r'on[- ]site (staff|support|personnel)|work(ed)? on[- ]site', t, re.I))
    else:
        onsite = sled or n.startswith(ONSITE_PFX) or bool(SITE.search(t))
    code = (o.get('set_aside') or {}).get('code') if isinstance(o.get('set_aside'), dict) else o.get('set_aside')
    sa = SA.get(code); low = t.lower()
    if not sa:   # set-aside codes are unreliable: BIE Buy Indian arrives as SBA, VA SDVOSB arrives blank
        for pat, lab in [(r'service[- ]disabled veteran|sdvosb', 'SDVOSB'), (r'\b8\(a\)', '8(a)'), (r'hubzone', 'HUBZone'),
                         (r'women[- ]owned|wosb', 'WOSB'), (r'buy indian|isbee|indian small business', 'ISBEE')]:
            if re.search(pat, low) and re.search(r'set[- ]aside|must be|restricted|eligible', low): sa = lab + '?'; break
    if sa == 'SB' and re.search(r'buy indian|isbee|indian small business economic', low): sa = 'ISBEE?'
    try: val = float(o.get('val_est_high') or o.get('val_est_low') or 0) or None
    except (TypeError, ValueError): val = None
    return dict(n=n, days=days, onsite=onsite, sa=sa, val=val, sled=sled, typ=typ, tok=toks(t), ttok=toks(o.get('title') or ''),
                recomp=bool(RECOMP.search(t)), early=any(k in typ for k in ('sources sought', 'presol', 'special')),
                part=bool(PART.match(sid_of(o)) or PART_TITLE.search(o.get('title') or '')))

def load_contacts(path, today, suppression=()):
    supp = [s.strip().lower() for s in suppression if s.strip()]
    out = []
    for r in json.load(open(path, encoding='utf-8'))['records']:
        v = r['cellValuesByFieldId']
        st = v.get(C['status']); st = st.get('name') if isinstance(st, dict) else st
        if st not in (None, 'Active'): continue
        if (v.get(C['next_eligible']) or '') > today.isoformat(): continue
        email = (v.get(C['email']) or '').lower(); dom = email.split('@')[-1]; co = (v.get(C['company']) or '').lower()
        if not email or re.search(r'\.(gov|mil|edu|org)$', email): continue
        if any(s == email or s == dom or (len(s) > 4 and s in co) for s in supp): continue     # email AND domain AND company
        raw = v.get(C['naics']) or ''
        rev = v.get(C['revenue'])
        out.append(dict(id=r['id'], email=email, co=v.get(C['company']), name=v.get(C['name']),
                        st=(v.get(C['state']) or '').upper() or None,
                        rev=None if (not rev or rev in PLACEHOLDER_REVENUE) else float(rev),
                        certs=(v.get(C['certs']) or '').upper().replace('(', '').replace(')', ''),
                        nl={m.group(1): (m.group(2) or 'Y') for m in re.finditer(r'(\d{6})([YNE])?', raw)},
                        cap=toks(v.get(C['cap'])), captext=v.get(C['cap']) or '', nametok=toks(v.get(C['company'])),
                        ntitle=toks(' '.join(re.findall(r'\d{6}[YNE]?:([^,]+)', raw)))))
    return out

def match(contacts, opps, today, per_notice=5, threshold=70):
    notices = [(o, g) for o in opps for g in [notice_gates(o, today)] if g]
    cdf = collections.Counter(t for c in contacts for t in c['cap'])
    odf = collections.Counter(t for _, g in notices for t in g['tok'])
    NC, NO = max(len(contacts), 1), max(len(notices), 1)
    def w(t):
        if cdf[t] / NC > 0.20 or odf[t] / NO > 0.05: return 0.0
        return 0.5 * math.log((NC + 1) / (cdf[t] + 1)) + 0.5 * math.log((NO + 1) / (odf[t] + 1))
    byn = collections.defaultdict(list)
    for o, g in notices: byn[g['n']].append((o, g))
    pairs = []
    for c in contacts:
        k = len(c['nl'])
        for n, flag in c['nl'].items():
            if flag == 'N': continue                              # not small under this NAICS
            for o, g in byn.get(n, []):
                pst = (o.get('pop_state') or '').upper()
                if g['onsite'] and (not c['st'] or c['st'] != pst): continue           # location gate
                sa, adv = g['sa'], 0
                if sa and sa.endswith('?'):
                    base = sa[:-1]
                    if base == 'ISBEE' or not any(x in c['certs'] for x in CERT.get(base, ['@@'])): continue
                    sa = base
                if sa == 'SB': adv = 8
                elif sa in CERT:
                    if not any(x in c['certs'] for x in CERT[sa]): continue
                    adv = 20
                elif sa: continue
                if g['sled'] and sa and sa != 'SB': continue      # state programmes are not federal certs
                band = 9
                if g['val']:
                    yrs = 1 if n.startswith('23') else (5 if g['val'] > 1e6 else 1)
                    if c['rev']:
                        r = g['val'] / yrs / c['rev']
                        if r > (1.0 if n.startswith('23') else 2.0) or r < 0.01: continue
                        band = 15 if 0.05 <= r <= 0.75 else 9
                    elif g['val'] > 5e6: continue
                terms = sorted((t for t in c['cap'] & g['tok'] if w(t) > 0 and (t not in c['ntitle'] or t in g['ttok'])), key=lambda t: -w(t))
                in_title = any(t in g['ttok'] for t in terms)
                custom = [t for t in terms if t not in c['ntitle']]
                name_hit = [t for t in c['nametok'] & g['tok'] if t not in NAME_STOP and len(t) > 3 and odf[t] / NO <= 0.05]
                if g['part'] and not in_title: continue            # part buys: the firm must name the part
                if k <= 5:                                          # focused firm
                    if n in CATCH_ALL and not (len(terms) >= 2 and in_title) and not name_hit: continue
                    fit = 25 if (terms or name_hit) else 20
                else:                                               # broad registrant
                    if len(terms) < 2 or not in_title or not (custom or name_hit): continue
                    fit = min(25, 10 + 5 * len(terms) + (5 if name_hit else 0))
                score = fit + adv + band + (10 if g['early'] else 5) + (10 if g['days'] >= 21 else 6) + (0 if g['recomp'] else 5) + (12 if g['val'] else 8)
                pairs.append(dict(score=score, contact_id=c['id'], email=c['email'], company=c['co'], contact=c['name'], state=c['st'],
                                  certs=c['certs'], capability=c['captext'][:400], source_id=sid_of(o), title=o.get('title'),
                                  agency=(o.get('agency') or {}).get('agency_name'), pop_state=pst, pop_city=o.get('pop_city'),
                                  naics=n, set_aside=sa or 'open', value_low=o.get('val_est_low'), value_high=o.get('val_est_high'),
                                  due=(o.get('due_date') or '')[:10], days_left=g['days'], notice_type=g['typ'], onsite=g['onsite'],
                                  focused=k <= 5, terms=terms[:6], url=o.get('source_path'), summary=(o.get('ai_summary') or '')[:1500]))
    pairs.sort(key=lambda p: -p['score'])
    picks, seen, per = [], set(), collections.Counter()
    for p in pairs:
        key = (p['company'] or '').lower()
        if p['score'] < threshold or p['email'] in seen or key in seen or per[p['source_id']] >= per_notice: continue
        seen.update((p['email'], key)); per[p['source_id']] += 1; picks.append(p)
    return notices, pairs, picks

def cmd_match(a):
    today = today_arg(a.today)
    supp = open(a.suppression, encoding='utf-8').read().splitlines() if a.suppression else []
    contacts = load_contacts(a.contacts, today, supp)
    notices, pairs, picks = match(contacts, json.load(open(a.opps, encoding='utf-8')), today)
    json.dump(picks, open(a.out, 'w', encoding='utf-8'), indent=1)
    print(f'match: contacts {len(contacts)} (with State {sum(1 for c in contacts if c["st"])}) | notices past gates {len(notices)} | '
          f'pairs {len(pairs)} | picks {len(picks)} across {len({p["source_id"] for p in picks})} notices | on-site {sum(p["onsite"] for p in picks)}')


# --------------------------------------------------------------------------------------------
# review sheet
# --------------------------------------------------------------------------------------------
def cmd_review(a):
    picks = json.load(open(a.picks, encoding='utf-8'))[:a.n]
    with open(a.out, 'w', encoding='utf-8') as fh:
        for i, p in enumerate(picks):
            fh.write(f"[{i}] {p['score']}  {p['company']} ({p['state']}, {p['certs'] or 'no certs'})  <{p['email']}>\n"
                     f"   FIRM : {p['capability']}\n"
                     f"   OPP  : {p['source_id']} | {p['title']} | {p['agency']} | {p['pop_city']} {p['pop_state']} | {p['notice_type']} "
                     f"| due {p['due']} ({p['days_left']}d) | SA {p['set_aside']} | ${p['value_low']}-{p['value_high']} | {'ON-SITE' if p['onsite'] else 'remote'}\n"
                     f"   TERMS: {', '.join(p['terms']) or '(exact NAICS, focused firm)'}\n   SUM  : {p['summary'][:600]}\n\n")
    print(f'review: {len(picks)} picks written to {a.out} - read every one; drop anything a capture manager would reject')


# --------------------------------------------------------------------------------------------
# draft - assemble emails from facts the LLM wrote. The LLM never hand-assembles HTML.
# facts.json: [{email, subject, link_text, notice_phrase, scope, why, catch, footprint}]
#   notice_phrase e.g. "a presolicitation due November 18"; footprint e.g. "roofing"
# --------------------------------------------------------------------------------------------
CLOSES = ['I will hold time Thursday unless you tell me otherwise.', 'Worth a brief call this week to decide whether we respond?',
          'Short call this week?', 'If {fp} is not your footprint, tell me what is and I will aim the next one properly instead of guessing.']

def signature(acct):
    """acct: the machine's Sending Accounts row fields, read fresh every run. Halt if Operator/Title blank."""
    op, title = (acct.get('Operator') or '').strip(), (acct.get('Operator Title') or '').strip()
    if not op or not title: sys.exit('HALT: Operator or Operator Title blank on the account row')
    lines = [f'{op} - {title}', 'Top Notch Acquisitions, LLC' + (f" - M. {acct['Operator Mobile']}" if acct.get('Operator Mobile') else '')]
    if acct.get('Operator Direct Line'): lines.append(f"D. {acct['Operator Direct Line']}")
    lines.append('topnotchacquisitions.com/govcon')
    return '<p>Respectfully,<br>' + '<br>'.join(lines) + '</p>'

def cmd_draft(a):
    picks = {p['email']: p for p in json.load(open(a.picks, encoding='utf-8'))}
    facts = json.load(open(a.facts, encoding='utf-8'))
    sig = signature(json.load(open(a.account, encoding='utf-8')))
    out = []
    for f in facts:
        p = picks[f['email']]
        first = (p['contact'] or '').split()[0] if p.get('contact') else None
        if first and (len(first) < 2 or not first.isalpha()): first = None
        variant = int(hashlib.md5(f['email'].encode()).hexdigest(), 16) % 4
        paras = [f'{first},' if first else 'Hello,',
                 f"{p['agency']} posted <a href=\"{p['url']}\">{f['link_text']}</a>, {f['notice_phrase']}. {f['scope']}",
                 'Why this is yours: ' + f['why'], 'The catch: ' + f['catch'], CLOSES[variant].format(fp=f.get('footprint') or 'this work')]
        body = ' '.join(re.sub('<[^>]+>', ' ', x) for x in paras)
        if re.search(r'you are in|near you|miles from|your state', body, re.I): sys.exit(f"HALT: location mentioned in {f['email']}")
        out.append(dict(email=f['email'], contact_id=p['contact_id'], company=p['company'], source_id=p['source_id'],
                        opp=f"{p['source_id']} - {f['link_text']}", score=p['score'], subject=f['subject'], variant=variant,
                        html=''.join(f'<p>{x}</p>' for x in paras) + sig, text='\n'.join(re.sub('<[^>]+>', '', x) for x in paras),
                        words=len(body.split())))
    json.dump(out, open(a.out, 'w', encoding='utf-8'), indent=1)
    print(f'draft: {len(out)} emails assembled, words {min(o["words"] for o in out)}-{max(o["words"] for o in out)}' if out else 'draft: 0')


# --------------------------------------------------------------------------------------------
# schedule - 10:00 local or now, then 4-9 minutes apart; on a re-run continue from the latest queued
# --------------------------------------------------------------------------------------------
def cmd_schedule(a):
    d = json.load(open(a.drafts, encoding='utf-8'))
    rnd = random.Random(a.start)
    gap = lambda: datetime.timedelta(seconds=rnd.randint(240, 540))       # 4-9 minutes, never compressed
    t = datetime.datetime.fromisoformat(a.start.replace('Z', '+00:00'))  # 10:00 local, or now if later
    if a.after:                                                           # re-run: continue after today's latest
        t = max(t, datetime.datetime.fromisoformat(a.after.replace('Z', '+00:00')) + gap())
    for i, x in enumerate(d):
        if i: t += gap()
        x['send_after'] = t.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    json.dump(d, open(a.drafts, 'w', encoding='utf-8'), indent=1)
    print(f"schedule: {len(d)} emails, {d[0]['send_after']} to {d[-1]['send_after']}" if d else 'schedule: 0')


# --------------------------------------------------------------------------------------------
# payloads - Airtable batches of 10
# --------------------------------------------------------------------------------------------
def cmd_payloads(a):
    d = json.load(open(a.drafts, encoding='utf-8')); day = a.today or datetime.date.today().isoformat()
    cool = (datetime.date.fromisoformat(day) + datetime.timedelta(days=a.cooldown)).isoformat()
    os.makedirs('payloads', exist_ok=True)
    ob = [{'fields': {OUTBOX['key']: f"{x['email']}|{day}", OUTBOX['to']: x['email'], OUTBOX['subject']: x['subject'],
                      OUTBOX['html']: x['html'], OUTBOX['machine']: a.machine, OUTBOX['send_after']: x['send_after'],
                      OUTBOX['status']: 'Queued', OUTBOX['contact_email']: x['email']}} for x in d]
    se = [{'fields': {SENDS['key']: f"{x['email']}|{day}", SENDS['email']: x['email'], SENDS['company']: x['company'],
                      SENDS['machine']: a.machine, SENDS['address']: a.address, SENDS['sent_at']: x['send_after'], SENDS['opp']: x['opp'],
                      SENDS['score']: x['score'], SENDS['subject']: x['subject'], SENDS['snapshot']: x['text'], SENDS['variant']: x['variant']}} for x in d]
    ct = [{'id': x['contact_id'], 'fields': {C['last_touched']: day, C['next_eligible']: cool, C['times_touched']: 1,
                                             C['claimed_by']: None, C['claimed_at']: None}} for x in d]
    n = 0
    for name, rows in (('outbox', ob), ('sends', se), ('contacts', ct)):
        for i in range(0, len(rows), 10):
            json.dump(rows[i:i + 10], open(f'payloads/{name}_{i // 10:02d}.json', 'w', encoding='utf-8')); n += 1
    print(f'payloads: {len(d)} emails -> {n} files in payloads/ (outbox, sends, contacts; 10 per file). '
          f'Times Touched is written as 1 - add the prior value if the row had one.')


# --------------------------------------------------------------------------------------------
# backfill - HigherGov search_awardees lookups -> State for contacts missing it
# lookup TSV lines: id \t clean|legal|state|city|poc|uei|country \t ... (up to 3 candidates)
# items JSON: [{i, co, contacts:[{id, contact, email}]}]
# --------------------------------------------------------------------------------------------
SUF = r'\b(llc|l l c|inc|incorporated|corp|corporation|co|company|ltd|limited|lp|llp|pllc|pc|the|dba)\b'
def _norm(s):
    s = (s or '').lower().replace('&', ' and '); s = re.sub(r'[^a-z0-9 ]', ' ', s); return ' '.join(re.sub(SUF, ' ', s).split())
def _last(n):
    t = [x for x in re.sub(r'[^a-z ]', ' ', (n or '').lower()).split() if x not in ('jr', 'sr', 'ii', 'iii', 'iv', 'mr', 'mrs', 'ms', 'dr')]
    return t[-1] if t else ''

def cmd_backfill(a):
    items = {x['i']: x for x in json.load(open(a.items, encoding='utf-8'))}
    rows = {}
    for f in glob.glob(a.lookups):
        for line in open(f, encoding='utf-8'):
            p = line.rstrip('\n').split('\t')
            if p and p[0].strip().isdigit(): rows[int(p[0])] = p[1:]
    out, tiers = [], collections.Counter()
    for i, it in items.items():
        q = _norm(it['co']); cands = []
        for cell in rows.get(i, []):
            f = cell.split('|')
            if len(f) >= 7 and q and q in (_norm(f[0]), _norm(f[1])):
                cands.append(dict(st=f[2].strip().upper(), poc=f[4]))
        good = [c for c in cands if c['st'] in US]
        for ct in it['contacts']:
            cl = _last(ct.get('contact'))
            poc = [c for c in good if cl and len(cl) > 2 and _last(c['poc']) == cl]
            if poc: tier, pick = 'HIGH', poc[0]                      # SAM point-of-contact is our contact
            elif good and len({c['st'] for c in good}) == 1: tier, pick = 'MED', good[0]   # exact name, one state
            else: tier, pick = ('AMBIGUOUS' if good else 'NO_MATCH'), None
            tiers[tier] += 1
            if pick: out.append({'id': ct['id'], 'fields': {C['state']: pick['st']}})
    json.dump(out, open(a.out, 'w', encoding='utf-8'))
    print('backfill:', dict(tiers), f'-> {len(out)} State updates in {a.out} (write HIGH+MED only; never guess)')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = ap.add_subparsers(dest='cmd', required=True)
    x = s.add_parser('ingest'); x.add_argument('files', nargs='+'); x.set_defaults(f=cmd_ingest)
    x = s.add_parser('gate'); x.add_argument('--today'); x.set_defaults(f=cmd_gate)
    x = s.add_parser('match'); x.add_argument('contacts'); x.add_argument('opps'); x.add_argument('out'); x.add_argument('--today')
    x.add_argument('--suppression'); x.set_defaults(f=cmd_match)
    x = s.add_parser('review'); x.add_argument('picks'); x.add_argument('out'); x.add_argument('--n', type=int, default=80); x.set_defaults(f=cmd_review)
    x = s.add_parser('draft'); x.add_argument('picks'); x.add_argument('facts'); x.add_argument('account'); x.add_argument('out'); x.set_defaults(f=cmd_draft)
    x = s.add_parser('schedule'); x.add_argument('drafts'); x.add_argument('--start', required=True); x.add_argument('--after')
    x.set_defaults(f=cmd_schedule)
    x = s.add_parser('payloads'); x.add_argument('drafts'); x.add_argument('--machine', required=True); x.add_argument('--address', required=True)
    x.add_argument('--cooldown', type=int, default=30); x.add_argument('--today'); x.set_defaults(f=cmd_payloads)
    x = s.add_parser('backfill'); x.add_argument('items'); x.add_argument('lookups'); x.add_argument('out'); x.set_defaults(f=cmd_backfill)
    a = ap.parse_args(); a.f(a)

if __name__ == '__main__':
    main()
