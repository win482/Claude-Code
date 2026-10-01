---
name: "tna-10x-sender-m2"
description: "Run one day of the TNA 10X outreach campaign as a sending machine. Use when the daily scheduled task fires, or when Daniel says \"run the 10X send\", \"run the daily send\", or \"run the campaign\". Searches HigherGov for live opportunities, matches them opportunity-first against the whole eligible contact list (state-gated for on-site work), claims only the firms that genuinely fit, re-checks each notice live, queues each email to the Airtable Email Outbox, and logs everything. Safe to re-run the same day - it tops up rather than duplicating."
---

# TNA 10X Sender

## MACHINE IDENTITY - the only block that changes between machines

```
MACHINE ID        M2
SENDING ADDRESS   win@topnotchacquisitions.com
ACCOUNT ROW       recDD4KwhfsJ4Lx7J
POLLER            Make scenario 6129832
```

Everything else in this file is identical on every machine. To stand up M3-M8,
copy this file, change the four lines above, and create the matching Sending
Accounts row. **Never change anything else per machine.** The operator name,
title and phone numbers are read from the Airtable row at runtime and never
appear in this file.

---

## RUNNING IN THE CLOUD (from 2026-10-01)

Both machines run as **cloud routines** - no one's computer has to be on. The
Make poller was always cloud-side; now the daily run is too.

**Setup every run (the sandbox starts empty):**

1. From Google Drive folder **TNA 10X Machine Package**, download this skill
   file and `tna10x_pipeline.py` into the working directory (`/home/claude`).
   If either is missing, halt at preflight and write the Run Log row - never
   run from memory of the rules.
2. Every command in the pipeline prints ONE line of counts. Data lives on disk;
   never paste tool output or rows into your reasoning.

**The pipeline does every deterministic step. You do the tool calls, the review
and the writing:**

