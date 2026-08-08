# Encounters with no discharge timestamp — open stays

An encounter whose **discharge timestamp is missing** — `STOP` is null, there is no
discharge date and no discharge time — is a stay that has not ended. The patient is
still admitted as of the data cut, so the discharge does not exist yet rather than
having been lost.

This is the absence of a value, not a wrong one: there is nothing to compare, nothing to
subtract, and no discharge event to join onto. (A discharge that is recorded but invalid
is a separate problem — see [[reversed_stays]].)

`STOP` is a real `TIMESTAMP` column and the missing values are genuine SQL `NULL`s, not
empty strings. Comparisons against them return `NULL` rather than false, so they drop
out of filters silently rather than erroring.

## Rules by measure

- **Volume and admission counts: include them.** A still-admitted patient was admitted.
  A stray `STOP IS NOT NULL`, or an inner join onto a discharge event, removes them and
  undercounts. See [[admission]].
- **Length of stay: exclude them.** The length of an unfinished stay is not defined
  yet. Treating null as zero drags the average down; substituting the data-cut date
  mixes complete and incomplete stays and understates the true figure for long ones.
  See [[length_of_stay]].
- **Readmission index admissions: exclude them.** An index admission needs a discharge
  date to anchor its follow-up window. See [[readmission_30day]].

## The principle behind the split

"Include" and "exclude" are both correct, and the reason generalises past this entry:

> **The event occurred; the interval did not complete.**

An open stay is a real admission — the patient was admitted, occupies a bed, and appears
in today's census. So any measure that *counts events* includes it. But its duration,
its discharge date, and anything anchored on discharge do not exist yet, so any measure
that *requires a completed interval* must exclude it rather than invent one.

This is how hospitals actually report: census and admission volume include current
inpatients; length of stay and readmission are computed on discharges. Apply the same
test to any measure not covered here — ask whether it counts an occurrence or measures a
completed span, and the treatment follows without needing a rule written for it.

State which treatment was applied.

## Reporting

When a measure excludes open stays, report how many were excluded alongside the result.
A silently shrinking denominator is the thing that makes this error hard to catch later.
