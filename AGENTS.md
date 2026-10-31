# Rules for AI agents working in this repo

Read README.md first.

1. **Ask before any change in AWS** (`up`, `break`, `recover`, `reset`, `down`, `eval`, or any Terraform apply
   or destroy). `./check`, bare `./kopi`, `./kopi check`, `./kopi safe` and `terraform plan` are read-only.
2. **Never print or commit secrets or account IDs.** Terraform state holds the account ID and stays local.
3. **Prove changes by running them.** `./check` must say GREEN. Changes to the agent's prompt or model also need
   `./kopi eval` to pass 3/3, twice.
4. **Every state has a name and a next step.** A new way to fail goes in `kiosk/states.py` or names its own
   `Next:`, with a test.
5. **Keep it small.** The scenario file and `config.env` are the swap points. Add no command, service or
   abstraction a task does not need now.
