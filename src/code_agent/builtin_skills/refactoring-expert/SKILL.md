---
name: refactoring-expert
description: Perform behavior-preserving structural refactors with dependency analysis, incremental edits, and regression verification.
---

Define the preserved behavior and motivation before editing. Map callers, tests, configuration, generated code,
and public compatibility boundaries. Separate mechanical moves from semantic changes. Keep steps reversible and
avoid unrelated cleanup. Use semantic rename support when available. Run focused tests after each risky boundary
and the broader affected suite before completion.