| Step | Who | Command / action |
|---|---|---|
| Discovery | subagents | HigherGov `search_opportunities`, paged, 30-day window, federal + state/local, NAICS axis for every code held by eligible contacts + keyword clusters. Each subagent saves raw results to `hg/in/*.json` and returns one line of counts. |
| Ingest | script | `python tna10x_pipeline.py ingest "hg/in/*"` (keeps the NEWEST version of each solicitation) |
| Ingestion gates | script | `python tna10x_pipeline.py gate` |
| Contacts | you | Airtable `list_records_for_table` on Contacts, all pages, saved to `contacts.json` as `{"records":[...]}`; Suppression saved one value per line to `supp.txt` |
| Match | script | `python tna10x_pipeline.py match contacts.json hg/clean.json picks.json --suppression supp.txt` |
| Review | you | `python tna10x_pipeline.py review picks.json review.txt --n 80`, then read every entry (Phase 4 Step 5) |
| Live check | you | `search_opportunities source_id=<sol#>` per surviving notice, newest version (Phase 4 gate 6) |
| Claim | you | write `Claimed By`/`Claimed At` for the survivors, wait 60 s, re-read, keep only rows you won |
| Facts | you | write `facts.json`: `[{email, subject, link_text, notice_phrase, scope, why, catch, footprint}]` under Phase 5's rules |
| Draft | script | `python tna10x_pipeline.py draft picks.json facts.json account.json drafts.json` (`account.json` = this machine's Sending Accounts row: Operator, Operator Title, Operator Mobile, Operator Direct Line - read fresh; the script halts if Operator or Title is blank) |
| Schedule | script | `python tna10x_pipeline.py schedule drafts.json --start <10:00 local or now, ISO> [--after <latest Send After already queued today>]` |
| Payloads | script | `python tna10x_pipeline.py payloads drafts.json --machine <ID> --address <SENDING ADDRESS>` |
| Write | you | create Outbox rows from `payloads/outbox_*.json`, Sends from `payloads/sends_*.json`, update Contacts from `payloads/contacts_*.json` - verbatim, 10 per call |
| Log + drain | you | Phase 7 |

**State backfill** (only when on-site trades have blank `State`): look each firm
up with `search_awardees keyword="<name>" page_size=3` (one firm per call - batched
OR queries return garbage), write lookups as TSV, then
`python tna10x_pipeline.py backfill items.json "lookups/*.tsv" backfill_state.json`
and write only its output. HigherGov has **no revenue data**; never fill revenue
from federal obligations.

## THE TRANSPORT

**You do not send email. You queue it.** Write one row per email to the
**Email Outbox** (`tbl64P2n8MQGbieNr`). The machine's Make scenario polls every
15 minutes, sends anything `Queued` whose `Send After` has passed, and marks it
`Sent`.

- **You have no outbound HTTP.** `curl` returns 000. No webhooks, no SMTP. It
  fails silently, which is worse than failing loudly.
- **The Gmail connector is banned for sending** - it prompts for human approval
  on every send, which makes unattended running impossible. Reading the mailbox
  through it is fine and expected.

## Airtable - base `appa68g31bjkD7QfA`

```
TBL_CONTACTS     tblKQLQyCdJKL4TiF
TBL_ACCOUNTS     tblpNuS5QL6b5N3Ee
TBL_OUTBOX       tbl64P2n8MQGbieNr     <- the transport
TBL_POOL         tblHGZoKzcm0x1XjE     <- same-day scratchpad, see Phase 3
TBL_SENDS        tblpmu44EeYM2RV2H
TBL_REPLIES      tbljkmxJQXtUyo5rc
TBL_MEETINGS     tbloOfBpAtGdYTXq9
TBL_CONVERSIONS  tblSHoH4BErjzPBH8
TBL_SUPPRESSION  tblw3yZ4ZNQN19nu7
TBL_SEGMENTS     tblHTyJNUOIYdj1bZ
TBL_RUNLOG       tbl9shIY4wX7nCsVT
TBL_CONTROL      tbl2p9nOIQr499iJU
ESCALATION       daniel@topnotchacquisitions.com
```

## Five things about how this works

**The cap governs SENDS, not claims.** 50 is emails to queue, not contacts to
look at. Match rates run 20-40%, so filling 50 means claiming 150-250 contacts
across waves. Claiming exactly 50 and sending whatever matched is the bug that
produced 7 emails on the first live run.

**The cap is a TOTAL FOR THE DAY.** Re-running is safe: count what today already
has and top up. Never add a second cap.

**Match opportunity-first, across the WHOLE eligible list.** Discover what is
live today, then run every clean opportunity against every `Available Now`
contact and claim only the firms that actually match. Changed 2026-09-30: the old
contact-first waves inspected at most ~600 contacts in queue order and found 11
matches, while the same 1,292 clean opportunities run against all 6,774
eligible contacts found 1,764 candidates. The supply was there; the method was
looking at the wrong 600 firms. See Phase 2B.

**Discovery is same-day and live.** You never email from a stale snapshot:
every notice is re-checked live on HigherGov immediately before its email is
queued (Phase 4, gate 6).

**No privileged machine.** Every machine claims its own contacts and runs its
own discovery. The Opportunity Pool is a within-day cache so a second machine
does not re-pay for a segment the first already searched this morning.

**Ownership and the signature follow the mailbox.** Every email is signed by the
operator on this machine's own account row, and every reply, meeting and close
is owned by that person.

## THE RULE THAT OVERRIDES EVERYTHING ELSE

**If no opportunity genuinely fits a contact, do not email that contact.**
Release the claim, count the skip, move on. Never stretch a match, never lower
the threshold, never queue a generic "here is what is new this week" email. The
moment this becomes a newsletter it stops working and starts costing the domain
- every mailbox is on `topnotchacquisitions.com`, so there is no firewall
between this campaign and every client and teaming thread the company has.

**Fill the cap by looking at more contacts, never by accepting weaker matches.**
If it cannot be filled honestly, report the shortfall and name the constraint.

---

# PHASE 0 - PREFLIGHT

Read `TBL_CONTROL` and this machine's `TBL_ACCOUNTS` row. **Halt and email
ESCALATION if:**

- `Campaign Active` unchecked - this machine's `Active` unchecked - `Health` not Green
- **`Outbox Sender Scenario` blank** - no transport; never improvise one
- **`Operator` or `Operator Title` blank** - never fall back to someone else's name
- **any Outbox row for this machine still `Queued` from a PREVIOUS day** - the
  poller is down; queueing more builds a silent backlog
- `Bounces 7d` over 3% of 7-day sends - system rate over 5% - `Complaints 7d` at 3+ (halt 14 days)
- Airtable unreachable - halt; never queue without logging

**Confirm the machine's Make scenario is `isActive: true` and `isinvalid: false`.**
On 2 September one silently deactivated after erroring on empty polls and a day's
queue nearly went undelivered. Reactivate and note it.

**When you halt, still write a `TBL_RUNLOG` row** with Status `Halted - preflight`
and the reason. A halt with no log looks identical to a dead machine, and that
ambiguity cost this campaign several days in September.

Read into memory: `Daily Cap Today`, `Match Threshold`, `Max Opportunities Per
Email`, `Cooldown Days`, `Claim Expiry Hours`, `Max Claim Waves`,
`Discovery Lookback Days`, and the four signature fields. **Ignore `Max GovTribe
Searches Per Run`** - a dead field from the GovTribe era. HigherGov calls are
free and there is no discovery-call ceiling any more. Weekends multiply the
cap by `Weekend Cap Multiplier`.

---

# PHASE 1 - INBOX SCAN

| What it is | What you do |
|---|---|
| **Hard bounce** - 550, 5.1.1, mailer-daemon, "address not found" | `Status` = **Bounced**. Add to `TBL_SUPPRESSION` (Email, Hard bounce, Permanent). Increment `Bounces 7d`. **This is how bounces leave the list.** |
| **Soft bounce / mailbox full** | Push `Next Eligible` out 7 days. Two consecutive = treat as hard. |
| **Auto-reply / out of office** | No status change, no reply. Push `Next Eligible` out 3 days. |
| **Unsubscribe** - "no thanks", "remove me", "stop", "do not contact" | `Status` = **Unsubscribed**, add to Suppression permanently, and **queue** a one-line confirmation with `Send After` = now. Within 24 hours. |
| **"Interested" or any other human reply** | `Status` = **Replied**. `TBL_REPLIES` row with a classification and `Owner` = this machine's Operator; same on the contact. **Email ESCALATION the same day if Interested.** Never queue this contact again. |

Classify as exactly one of: Interested - Wants info - Not now - Not a fit -
Referral - Unsubscribe - Auto-reply - Bounce.

**These mailboxes carry far more non-campaign traffic than campaign traffic.**
Client opportunity replies, teaming approvals, internal TNA mail, vendor notices
and Outlook Bridge forwards all land here. **Before treating anything as a
campaign event, check the address AND the domain against `TBL_CONTACTS`.** A
bounce on a client mailing is not a campaign bounce and must not be counted
against the gate or written to Suppression - this has come up nearly every day
and mis-counting it would halt a healthy machine.

**Never answer a human reply yourself, and never send meeting times.** The email
promised a person would.

---

# PHASE 2 - COUNT TODAY, THEN MATCH OPPORTUNITY-FIRST

## 2A. Establish the starting point

1. Count `TBL_OUTBOX` rows where `Machine ID` = this machine and the
   `Idempotency Key` ends in today's date, **any status**.
2. Add `TBL_SENDS` rows for today not already counted.
3. `queued` = that total. `target` = `Daily Cap Today` (x weekend multiplier).
4. **If `queued >= target`, the day is done.** Log it, check the drain, stop.
5. Otherwise carry on with `queued` at its real value - the run **tops up**.

**Never treat `queued` as zero on a fresh run.** Two runs in one day produce one
cap between them, not two.

## 2B. Match opportunity-first, then claim only the winners

**This replaced contact-first claim waves on 2026-09-30.** Claiming a batch of
contacts and then asking "what fits these?" caps the day at whatever fits the
first few hundred firms in queue order. On 2026-09-30 that was 11 of 50. Running
the same opportunities against the whole eligible list found 1,764 candidates.

1. **Load the whole eligible list** - every contact with `Available Now` = YES,
   `Status` Active, not claimed by another machine, not suppressed on email or
   domain. Read it in pages; it is ~7,000 rows and that is fine. Record how many
   have a `State` - it decides how much on-site work can be matched (gate 4).
2. **Discover** (Phase 3) for every segment represented in that list, both axes,
   paged out, federal AND state/local. Apply every ingestion filter.
3. **Match** (Phase 4) every clean opportunity against every eligible contact:
   notice gates, pair gates, fit evidence, the 97-point score, threshold 70.
   Nothing about the method loosens a gate - it only widens who is looked at.
4. **Pick** per Phase 4 Step 4 - one notice per firm and per company, at most 5
   firms per notice, until you hold `(target - queued)` picks plus a 20% reserve.
5. **Human-grade review of every pick** before anything is claimed: read the
   scope, the notice text and the contact's capability line side by side. Drop
   anything that fails the sanity check, a hidden set-aside, the on-site state
   rule or the live-status check. Pull the next pair from the reserve.
6. **Claim only the picks.** Write `Claimed By` = this machine, `Claimed At` =
   now, in tens. **Wait 60 seconds**, re-read, keep only rows still claimed by
   this machine and count the rest as `Claims Lost To Other Machines`.
7. **Draft and queue** (Phases 5-6) the claims you won, stopping when `queued`
   reaches `target`.
8. **Short after losses or drops?** Repeat steps 4-7 from where the list left
   off. Each repeat is one claim round; `Max Claim Waves` now caps claim
   rounds, not contacts inspected. Contacts inspected is always the whole list.

**If the whole list, fully matched, still cannot fill the cap, stop.** That is a
genuine shortfall of opportunities that fit - report it and name it. Never go
back and loosen anything to close the gap.

A claim older than `Claim Expiry Hours` (6) is dead, so a crashed run frees its
own rows.

---

# PHASE 3 - DISCOVERY

## The design rule, and why it is not negotiable

**Search for the contacts you are about to email, today, and throw the results
away at the end of the day.**

Do NOT build a standing library of opportunities. Do NOT sweep all 28 segments.
Do NOT carry rows over to tomorrow.

The reason is arithmetic. At 50-100 emails a day against roughly 9,000 available
contacts, any individual contact comes round every 90-180 days. An opportunity
found today is dead long before the list gets to contact 5,000. A large
pre-built pool therefore does not help the contacts it was built for - it only
ages. Worse, it tempts a run into matching a firm against a notice that closed a
week ago, which is the single most credibility-destroying thing this campaign
can do.

So: look up what is live right now for the segments your eligible contacts are
in, match it against the whole eligible list, claim the firms that fit, fill as
close to the cap as the day allows.

**The Opportunity Pool is a same-day scratchpad.** Its only purpose is so that
when M2 runs at 10:20 it does not re-pay for a segment M1 already searched at
10:01. Check `Last Swept` = **today**; anything older is worthless, not a cache
hit. Expire every row at end of day.

## Discovery runs on HIGHERGOV

**The tool is `search_opportunities`, on the HigherGov MCP server.** Its other
tools - `search_awardees`, `get_awardee_detail`, `search_federal_contracts` -
are for enrichment and research, not for the daily run. If `search_opportunities`
is not available, the HigherGov connector is not attached: halt at preflight and
say so. Do not fall back to anything else.

The GovTribe MCP is frozen for credits and every GovTribe tool returns
`ACCOUNT_USAGE_FROZEN_EXCEPTION`. **Never call `Search_Federal_Contract_Opportunities`,
`Search_State_And_Local_Contract_Opportunities`, `Search_Vendors` or any other
GovTribe tool.** HigherGov is the source of record. It is included in the paid
plan, so calls are not metered the way GovTribe credits were - be efficient, not
frightened.

## What HigherGov actually honours

Tested 2026-09-22. Everything else you pass is **silently ignored** - the call
returns 200 with an unfiltered result set, which is how a whole run can quietly
match against garbage.

| Parameter | Honoured |
|---|---|
| `naics_code` | YES - one code per call |
| `keyword` | YES - free text, `"quoted phrase"`, `or` |
| `naics_code` + `keyword` together | YES - they AND |
| `posted_date`, `captured_date` | YES - single date, not a range |
| `page_size` | YES - use **100** |
| `opportunity_type` | YES - `federal_contract`, `state_local` |
| `active_opportunity` | YES |
| `psc_code`, `set_aside`, `pop_state`, `state`, any `due_date_*` | **NO - ignored** |

## The two search axes

For each segment represented among **today's eligible contacts** (Phase 2B
step 1) - and no others:

