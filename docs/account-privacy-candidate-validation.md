# Account privacy source qualification — 2026-09-21

This increment is source-only and targets release-V01. It does not activate an API,
change an AWS alias, create inventory approvals or mark SECUR4ALL-200/236 complete.

- Python 3.14 full suite: 1,755 passed, 230 subtests; isolated integration modules
  intentionally excluded from the ordinary process because legacy tests install
  global SDK stubs. The additional admission module also skips in that process.
- Isolated real SDK/Moto: 24 purchase ownership cases, 4 export traversal/access
  cases, 4 account-deletion admission transaction cases pass. No real provider calls.
- Export candidate.2: five synthetic request/response fixtures validate against
  their JSON Schemas and SHA256SUMS matches every pinned contract file.
- compileall, shellcheck build script and git diff --check pass.
- Linux ARM64 Python 3.14 export ZIP resolves pinned cryptography 50.0.1,
  cffi 2.1.1 and pycparser 3.0 wheels. Source ZIP imports its packaged handler and
  dependencies using the host Python 3.14 environment. This is not an AWS runtime
  invocation or Linux native-extension execution test.
- Account-data and purchase source-only Python 3.14 ZIPs build. They do not claim
  provider dependency/deployment qualification for the purchase candidate.

Owner-approved export requires completed onboarding and an active device, fresh
signed authentication, no subscription and no allowance deduction. Pending-age
owners can request account deletion; transaction tests retain exact ownership and
allowed-state checks. No device-less export exception was added.

Coverage limits remain explicit in account-export-field-map.md and
purchase-ownership-lifecycle.md: full retained-source inventory, legacy migration,
all writer fences, real upper-volume bounds, complete deletion integration and
campaign pagination/retirement, route/key provisioning, and staging/device/restore
qualification. No passing unit suite substitutes for those acceptance requirements.
