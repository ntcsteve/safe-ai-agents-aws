# Safe AI agents on AWS

See what it takes to let an AI agent near production data. In about 30 minutes you deploy a small menu
site, break it with a bad change, watch an agent investigate it without being able to touch anything, and
put it right with one approved restore. Then you remove it all.

**Lost at any point? Run `./kopi`. It tells you where you are and what to run next.**

```
You (terminal)                        Customers (browser)
   |                                        ^
   | break / recover / reset                | reads the menu
   | (only you can write)                   |
   v                                        |
S3 bucket, versioned  ---------------> CloudFront
   ^   menus/stall-kopi.json    <- gets broken
   |   menus/stall-seduh.json   <- must stay untouched
   | read only
Lambda (2 tools, each logs a receipt)
   ^
AgentCore Gateway
   ^
AgentCore agent (Claude): investigates, can never write
```

Three promises, each one proven by a command:

| Promise | Means | Proven by |
|---|---|---|
| Safe | No role the agent reaches has permission to write | `./kopi safe` |
| Observable | Every tool call leaves a receipt the agent cannot edit | `./kopi observe` |
| Recoverable | A person approves a restore of exactly one object | `./kopi recover` |

## Before you start

**This is a learning demo, not a production pattern. Use a test AWS account.** It leaves out what
production needs:

- The agent runs in public network mode, not inside your VPC.
- Data is not encrypted with your own KMS key.
- CloudTrail data events are off, so S3 reads are not audited separately.
- Receipts are matched to tool calls by time and tool name, not by a correlation ID.
- The menu page is public to anyone with its URL.
- Terraform state is a local file. Do not delete `infra/terraform.tfstate` until after `./kopi down`.
- Your terminal holds write access. The agent never gets those credentials.

**Cost.** A full run should cost a few cents to a few tens of cents while it runs. The author's whole of
October 2026, 32 recorded agent runs plus other testing, cost under US$4 for AgentCore, Claude and S3
together. CloudFront is not in that figure. Your bill may differ, so run `./kopi down` when you finish.

**You need:**

