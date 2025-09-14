# Alias Cleanup

The project formerly exposed multiple names for the same configuration
values. This release begins consolidating them so only one canonical name is
used across the codebase.

| Deprecated Names | Canonical Name |
|------------------|----------------|
| `VANITY_TXT_DIR` | `VANITY_OUTPUT_DIR` |

Accessing a deprecated alias triggers a `DeprecationWarning` and returns the
value of the canonical setting. The shim will be removed in a future release.
