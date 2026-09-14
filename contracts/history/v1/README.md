# History and recognition contract 1.0.0

This package is the canonical Lambda/API contract for Sprint 7 History and
recognition. The authenticated account is always the validated Cognito access
token `sub`; request bodies, paths, and query strings cannot select another
account.

`DELETE /v1/users/history/account` deletes only the caller's History and
recognition data. It is not a full product-account deletion endpoint. Full
account deletion enters through the authoritative deletion-ledger stream item
`PK=ACCOUNT#<sub>, SK=ACCOUNT_DELETION` with event type
`account.deletion.requested`.

Campaign participation consent is independent of History. Clearing History does
not reset badges, and resetting badges does not clear History.
