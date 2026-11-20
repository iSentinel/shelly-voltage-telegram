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
   The read-only QA audit requires an explicit empty bypass_actors
   list in full ruleset details; always, exempt or an unknown
   bypass policy fails closed until an owner-approved workflow can
   independently verify a strictly scoped alternative. This is a
   conservative QA check, not proof that tag creation is feasible.
   Configure owner-specific bypass/privileges explicitly; an active
   ruleset label alone does not prove human authorization.
2. A GitHub environment `shelly-release-approval` with a required
   reviewer. Verify the sole-owner review workflow is usable rather
   than assuming self-review settings or unsupported policies.
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
    python3 scripts/qa-release-tag-protection.py --qa-only \
      --version 0.5.26-hw \
      --commit add29de629f4b783f3e6a9635ef8d01ec3858d7a \
      --rulesets-json /PRIVATE/rulesets.json \
      --environments-json /PRIVATE/environments.json \
      --workflows-dir /root/svt-postsign-acceptance-qa-20261120/.github/workflows --jsonl

The exported metadata is a snapshot, NOT independently authenticated.
The tool only reports evidence; it never changes GitHub settings,
creates tags, signs artifacts or deploys the bot. Every outcome has
release_authorized=false and production_modified=false.
