# Kubernetes Troubleshooting Agent

A local `kind` cluster you can break on purpose, and a Claude-powered CLI agent that
diagnoses and fixes what you broke -- with your approval before anything actually changes.

```
 make chaos ──▶ breaks deployment/web ──▶ make chat ──▶ agent reads (get/describe/logs/events)
                                                        │
                                                        ▼
                         you approve (y/N) ◀── dry-run preview ◀── agent proposes one fix
                                │
                                ▼
                 backup to backups/ ──▶ kubectl patch/scale/restart/delete ──▶ audit.log entry
```

## Project layout

```
agent/
  kube.py           # the ONLY module that shells out to kubectl -- all guardrails live here
  approval.py       # ApprovalManager: per-fix / plan modes + session mutation cap
  claude_agent.py   # system prompt, tool definitions, manual tool-use loop (diagnose_and_fix)
  skill_loader.py   # loads skills/kubernetes-skill/SKILL.md into the system prompt
  cli.py            # interactive REPL on top of diagnose_and_fix()
scripts/
  setup_cluster.sh  # create/reuse the kind cluster + deploy the baseline app
  install_skill.sh  # git-clone (or pull) the vendored KubeShark skill
  chaos.py          # inject one of four failures into deployment/web
manifests/
  demo-app.yaml     # namespace `demo`: Deployment `web` (2x nginx:alpine), Service `web-svc`,
                    # ConfigMap `web-config`
tests/
  test_guardrails.py       # unit tests for kube.py safety checks -- no cluster, no API key
  test_chaos_scenarios.py  # each chaos scenario produces its expected broken state
  test_agent_fixes.py      # integration: chaos -> real agent -> healthy again
backups/            # pre-mutation YAML snapshots + audit.log (git-ignored)
skills/             # vendored kubernetes-skill (git-ignored, created by `make skill`)
requirements.txt    # anthropic, python-dotenv, pytest
.env.example        # template for .env (committed)
.env                # your ANTHROPIC_API_KEY etc. (git-ignored -- never commit it)
.gitignore
```

## Prerequisites

Already installed via Homebrew on this machine: `kind`, `kubectl`, `docker`, `python3`, `git`.
If starting fresh elsewhere: `brew install kind kubectl python3`, plus Docker Desktop (or another
container runtime `kind` can use).

Python dependencies (`requirements.txt`, installed into `.venv` by `make install`):

| Package         | Used for                                                        |
|-----------------|-----------------------------------------------------------------|
| `anthropic`     | Claude API client (`agent/claude_agent.py`)                     |
| `python-dotenv` | loads `.env` in `agent/cli.py` and `tests/conftest.py`          |
| `pytest`        | test runner                                                     |

## One-time setup