1. **NAICS axis.** One call per code in that segment's `NAICS Codes`.
2. **Keyword axis.** One call using that segment's `Keyword Cluster` field,
   joined with `or`. This field exists on `TBL_SEGMENTS` and is the difference
   between finding an opportunity and missing it: a spot check found the keyword
   axis surfaced 28% more usable opportunities than NAICS alone, because a
   Contracting Officer's choice of NAICS code is close to arbitrary.

## ONE CALL IS NOT ONE SEARCH - YOU MUST PAGE

**This is the single biggest defect in the system and it has been silently
throwing away 90 to 95 percent of every search.**

A keyword call typically matches **1,500 to 7,000 opportunities** and returns
**the newest 100**. On 2026-09-30 the whole run queued one email out of a cap of
fifty, and the log named the reason: *"Each HigherGov keyword call returns only
the newest 100 of 1,500-7,000 matches (posted 09-29/09-30 only), so the per-day
pool is thin."* The machine was not short of opportunities. It was looking at
the first two percent of them.

**Read the result's total-match count on every call.** If it exceeds what came
back, you have not finished that search.

**Page until you have the window or you hit the budget**, whichever comes first:

1. **Use the API's page parameter** if the tool exposes one. Request page 2, 3,
   4 and so on with the same filters.
2. **If it does not, slice by date.** `posted_date` is honoured as a single
   date, so repeat the identical call once per day across the posted window,
   walking backwards from today. Each day-slice returns its own newest 100,
   which for a single day is usually the whole day. This always works, uses only
   parameters known to be honoured, and is the fallback to reach for rather than
   accepting a truncated result.

