# CLAUDE.md

This file covers the repository as a whole. A plugin that lives under `plugins/` carries its own `CLAUDE.md` next to its `plugin.json`, and that one is the authority on the plugin's internals. A plugin listed here from another repository is governed by that repository.

## What this is

`tundra` is a public Claude Code plugin marketplace for SoftwareOne Platform. `.claude-plugin/marketplace.json` at the root lists what is on offer. An entry's `source` is either a relative path into `plugins/` or a pinned commit in another repository.

**`tundra` is the default home for a plugin.** Its sibling `tundra-internal` takes only plugins whose content is itself internal — a tenant, an internal service, an internal process. The reason is the direction of travel: a plugin never moves from here to there, while the other way round means every user has to uninstall and re-add, because a plugin's identity is `<plugin>@<marketplace>` and nothing migrates across marketplaces.

## Layout

```
.claude-plugin/marketplace.json     the catalogue, and the only thing /plugin marketplace add reads
plugins/<name>/                     a plugin that lives in this repository
  .claude-plugin/plugin.json
  CLAUDE.md
  README.md
  skills/, commands/, agents/
scripts/sync-plugin-sources.py      pins every remote source to its ref's tip
LICENSE                             covers everything under the root
```

**A plugin's folder is the unit that gets copied, so everything a plugin needs lives inside it.** A copied plugin cannot reach a `../shared-utils` outside its own directory, and organisation sync packages each plugin folder on its own.

A plugin is registered in exactly one marketplace. Two entries means two identities, two caches and two update paths.

## Versions live in plugin.json, and only there

Every plugin declares its `version` in its own `.claude-plugin/plugin.json`. A marketplace entry never does, whether its source is a path into `plugins/` or another repository.

**Not both**, because the second copy is inert rather than redundant. The documentation is direct about it: *"Avoid setting `version` in both `plugin.json` and the marketplace entry. Claude Code always uses the `plugin.json` value without warning, so a stale manifest version can mask a version you set in `marketplace.json`."*

**Not neither**, because with no version anywhere the resolved version is the source's commit SHA, so a user asking what they are running gets `c447c3207a42`.

The sha in an entry decides which commit is fetched, and the version in that commit's `plugin.json` decides whether a user is offered an update. A push that does not change the version delivers nothing.

## Remote sources

An entry for a plugin in another repository uses a `git-subdir` source with both `ref` and `sha`. The `ref` names the branch being tracked, and the `sha` is the commit actually published. Never omit the `sha`, which would float the entry on whatever the branch holds.

`scripts/sync-plugin-sources.py` does the lookup. It asks each remote for its ref's tip with `git ls-remote`, prints the version `plugin.json` declares at that commit, and refuses a `version` key on any entry.

```
python scripts/sync-plugin-sources.py --check    # report only, exit 1 when an entry is behind
python scripts/sync-plugin-sources.py --write    # pin every entry to its ref's tip
```

`--write` rewrites nothing when any source is unreachable, so a batch is never left half pinned.

Releasing a remote plugin moves two repositories: bump the version where the plugin lives and push, then run `--write` here and commit. Forgetting the second step fails closed — the old commit is still fetched, its version has not changed, and nothing is delivered.

The four `issue-to-pr` plugins are released together at one commit, so their four entries always carry the same sha.

## Installing from here

```
/plugin marketplace add https://github.com/softwareone-platform/tundra.git
/plugin install <plugin>@tundra
```

GitHub `owner/repo` shorthand is also accepted and clones over SSH by default, so the shorthand and the HTTPS URL are not the same instruction. Write the full HTTPS URL.

## Conventions

Working notes live in `.claude/plans/`, which holds a `.gitignore` of its own that ignores the folder and itself. Nothing there is ever committed, and this repository is public, so that is the place for anything that must not be.

`.gitattributes` pins the working tree to LF on every platform, `*.cmd` excepted.

Commit messages carry no prefix and no attribution trailer.
