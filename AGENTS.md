# Repository working rules

## Release integration and story completion

- `release-V01` is the integration branch for ongoing V1 work in this repository.
- Start new feature/fix branches from the latest `origin/release-V01`, using the
  `codex/` prefix unless the owner supplies a branch name. Target `release-V01`
  for all development pull requests, including work on existing feature branches.
- Do not push or merge development changes into `main`. Promotion from
  `release-V01` to `main` requires an explicit owner instruction for a release.
  Do not force-push or silently rewrite existing feature history to adopt this rule.
- A development story is complete only when its agreed acceptance criteria are
  satisfied, appropriate validation passes, implementation and required handoff
  records are committed, and those commits are pushed to GitHub and verified on
  the intended remote branch and integrated into `release-V01` (or already covered
  in `main`). A pushed feature branch alone, local commit or local deployment is
  not Done. Verify exact merge SHA or documented patch/squash equivalence; do not
  mistake an unmerged branch with completed tests for integrated work.
- Update the YouTrack story with validation evidence, commit/branch links and any
  remaining limitations before marking it Done. If pushing is blocked, report the
  blocker and keep the story open until publication is verified.
- Unperformed acceptance tests are not passes. Only an explicit owner-approved
  scope change may defer required acceptance into a linked follow-up; record the
  decision and keep that follow-up open until testing is performed.
- Pushed, merged into `release-V01`, deployed, and released from `main` are distinct
  states. Report them accurately. Preserve existing deployment approvals and
  environment gates; this branch policy does not authorize a deployment.

## Independent component acceptance and physical-device testing

- The owner's standing rule applies to all stories: do not require or perform
  physical-device testing at this stage. Use emulator/simulator evidence where
  suitable, and explicitly defer acceptance that needs physical hardware to the
  linked physical-device follow-up, currently
  [ATCR-148](https://andmorethings.youtrack.cloud/issue/ATCR-148).
- A blocked Android or iOS task, or unavailable mobile hardware, does not by
  itself block Lambda or infrastructure work. Continue backend, contract, fixture
  and integration qualification that can be completed independently. Name any
  concrete technical dependency and limit the blocked scope to the work that
  actually depends on it.
- A component story may close when its scoped acceptance, relevant validation,
  committed and pushed evidence, verified release integration and tracker updates
  meet the completion rules above. Record any deferred physical-device acceptance
  explicitly and link the follow-up. This standing owner approval permits that
  deferral; it does not turn an unperformed test into a pass or waive unresolved
  backend acceptance.
- Component completion, emulator/simulator validation, full end-to-end acceptance
  and release readiness are separate claims. Keep the physical-device follow-up
  open until its acceptance is performed. Preserve all existing activation,
  deployment, privacy, retention, review and CI gates; this rule grants no new
  runtime activation or deployment permission.

## CI timing and local validation

- GitHub CI checks run automatically only on pushes or merges to `main`. Do not
  add pull-request, feature-branch or `release-V01` automatic CI triggers, or
  dispatch a workflow to substitute for required local validation before main.
- Run the meaningful checks appropriate to the change locally before publishing
  or merging feature/release work, and record commands, results and any limits in
  the PR. Local evidence is sufficient for feature/`release-V01` integration under
  this policy; absence of pre-main GitHub runs is expected, not a validation pass.
- Keep all substantive main-branch CI jobs and checks. A main push is the GitHub
  validation boundary; promotion to main still requires explicit owner instruction.
- Do not bypass a live protection/ruleset requirement with admin merge or fabricate
  check results. Report incompatible pre-main check requirements for authorized
  policy alignment. Preserve branch review protections and publishing/deployment
  approval gates; this CI policy grants no deployment or workflow-dispatch authority.