**Twenty pages of one good keyword beats one page of twenty keywords.** Depth on
the segments your eligible contacts actually belong to is worth more than breadth
across segments nobody on today's list is in.

**Calls are included in the paid plan.** Be efficient, not frightened. The
failure mode that has actually cost this campaign is under-searching, every
time; there has never been an incident caused by too many calls.

## The posted window

**Search back 30 days, not one.** Match on `posted_date` within 30 days and let
the due-date filter do the work of deciding what is still live. A notice posted
three weeks ago with sixty days to run is a better opportunity than one posted
this morning with nine - it has been through its questions period and the scope
is settled.

Discovery scoped to today and yesterday is what made every day start from zero.

## State and local

**Mandatory, not optional.** Every run gets a state pass as well as a federal
one.

```
opportunity_type = "state_local"
keyword          = <that segment's keyword cluster, joined with or>
page_size        = 100      <- then PAGE, as above
```

**Do NOT put the state name in the keyword.** This was the instruction here
until 2026-09-30 and it does not work: HigherGov treats the keyword string as
**OR at the top level and ignores parentheses**, so `"Virginia" roofing or
masonry` returns everything in Virginia *plus* every roofing and masonry notice
in the country. Every state pass run under that rule returned zero usable
matches - the 09-30 log records `VA/FL/MD pop_state 0 usable matches`. It made
the search worse than not scoping it at all.

**Filter by `pop_state` client-side instead.** Search the capability cluster
alone, page it, then keep only rows whose place of performance equals the
contact's two-letter `State`. There is no server-side state filter and there
never was; the client-side check was always doing the real work.

**Contacts with a blank `State` get no state work at all.** About half the list
is in that position, and on 2026-09-28 all 600 contacts inspected had a blank
State, so no state opportunity could reach any of them. That is correct
behaviour under gate 4 - a firm with no verified home state must never be told a
state job is theirs - but it means **the state pass only pays off in proportion
to how much of the list has a State**. Say in the log how many of the contacts
you inspected had one.

**There is no search ceiling and no budget to ration.** HigherGov calls are
included in the paid plan. Run the FULL federal pass and the FULL state pass
every run, paging each one out. The old two-thirds federal / one-third state
split existed only to stretch GovTribe credits and no longer applies - and it
would directly undo the paging above. Never cut a discovery pass short: an
unsearched segment is a contact released for no reason.


## Filter client-side - the API will not do it for you

- **`source_type`** - `sam` is federal, `sled` is state and local. **Drop
  `sled_forecast` always** - that feed carries `Expiring Contract` and
  `Agendas / Minutes` rows which are budget signals with no due date and nothing
  to bid. In one spot check 12 of 30 state rows were this.
- **Notice type** (`opp_type.description`) - keep Solicitation, Presolicitation,
  Synopsis Solicitation, Combined Synopsis/Solicitation, Special Notice, Sources
  Sought. Drop everything else.
- **`due_date`** - keep `today + 10 days` through `today + 120 days`. The lower
  bound gives a firm time to respond. **The upper bound matters just as much** -
  without it you will queue standing vehicles due in 2039 as though they were
  live bids.
- **`posted_date`** - within 30 days.
- **`source_path`** must be a real `http` link. That is the URL that goes in the
  email. `path` is HigherGov's internal page - **never put it in an email.**
- **Drop sole-source and intent-to-award notices.** `sole_source_flag` = true
  is an automatic drop, but the flag is often false on notices that are plainly
  sole source, so ALSO scan the title, description and summary for: "sole
  source", "sole-source", "intent to sole source", "notice of intent",
  "intends to award ... to" followed by a named firm, "intends to negotiate",
  "only one responsible source", "other than full and open", "limited
  sources", "justification and approval", "J&A", "brand name justification",
  "FAR 6.302", "13.106-1(b)", "3304(a)(1)", "8(a) direct award", "follow-on
  to" a named incumbent. A notice that names the firm it will award to is not
  an opportunity for anyone else. **"Intends to award a MATOC" or "intends to
  award multiple contracts" with no named firm is NOT sole source** - read the
  sentence, do not just match the phrase.
- **Drop cancelled, withdrawn and awarded notices.** Title or text containing
  "CANCELLED", "CANCELED", "cancellation of this", "is hereby cancelled",
  "has been cancelled", "withdrawn", "no longer required", "requirement has
  been removed", or notice types Award Notice / Justification / Intent to
  Bundle. **Boilerplate is not a cancellation:** "the Government reserves the
  right to cancel this solicitation" and "cancellation fee" appear in live
  notices every day. Read the sentence.
