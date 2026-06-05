# Web3 Bug Bounty CLI Workbench

`web3bb` is a local-first CLI for turning a Web3 bounty target and a Foundry or Hardhat repo zip into a repeatable audit run.

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
python -m workbench --help
```

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
web3bb export --run runs/axelar/<timestamp>
```

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

Runs available tools only:

- `forge build` when Foundry exists
- `forge test` when Foundry tests exist
- `slither` when available
- `semgrep` when available
- `aderyn` when available
- `surya` when available
- `sol2uml` when available

Each execution records command, start/end time, exit code, stdout path, stderr path, and a short parsed summary. One failing tool does not fail the whole scan.

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

### Seed Axelar Sample

```powershell
web3bb seed-axelar --run runs/axelar/<timestamp>
```

Adds the sample Axelar ITS express execution reimbursement mismatch hypothesis with known-issue risk and PoC next action.

### Export

```powershell
web3bb export --run runs/my-target/<timestamp>
```

Writes:

- `tracker/tracker.csv`
- `tracker/tracker.xlsx`
- `tracker/summary.md`
- `tracker/tool_versions.json`
- `tracker/run_summary.md`

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
