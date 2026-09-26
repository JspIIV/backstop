# Backstop

**SLA escrow that releases to the provider if uptime held, and refunds the buyer if it did not.** A settlement primitive for GenLayer Intelligent Contracts.

A service level agreement is only as good as what happens when it is broken. Off chain that is a support ticket and an argument. Backstop makes the SLA self-enforcing. A buyer escrows the payment for a service against a plain-language target and one public status page, over a window. When the window closes, anyone can settle it: the contract fetches the status page itself, tells the round what the target and the window were, and a round of GenLayer validators reads the page and decides whether the service met the target over that window. If it did, the escrow is released to the provider. If it did not, it is refunded to the buyer. Nobody opens a ticket; nobody argues.

The point is that neither side settles it. The provider cannot mark their own uptime, and the buyer cannot withhold payment for an outage that did not happen: the deciding evidence is the public status page the contract read, against the target the two agreed in advance.

## Interface

- **`open_sla(provider, service, status_url, target, window_end, escrow)`** — a buyer names the provider, the service, the status page it is judged at, a plain-words target, a window that must close in the future, and the escrowed amount. Bound to `gl.message.sender_address`. Starts `FUNDED`.
- **`settle(sla_id)`** — open to anybody, only **after** the window closes. The contract **fetches the status page** and a GenLayer round returns `MET` / `MISSED` / `UNCLEAR`. `MET` releases the escrow to the provider, `MISSED` refunds it to the buyer, `UNCLEAR` leaves it held to be settled again.
- **`balance(address)`** — units credited to an address: releases received as a provider, refunds received as a buyer.

Reads: `status(id)`, `get(id)`, `size()`, `page(start, count)`. It composes: a payment or settlement contract can read `status`/`get` and act on `RELEASED` or `REFUNDED`.

## The window is part of the settlement

The window is refused if it is not in the future; `settle` is refused before it closes, so an SLA cannot be judged mid-period; and it is injected into the round, so validators judge the uptime the page reports *over the agreed window*. Only `MET` releases and only `MISSED` refunds; the mapping lives in the contract (`_outcome`).

## Why it needs GenLayer

Whether a service met a target stated in words, over a period, is a judgement over a real status page that no ordinary contract can make and no single party should be trusted with. GenLayer validators each fetch the page and reach consensus on one categorical field; the escrow is moved by evidence, not by either party's say-so.

## What it refuses

- **Refuses a back-dated window** and a **buyer opening an SLA against themselves**.
- **Never settles mid-window.** `settle` is refused until the window closes.
- **Never moves on silence.** An unrelated, unreadable, or not-found status page is `UNCLEAR`; the escrow stays held and can be settled again.
- **Settles for good.** Once released or refunded, an SLA cannot be settled again.
- **Binds the buyer to the caller.** Nobody funds an escrow they did not agree to.

## Tests

`python tests/backstop_rules.py` — the escrow rules exercised through the real `open_sla()` and `settle()` on a Backstop built against a stub of the runtime, with time and the round verdict controlled. It proves back-dated windows and self-dealing are refused, an SLA cannot be settled before its window, `MET` releases to the provider and `MISSED` refunds the buyer, and an unreadable or not-found page moves nothing. 23 checks.

## Live

- **Contract (GenLayer Asimov):** `0x4512EDDc4F4A9B8D8DF708bbC7054bB8F7aa2967`
- Explorer: https://explorer-asimov.genlayer.com/address/0x4512EDDc4F4A9B8D8DF708bbC7054bB8F7aa2967

## Proven on Asimov

`scripts/prove.mjs`, `results/proved.json`. A buyer opens three SLAs against a provider:
- one whose status page (`docs/status-good.txt`) reports 99.98% uptime, no outage → **MET** → `RELEASED`, the escrow credited to the provider.
- one whose status page (`docs/status-bad.txt`) reports a six-hour outage and 96.2% uptime → **MISSED** → `REFUNDED`, the escrow credited back to the buyer.
- one whose status page is unreadable → `UNCLEAR`, the escrow stays `FUNDED`.
- and an SLA settled before its window closes is refused, leaving it `FUNDED`.

```
genlayer call 0x4512EDDc4F4A9B8D8DF708bbC7054bB8F7aa2967 size
genlayer call 0x4512EDDc4F4A9B8D8DF708bbC7054bB8F7aa2967 get --args '"0"'
```

## Where it stops, plainly

It reads what a public status page says, against the target the parties set: name a page a third party controls, and a target a page like it can actually answer. It settles the payment, not the damages: what a missed SLA costs beyond the escrow is left to the parties. On Asimov the escrow moves as a credited balance rather than native value.

## Licence

AGPL-3.0-or-later.