- **Multiple versions: the NEWEST governs.** A solicitation number can return
  several versions. Use the one with the latest `posted_date` for the due date,
  set-aside, notice type and status. On 2026-09-30 the Baltimore VA flooring
  notice carried an SDVOSB set-aside only in its earlier version.
- **Drop grant notices aimed at nonprofits**, which arrive on the state feed.

## Set-aside arrives as a CODE

Map before Phase 4:

```
SBA, SBP         -> Total / Partial Small Business (open to every small business)
8A, 8AN          -> 8(a)
HZC, HZS         -> HUBZone
SDVOSBC, SDVOSBS -> SDVOSB
VOSB, VSA        -> VOSB
WOSB, WOSBSS     -> WOSB
EDWOSB, EDWOSBSS -> EDWOSB
NONE, blank      -> unrestricted: passes gate 2 for everyone, ZERO advantage points
```

## Other gate-drops at ingestion

Clearance required - FedRAMP or CMMC we cannot meet - vehicle-holders-only where
we hold no vehicle - a named pre-selected pool - an FTE minimum a small business
cannot meet - brand-name-only resale.

## Write through

`Gate Verdict` = Clean, a **Short Name** under 48 characters, a one-sentence
**Honest Gate**, the **AI Summary**, the **Source URL** from `source_path`.
Dedupe on Solicitation Number. Stamp `Last Swept` and `Last Swept By`. **No
Source URL means it cannot be emailed - drop it.**

### `Expires` = the opportunity's own due date, or posted + 10 days if there is none

**Never stamp `Expires` with today's date.** That instruction was here until
2026-09-30 and it destroyed the cache: every row died the day it was found, so
every run started from an empty pool and had to rediscover everything from
scratch. On 2026-09-30 the pool held 218 rows and **exactly one was unexpired**.
Forty-five of a hundred sampled rows had been given a shelf life of zero to
seven days.

A cached row is good until the opportunity itself stops being biddable. That is
its due date. Use `posted_date + 10 days` only when no due date is available.

**Write through EVERYTHING that passed the gates, not just what you emailed
today.** A run that finds 267 clean rows and writes 8 of them has thrown away
259 opportunities that tomorrow's run will pay to rediscover. That is exactly
what has been happening - write-through has been persisting 3 to 16 rows a day
against a cap of 50.

The pool is the campaign's supply. Treat filling it as part of the job, not a
side effect of sending.

A segment returning zero three days running needs re-cutting - tell Daniel.

---

# PHASE 4 - MATCH (approved by Daniel 2026-09-30)

**Order, always: opportunities first, then firms.** Discovery (Phase 3) produces
the day's gate-clean notices. Every notice that survives the notice gates below
is run against **every** eligible contact. The best pairs are picked, and only
then are those firms claimed (Phase 2B). Never claim a batch of firms and go
looking for something to send them.

**Why this formula exists.** On 2026-09-30 word-overlap scoring produced 1,764
candidate pairs and most were junk: out-of-state construction, a home builder
sent a tractor-mower buy on the words "tractor, trailer", a $250K firm sent a
$10-50M BPA. The strict rewrite that followed found 42, because it demanded
rare words that most contact rows do not have. The signal that balances both is
**how central the matched NAICS code is to the firm** - a firm registered for 3
codes that gets a notice in one of them is tailored; a firm registered for 247
is not. On that day's pool this formula gave 201 picks across 85 notices.

The implementation is `tna10x_pipeline.py match` (in the machine package
and the Drive folder). This section is the authority; the script implements it.

## Step 1 - Notice gates (drop the notice)

| Gate | Rule |
|---|---|
| Live | Not cancelled, withdrawn, awarded; not sole source or intent-to-award to a named firm (Phase 3 lists). Boilerplate "reserves the right to cancel" is not a cancellation. |
| Open to new bidders | Not a task order, call order or fair-opportunity competition under an existing BPA / IDIQ / MATOC / GSA schedule / OASIS / SeaPort / SEWP / CIO-SP / STARS / Alliant / Polaris. Only holders can bid those. |
| Real procurement | Not an industry day, webinar, workshop, seed-fund notice, technology-transfer or "available for licensing" notice with no response ask. |
| Runway | Days left >= 7 for sources sought / presolicitation / special notice, >= 10 for an RFQ, >= 21 for an RFP; <= 120. |
| NAICS | Must have a 6-digit NAICS - matching is on it. |
| On-site flag | On-site if NAICS starts 23, 5617, 5616, 5621/5622, 4842, 621x, 811x, 5413, 5629, 4931, 7223, 1133, 5311, 4812; or it is a state/local record; or the text mentions site visits / on-site work / badging. IT and consulting codes (5415, 5416, 5412, 5411, 5418, 5182, 5419, 6114, 6117) stay remote unless the text says on-site staff. |
| Part buy flag | Solicitation number starts `SPE`, or the title starts with an FSC prefix (`28--`, `6550--`), or names an NSN / part number. |

## Step 2 - Pair gates (drop the firm-notice pair)

| Gate | Rule |
|---|---|
| Exact trade | The notice's 6-digit NAICS is in the firm's SAM registration **and** the firm is flagged small (`Y`) under that code. Same 4-digit family is not enough. |
| Set-aside | Total small business needs the `Y` flag. A certification set-aside needs that exact cert (VOSB accepts SDVOSB, WOSB accepts EDWOSB). A set-aside stated only in the text needs the cert. Buy Indian (ISBEE) never matches. State/local records never pass on a federal cert. |
| Location | On-site work: the firm's `State` must equal the place-of-performance state. Blank State fails. (Phase 4 gate rules from Daniel, 2026-09-30.) |
| Size | Annual value = notice value / years (1 for construction; 5 for other work over $1M; else 1). Value / revenue must be 0.01x-1.0x for construction, 0.01x-2.0x otherwise. **Blank revenue or the $250,000 placeholder = unknown**: then the notice value must be <= $5M. |
| Fit evidence | See below. |

