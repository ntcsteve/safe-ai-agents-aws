# Clumio add-on

Optional. A second way to answer "Can I get back?", using [Clumio](https://clumio.com) Backtrack. Run the main
[README](../../README.md) first, and stop before its step 7, because this needs the demo running.

Not using it? Delete this folder and the AWS demo still works. If you have connected it, run `./kopi down` first,
because this folder holds the only record of what to remove.

## What it adds

| | `./kopi recover` | `./kopi recover --clumio` |
|---|---|---|
| Restores from | The bucket's own S3 versions | The bucket's own S3 versions, indexed daily by Clumio |
| Choosing the version | This demo finds the last good one | A point in time, the last sync |
| Scale | One object, in this code | One object here. Clumio can roll back a prefix or a bucket |
| Record | A line in the demo's log | A Clumio task, with its start and end times |

**What this does not show.** Backtrack uses the same versions as `./kopi recover`, so it does not protect you if someone
deletes them. The policy here also keeps a daily SecureVault copy, in Clumio's air-gapped, immutable storage, and that
is what survives a stolen credential. This demo never restores from it.

## Before you start

- **Use a test account.** Connecting creates IAM roles that let Clumio read, write and delete objects in every S3 bucket
  in the account, not only the menus bucket. A rollback needs write access. So "only you can write" in the main README
  stops being true once this add-on is connected.
- **Allow for a wait.** Backtrack rolls back to a sync. In our tests the first sync ran within 5 minutes of connecting,
  but allow up to a day.
- **Clumio bills for what it protects.** Run `./kopi down` when you finish. SecureVault copies stay until their 30-day
  retention ends, so check how your Clumio plan bills them.

You need:

- The AWS demo running: `./kopi` says `HEALTHY`. If you just finished step 6 of the main README, it already does.
- A Clumio account. No account yet? See [Get started with Clumio on AWS](https://github.com/ntcsteve/get-started-clumio-aws#choose-your-path),
  or skip this add-on, because the main demo is complete without it.
- An API token, from the Clumio console under Administration, Access management, API tokens.
- Your API URL, which depends on your portal. See the [table of URLs](https://github.com/ntcsteve/get-started-clumio-aws#before-you-begin).

## Run it

### 1. Add your settings

There is one file to edit, and it is gitignored:

```
cp addons/clumio/clumio.env.example addons/clumio/clumio.env
```

| Key in `clumio.env` | Value |
|---|---|
| `CLUMIO_API_TOKEN` | Your API token |
| `CLUMIO_API_BASE_URL` | Your portal's API URL |
| `CLUMIO_OU_ID` | Optional. Leave blank unless your tenant uses organizational units |

Paste plain values, because `$(...)` is not run. Any other key is refused, so this file can never change which AWS
account a run uses. Shell exports and `terraform.tfvars` are ignored: `./kopi` reads this file and nothing else.

### 2. Connect

```
./kopi clumio
```

Terraform shows its plan and asks you to type `yes`, twice on a first connect. The first adds the connection and
Clumio's IAM roles. Then it waits for Clumio to discover the bucket, and the second adds the protection.

**Success: `Clumio is connected and protects the menus bucket`.**

### 3. Wait for the first sync

```
./kopi
```

Run this now and then. Don't run `./kopi break` until it says ready, because a sync taken while a menu is broken
captures the bad change.

**Success: `Clumio: ready`.**

### 4. Break it and roll it back

```
./kopi break
./kopi observe
./kopi recover --clumio
```

It shows the one menu it would restore. Type `ROLL BACK` to approve, or anything else to stop. Clumio took about
2 minutes in our test.

**Success: `RECOVERED`.** Open the task it names in the Clumio console to see the record.

### 5. Clean up

**Don't skip this. The demo and the protection cost money until you do.**

```
./kopi down
```

It removes the protection, the connection and Clumio's IAM roles first, then the demo. Type `yes` at both prompts. This
took about 6 minutes in our test.

**Success: `DOWN`.**

## If you get stuck

Every message names what happened and what to run next, and `./kopi` shows where you are. These need a little more:

| You see | It means | Do this |
|---|---|---|
| `CLUMIO API FAILED` | The token or URL was refused | Check `clumio.env` |
| `CLUMIO BUCKET NOT SEEN` | Clumio has not discovered the bucket yet. Nothing is broken | `./kopi clumio` again later |
| `CLUMIO POINT BAD` | The last sync captured the bad change | `./kopi recover` |
| `CLUMIO DISCONNECT FAILED` | Clumio or Terraform stopped the removal, and the demo is still running | Read the error, then `./kopi down`. If your tenant refuses, ask your Clumio admin |

## Going further

This add-on connects with defaults for a test account. For a company account, start with the
[full guide](https://github.com/ntcsteve/get-started-clumio-aws), which shows every permission first.
