<div align="center">

<img src="docs/assets/magicite-logo.png" alt="Magicite" width="800">

### Reusable skills for your AI agents.

Magicite helps your AI agent find and load useful project instructions through
a local MCP server. Keep skills with your project, review what the agent loads,
and let your host execute the instructions.

[![CI](https://github.com/Rynaro/magicite/actions/workflows/ci.yml/badge.svg)](https://github.com/Rynaro/magicite/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-7c3aed)](https://github.com/Rynaro/magicite/releases/tag/v1.0.0)
[![PyPI](https://img.shields.io/pypi/v/magicite)](https://pypi.org/project/magicite/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-stdio-0f766e)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/github/license/Rynaro/magicite)](LICENSE)

[What it does](#what-magicite-does) · [Get started](#get-started) ·
[For skill authors](#write-and-review-a-skill) · [For LLM builders](#for-llm-builders-and-enthusiasts) ·
[Docs](#documentation)

</div>

---

## What Magicite does

As an LLM project grows, useful instructions spread across prompt files,
agents, and personal notes. Magicite gives those instructions a local registry
and an MCP interface. The host can ask for a route, inspect the decision, and
load a selected skill's body with current content and policy digests.

This is useful when you want to:

- Keep reusable task instructions in versionable files alongside a project.
- Give an LLM host a structured way to find and load relevant instructions.
- Review the exact content before trusting an imported skill.
- Record use and outcomes, then inspect proposed lifecycle changes.
- Experiment with skill composition while keeping stable routing as the default.

Magicite is a routing and governance layer. It does not execute skill
instructions, grant the host new permissions, or guarantee better answers.
The agent host remains responsible for interpreting instructions and for its
own tools, permissions, and sandbox.

## Get started

Magicite 1.0.0 is available on [PyPI](https://pypi.org/project/magicite/).
It supports Python 3.11 and 3.12. Install it in a permanent environment that
your MCP host can run:

```bash
python3.12 -m venv "$HOME/.local/share/magicite/venv"
"$HOME/.local/share/magicite/venv/bin/python" -m pip install magicite==1.0.0
"$HOME/.local/share/magicite/venv/bin/magicite" --version
```

For Python 3.11, substitute `python3.11`. Keep the environment at a stable
absolute path; your host configuration will call its `magicite` executable.
For artifact and provenance verification, see [Install](docs/install.md).

### Before connecting a project

The supported v1 operating profile is **Linux with protected project custody**.
An administrator sets up a separate custodian identity and protected authority
for the project before the project connects. This keeps edits in the project
from silently granting trust or changing protected policy. It is a prerequisite
for use, not something `magicite init` installs for you.

1. Ask a Linux administrator to follow [Setup](docs/setup.md) for the project.
   It provisions custody and initializes the project's journal.
2. In that enrolled project, fetch the model and connect Claude Code:

   ```bash
   cd /absolute/path/to/project
   /absolute/path/to/magicite/venv/bin/magicite init --host claude --fetch-model
   ```

   `--fetch-model` authorizes downloading the default embedding model. Omit
   this flag if the model is already cached for the same client account and
   environment. You can also provide the canonical project path with
   `--project-root /absolute/path/to/project`.
3. Reconnect Claude Code, then follow [Quick Setup](docs/quick-setup.md) to
   register a skill, review it, and try a route.

`init` checks existing custody, runs a local model inference, merges the
Magicite MCP entry and its marked instruction block, and checks the local MCP
connection. It does not provision custody, approve skills, or promote drafts.
An optional `--fetch-model` flag authorizes model download only. Keep the
default human-review mode; approval of an imported draft does not by itself
make that draft eligible for routing.

### Your first route

After [Quick Setup](docs/quick-setup.md) has connected the host and you have a
reviewed, eligible skill, ask your assistant something like:

> Use Magicite to find instructions for reviewing this change. Show me the
> route result, then load the selected skill using the returned content and
> policy digests before following it.

Magicite may select a skill or abstain. Review the selection and the loaded
content in your host before it acts. Abstention is a valid outcome when no
eligible skill is a good fit.

## Write and review a skill

Engrams are human-readable Markdown files. In `engram/1.0`, the front matter
names the skill and describes what it does, when it applies, and which typed
capabilities it requires or produces. The body contains the procedure. Start
with this [complete v1 example](tests/fixtures/engram-v1/positive/sample-host-tooling.egr.md)
and the [format guide](docs/04-engram-format.md) when authoring your own.
The router binds its v1 routing view to a digest of the body. Finish edits
before reviewing the current content digest; a file's own trust fields do not
approve it.

The newcomer path in [Quick Setup](docs/quick-setup.md) shows registration,
human digest review, approval, routing, and loading. It also explains importing
existing `SKILL.md` files. Those imports can start as drafts and need lifecycle
preparation after approval.

## For LLM builders and enthusiasts

### Routing and learning

The stable `dense-v1` policy ranks eligible skills using their canonical
routing content and applies typed eligibility, contraindication, and bounded
composition rules. It is the default and remains nonadaptive: learned weights
and Dream consolidation do not silently change its ordering.

Magicite also records session-scoped use and outcome signals. Its resumable
**Dream** workflow consolidates durable skill and relationship changes through
reviewable lifecycle operations. This makes behavior inspectable; it does not
establish that graph composition or learning improves retrieval quality.
Adaptive policies are experimental and are not the stable default.

### MCP surface

The 16 MCP tools fall into five practical groups:

| Use | Tools |
|---|---|
| Retrieve and inspect | `route`, `load_skill_body`, `introspect`, `flag_dead` |
| Record signals | `signal_use`, `signal_outcome`, `session_end` |
| Manage the registry | `register`, `sync`, `checkpoint`, `export` |
| Govern lifecycle | `nucleate`, `sharpen`, `promote`, `archive` |
| Consolidate evidence | `consolidate` |

See the generated [runtime reference](docs/generated/runtime-reference.json)
for tool schemas, and [Protocol and signals](docs/05-protocol-and-signals.md)
for the behavior behind them. The MCP server uses stdio; host-specific hooks
can improve signal capture but are optional.

At the protocol level, a typical client first calls `route` with a query and
session ID. If the result is `selected`, it takes the selected skill name,
content digest, and policy digest from that response and passes them to
`load_skill_body` at level `L2`. This binds the loaded procedure to the
decision the client just inspected. The exact request fields and stale-decision
handling are shown in [Quick Setup](docs/quick-setup.md#5-route-then-read-the-selected-body).

### Local state and trust boundary

The project stores portable engrams and a local SQLite index under
`.magicite/`. The index can be rebuilt from source files, but it is only one
part of project state: approvals, evidence, policy, protected trust authority,
and custody history have separate recovery requirements. Do not treat a copy
of the database or the engram files alone as a complete backup; see
[Operations](docs/operations.md).

Autonomous approval is off by default. Keep it off and review imported content
by digest. Magicite's MCP tools do not expose arbitrary shell, SQL, or
filesystem access, and skill bodies remain instructions for the host rather
than code Magicite runs. See [Trust and governance](docs/06-trust-governance-lifecycle.md)
for the detailed model.

## Release status

Magicite **1.0.0** is published on [PyPI](https://pypi.org/project/magicite/),
as wheel and source artifacts on [GitHub Releases](https://github.com/Rynaro/magicite/releases/tag/v1.0.0),
and as a signed multi-architecture OCI image. The release delivers the local
MCP workflow, protected lifecycle controls, and recovery mechanisms described
above.

The release does not claim improved retrieval quality, a validated scale or
performance envelope, production adoption, or independent security
certification. These remain qualification work; see the
[release record](docs/releases/1.0.0.md) and
[post-v1 issues](https://github.com/Rynaro/magicite/issues?q=label%3Astatus%3Apost-v1).
Production macOS custody is also unqualified; use the documented Linux profile.

The OCI image is a distribution option, not a custody installer. For signature,
digest, and deployment guidance, use the [release page](https://github.com/Rynaro/magicite/releases/tag/v1.0.0)
and [Operations](docs/operations.md).

## Documentation

| If you want to… | Read… |
|---|---|
| Install the published package | [Install](docs/install.md) |
| Provision a project before use | [Setup](docs/setup.md) |
| Connect a host and try a skill | [Quick Setup](docs/quick-setup.md) |
| Understand the system design | [Architecture](docs/02-architecture.md) |
| Learn routing, signals, and Dream | [Learning model](docs/03-learning-model.md) |
| Author or migrate engrams | [Engram format](docs/04-engram-format.md) |
| Read MCP contracts and signal tiers | [Protocol and signals](docs/05-protocol-and-signals.md) |
| Review trust, approvals, and recovery | [Trust and governance](docs/06-trust-governance-lifecycle.md) and [Operations](docs/operations.md) |
| Understand evidence and open hypotheses | [Evaluation](docs/07-evaluation-and-observability.md) and [Authority](docs/AUTHORITY.md) |
| Find the full document map | [Documentation index](docs/README.md) |

## Develop Magicite

This repository uses [uv](https://docs.astral.sh/uv/) for development:

```bash
uv sync --all-extras
uv run magicite tools
```

For project conventions and the generated documentation contract, start with
[AGENTS.md](AGENTS.md) and [EIDOLONS.md](EIDOLONS.md). The [changelog](CHANGELOG.md)
and [GitHub Releases](https://github.com/Rynaro/magicite/releases) track changes.

## License

Magicite is available under the [Apache License 2.0](LICENSE).
