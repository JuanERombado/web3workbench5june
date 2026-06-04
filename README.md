# Hypothesis Workbench

Local-only dashboard and CLI for tracking Web3 bug bounty hypotheses and mock tool runs.

## Commands

```powershell
python -m workbench serve
python -m workbench init-target --name my-target --repo C:\path\to\repo --scope C:\path\to\scope.md
python -m workbench add-hypothesis --target my-target --file hypothesis.md
python -m workbench run-tool --target my-target --hypothesis 1 --tool foundry --command "mock:foundry:pass"
python -m workbench status --target my-target
```

The MVP does not execute real Foundry or Slither commands. It supports mock commands only and stores artifacts under `workbench_runs/`.