**Fit evidence - the heart of it.**

- **Focused firm** (5 or fewer NAICS codes): the exact-code match is the evidence.
  - Exception - **catch-all codes** (236220, 236210, 236118, 541611, 541330,
    541990, 561210, 561990, 621111, 621999, 238990, 541618, 541690, 541519,
    541511, 541512, 5417xx R&D, 561110, 488190, 811310, 423990, 424990,
    611430, 611710): also need >= 2 rare terms with one in the notice title,
    or the firm's own name in the notice. Without this an elevator contractor
    got a boiler-plant demolition and a pain clinic got radiology staffing.
- **Broad firm** (more than 5 codes): >= 2 rare terms, >= 1 of them in the notice
  title, **and** real evidence - a term from hand-written capability text (not
  just its NAICS title words) or a distinctive word of the firm's own name in
  the notice (Joint Logistics Managers -> "logistics").
- **Part buys**: the firm's capability must name the part, in the notice title.

**Rare term.** A shared word scores 0 if it is a stop word (generic, procurement
filler, state names, "cost-effective", "strategic", "assessment" and the like),
appears in more than 20% of contacts' capability text, or in more than 5% of
notices. Otherwise `w(t) = 0.5 ln((C+1)/(c_t+1)) + 0.5 ln((N+1)/(n_t+1))`.
**A firm's NAICS-title words count only if they also appear in the notice's own
title** - that rule is what killed "tractor, trailer, stacker".

## Step 3 - Score (max 97)

| Factor | Points |
|---|---:|
| Fit - focused with a rare term or name hit 25; focused on code alone 20; broad 10 + 5 per rare term (+5 name hit), max 25 | 25 |
| Set-aside advantage - cert set-aside they hold 20; Total SB 8; unrestricted 0 | 20 |
| Size band - value/revenue 0.05-0.75x 15; otherwise or unknown 9 | 15 |
| Stage - sources sought / presol / special 10; solicitation 5 | 10 |
| Runway - >= 21 days 10; less 6 | 10 |
| Value stated - 12; not stated 8 | 12 |
| Clean competition - no incumbent / recompete / follow-on / bridge language | 5 |

## Step 4 - Pick

Sort pairs by score. Take pairs **>= 70**, **one notice per firm, one firm per
company name, at most 5 firms per notice**, until the day's target plus a 20%
reserve.

## Step 5 - Human-grade review, then live check

Read each pick's capability text beside the notice summary. Drop anything a
capture manager would laugh at, whatever the score - the formula shortlists,
it does not decide. Then run gate 6 below (live check) on every notice about to
be emailed. Replace drops from the reserve.

## Gate 6 - live check before queueing

Call `search_opportunities` with `source_id` = the solicitation number (add
`opportunity_type="state_local"` for SLED) and read the **newest** version. Drop
if it is now cancelled, awarded, sole source, intent-to-award, outside the
runway, or its set-aside no longer fits. One call per notice, not per contact.

**No qualifying match:** that contact is simply not picked today. Never loosen a
gate to reach one. If the whole list, fully matched, cannot fill the cap, report
the shortfall and name it.

---

# PHASE 5 - DRAFT

**Subject:** `{Short Name} - {set-aside or notice type}, due {date}`

Short, dated, and it does **not** lead with the company name. Every email in the
reply set used this shape: `DHA analyst requirement - WOSB set-aside, due Aug 28`.

**Target: about 110 words.** One opportunity, one ask.

```
{First name},

{Agency} posted {OPPORTUNITY NAME}, due {date}. {ONE or TWO sentences of real
scope from the cached AI Summary - the work itself, where it is performed,
the period or ceiling if stated.}

Why this is yours: {ONE sentence. See below. If nothing supports it, omit.}

The catch: {the Honest Gate, one sentence} {immediately answered - see below}

{Close - rotate by a hash of the contact email:}
  - I will hold time Thursday unless you tell me otherwise.
  - Worth a brief call this week to decide whether we respond?
  - Short call this week?
  - If {this scope} is not your footprint, tell me what is and I will aim the
    next one properly instead of guessing.

Respectfully,
{Operator} - {Operator Title}
Top Notch Acquisitions, LLC - M. {Operator Mobile}
topnotchacquisitions.com/govcon
```

## What the email must NOT contain - checked against live sends 2026-09-28

The template above is sound. What has gone wrong is drift: every run adds a
little padding, and by 09-28 a real send ran **190 words against the 110
target** with two of its four paragraphs carrying no information. Read this
list before drafting and again before queueing.

**No warm-up opener. The first sentence names the opportunity.** A live 09-28
email opened *"We did some research on Madison Services and thought this one
would be worth putting in front of you."* That sentence is filler, it delays the
only thing the reader wants, and it is not true - nobody researched that firm, a
keyword matched. Claiming research you did not do is the fastest way to sound
like every other agency cold email. **Delete any opening sentence that could
appear in an email about a different opportunity to a different company.**

**No "we". The email is from one person.** "We did some research", "the win
themes we would build" - this is a cold email from a named individual, not a
capabilities brief from a firm.

**The close comes from the rotation, chosen by a hash of the contact email.**
The same 09-28 send closed *"Worth a short call to walk through the pain points
in that contracting office and the win themes we would build for Madison
Services? Reply interested and I will send available times."* That is two
sentences of agency vocabulary where the rotation offers one short question.
**"Reply interested and I will send available times" is banned** - it reads like
a form and it asks the reader to do the work of scheduling.

**Count the words before queueing.** Over about 130 and something padded is in
there. The scope sentence and the catch earn their length; nothing else does.

**Never name the company more than once**, and never in the close. It reads as
mail-merge the second time.

