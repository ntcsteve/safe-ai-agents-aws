# Rules for AI agents working in this repo

Read README.md first.

1. **Ask before any change in AWS or Clumio** (`up`, `clumio`, `break`, `recover`, `reset`, `down`, `eval`, or any Terraform apply
   or destroy). `./check`, bare `./kopi`, `./kopi check`, `./kopi safe` and `terraform plan` are read-only.
2. **Never print or commit secrets or account IDs.** Terraform state holds the account ID and stays local.
   Clumio credentials live only in `addons/clumio/clumio.env`, which is gitignored. Everything Clumio stays in
   that folder, including its tests.
3. **Prove changes by running them.** `./check` must say GREEN. Changes to the agent's prompt or model also need
   `./kopi eval` to pass 3/3, twice.
4. **Every state has a name and a next step.** A new way to fail goes in `kiosk/states.py` or names its own
   `Next:`, with a test.
5. **Judge the agent by what AWS shows, never by what it says.** Words are for people. Tests and printed claims
   rest on IAM, S3 version IDs or receipts.
6. **Keep it small.** The scenario file and `config.env` are the swap points. Add no command, service or
   abstraction a task does not need now.
