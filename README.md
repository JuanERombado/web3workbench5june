# Web3 Bug Bounty Workbench

`web3bb` is a local-first workbench for turning a Web3 bounty target and a Foundry or Hardhat repo zip/folder into a repeatable audit run. It includes the CLI, a localhost browser UI, and an experimental Windows desktop GUI.

The MVP does not submit transactions, does not install tools for you, and does not claim vulnerabilities automatically. It detects what is already available, runs what it can, records evidence, and keeps hypotheses reviewable by a human.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

You can then run either:

```powershell
web3bb --help
web3bb-web
web3bb-gui
python -m workbench --help
python -m workbench.gui
```

The recommended UI is the local browser app:

```powershell
web3bb-web
```

It starts FastAPI on `http://127.0.0.1:8765` and opens that URL in your browser. It wraps the same local pipeline as the CLI: create runs, ingest repos, edit scope notes, run doctor, scan, import/gate/close hypotheses, and export review packets. It does not use AI agents, paid API calls, cloud sync, or OpenAI integration.

## Safety Rules

- Local analysis only.
- No live mainnet or testnet transactions.
- Forked simulation is allowed only through Foundry or Hardhat.
- Missing tools do not block the app.
- Scan output is evidence for manual review, not an automatic vulnerability claim.
- Every real finding should be mapped to scope, affected asset, and listed impact before report work.

## Example Workflow

```powershell
web3bb doctor
web3bb init --target-name axelar --program-url https://immunefi.com/bug-bounty/axelarnetwork/information/ --zip ./foundry-axelar.zip
web3bb ingest --run runs/axelar/<timestamp>
web3bb scope --run runs/axelar/<timestamp>
web3bb scan --run runs/axelar/<timestamp>
web3bb seed-axelar --run runs/axelar/<timestamp>
web3bb import-leads --run runs/axelar/<timestamp> --file leads.csv
web3bb export --run runs/axelar/<timestamp>
```

## Local Browser UI

Start the local web app with:

```powershell
web3bb-web
```

Open:

```text
http://127.0.0.1:8765
```

The browser UI includes:

- Dashboard for existing runs and review packet export.
- New Target page for target metadata and zip/folder source selection.
- Tool Doctor page for local tool detection and `tool_versions.json`.
- Scope page for editing `scope/scope_brief.md`.
- Scan page for generic, selected-profile, and all-profile scans.
- Hypotheses page for add/import/gate/close workflows.
- Known Issues page for local audit/report/docs/GitHub/scope/rejection/manual corpus indexing and duplicate checks.
- Review Packet page for `review_packet/chatgpt_packet.md` with copy-to-clipboard.
- Dashboard run overview with a one-click **Prepare Intelligence Packet** workflow.

The app binds to `127.0.0.1` only and uses the existing run folders plus SQLite DB under each run.

## Windows Desktop GUI

Start the app with:

```powershell
web3bb-gui
```

The PySide6 desktop app is still present but no longer the primary UI path.

## Commands

### Doctor

```powershell
web3bb doctor
```

Detects local tools and writes `tool_versions.json` in the current directory. Missing tools are printed with install suggestions only.

Checked tools: `forge`, `cast`, `anvil`, `slither`, `solc`, `solc-select`, `echidna`, `medusa`, `halmos`, `semgrep`, `surya`, `sol2uml`, `aderyn`, `jq`, `git`, `python`, `node`, `npm`, `rust`, and `cargo`.

### Init A Target

```powershell
web3bb init --target-name my-target --program-url https://example.com/bounty --zip ./repo.zip
```

Creates:

```text
runs/<target-name>/<timestamp>/
  input/
  scope/
  repo/
  tool-output/
  hypotheses/
  poc/
  reports/
  tracker/
  metadata/
```

The source zip is copied into `input/`, extracted into `repo/`, and run metadata plus a SQLite DB are created under `metadata/`.

### Ingest

```powershell
web3bb ingest --run runs/my-target/<timestamp>
```

Detects Foundry and Hardhat markers, Solidity versions, test folders, contract folders, likely core contracts, proxy contracts, token contracts, bridge/cross-chain contracts, and access-control/admin contracts.

Outputs:

