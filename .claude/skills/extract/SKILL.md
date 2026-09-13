---
name: extract
description: Fan out Claude Code Agent-tool subagents over pending spell segments to extract structured D&D 3.5e records. Use when the user runs /extract <book_id|all> to populate records/ from segments/.
user_invocable: true
---

# extract

Runs the escalation-ladder extraction loop (spec 4.5) for one book, or every
book with segments. All decision logic lives in `owlsperch queue ...` and
`owlsperch validate` (Python, unit tested) -- this procedure just drives
them and launches subagents.

## Arguments

`/extract <book_id|all> [--limit N] [--parallel N=8] [--kind K] [--tier T]`

- `<book_id|all>` -- a manifest book_id, or `all` to run every book_id that
  has a `segments/<book_id>/` directory under `$OWLSPERCH_DATA` (list them
  with `ls $OWLSPERCH_DATA/segments/`; run this whole procedure once per
  book_id in turn).
- `--limit N` -- stop after N segments total for this book (default:
  unlimited -- run until nothing pending is left).
- `--parallel N` -- subagents in flight at once (default: 8).
- `--kind K` -- restrict to one kind_hint (default: omit it -- `queue next`
  then selects across every kind_hint with a registered schema at once,
  currently spell/feat/table/rules_section; `stat_block` has no schema yet
  and is never selected unless `--kind stat_block` is passed explicitly).
- `--tier T` -- restrict to one tier (`haiku`/`sonnet`/`opus`). Default:
  omit it -- `queue next` picks the lowest tier with pending work on its
  own, so a plain `/extract <book_id>` naturally drains haiku, then sonnet,
  then opus, without the skill tracking tiers itself.

## Loop, per book_id

1. Compute this wave's size: `min(--parallel, remaining --limit)`.
2. Run:
   `uv run owlsperch queue next <book_id> [--tier T] --limit <wave size> [--kind K] --json`
   Parse the JSON array of `{seg_id, segment_path, kind_hint, prompt_path,
   tier, model}`. **The loop for this book_id ends only when this returns
   `[]`.** Before selecting, `queue next` itself resets any segment stuck
   `in_progress` for over 60 minutes back to `pending`, and lazily advances
   any pending segment that already has a failed/malformed attempt at its
   own current tier -- so nothing is ever skipped forever, and `[]` really
   does mean nothing is pending at any tier (when `--tier` is omitted) or at
   the requested tier. If it is empty, this book is done -- go to step 6.
3. For every item in the wave, in **one message** (so they run concurrently,
   up to `--parallel` at a time), launch an Agent tool call with:
   - `subagent_type`: omit (fresh agent), `model: "<item.tier>"` (the tier
     `queue next` returned for that item -- haiku/sonnet/opus, not always
     haiku)
   - `prompt`: `Read and follow the instructions in <prompt_path>. Reply
     with only the JSON object it specifies.`
4. For each subagent's final reply (in the order they complete):
   - Write the raw reply text to a fresh temp file (`mktemp`).
   - Run `uv run owlsperch queue complete <seg_id> --result <temp file>`.
   - **If a subagent errored, timed out, or returned anything that isn't
     its final message** (a tool-use loop that never finished, empty
     output, a refusal): write whatever text is available (or a short
     note like `"subagent error: <what happened>"` if there is none) to
     the temp file and run `queue complete` anyway. `queue complete` treats
     non-conforming text as `malformed_result`, which is now (batch B8)
     **treated exactly like a validation failure and escalated** -- the
     segment moves to the next tier (or to `human/` if it was already on
     opus) rather than staying on the same tier. Never leave a segment
     stuck `in_progress`.
5. After the whole wave has been completed (step 4 done for every item):
   - Run `uv run owlsperch validate <book_id> --json` (promotes passing
     records; a FAIL escalates the segment's tier, moving it to `human/` if
     it just failed on opus).
   - Run `uv run owlsperch queue summary <book_id>` and show it -- it now
     prints per-tier pass/escalated counts plus `needs_context_retries` and
     `human`, plus `pending by kind_hint` / `pending by tier` (`--json`'s
     `pending_by_kind`/`pending_by_tier`), the pending-only breakdown. Use
     this, not `queue next`, to size and plan the next wave, since `queue
     next` selects and marks segments `in_progress`.
   - Subtract the wave size from the remaining `--limit` (if set); if the
     wave from step 2 was smaller than requested, or `--limit` is now 0,
     go to step 6. Otherwise go back to step 1.
6. Print `owlsperch queue summary <book_id>` one final time for this book_id
   (skip a repeat print if step 5 just printed the same state with nothing
   left pending).

For `all`, move to the next book_id and restart at step 1; print each
book's final summary as you go.

## Outcomes a subagent's reply can produce (batch B8)

Beyond a plain `records`/`no_content` reply, `queue complete` also handles:

- **`needs_context`**: the entity is truncated at the start/end of the
  segment text and continues into a named adjacent segment. The *first*
  `needs_context` reply for a segment keeps it on the same tier for one
  retry -- the next `queue next` wave re-selects it with the adjacent
  segment's text merged into the prompt. A *second* `needs_context` at the
  same tier escalates like any other failure.
- **`proposed_type`**: no existing schema fits the segment at all. The
  segment moves straight to `human/<book_id>/` with the proposal attached --
  it will not be reselected by `queue next`.
- **Escalation exhausted**: a validation FAIL, a malformed reply, or a
  second `needs_context` recorded while the segment was already on `opus`
  moves it to `human/<book_id>/` with every attempt kept, instead of
  advancing further.

None of this needs special handling in the loop above -- steps 4-5 already
route every outcome through `queue complete`/`owlsperch validate`, which do
the escalation/human-move bookkeeping.

## Tables belonging to an entity (batch B10)

When a segment's text contains a table belonging to the entity being
extracted (a feat, rules_section, or spell with tab-separated rows), the
subagent's prompt tells it to write TWO record files for that one segment:
the entity's own record, and a separate `table` record (cross-linked via
the table's `fields.parent_record` and the owning record's `tables` list).
The subagent lists BOTH paths in its reply's `records` array, and `queue
complete` already accepts any path under `records/<book_id>/` -- no change
to this loop is needed to handle it.

## Report to the user

After every book_id is done, summarize: segments attempted, passed
(validated) per tier, escalated per tier, no-content, moved to `human/`, and
total records written -- read straight from the last `queue summary` for
each book.
