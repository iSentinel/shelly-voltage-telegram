# Offline pair staging design (QA only)

The offline verifier produces content evidence, not a staged receipt.
Keep the existing online pair verifier and strict supervisor unchanged
until a new versioned contract and its transaction tests are reviewed.

## Trust boundaries

1. Refresh Sigstore TUF on the trusted connected host with the embedded
   root and default authenticated mirror. Record the selected root hash
   from the trusted host; a matching transfer hash is not independent
   authentication or a freshness/rotation policy inside the guest.
2. Transfer the original release archive, source bundle, raw OCI index,
   image signature bundle and original OCI layout to a private guest
   directory. Pin the version, full Git SHA, index digest and root hash
   outside the imported directory. Do not trust caller-supplied success
   JSON, mount paths, mutable image tags or arbitrary trust roots.
3. Execute both Cosign checks on the actual bytes, with exact GitHub
   issuer, repository, workflow, tag ref and commit; validate source
   archive structure and checksum, signed index bytes and reachable
   graph. Embedded SPDX/SLSA metadata is covered by the signed index;
   it is not evidence of a separate attestation signature.
4. Resolve one target architecture and bind the immutable local Docker
   config ID, revision and ordered DiffIDs; separately verify actual
   serialized Docker config and layer content. A local import with
   empty RepoDigests does not establish a registry digest alias.

## Current actual QA result

On disconnected LXC 114, qa_verify_offline_pair.py verified the original
published 0.5.26-hw pair, Git e70473099b168cd077d555b6abc668db3ed3dd14,
index cd8ee5e8044f924e089fc9f603b299b515746fb816b0305818db2475177f7b49
and AMD64 config 0d1926551eecc4afd7ed25cf0e61187c329a4338ebf47b04d28ce7d2a932b2ef.
Ten ordered DiffIDs matched. The combined run refused incorrect root
pin, modified index and incorrect commit with exit 3. No receipt was
written, no container launched, and no image was retagged.

## Required next implementation

A proposed offline receipt schema must be distinct from the existing
online receipt and include the exact identity of the verified platform
manifest/config, source archive and trust-policy version. Before
committing it, reverify root policy and actual sealed input bytes under
the selected transaction token, check retired editors/old owners and
the latest quiescent WAL/PHOTO rescue, hold the writer lease and update
gate, and atomically publish a private fsynced receipt. Do not infer
online paired proof from an offline report.

A future strict offline supervisor must explicitly support the new
receipt type, bind the selected immutable stopped container ID to the
verified immutable image/config ID and exact data/fence mounts, and
recheck those facts before each start under the update gate. It must
refuse unknown receipt versions, untrusted root pins, stale rescue,
wrong architecture, reordered/missing layers, replaced container/image,
unexpected mount/env, changed restart policy, pending update or editor
ownership. Do not insert or fabricate RepoDigests or masquerade a local
tag as a registry reference. Use a separate tested policy rather than
weakening the installed strict supervisor.

A positive isolated boot/reboot, crash/fault tests at each committed
transaction boundary and independent all-owner audit remain mandatory.
No workflow approval, release signature, deployment authorization or
production ownership follows from this document or QA verifier.
