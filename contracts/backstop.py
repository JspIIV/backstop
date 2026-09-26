# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""Backstop: SLA escrow that releases to the provider if uptime held, and refunds the buyer if it did not.

A service level agreement is only as good as what happens when it is broken. Off chain
that is a support ticket and an argument. Backstop makes the SLA self-enforcing. A buyer
escrows the payment for a service against a plain-language target and one public status
page, over a window. When the window closes, anyone can settle it: the contract fetches
the status page itself, tells the round what the target and the window were, and a round
of GenLayer validators reads the page and decides whether the service met the target over
that window. If it did, the escrow is released to the provider. If it did not, it is
refunded to the buyer. Nobody opens a ticket; nobody argues.

The point is that neither side settles it. The provider cannot mark their own uptime, and
the buyer cannot withhold payment for an outage that did not happen: the deciding evidence
is the public status page the contract read, against the target the two agreed in advance.

## What it settles

    MET       the page shows the target was met over the window -> the escrow is RELEASED to the provider
    MISSED    the page shows the target was not met            -> the escrow is REFUNDED to the buyer
    UNCLEAR   the page could not be read, or does not settle it -> the escrow stays held, to be settled again

Only these move the money, and only after the window has closed: an SLA cannot be settled
mid-window, before there is a full period to judge.

## What it refuses

The provider, the target, the status page, the window and the escrow are fixed when the
SLA is opened and cannot be edited. A buyer cannot open an SLA against themselves. It
cannot be settled before its window closes, and once released or refunded it is settled for
good. The buyer is bound to the opener, so nobody funds an escrow they did not agree to.

## Where it stops, plainly

It reads what a public status page says, against the target the parties set: name a page a
third party controls, and a target a page like it can actually answer. It settles the
payment, not the damages: what a missed SLA costs beyond the escrow is left to the parties.
On Asimov the escrow moves as a credited balance rather than native value.
"""

from genlayer import *
import json

MET = "MET"
MISSED = "MISSED"
UNCLEAR = "UNCLEAR"
VERDICTS = (MET, MISSED, UNCLEAR)

FUNDED = "FUNDED"
RELEASED = "RELEASED"
REFUNDED = "REFUNDED"

MAX_SERVICE = 200
MAX_TARGET = 300
MAX_URL = 300
MAX_PAGE = 6000
MAX_REASON = 300
MAX_QUOTE = 300
MAX_AMOUNT = 10 ** 30

FETCH_FAILED = "__FETCH_FAILED__"


def _now() -> int:
    from datetime import datetime, timezone
    return int(datetime.now(timezone.utc).timestamp())


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _iso(ts: int) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


def _clip(text: str, limit: int) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[:limit] + " [...]"


def _whole(value) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return -1


def _amount(value):
    n = _whole(value)
    if n <= 0 or n > MAX_AMOUNT:
        return None
    return n


def _addr(value) -> str:
    text = str(value).strip().lower()
    if not text.startswith("0x") or len(text) != 42:
        return ""
    for character in text[2:]:
        if character not in "0123456789abcdef":
            return ""
    return text


def _url_ok(url: str) -> bool:
    text = str(url).strip()
    if len(text) < 8 or len(text) > MAX_URL or " " in text:
        return False
    return text.startswith("https://") or text.startswith("http://")


def _valid_window(value, now: int):
    when = _whole(value)
    if when <= 0:
        return False, "give the window close as a unix timestamp in the future"
    if when <= now:
        return False, "the coverage window must close in the future"
    return True, when


def _outcome(verdict: str):
    """Map a settlement verdict to the SLA's next status and who the escrow moves to.

    Kept pure so the release/refund rule can be tested on its own. Returns
    (status, beneficiary) where beneficiary is "provider", "buyer" or "" (held).
    """
    if verdict == MET:
        return RELEASED, "provider"
    if verdict == MISSED:
        return REFUNDED, "buyer"
    return FUNDED, ""


def _field(raw: str, name: str, allowed, fallback: str) -> str:
    try:
        text = str(raw).strip()
        obj = json.loads(text[text.index("{"):text.rindex("}") + 1])
        if isinstance(obj, dict):
            said = str(obj.get(name, "")).strip().upper()
            return said if said in allowed else fallback
    except Exception:
        pass
    return fallback


def _text_field(raw: str, name: str, limit: int) -> str:
    try:
        text = str(raw).strip()
        obj = json.loads(text[text.index("{"):text.rindex("}") + 1])
        if isinstance(obj, dict):
            return _clip(str(obj.get(name, "")), limit)
    except Exception:
        pass
    return ""


def _task(service: str, target: str, window_from: str, window_to: str, page: str) -> str:
    return f"""A service level agreement names a target and a public status page. Read the page and
decide whether the service met the target over the coverage window.

THE SERVICE:
{service}

THE TARGET (the SLA that had to be met over the window):
{target}