```bash
make install           # creates .venv, installs requirements.txt
make skill             # git-clones github.com/LukasNiessen/kubernetes-skill into skills/kubernetes-skill
cp .env.example .env   # then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

### Environment variables (`.env`)

| Variable            | Required for                          | Default | Meaning                                         |
|---------------------|---------------------------------------|---------|-------------------------------------------------|
| `ANTHROPIC_API_KEY` | `make chat`, `make test-integration`  | --      | your Claude API key                             |
| `KEEP_CLUSTER`      | optional                              | `0`     | `1` keeps the kind cluster up after a test run  |

`.env` is loaded automatically by the CLI and the test suite. Anything already exported in your
shell takes precedence, so `export ANTHROPIC_API_KEY=...` or `KEEP_CLUSTER=1 make test` still
work. `.env` is git-ignored -- keep real keys out of `.env.example`.

`make skill` is required before `make chat` or `make test-integration` -- the agent's system
prompt is built from `skills/kubernetes-skill/SKILL.md` and fails fast if it's missing.
Re-running it pulls the latest version.

## Daily loop

```bash
make cluster-up                    # create (or reuse) kind cluster `k8s-troubleshoot` + deploy demo app
make chaos SCENARIO=crashloop      # break it (see scenarios below)
make chat                          # talk to the agent
make cluster-down                  # delete the cluster when you're done
```

### Chaos scenarios

Each one is a strategic-merge patch on `deployment/web`:

| `SCENARIO`       | What it changes                                       | Symptom you'll see             |
|------------------|-------------------------------------------------------|--------------------------------|
| `crashloop`      | container `command` set to `/bin/false`               | `CrashLoopBackOff`             |
| `oom`            | memory request/limit dropped to `6Mi`                 | `OOMKilled`                    |
| `bad_probe`      | liveness probe pointed at port `9999`                 | repeated restarts              |
| `missing_config` | `GREETING` env references ConfigMap key `MISSING_KEY` | `CreateContainerConfigError`   |

Chaos goes through the same guarded `kube.execute_fix()` the agent uses, so it is also backed
up and written to the audit log.

### Talking to the agent

```
you> Something's wrong with the web deployment in the demo namespace, can you take a look?
agent> ...
```

The agent gathers evidence with read-only kubectl calls first (`get`, `describe`, `logs`,
`events`, `top`, `explain` -- these run immediately, no approval needed), forms a hypothesis,
and proposes exactly one fix at a time. It will not apply anything without your confirmation.

Each prompt you type starts a fresh diagnostic conversation -- the agent does not remember
earlier prompts in the same chat (the approval mode and mutation cap do carry over). Nothing is
remembered across `make chat` runs either; `backups/audit.log` is the record of what happened.

### Undoing a change by hand

```bash
make rollback RESOURCE=deployment/web
```

This restores the **most recent** snapshot in `backups/` for that resource using
`kubectl replace --force` (delete + recreate, so patched-in fields are really removed). The
snapshot is the state just *before* the last mutation -- so right after chaos it undoes the
chaos; right after an agent fix it undoes the fix (back to broken). For a fully clean
baseline, use `make cluster-down && make cluster-up`.

## Approval modes

- **`per-fix`** (default) -- every proposed fix is confirmed individually: the agent shows the
  command, a dry-run preview, and waits for `y/N`.
- **`plan`** -- the agent first proposes a full ordered plan; you approve it once, and its
  steps then apply without re-prompting.

Pick the mode at startup with `.venv/bin/python -m agent.cli --approval-mode plan` (`make chat`
always starts in `per-fix`), or switch mid-conversation with `/mode per-fix` / `/mode plan`.
Switching modes clears any previously approved plan. `/exit` (or Ctrl-D) quits.

## Guardrails

The agent cannot make things worse than the chaos scenario it's fixing, even if you rubber-stamp
every prompt:

- Every kubectl call is pinned to the `kind-k8s-troubleshoot` context, and every mutation to
  the `demo` namespace -- it can never touch another cluster or namespace, even by mistake.
- Read-only tool calls are limited to `get`, `describe`, `logs`, `events`, `top`, `explain`.
- Fix actions are limited to `patch`, `scale`, `restart` (deployments only), and `delete` --
  there is no "apply arbitrary manifest" action.
- Only `deployment`, `pod`, `service`, `configmap`, `secret`, and `replicaset` can be mutated --
  never a `namespace`, `node`, `persistentvolume`, `clusterrole(binding)`, or CRD.
- Scaling a workload to 0 replicas is never accepted as a "fix". Malformed patch JSON is
  rejected before it reaches kubectl.
- Every mutating action runs `--dry-run=server` first and shows you the result before it's
  real (except `restart`, which has no dry-run and changes no spec).
- Every applied mutation (except `restart`) is backed up to `backups/` first, so
  `make rollback` can undo it.
- A session is capped at 10 approved fixes -- past that, start a new `make chat`.
- Every applied or guardrail-blocked mutation is logged to `backups/audit.log` (JSON lines).

These are enforced in `agent/kube.py` and `agent/approval.py`, in code -- not just requested of
the model.

## Tests (TDD)

```bash
.venv/bin/python -m pytest tests/test_guardrails.py   # guardrails only -- no cluster, no API key, fast
make test               # guardrails + chaos scenarios -- needs Docker/kind, no API key
make test-integration   # chaos -> agent fixes it -> healthy again, against the live API (costs money)
```

`make test` creates (or reuses) the kind cluster, resets the demo app to a clean baseline
before each test, injects each chaos scenario, and asserts the expected broken state appears.
`make test-integration` additionally runs the real agent (auto-approving its fixes) against each
scenario and asserts the pods return to Running/Ready. **By default the test session deletes the
cluster when it finishes** -- set `KEEP_CLUSTER=1` (in `.env` or the shell) to leave it up.

## Other commands

```bash
make clean   # remove .venv, pytest/__pycache__ caches, and all backups + audit.log (keeps .env)
```

## Configuration notes

- Model: `claude-opus-5` (`agent/claude_agent.py::MODEL`), `max_tokens` 16000. The vendored
  skill text is prompt-cached (`cache_control: ephemeral`) since it's resent on every turn.
- Cluster/context/namespace names are constants in `agent/kube.py` and
  `scripts/setup_cluster.sh`; change them together.

## Extending the project (test-first)

- **New chaos scenario** -- add a `tests/test_chaos_scenarios.py` case asserting the expected
  broken state first, then add the scenario to `SCENARIOS` in `scripts/chaos.py`.
- **New fix action or resource kind** -- widen the allowlists in `agent/kube.py` and add a
  matching case to `tests/test_guardrails.py` in the same change; never widen a guardrail
  without a test proving the new boundary holds.
- **New agent capability / prompt change** -- add a `tests/test_agent_fixes.py` case first,
  then validate with `make test-integration` (live API, so run it sparingly).
- Keep `agent/kube.py` the only place that calls `kubectl`.
