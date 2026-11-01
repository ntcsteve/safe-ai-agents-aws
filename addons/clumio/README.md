# Clumio add-on

Optional. Finish the main [README](../../README.md) first. This adds a second way to recover, using
[Clumio](https://clumio.com) Backtrack. Delete this folder and the AWS demo still works.

**Allow for a wait.** Backtrack rolls back to a sync. In our test the first sync ran 5 minutes after connecting, but
allow up to a day.

## What it adds

| | `./kopi recover` | `./kopi recover --clumio` |
|---|---|---|
| Restores from | The bucket's own S3 versions | The bucket's own S3 versions, indexed daily by Clumio |
| Choosing the version | This demo finds the last good one | A point in time, the last sync |
| Scale | One object, in this code | One object here. Clumio can roll back a prefix or a bucket |
| Record | A line in the demo's log | A Clumio task, with its start and end times |

**Be clear about what this does not show.** Backtrack uses the same versions as `./kopi recover`, so it does not
protect you if someone deletes them. The policy here also keeps a daily SecureVault copy, in Clumio's air-gapped,
immutable storage, and that is what survives a stolen credential. This demo never restores from it.

## You need

- The AWS demo running: `./kopi` says `HEALTHY`.
- A Clumio account. No account yet? See [Get started with Clumio on AWS](https://github.com/ntcsteve/get-started-clumio-aws#choose-your-path).
- An API token, from the Clumio console under Administration, Access management, API tokens.
- Your API URL, which depends on your portal. See the [table of URLs](https://github.com/ntcsteve/get-started-clumio-aws#before-you-begin).

Clumio bills for what it protects. Run `./kopi down` when you finish.

## Set it up

There is one file to edit, and it is gitignored:

```
cp addons/clumio/clumio.env.example addons/clumio/clumio.env
```

| Key in `clumio.env` | Value |
|---|---|
| `CLUMIO_API_TOKEN` | Your API token |
| `CLUMIO_API_BASE_URL` | Your portal's API URL |
| `CLUMIO_OU_ID` | Optional. Leave blank unless your tenant uses organizational units |

Any other key is refused, so this file can never change which AWS account a run uses. Do not use shell exports or
a `terraform.tfvars`: `./kopi` reads this file and nothing else.

## Run it

**1. Connect**

```
./kopi clumio
```

Terraform shows its plan and asks you to type `yes`, twice on a first connect. The first adds the connection and
Clumio's IAM roles. Then it waits for Clumio to discover the bucket, then adds the protection.

**2. Wait for the first sync.** Run `./kopi` to check. Leave the menus `HEALTHY` until it says `Clumio: ready`, because
a sync taken while a menu is broken captures the bad change.

**3. Break it and roll it back**

```
./kopi break
./kopi observe
./kopi recover --clumio
```

It previews the one object, then asks for `ROLL BACK`. Clumio took about 2 minutes in our test.

**4. Remove everything**

```
./kopi down
```

It removes the protection, the connection and Clumio's IAM roles first, then the demo. Type `yes` at both prompts.
In our test this took about 6 minutes. SecureVault copies stay until their 30-day retention ends.

## If something goes wrong

| You see | It means | Do this |
|---|---|---|
| `CLUMIO NOT SET UP` | `clumio.env` is missing or wrong, or the bucket is not connected | Follow its `Next:` |
| `CLUMIO API FAILED` | The token or URL was refused | Check `clumio.env` |
| `CLUMIO BUCKET NOT SEEN` | Clumio has not discovered the bucket yet. Nothing is broken | `./kopi clumio` again later |
| `CLUMIO NO SYNC` | No sync has run yet | Wait, or use `./kopi recover` |
| `CLUMIO POINT BAD` | The last sync captured the bad change | `./kopi recover` |
| `WRONG SCOPE` | The preview is not exactly the one object | Stop. Nothing was rolled back |
| `PREVIEW ONLY` | You did not type `ROLL BACK` | `./kopi recover --clumio` |
| `CLUMIO TASK FAILED` | The rollback failed or timed out | Open the task in the Clumio console. Do not submit it again |
| `CLUMIO DISCONNECT FAILED` | Clumio or Terraform stopped the removal, and the demo was left running | Read the error. If your tenant refuses, ask your Clumio admin. Then `./kopi down` |

## Going further

This add-on connects with defaults for a test account. For a company account, start with the
[full guide](https://github.com/ntcsteve/get-started-clumio-aws), which shows every permission first.