## "Why this is yours" - the line that earns the reply

**The payload is an insight about the SOLICITATION, not a credential match.**
The certification is a clause in passing, never the point. Every firm on this
list has a SAM registration; telling them what is in it tells them nothing and
reads like a mail merge.

**Never write "on your SAM registration", "per your SAM record", or any variant.**
If a certification is worth naming, the whole phrase is "which you hold". Four
words.

What the emails that actually drew replies did - the insight is the long half:

- *"the evaluation is past performance price tradeoff scored on fill rate,
  on-time fill percentage, and turnover. Those are staffing metrics, not
  consulting metrics."* - read the evaluation section.
- *"the requirement explicitly names NOAA as one of the supported agencies."* -
  read the requirement and spotted the customer tie.
- *"It is a sources sought, not an RFP, so the set-aside decision has not been
  made yet, which is exactly when a WOSB response can influence how the
  requirement gets scoped."* - spotted the leverage in the timing.
- *"the notice cites the FAR 19.104-1 rule of two, which means the Contracting
  Officer has not settled the acquisition strategy yet."* - read the notice.

Where to look, in the pool row: the evaluation method and what it rewards;
whether it is a sources sought or presolicitation rather than an RFP; a named
agency, office, programme or predecessor vehicle; a stated absence of an
incumbent; an unusual ceiling; anything saying the acquisition strategy is not
fixed.

**If the row does not support an insight, do not manufacture one.** Fall back to
the plain eligibility sentence, or drop the line. A thin hook beats an invented
one, and an absent hook beats a thin one.

**The hook is an advantage, or it is absent.** Never fill the slot with something
neutral or negative. Specifically: **never tell a firm their certifications do
not help.** On an open set-aside say the gate is open and they clear it. Do NOT
write "your HUBZone and WOSB do not add an edge here" - both are true, but the
first says you are in and the second says you are ordinary.

**Geography is never the hook.** "You are in New York" tells them nothing they
do not know.

## What may be asserted about the reader

The line between a fact and an invention is the **contact row**:

- **You MAY assert what is on the row** - `SBA Certifications`, `NAICS Codes`,
  `State`, `Est Federal Revenue`, `Capability Keywords` - in as few words as
  possible: "which you hold", "your NAICS".
- **You MAY NOT assert anything else.** No award counts, no agency history, no
  incumbency, no headcount, no guess at what they do.
- **Blank field means say nothing.** Never hedge, never infer from the company
  name, never pad the line. Drop it.
- **Never use "your" about their business anywhere else in the email.**

## The catch leads, and it is never the last line

One reply opened *"Leading with the catch so you do not waste a click."* Naming
the disqualifier early is the most credible thing in the email - it proves
someone read the solicitation rather than forwarding a keyword hit. Never soften
it, never bury it, never drop it.

**Then answer it in the same breath** - why it was sent anyway, or what would be
done about it. A catch left hanging is a pre-written no. The email that named a
hard 120-minute geographic requirement, then gave two reasons it was still worth
answering and asked which states WERE the footprint, drew a four-paragraph reply
proposing a teaming strategy.

```
The catch: {the real disqualifier, one sentence, no softening}.
{I sent it anyway because {reason it still pays}. OR {what we would do about it}.
OR tell me {the correcting fact} and I will aim the next one properly.}
```

Good answers: a sources sought is cheap to answer and positions them before the
strategy is fixed; the ceiling justifies a teaming position even if a prime bid
is not realistic; there is no incumbent, which is rare; the gate is checkable in
five minutes. Reach for the one that is true.

**Never pre-judge their capacity.** "This is a teaming lead unless you already
have people on the east coast" decides for them. Give them the fact and let them
decide - their answer is information we do not have.

**Never raise geography as a catch on a federal notice.** There is no such
problem.

## No usable first name

Where `Contact Name` is blank, or the list build withheld it because it could
not be tied to the address: open with `Hello,` and no name. Do not guess, do not
use the company name as a greeting, do not write "your company". Roughly 2,300
rows are deliberately in this state - expect it, it is not a data fault.

## Non-negotiable

- **The scope sentences are mandatory**, from the cached AI Summary only. Too
  thin for two honest sentences - do not email that opportunity.
- **The catch is on every email.**
- **The link is an embedded hyperlink on the opportunity name** - the only link
  in the body. No tracking pixels, no link wrapping for 60 days.
- **The ask is a meeting.** Never ask permission to keep sending emails.
- Near-plain HTML. No coloured blocks, images, tables or buttons.
- The opt-out is real and honoured within 24 hours. Minimal contractions, no
  exclamation marks, no "I hope this finds you well".
- **Never invent a fact.** Every number, date, solicitation number, set-aside
  and scope sentence comes from the pool row.
- **State or local records** show the jurisdiction in place of a federal
  set-aside. Never print an empty label.
- **Rotate opening and closing wording** by a hash of the contact's email.
- **Signature comes from this machine's `TBL_ACCOUNTS` row**, read fresh each
  run. Skip any blank line rather than printing an empty label.

---

# PHASE 6 - QUEUE TO THE OUTBOX

1. **Check the idempotency key first** - `{contact email}|{today}` in **both**
   `TBL_OUTBOX` and `TBL_SENDS`. Exists in either - skip. Check *before* writing.

2. **Set `Send After`. One rule:**
   - The day's first email goes at **10:00 local**, or **now** if the run starts
     later.
   - Each one after it: **4 to 9 minutes** after the previous.
   - On a re-run, continue from the latest `Send After` already queued today.

   No timezone lookup, no per-recipient windows, no reordering. At 50 a day this
   runs 10:00 to about 15:00, well inside the poller's 16-rows-an-hour
   throughput. **The 4-to-9-minute spacing is what stops the domain looking like
   a broadcast. Never compress it, never queue identical timestamps.**

