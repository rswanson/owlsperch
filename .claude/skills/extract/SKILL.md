---
name: extract
description: Fan out Claude Code Agent-tool subagents over pending spell segments to extract structured D&D 3.5e records. Use when the user runs /extract <book_id|all> to populate records/ from segments/.
user_invocable: true
---

# extract

Runs the haiku-tier extraction loop (spec 4.5) for one book, or every book
with segments. All decision logic lives in `owlsperch queue ...` and
`owlsperch validate` (Python, unit tested) -- this procedure just drives
them and launches subagents.

## Arguments

`/extract <book_id|all> [--limit N] [--parallel N=8] [--kind K]`

- `<book_id|all>` -- a manifest book_id, or `all` to run every book_id that
  has a `segments/<book_id>/` directory under `$OWLSPERCH_DATA` (list them
  with `ls $OWLSPERCH_DATA/segments/`; run this whole procedure once per
  book_id in turn).
- `--limit N` -- stop after N segments total for this book (default:
  unlimited -- run until nothing pending is left).
- `--parallel N` -- subagents in flight at once (default: 8).
- `--kind K` -- restrict to one kind_hint (default: `spell`, the only type
  with a schema this batch; other kinds are simply never selected).

## Loop, per book_id

1. Compute this wave's size: `min(--parallel, remaining --limit)`.
2. Run:
   `uv run owlsperch queue next <book_id> --tier haiku --limit <wave size> --kind <kind> --json`
   Parse the JSON array of `{seg_id, segment_path, kind_hint, prompt_path}`.
   If it is empty, this book is done -- go to step 6.
3. For every item in the wave, in **one message** (so they run concurrently,
   up to `--parallel` at a time), launch an Agent tool call with:
   - `subagent_type`: omit (fresh agent), `model: "haiku"`
   - `prompt`: `Read and follow the instructions in <prompt_path>. Reply
     with only the JSON object it specifies.`
4. For each subagent's final reply (in the order they complete):
   - Write the raw reply text to a fresh temp file (`mktemp`).
   - Run `uv run owlsperch queue complete <seg_id> --result <temp file>`.
   - **If a subagent errored, timed out, or returned anything that isn't
     its final message** (a tool-use loop that never finished, empty
     output, a refusal): write whatever text is available (or a short
     note like `"subagent error: <what happened>"` if there is none) to
     the temp file and run `queue complete` anyway. `queue complete`
     treats non-conforming text as `malformed_result` and returns the
     segment to `pending` on the same tier -- never leave a segment stuck
     `in_progress`.
5. After the whole wave has been completed (step 4 done for every item):
   - Run `uv run owlsperch validate <book_id> --json` (promotes passing
     records, leaves failures pending with errors attached).
   - Run `uv run owlsperch queue summary <book_id>` and show it.
   - Subtract the wave size from the remaining `--limit` (if set); if the
     wave from step 2 was smaller than requested, or `--limit` is now 0,
     go to step 6. Otherwise go back to step 1.
6. Print `owlsperch queue summary <book_id>` one final time for this book_id
   (skip a repeat print if step 5 just printed the same state with nothing
   left pending).

For `all`, move to the next book_id and restart at step 1; print each
book's final summary as you go.

## Report to the user

After every book_id is done, summarize: segments attempted, passed
(validated), failed (still pending with errors), no-content, and total
records written -- read straight from the last `queue summary` for each
book.
