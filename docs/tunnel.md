# Reaching this machine from Razorpay

Razorpay's servers deliver webhooks over the public internet. `127.0.0.1` is not on it, so without
a tunnel the provider can create orders against this project and never report an outcome — the
payment stops at `AUTHORIZED`, because `verify_callback` deliberately refuses to treat a browser
callback as capture evidence.

This is the setup that closes that gap. It was worked out on 26 August 2026 and lived only in a
chat log and on one laptop until now, which is the reason this file exists: a demo prerequisite
that is not written down is a demo prerequisite that disappears when the machine is rebuilt.

## Why cloudflared and not ngrok

ngrok was tried first and abandoned. Three separate problems: the winget package id is
`Ngrok.Ngrok` rather than the lowercase form the docs suggest, the version that installed was
3.3.1 and too old for the account, and Windows Defender quarantined the binary. Adding an
antivirus exclusion to make a demo convenience work is a bad trade on any project and a worse one
on this project, whose entire subject is not weakening a control because it is in the way.

`cloudflared` installed cleanly, is signed by Cloudflare, needs no account and no authtoken for a
quick tunnel, and has not been flagged.

## Setup

Installed already on the recording machine; this is the record for a rebuild.

```powershell
winget install --id Cloudflare.cloudflared -e
```

Start the tunnel and **leave the window open** — closing it kills the tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

It prints a URL like `https://some-random-words.trycloudflare.com`.

**Prove it reaches the API before involving Razorpay.** Open in a browser:

```
https://<your-tunnel>.trycloudflare.com/health
```

You want `{"status":"ok"}`. If you get anything else, stop here — there is no point debugging
signatures against a tunnel that is not connected.

## Registering the webhook

Razorpay dashboard → **Account & Settings → Webhooks → Add New Webhook**:

| Field | Value |
|---|---|
| Webhook URL | `https://<your-tunnel>.trycloudflare.com/api/v1/razorpay/webhook` |
| Secret | the `RAZORPAY_WEBHOOK_SECRET` value from `.env` |
| Active events | `payment.authorized`, `payment.captured`, `payment.failed` |

The secret must match `.env` exactly. A mismatch gives `RAZORPAY_WEBHOOK_SIGNATURE_INVALID`, which
is the handler working correctly and is indistinguishable, from the dashboard, from being broken.

## The part that catches people

**A quick tunnel's URL changes every time you restart it.** The dashboard entry from a previous
session is stale the moment the tunnel comes back up, and stale means Razorpay posts into nothing,
retries, and emails a delivery alert for each attempt. Nothing is damaged; it is noise that looks
like failure.

So: start the tunnel once, update the dashboard URL once, and keep that window open for the whole
recording session.

## When the tunnel is not up

`python -m agent.capture` signs the two lifecycle events locally with the same
`RAZORPAY_WEBHOOK_SECRET` and posts them, carrying a paid order to `CAPTURED` without any public
URL. It prints its own provenance every run, because the events did not come from Razorpay and a
viewer must not be left thinking they did.

The two are not equivalent and the difference is worth stating plainly:

| | Order | Webhook delivery |
|---|---|---|
| Tunnel up | Real, provider-originated | **Real, provider-originated** |
| `agent.capture` | Real, provider-originated | Locally signed by this project |

The tunnel is strictly better evidence. `agent.capture` is what makes the demo survive a tunnel
that will not come up five minutes before recording.

## What is still not proven

`docs/evidence/m3-webhook-lifecycle.json` records a full `AUTHORIZED → PROVIDER_PENDING →
CAPTURED` lifecycle whose payloads were signed locally. A run where **Razorpay itself** delivered
the events has been set up for but never preserved. When it happens, save it separately — the
evidence README has said so since 25 August and it is still true.
