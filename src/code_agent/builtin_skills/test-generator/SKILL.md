---
name: test-generator
description: Generate focused unit, integration, regression, property, and failure-path tests grounded in observable behavior and repository conventions.
---

Inspect the implementation and existing test style first. Test public behavior, boundaries, error paths, and the
specific regression rather than implementation trivia. Keep fixtures minimal and deterministic. Avoid network,
clock, randomness, and global-state coupling unless explicitly controlled. Run the narrow test first, then the
relevant broader suite. Do not weaken assertions to make broken behavior pass.
