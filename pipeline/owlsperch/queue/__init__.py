"""`owlsperch queue` -- the extraction queue (spec 4.5; batch B5).

Orchestrates the `/extract` skill's Python-side logic: selecting pending
segments for a subagent to work on (`next`), rendering the subagent's prompt
(`prompt`), ingesting its result (`complete`), reporting progress
(`summary`), and undoing an `in_progress` mark (`reset`). The skill itself
(`.claude/skills/extract/SKILL.md`) is a thin loop over these commands plus
Agent-tool subagent launches -- everything that can be tested in Python
lives here instead.
"""