THE COVERAGE WINDOW:
from {window_from} to {window_to}

THE STATUS PAGE:
{page}

Decide one of:
  {MET} the page shows the target was met across the window
  {MISSED} the page was read and shows the target was not met (an outage, or figures below the target)
  {UNCLEAR} the page could not be read, or does not give enough to tell whether the target was met

Judge from what the page reports for the window, against the target above. A page that is an
error page, a "404" or "not found" notice, an empty page, or a page unrelated to this service
does not tell you the target was met: that is {UNCLEAR}, never {MET}. Do not assume uptime the
page does not state.

Reply with bare JSON and nothing else:
{{"verdict": "{MET}" or "{MISSED}" or "{UNCLEAR}",
  "figure": "the uptime or incident figure the page gave, or empty",
  "quote": "the sentence on the page that decided it, or empty",
  "reason": "one sentence naming what decided it against the target"}}"""


class Backstop(gl.Contract):
    """SLA escrows, each released to the provider or refunded to the buyer from its own status page."""

    # str(id) -> the SLA as JSON.
    items: TreeMap[str, str]
    ids: DynArray[str]
    # address -> credited units (releases received, refunds received), as a JSON int.
    balances: TreeMap[str, str]

    def __init__(self) -> None:
        pass

    def _credit(self, who: str, amount: int) -> None:
        cur = self.balances.get(who, None)
        base = int(cur) if cur is not None else 0
        self.balances[who] = str(base + int(amount))

    @gl.public.write
    def open_sla(self, provider: str, service: str, status_url: str, target: str,
                 window_end: str, escrow: str) -> str:
        """Open an SLA and escrow the payment. Bound to the caller (the buyer).

        Names the provider to be paid if the target holds, the plain-words target, the
        public status page it is checked at, a window that must close in the future, and
        the escrowed amount. The escrow is released or refunded only when it is settled.
        """
        buyer = gl.message.sender_address.as_hex.lower()
        prov = _addr(provider)
        svc = _clip(service, MAX_SERVICE)
        tgt = _clip(target, MAX_TARGET)
        link = str(status_url).strip()
        amt = _amount(escrow)
        if not prov:
            return json.dumps({"ok": False, "error": "give the provider's address"})
        if prov == buyer:
            return json.dumps({"ok": False, "error": "a buyer cannot open an SLA against themselves"})
        if not svc:
            return json.dumps({"ok": False, "error": "name the service"})
        if len(tgt) < 4:
            return json.dumps({"ok": False, "error": "state the target in plain words"})
        if not _url_ok(link):
            return json.dumps({"ok": False, "error": "give an http(s) URL for the status page"})
        if amt is None:
            return json.dumps({"ok": False, "error": "give a positive escrow as a whole number of units"})
        ok, when = _valid_window(window_end, _now())
        if not ok:
            return json.dumps({"ok": False, "error": when})

        sid = str(len(self.ids))
        record = {
            "id": sid,
            "buyer": buyer,
            "provider": prov,
            "opened_at": _now_iso(),
            "opened_ts": _now(),
            "service": svc,
            "status_url": link,
            "target": tgt,
            "window_end": when,
            "window_iso": _iso(when),
            "opened_iso": _iso(_now()),
            "escrow": amt,
            "status": FUNDED,
            "settlements": 0,
            "verdict": "",
            "figure": "",
            "reason": "",
            "quote": "",
            "settled_at": "",
        }
        self.items[sid] = json.dumps(record)
        self.ids.append(sid)
        return json.dumps({"ok": True, "id": sid, "status": FUNDED, "escrow": amt, "window": _iso(when)})

    @gl.public.write
    def settle(self, sla_id: str) -> str:
        """After the window, read the status page and release or refund the escrow. Open to anybody.

        The page is fetched by the contract inside the round, which is told the target and
        the window, so uptime is judged over the agreed period. MET releases to the provider,
        MISSED refunds the buyer; nobody passes in the verdict.
        """
        sid = str(sla_id).strip()
        stored = self.items.get(sid, None)
        if stored is None:
            return json.dumps({"ok": False, "error": "no SLA with that id"})
        record = json.loads(stored)
        if record["status"] != FUNDED:
            return json.dumps({"ok": False, "error": "this SLA is already " + record["status"].lower(),
                               "status": record["status"]})
        if _now() < int(record["window_end"]):
            return json.dumps({"ok": False, "error": "too early; an SLA cannot be settled before its window closes",
                               "window_end": record["window_end"], "now": _now()})

        # Copy into locals before the round. Nothing inside the block reads self
        # and nothing inside it raises.
        service = record["service"]
        target = record["target"]
        url = record["status_url"]
        window_from = record["opened_iso"]
        window_to = record["window_iso"]

        def look() -> str:
            page = ""
            try:
                got = gl.nondet.web.render(url)
                page = got if isinstance(got, str) else getattr(got, "body", "")
                if isinstance(page, (bytes, bytearray)):
                    page = page.decode("utf-8", "replace")
                page = _clip(str(page), MAX_PAGE)
                if page.strip().lower().startswith(("404: not found", "404 not found", "not found")):
                    page = FETCH_FAILED
            except Exception:
                page = FETCH_FAILED
            if not page or page == FETCH_FAILED:
                return json.dumps({"verdict": UNCLEAR, "figure": "", "quote": "",
                                   "reason": "the status page could not be read"})
            try:
                return str(gl.nondet.exec_prompt(_task(service, target, window_from, window_to, page)))
            except Exception as error:
                return json.dumps({"verdict": UNCLEAR, "figure": "", "quote": "",
                                   "reason": _clip("the prompt failed: " + str(error), MAX_REASON)})

        raw = gl.eq_principle.prompt_comparative(
            look,
            principle=(
                f"Both answers must carry the same value in the field named verdict, one of "
                f"{MET}, {MISSED} or {UNCLEAR}. That single field decides whether the escrow is "
                "released to the provider or refunded to the buyer, so two readers differing on it "
                "disagree about whether the service met its target, not about wording. The other "
                "fields are not compared, and the two readers will not have fetched byte-identical "
                "copies of the page."
            ),
        )

        verdict = _field(raw, "verdict", VERDICTS, "")
        if not verdict:
            return json.dumps({"ok": False, "error": "the round produced no verdict this contract recognises",
                               "round_said": _clip(str(raw), 400)})

        status, beneficiary = _outcome(verdict)
        record["settlements"] = int(record.get("settlements", 0)) + 1
        record["verdict"] = verdict
        record["figure"] = _text_field(raw, "figure", 80)
        record["reason"] = _text_field(raw, "reason", MAX_REASON)
        record["quote"] = _text_field(raw, "quote", MAX_QUOTE)
        if status != FUNDED:
            record["status"] = status
            record["settled_at"] = _now_iso()
            who = record["provider"] if beneficiary == "provider" else record["buyer"]
            self._credit(who, int(record["escrow"]))
        # UNCLEAR: stays FUNDED, can be settled again.
        self.items[sid] = json.dumps(record)
        return json.dumps({"ok": True, "id": sid, "verdict": verdict, "status": record["status"],
                           "beneficiary": beneficiary, "reason": record["reason"]})

    # ------------------------------------------------------------------ reads

    @gl.public.view
    def balance(self, address: str) -> str:
        """Units credited to an address: escrow releases as a provider, refunds as a buyer."""
        a = _addr(address)
        if not a:
            return json.dumps({"exists": False, "balance": 0})
        cur = self.balances.get(a, None)
        return json.dumps({"exists": cur is not None, "address": a, "balance": int(cur) if cur is not None else 0})

    @gl.public.view
    def status(self, sla_id: str) -> str:
        """An SLA's current standing and the reason it was settled."""
        sid = str(sla_id).strip()
        stored = self.items.get(sid, None)
        if stored is None:
            return json.dumps({"exists": False})
        record = json.loads(stored)
        return json.dumps({"exists": True, "id": sid, "status": record["status"],
                           "verdict": record.get("verdict", ""), "settlements": record["settlements"],
                           "reason": record.get("reason", "")})

    @gl.public.view
    def get(self, sla_id: str) -> str:
        """The whole SLA, including the deciding verdict, figure, quote and reason once settled."""
        sid = str(sla_id).strip()
        stored = self.items.get(sid, None)
        if stored is None:
            return json.dumps({"exists": False})
        return stored

    @gl.public.view
    def size(self) -> str:
        """How many SLAs are funded, released and refunded, and the totals moved."""
        funded = 0
        released = 0
        refunded = 0
        released_units = 0
        refunded_units = 0
        for position in range(len(self.ids)):
            record = json.loads(self.items[self.ids[position]])
            state = record["status"]
            if state == FUNDED:
                funded += 1
            elif state == RELEASED:
                released += 1
                released_units += int(record["escrow"])
            elif state == REFUNDED:
                refunded += 1
                refunded_units += int(record["escrow"])
        return json.dumps({"total": len(self.ids), "funded": funded, "released": released,
                           "refunded": refunded, "released_units": released_units,
                           "refunded_units": refunded_units})

    @gl.public.view
    def page(self, start: str, count: str) -> str:
        """A slice of the book, newest first."""
        total = len(self.ids)
        begin = _whole(start)
        want = _whole(count)
        if begin < 0:
            begin = 0
        if want < 1:
            want = 20
        if want > 50:
            want = 50
        out = []
        seen = 0
        position = total - 1 - begin
        while position >= 0 and seen < want:
            out.append(json.loads(self.items[self.ids[position]]))
            position -= 1
            seen += 1
        return json.dumps({"total": total, "start": begin, "count": len(out), "items": out})
