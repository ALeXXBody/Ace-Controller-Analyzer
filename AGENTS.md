
## Findings Ledger (read before working on analyzer/firmware logic)
`docs/IC_FINDINGS.md` — evidence log of every conclusion about the
CD3217/ACE2 ICs and the app, with VERIFIED/PROVISIONAL/WRONG/OPEN
verdicts. Rules: update it in the same commit as any protocol/logic
change it justifies; never delete WRONG rows; ask users for the debug
trace (`cd3217_debug.log`) before theorizing; register responses carry
a length-prefix byte — byte-wise sub-reads are meaningless, use
response merging (see §2.7).

## Task Queue (FIFO) — how user messages become work
`QUEUE.md` is the FIFO. Whatever the user writes that is a task, finding
or hardware info gets APPENDED there first (verbatim, oldest-first),
then processed one item at a time. Never drop or reorder; mark DONE with
results or WONTFIX with a reason. Check QUEUE.md at session start.

## Communication rule (standing agreement, from the owner)
Before implementing anything non-trivial: restate the understanding,
ask remaining questions, and wait for the owner's confirmation.
If any prior question was left unanswered, say so explicitly instead of
assuming. "Better to properly understand each other than doing
mistakes." QUEUE.md is the FIFO for tasks; this rule governs how
tasks are clarified before work starts.