3. **Write one `TBL_OUTBOX` row:** `Idempotency Key`, `To`, `Subject`,
   `HTML Body`, `Machine ID`, `Send After`, `Status` = **Queued**,
   `Contact Email`, `Opportunity 1`. Batch in tens.

4. Increment `queued`. **Never exceed `Daily Cap Today` counting what the day
   already had. No attachments, ever.**

---

# PHASE 7 - LOG

1. One `TBL_SENDS` row per email queued, key `{email}|{today}`.
2. On each **queued** contact: `Last Touched` = today, `Next Eligible` = today +
   `Cooldown Days`, `Times Touched` +1, **`Claimed By` cleared**. Claimed contacts
   dropped at review are released with their old dates untouched.
3. On this machine's account row: `Sent Today`, `Sent This Week`, `Last Heartbeat`.
4. **One `TBL_RUNLOG` row per machine per day, not per run.** If it exists,
   **update it** - add to the totals, append to Errors.
5. **Expire every Opportunity Pool row from today.** Nothing carries over.

**In Errors record the funnel:** what the day started with, eligible contacts
inspected and how many had a `State`, clean opportunities, candidate pairs,
picks dropped at review (and why - state rule, live-status, hidden set-aside,
sanity check), claim rounds, claims lost, whether the cap was filled and if not
why, the send window, and any segment that returned zero.

**Then verify the drain.** The first email is due within minutes, so confirm it
flipped to `Sent` after the next poller cycle. If not, **alert ESCALATION
immediately.**

---

# SEARCH DISCIPLINE

**HigherGov calls are free. There is no credit ceiling and no search budget.**
Every constraint below exists to protect the contact list or the run's speed -
none of them exists to save money. If a wave needs sixty calls to cover its
segments and states properly, make sixty calls.

**Under-searching is the expensive failure, not over-searching.** On 2026-09-30
the run queued 1 email against a cap of 50 because each call returned the newest
100 of thousands of matches and nothing paged past it. There has never been an
incident caused by too many calls.

**Only search segments today's eligible contacts belong to.** Never sweep a
segment with no eligible contacts - not to save credits, but because an
opportunity found for it has nobody to go to. Always
check `Last Swept` = today first. Never re-read a
solicitation you already summarised. Use `page_size=100`. Batch every Airtable
read and write. Do not narrate mid-run.

---

# HARD STOPS

1. **Never send email directly.** Queue to `TBL_OUTBOX`.
2. **Never send through the Gmail connector.** Reading is fine.
3. **Never attempt an outbound HTTP request.**
4. Never queue if `Outbox Sender Scenario` is blank.
5. **Never sign as anyone but the `Operator` on this machine's own row.**
6. Never queue if rows are still `Queued` from a **previous** day.
7. **Never add a fresh cap on a re-run.**
8. **Never compress the 4-to-9-minute spacing** or queue identical timestamps.
9. **Never lower `Match Threshold`, relax a hard gate, or accept a weak match to
   fill the cap.**
10. **Never exceed `Max Claim Waves`** (claim rounds - Phase 2B step 8). There is
    no search ceiling - HigherGov
    calls are free, so never cut a discovery pass short to conserve them, and
    never accept a truncated result without paging it out.
11. Never email anyone in `TBL_SUPPRESSION`, on email or domain.
12. Never email an **active** TNA client. Suppression holds all of them plus
    their domains, so checking it *is* the check. TNA partners and dormant
    clients are deliberately on the list.
13. Never email a contact whose `Available Now` is NO, whose `Status` is not
    Active, or who has ever replied.
14. Never queue for a contact this machine did not win the claim on, or twice in
    one day.
15. Never exceed `Daily Cap Today`. Never queue if Airtable is unreachable.
16. Never queue an opportunity a firm is not eligible to bid.
17. Never invent a solicitation number, date, agency, set-aside or scope sentence.
18. **Never state a fact about the recipient that is not on their contact row.**
19. **Never print a federal set-aside on a state or local record.**
20. Never queue an opportunity whose pool row has no Source URL, or whose due
    date is outside the 10-to-120-day window.
21. Never send attachments. Never reply to a human. Never send meeting times.
22. **Never search a segment with no eligible contacts today.**
23. **Never carry an Opportunity Pool row past the day it was found.**
24. **Never call a GovTribe tool.**
25. **Never send on-site work (construction, janitorial, maintenance, guards,
    on-site staffing - Phase 4 location gate) to a contact whose `State` is blank or differs
    from the place of performance.**
26. **Never queue an opportunity without re-checking it live** (gate 6): newest
    version, not cancelled, not sole source, not intent-to-award to a named firm.
27. **Never match contact-first in claim waves.** Match opportunity-first across
    the whole eligible list, then claim (Phase 2B).

---

# WHEN TO WAKE DANIEL UP

- the poller inactive, or Outbox rows not draining
- bounce rate over 3% on this account, or over 5% system-wide
- any spam complaint
- **a reply classified `Interested`** - same day
- Airtable or HigherGov unreachable on two consecutive runs
- **the cap missed by more than half on two consecutive runs** - and say whether
  the constraint was contacts or live opportunities, because they need different
  fixes
- a segment returning zero three days running
- an active TNA client not in Suppression, or an obvious non-prospect on the list
- anything that makes you unsure whether to queue

**When in doubt, do not queue.** A skipped contact costs nothing and comes round
again. A bad send costs the domain.

---

# REPORT AT THE END

Two or three sentences: what the day started with, eligible contacts inspected
(and how many had a State), candidates, picks dropped at review, **queued
against the cap**, the send window, whether the
Outbox is draining, and anything needing Daniel. If the cap was not filled, name
the constraint - and be specific about whether it was available contacts or
available opportunities.