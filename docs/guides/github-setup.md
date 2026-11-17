# GitHub contribution workflow

Use the [contribution guide](../../CONTRIBUTING.md) for assignment, branches,
checks and pull requests. Work IDs are GitHub issue numbers.

1. Find or open an issue and obtain assignment before implementing it.
2. Create a task branch from updated `main`, keeping unrelated local work safe.
3. Keep the PR focused, describe the behavior and record checks actually run.
4. Link the issue with `Closes #<number>` in the PR description.
5. Wait for required repository checks and review before merging.

GitHub branch protection, permissions and workflow availability are maintained
in repository settings; this page does not claim a particular configuration is
enabled. Routine model checks use small synthetic function tests. Real inference
belongs to a named release candidate and platform, as described in the
[model contribution guide](model-contribution.md#release-candidate-inference).
