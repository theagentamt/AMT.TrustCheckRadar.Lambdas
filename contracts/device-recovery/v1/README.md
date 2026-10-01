# Device Recovery Consumer Contract V1

This is a source candidate. It is not deployed or activated.

The supported candidate route is `POST /v1/users/device-recovery`. Its successful
response describes the original operation only. It must never be interpreted as
current device-binding status. Normal `POST /device-registration` automatic
switching remains unchanged and is outside the recovery step-up/rate policy.

`status-route.proposed.json` is deliberately excluded from the supported route
list. It records bounded non-disclosure and no-write requirements while the
owner decides whether a read-only status API will exist. It does not freeze a
response shape, error vocabulary, deployment, or activation.

An expired receipt that is still present fails closed. Behavior after physical
receipt removal is unresolved because a random UUIDv4 contains no verifiable
prior-use evidence. No tombstone or longer retention is authorized by this
contract.

Local tests include a stateful, condition-enforcing transaction simulator for
pointer and deletion-fence races. Actual DynamoDB/API Gateway integration and
deployed acceptance remain gated and are not claimed by this artifact.
