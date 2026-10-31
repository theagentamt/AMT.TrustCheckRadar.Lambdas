# Export candidate.6: guarded URL summaries

Use the existing export route and unchanged START_EXPORT / CONTINUE_EXPORT body,
with x-account-export-contract-version: 1.0.0-account-export-candidate.6 on every
page. This explicitly permits validated guarded URL schema3 content-free summaries.
Original summary/check clocks and accounting are preserved, checkId removed, no
raw URL/destination/chain/input/provider content is added.

The header cannot enable Play-token or demographic export or any extra family.
The server's existing configured base profile (candidate.2, .4 or .5) selects the
same families, privacy scope and access/deletion/inventory rules. Candidate.6's
schema permits exactly those three existing scope declarations. New cursors bind
the export version AND configured base profile, authenticated account, current
device and inventory; changing header/profile cannot continue a prior cursor.
AEAD, byte/page/time limits and secret access are unchanged.

Older export versions remain immutable and fail closed on guarded URL summaries.
Candidate.6 can read old and guarded receipt summaries without mutating originals.
Update the native client and deploy the reader before creating guarded receipts.
No infrastructure/config/access change is part of this source candidate.
