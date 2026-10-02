# Backstop

**SLA escrow that holds the buyer's real deposit and releases it to the provider if uptime held, or refunds the buyer if it did not.** A settlement primitive for GenLayer Intelligent Contracts.

A service level agreement is only as good as what happens when it is broken. Off chain that is a support ticket and an argument. Backstop makes the SLA self-enforcing, with real money behind it. A buyer opens an SLA by depositing the payment as native value against a plain-language target and one public status page, over a window. The contract holds the deposit in escrow. When the window closes, anyone can settle it: the contract fetches the status page itself, tells the round the target and the window, and a round of GenLayer validators reads the page and decides whether the service met the target over that window. If it did, the deposit is released to the provider. If it did not, it is refunded to the buyer. Nobody opens a ticket; nobody argues; and neither side settles it themselves.

## Interface

- **`open_sla(provider, service, status_url, target, window_end)`** is **payable**. The buyer deposits the escrow as native value; the escrow is exactly the value sent, and the contract holds it. Bound to `gl.message.sender_address`. Starts `FUNDED`. The window must close in the future, and a buyer cannot open an SLA against themselves. It never raises once value is attached: on any invalid input the deposit is paid straight back.
- **`settle(sla_id)`** is open to anybody, only **after** the window closes. The contract **fetches the status page** and a GenLayer round returns `MET` / `MISSED` / `UNCLEAR`. `MET` **transfers the deposit to the provider**, `MISSED` **refunds it to the buyer**, each exactly once; `UNCLEAR` leaves it held, to be settled again.

Reads: `status(id)`, `get(id)`, `size()`, `page(start, count)`. It composes: a payment or settlement contract can read `status`/`get` and act on `RELEASED` or `REFUNDED`.

## The escrow path is real

- **Opening deposits the money.** `open_sla` is payable; the escrow is the native value the buyer sends and the contract genuinely holds it. A declared amount with no deposit cannot exist.
- **Settlement moves the real deposit, once.** On `MET` the contract transfers the deposit to the provider via `emit_transfer`; on `MISSED` it refunds the buyer; the terminal `RELEASED`/`REFUNDED` state means each pays exactly once.
- **A refused open never strands the buyer.** An invalid open pays the deposit straight back.

## The window is part of the settlement

The window is refused if it is not in the future; `settle` is refused before it closes, so an SLA cannot be judged mid-period; and it is injected into the round, so validators judge the uptime the page reports over the agreed window. Only `MET` releases and only `MISSED` refunds; the mapping lives in the contract (`_outcome`).

## Why it needs GenLayer

Whether a service met a target stated in words, over a period, is a judgement over a real status page that no ordinary contract can make and no single party should be trusted with. GenLayer validators each fetch the page and reach consensus on one categorical field; the deposit is moved by evidence, not by either party's say-so.

## Tests

`python tests/backstop_rules.py` — the escrow rules, with a real deposit, exercised through the real `open_sla()` and `settle()` on a Backstop built against a stub of the runtime, with time, the round verdict, and the native value controlled, recording every transfer. It proves opening escrows the value deposited, `MET` transfers that deposit to the provider and `MISSED` refunds the buyer (each once), an invalid open returns the deposit, and an unreadable page moves nothing. 23 checks.

## Live and proven

- **On GenLayer Studio (where native value moves):** `0xbFadB1CC427af612582614B69605BfDEf177E23F`
- **On GenLayer Asimov (identical source):** `0x2076111d1D77d69a16c2d180ebfCfF51A28386c1` ([explorer](https://explorer-asimov.genlayer.com/address/0x2076111d1D77d69a16c2d180ebfCfF51A28386c1))

`scripts/prove-studio.mjs`, `results/proved-studio.json`. On Studio, a buyer opens two SLAs, depositing **1 GEN** of escrow each:
- opening the two SLAs moves 2 GEN into the contract, held in escrow.
- the SLA whose page (`docs/status-good.txt`) reports 99.98% uptime and no outage is settled **MET**: the **provider's on-chain balance rises by exactly 1 GEN**.
- the SLA whose page (`docs/status-bad.txt`) reports a six-hour outage is settled **MISSED**: the **buyer's on-chain balance rises by exactly 1 GEN** (refunded).
- both deposits leave the contract, which returns to its opening balance.

The value path is demonstrated on Studio because native value transfers do not execute on the Asimov testnet (measured: the contract's bookkeeping is correct but no balance moves). The source deployed to both networks is identical to this repository.

## Where it stops, plainly

It reads what a public status page says, against the target the parties set: name a page a third party controls, and a target a page like it can actually answer. It settles the payment, not the damages: what a missed SLA costs beyond the deposit is left to the parties.

## Licence

AGPL-3.0-or-later.
