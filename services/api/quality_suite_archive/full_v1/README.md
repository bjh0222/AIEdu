# full_v1 quality-suite archive

This directory freezes the 21-file quality-suite surface used before
`correctness_only.v1`.

`manifest.json` records the immutable source commit
`b22a2bd11da220b7269eff6bec994f939b76e442`, file byte counts and SHA-256
hashes. The test suite recomputes every archive hash from these files.

Modules outside this quality-suite surface remain resolvable from the recorded
source commit. Runtime selection is isolated behind
`app/v2/quality_suite/__init__.py`; new web tasks use the
`correctness_only.v1` adapter, while legacy requests without `quality_layers`
retain the `full_v1` compatibility profile.