- A test AWS account, with credentials for an administrator role in your terminal.
- Access to Claude in Amazon Bedrock in `ap-southeast-2`. `./kopi up` checks this first.
- [Terraform](https://developer.hashicorp.com/terraform/install) 1.10 or newer.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which runs `./kopi` and installs its Python packages.

## Run it

**1. Deploy** (about 5 minutes)

```
./kopi up
```

It checks your setup, then shows the account and region it will use, then Terraform's plan. Check the
account, then type `yes`. The plan shows your account ID, so don't share your screen during this step. It ends with `HEALTHY` and a URL. Open the URL: every price is correct.

**2. Safe: can the agent touch the data?**

```
./kopi safe
```

AWS's IAM simulator checks each role the agent reaches. Expect `BOUNDARY HOLDS`. This reads the roles' own
policies, so it proves what they allow. It does not cover organization-wide policies, and it is not a live
call being refused.

**3. Break it**

```
./kopi break
```

A change that was approved, and wrong: three prices go up 100 times. Refresh the page and they are marked.
The clock starts against a 10-minute tolerance.

**4. Observable: what happened, and what did the agent do?**

```
./kopi observe
```

The agent reports facts, a hypothesis, gaps and a proposed recovery. Then come its receipts, so you can
check what it did as well as what it said. S3 version IDs, read before and after, prove it changed nothing.

**5. Recoverable: put it right**

```
./kopi recover
```

It previews the one object it would restore. Type `ROLL BACK` to approve, or anything else to stop. Expect
`HEALTHY`, and the promo on the other menu is still there.

**6. Remove everything** (about 4 minutes)

```
./kopi down
```

Type `yes`. To go round again without removing anything, run `./kopi reset`, then start from step 3.

## If something goes wrong

Every command names its state and what to do next. `./kopi` shows them at any time.

| You see | It means | Do this |
|---|---|---|
| `CONFIG INVALID` | A value in `config.env` is wrong | Fix the value it names |
| `SCENARIO INVALID` | A scenario file has a mistake | Fix the field it names |
| `PREFLIGHT FAILED` | A tool, your credentials or model access is missing | Follow its `Next:` |
| `NOT DEPLOYED` | Nothing is running | `./kopi up` |
| `CONFIG NOT APPLIED` | You edited a file since the last deploy | `./kopi up` |
| `REGION CHANGED` | `config.env` names a different region from the stack | `./kopi down`, then `./kopi up` |
| `NOT NOW` | That command does not fit the current state | Follow its `Next:` |
| `AGENT UNAVAILABLE` | The model or harness failed | `observe` shows the same evidence without the agent |
| `RECEIPTS INCOMPLETE` | CloudWatch has not stored them all yet | `./kopi observe` in a minute |
| `RECEIPTS MISMATCH` | A tool ran that the agent did not report | Trust the receipts |
| `AGENT CHANGED DATA` | S3 changed while the agent ran. This should be impossible | `./kopi reset`, then find out what wrote it |
| `NO GOOD VERSION` | No healthy version in the recent history | `./kopi reset` |
| `WRONG SCOPE` | More than the one menu is broken | `./kopi reset` |
| `WRONG SCOPE AFTER RESTORE` | S3 shows another menu changed too | `./kopi reset`, then find out why |
| `PREVIEW ONLY` | You did not type `ROLL BACK`, so nothing was written | `./kopi recover` |
| `BOUNDARY BROKEN` | A role can do more than it should | Fix `infra/`, then `./kopi up` |
| `TERRAFORM FAILED` during `down` | Usually a scenario file that is not valid JSON, which Terraform reads too | Fix the file it names, then `./kopi down` |

## Make it yours

There is one rule: **edit a file, then run `./kopi up`.** Until you do, other commands refuse and say what
changed. These files hold everything you would change:

| To change | Edit | Effect |
|---|---|---|
| The story: menus, prices, the bad change, the tolerance | `scenario/kopi.json`, or a new file there | Menus are reseeded |
| Region or model | `config.env` | Region change: `./kopi down` first, then `./kopi up` |
| The agent's instructions | `infra/investigator-prompt.md` | Then run `./kopi eval` |

`./kopi eval` runs three scripted checks on the agent. Run it after changing the prompt or the model. It judges
the agent by what AWS shows, not by its wording, so it cannot prove an answer is honest. For example, it would
not catch an agent saying it fixed something when it had not. S3 would still show that nothing changed.

Only `ap-southeast-2` has been tested. Another region needs AgentCore and a Claude inference profile there.
A wrong `MODEL` is refused before anything is created.

## Why S3 versions alone are not enough

`./kopi recover` restores from S3 versions, which is simple and works here. But those versions live in the
same bucket and the same account as the data. Anyone with delete rights can remove them, so this protects
you from a bad change, not from a stolen credential or a deleted bucket.

For a copy outside the account, see [Get started with Clumio on AWS](https://github.com/ntcsteve/get-started-clumio-aws).

## How it is built

```
scenario/kopi.json   the story: menus, the bad change, the good change, the tolerance
config.env           where it runs: region, model, scenario
kopi                 the one command
kiosk/states.py      what is true now, and what each command may do
kiosk/menu.py        what a correct menu is, and how the bad change breaks it
kiosk/investigation.py   the agent's two read-only tools, as pure logic
kiosk/recovery.py    which version to restore, and the scope gate
kiosk/commands.py    safe, break, observe, recover, eval
kiosk/s3_store.py, agent_client.py, tool_lambda.py, preflight.py, config.py   the edges
infra/               AWS, in Terraform. investigator-prompt.md is the agent's instructions
site/                the menu page
tests/               what ./check runs
```

`./check` runs every test and Terraform check. It changes nothing in AWS and needs Node 20 or newer too.

## Words used here

| Word | Means |
|---|---|
| stall | One menu, stored as one S3 object |
| MNT-142 | The bad change: three prices multiplied by 100 |
| PROMO-7 | A good change on the other stall. The restore must leave it alone |
| harness | The AgentCore agent. It has two read-only tools and no shell |
| receipt | A log line the tool writes for each call, outside the agent's reach |
| tolerance | How long a wrong menu is acceptable: 10 minutes. The clock measures against it |

## Status

Tested on 31 October 2026 in one AWS account, in `ap-southeast-2`, with Terraform 1.15, AWS provider 6.70.0
and `au.anthropic.claude-sonnet-4-6`. Never yet tested in a brand-new account.

This is a personal learning project, offered as is, with no support. It is not an official AWS or Commvault
product. Apache-2.0: see [LICENSE](LICENSE).