- `metadata/project_detect.json`
- `metadata/contracts_index.json`
- `metadata/profiles.json`

### Scope

```powershell
web3bb scope --run runs/my-target/<timestamp> --resource-url https://example.com/docs
```

Creates a manually editable `scope/scope_brief.md` with sections for program URL, in-scope assets, impacts, exclusions, PoC requirements, testing restrictions, known issues, and notes.

### Scan

```powershell
web3bb scan --run runs/my-target/<timestamp>
```

Profile-aware Foundry scans:

```powershell
web3bb scan --run runs/my-target/<timestamp> --profile default
web3bb scan --run runs/my-target/<timestamp> --all-profiles
```

Runs available tools only:

- `forge build` when Foundry exists
- `forge test` when Foundry tests exist
- `slither` when available
- `semgrep` when available
- `aderyn` when available
- `surya` when available
- `sol2uml` when available

Each execution records command, start/end time, exit code, stdout path, stderr path, and a short parsed summary. One failing tool does not fail the whole scan.

When a profile is selected, Foundry and Slither executions receive `FOUNDRY_PROFILE=<profile>`. Profiled Slither uses `slither . --compile-force-framework foundry --json slither.json`.

### Add Or Update Hypotheses

Interactive add:

```powershell
web3bb add-hypothesis --run runs/my-target/<timestamp>
```

Flag-based add:

```powershell
web3bb add-hypothesis --run runs/my-target/<timestamp> --title "Oracle stale price" --contract OracleVault --function withdraw --hypothesis "Withdraw may use stale oracle data" --scope-mapping "In-scope vault" --impact-mapping "Direct loss of funds" --next-action "Build Foundry PoC"
```

List:

```powershell
web3bb list-hypotheses --run runs/my-target/<timestamp>
```

Update:

```powershell
web3bb update-hypothesis --run runs/my-target/<timestamp> --id H-001 --poc-status "PoC started" --validation-status "Needs reproduction" --notes "Forge test scaffold created"
```

Hypotheses are stored in `metadata/web3bb.sqlite` and mirrored as Markdown files under `hypotheses/`.

### Import Leads

```powershell
web3bb import-leads --run runs/my-target/<timestamp> --file leads.csv
web3bb import-leads --run runs/my-target/<timestamp> --file leads.md
web3bb export --run runs/my-target/<timestamp>
```

CSV imports support these columns:

```text
title,target,contract,function,hypothesis,source,tool_evidence,manual_evidence,scope_mapping,impact_mapping,poc_status,validation_status,known_issue_check,notes,next_action
```

Markdown imports use one or more `# Title` leads with simple sections:

```markdown
# Oracle stale price

## Contract
OracleVault

## Function
withdraw

## Hypothesis
Withdraw may use stale oracle data.

## Source
Manual review

## Evidence
Trace notes or tool evidence summary.

## Scope Mapping
In-scope vault.

## Impact Mapping
Direct loss of funds.

## Next Action
Build Foundry PoC.
```

Imported leads become normal hypotheses with `H-###` IDs and are mirrored into the tracker exports.

### Gate Or Close Hypotheses

```powershell
web3bb gate-hypothesis --run runs/my-target/<timestamp> --id H-001 --decision "Needs manual review" --notes "Scope mapping is incomplete"
web3bb close-hypothesis --run runs/my-target/<timestamp> --id H-001 --status "Rejected - No Impact" --reason "PoC showed no recoverable value"
```

Lifecycle statuses:

```text
New
Needs PoC
PoC Validated
Needs Scoped Asset
Rejected - No Impact
Rejected - Out of Scope
Rejected - Known Issue
Report Candidate
Submitted
```

### Known Issue Corpus

Add public known-issue sources or local notes before spending PoC time:

```powershell
web3bb known-add --run runs/my-target/<timestamp> --url https://example.com/audit --type audit --title "Example audit"
web3bb known-import-file --run runs/my-target/<timestamp> --file .\known-issues.md --type manual
web3bb known-list --run runs/my-target/<timestamp>
web3bb known-search --run runs/my-target/<timestamp> --query "vault withdraw stale oracle"
web3bb check-known --run runs/my-target/<timestamp> --id H-001
web3bb known-dedupe --run runs/my-target/<timestamp>
web3bb known-intel --run runs/my-target/<timestamp>
web3bb prepare-intel --run runs/my-target/<timestamp>
web3bb known-export --run runs/my-target/<timestamp>
```

