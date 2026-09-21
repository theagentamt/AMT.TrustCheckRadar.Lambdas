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
