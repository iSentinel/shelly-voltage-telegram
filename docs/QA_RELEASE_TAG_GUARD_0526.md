# GitHub tag protection / release environment — QA-only evidence

A stable v* tag push automatically starts the **Container Image**
and **Source Release** workflows. Without GitHub-side enforcement,
neither the in-principle November 19 Owner Approval nor local QA
source checks can prevent a release being published on tag push.

On 20 November 2026 at the read-only Gateway check, GitHub returned
zero repository rulesets (including inherited parents), zero deployment
environments, and both publish jobs lacked a protected
`environment:` binding. The legacy tag-protection API returned 404.
This is an actual release **NO-GO**, not a request to bypass approval.

`scripts/qa-release-tag-protection.py` audits only SAVED GitHub API
JSON snapshots and workflow text. The report requires all of:

1. An active GitHub **tag ruleset** restricting creation of v* tags.
   On this public personal repository, the QA auditor requires
   exactly one bypass actor: User GitHub ID 83760791 (iSentinel)
   with bypass_mode=always so the sole owner can create a tag.
   Team, application, unrestricted role, exempt, second actors,
   unknown bypass metadata or empty bypass list are rejected.
   This grants tag creation only, not release publication.
   Configure owner-specific bypass/privileges explicitly; an active
   ruleset label alone does not prove human authorization.
2. A sole-owner approval environment `shelly-release-approval` with a required
   reviewer matching owner ID 83760791, with prevent_self_review=false and can_admins_bypass=false.
   GitHub must actually enforce a manual review on the tag workflow.
3. Both the source release and signed OCI publication jobs gated by
   that reviewed environment, or a reviewed equivalent that prevents
   tag-triggered publication. A missing environment can be
   auto-created unprotected: add the protection FIRST.
4. The exact release SHA and all signed provenance, newest SQLite
   continuity and a distinct final activation approval remain
   separate requirements. A configuration audit PASS is not approval.

**Important:** A job-level environment in `image.yml` would also
gate normal main-branch builds unless the workflow is reorganized to
separate tag publication from development CI. Do not blindly apply
a YAML edit. Do not modify repository rulesets or environments
without an explicit owner-authorized configuration step.

Read-only Gateway example:

    umask 077
    gh api 'repos/iSentinel/shelly-voltage-telegram/rulesets?includes_parents=true' > /PRIVATE/rulesets.json
    gh api 'repos/iSentinel/shelly-voltage-telegram/environments' > /PRIVATE/environments.json
    gh api 'repos/iSentinel/shelly-voltage-telegram' > /PRIVATE/repo.json
    python3 scripts/qa-release-tag-protection.py --qa-only \
      --version 0.5.26-hw \
      --commit add29de629f4b783f3e6a9635ef8d01ec3858d7a \
      --repo-json /PRIVATE/repo.json \
      --rulesets-json /PRIVATE/rulesets.json \
      --environments-json /PRIVATE/environments.json \
      --workflows-dir /root/svt-postsign-acceptance-qa-20261120/.github/workflows --jsonl

The exported metadata is a snapshot, NOT independently authenticated.
The tool only reports evidence; it never changes GitHub settings,
creates tags, signs artifacts or deploys the bot. Every outcome has
release_authorized=false and production_modified=false.


## API visibility limitation (2026-11-20)

Current GitHub REST documentation confirms that a repository tag ruleset
supports a User bypass actor, but the GET ruleset endpoint omits
bypass_actors when the caller lacks write access to that ruleset.
A public repository GET /environments response example also does not
show can_admins_bypass. That omission is not proof that administrator
bypass is disabled.

References:
- https://docs.github.com/en/rest/repos/rules?apiVersion=2026-03-10#get-a-repository-ruleset
- https://docs.github.com/en/rest/deployments/environments?apiVersion=2026-03-10#get-an-environment
- https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments

The guard now reports RULESET_BYPASS_METADATA_NOT_RETURNED and
ENV_ADMIN_BYPASS_METADATA_NOT_RETURNED separately from the existing
missing protection reasons. Either visibility issue blocks the audit,
even if another matching ruleset passes. An explicit false is not
inferred from a missing field. No administrative write token is
introduced in the publishing jobs.

A future owner-approved alternative can keep full administrative
verification outside CI, bind the inspection to the exact release,
and retain mandatory owner environment review. Before any implementation,
verify which protected environment settings are exposed through the
actual account API or UI. Do not configure permissions, modify stable
publication jobs, or substitute a local JSON snapshot for real GitHub
enforcement on the strength of this QA result.