Source types:

```text
audit
report
docs
github
scope
rejection
manual
```

The corpus is stored locally in the run SQLite DB using `known_sources`, `known_chunks`, and `known_hypothesis_links`. Text is chunked into searchable plain text. SQLite FTS5 is used when available; otherwise searches fall back to `LIKE`.

`known-add --url` fetches page text, stores the URL in `known_sources.url`, and indexes the fetched text into chunks. If fetching fails, the source is still added as a title/URL/note stub with `fetch_status` set to `FETCH_FAILED`.

Known sources use stable dedupe keys. URL sources key by normalized URL, while manual/rejection sources key by source type, normalized title, and text hash. `known-dedupe` merges old duplicates and prefers `fetch_status=fetched` over stubs.

Closed or rejected hypotheses are auto-indexed as `rejection` sources, so past dead ends become searchable negative-space context.

For Axelar runs, seed starter source notes with:

```powershell
web3bb seed-axelar-known --run runs/axelar/<timestamp>
```

This fetches and indexes the Immunefi pages, Code4rena reports, and Axelar ITS docs. Deployment/config repositories are added as URL stubs, and closed hypotheses are indexed as rejections.

`known-intel` writes deterministic local intelligence outputs:

- `known_intel/known_issue_intel.md`
- `known_intel/known_issue_terms.csv`

The report includes source inventory, top contracts/modules, top function names, known issue themes, prior rejected/closed hypotheses, overhunted areas, negative-space hints, and a query pack for ChatGPT.

`prepare-intel` runs the low-friction local workflow:

- seed target-specific known sources when recognized, currently Axelar
- index saved scope/program URLs for unknown targets
- dedupe known corpus
- export known corpus
- generate known issue intelligence
- export the ChatGPT review packet

It returns JSON with `review_packet`, `chatgpt_packet`, `known_intel`, `known_terms`, known source count, warnings, and errors.

### Seed Axelar Sample

```powershell
web3bb seed-axelar --run runs/axelar/<timestamp>
```

Adds the sample Axelar ITS express execution reimbursement mismatch hypothesis with known-issue risk and PoC next action.

### Export

```powershell
web3bb export --run runs/my-target/<timestamp>
web3bb export-review-packet --run runs/my-target/<timestamp>
```

Writes:

- `tracker/tracker.csv`
- `tracker/tracker.xlsx`
- `tracker/summary.md`
- `tracker/tool_versions.json`
- `tracker/run_summary.md`

`export-review-packet` creates `review_packet/` with scope notes, tracker exports, metadata, hypothesis markdown, selected tool output files, PoC notes, and `review_packet/chatgpt_packet.md`.

The review packet also includes known issue corpus exports:

- `tracker/known_sources.csv`
- `tracker/known_sources.md`

## Building A Windows EXE

PyInstaller packaging prep is included:

```powershell
.\scripts\build_exe.ps1
```

The script installs the editable package, installs PyInstaller, and builds a windowed executable under `dist\Web3 Bug Bounty Workbench\`.

## Bringing Results Back To ChatGPT Or A Manual Reviewer

Share these files from the run folder:

- `scope/scope_brief.md`
- `metadata/project_detect.json`
- `metadata/contracts_index.json`
- `tracker/summary.md`
- `tracker/run_summary.md`
- the specific `tool-output/<tool>/.../execution.json`, `stdout.txt`, and `stderr.txt` files relevant to a hypothesis
- any PoC files under `poc/`

Ask the reviewer to validate scope and impact mapping before treating any hypothesis as a reportable finding.

## Legacy Local Dashboard

The earlier mock dashboard commands still exist for compatibility:

```powershell
python -m workbench serve
python -m workbench init-target --name my-target --repo C:\path\to\repo --scope C:\path\to\scope.md
python -m workbench run-tool --target my-target --hypothesis 1 --tool foundry --command "mock:foundry:pass"
python -m workbench status --target my-target
```
