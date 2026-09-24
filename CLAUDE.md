# Working in this repo

## Shipping changes

When a change is finished and verified, open a pull request into `main` and
merge it without waiting to be asked:

1. Commit to the working branch and push it.
2. Open a PR into `main` describing what changed and how it was checked.
3. Merge it as a normal merge commit (not squash or rebase), pinned to the
   pushed head commit.
4. Report the PR link and merge commit.

Stop and ask instead of merging when:

- the PR has a merge conflict,
- a CI check fails,
- the change touches the API contract or data in a way callers would
  notice, or
- the change is partial, unverified, or the user said to hold off.

After a merge, start the next change from the updated `main`, not from the
merged branch.
