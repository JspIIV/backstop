"""The escrow rules, exercised through the real contract methods.

backstop.py is loaded against a stub of the runtime, a real Backstop is built, and the
assertions go through open_sla() and settle(). The stub controls the page the round
fetches, the verdict it returns, and the clock, so a window can be made to lie in the
future when the SLA opens and in the past when it settles. It proves an SLA cannot be
settled before its window, that MET releases to the provider and MISSED refunds the
buyer, and that an unreadable or not-found page moves nothing.

    python tests/backstop_rules.py
"""

import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, "..", "contracts", "backstop.py")


class _Store:
    def __init__(self, kind): self.kind = kind
    def __class_getitem__(cls, item): return cls("map" if isinstance(item, tuple) else "list")
    def make(self): return {} if self.kind == "map" else []


class _Address:
    def __init__(self, hex_value): self.as_hex = hex_value
    def __str__(self): return str(self.as_hex)


class _Message:
    def __init__(self):
        self.sender_address = _Address("0x" + "0" * 40)
        self.value = 0


class _Web:
    def __init__(self):
        self.page = "a status page"

    def render(self, url):
        if self.page is None:
            raise RuntimeError("could not fetch")
        return self.page


class _Nondet:
    def __init__(self, web):
        self.web = web
        self.last_prompt = None
        self.answer = "{}"

    def exec_prompt(self, task):
        self.last_prompt = task
        return self.answer


class _Write:
    def __call__(self, fn): return fn
    def payable(self, fn): return fn


class _PublicNS:
    def __init__(self):
        self.write = _Write()
        self.view = lambda fn: fn


class _EqPrinciple:
    def prompt_comparative(self, run, principle=None): return run()


class _GL:
    def __init__(self):
        self.Contract = object
        self.public = _PublicNS()
        self.message = _Message()
        self.nondet = _Nondet(_Web())
        self.eq_principle = _EqPrinciple()


def load():
    gl = _GL()
    fake = types.ModuleType("genlayer")
    fake.gl = gl
    fake.DynArray = _Store
    fake.TreeMap = _Store
    fake.u32 = int
    fake.u256 = int
    fake.Address = _Address
    sys.modules["genlayer"] = fake
    module = types.ModuleType("backstop_under_test")
    exec(compile(io.open(CONTRACT, encoding="utf-8").read(), CONTRACT, "exec"), module.__dict__)
    return module, gl


def fresh(module):
    contract = module.Backstop.__new__(module.Backstop)
    for field, declared in module.Backstop.__annotations__.items():
        setattr(contract, field, declared.make())
    contract.__init__()
    return contract


RESULTS = []


def check_(label, condition):
    RESULTS.append((label, bool(condition)))
    print(("  ok  " if condition else " FAIL "), label)


BUYER = "0x1111111111111111111111111111111111111111"
PROVIDER = "0x2222222222222222222222222222222222222222"

URL = "https://status.example.org"
SERVICE = "Example API"
TARGET = "99.9% uptime with no outage over one hour"
NOW = 1_000_000_000
FUTURE = NOW + 3600
PAST = NOW - 3600


def answer(verdict, figure="", reason="r", quote="q"):
    return json.dumps({"verdict": verdict, "figure": figure, "reason": reason, "quote": quote})


def bal(c, who):
    return json.loads(c.balance(who))["balance"]


def main():
    module, gl = load()
    clock = {"now": NOW}
    module._now = lambda: clock["now"]

    def as_(address): gl.message.sender_address = _Address(address)

    print("the pure window and outcome rules")
    check_("a future window is valid", module._valid_window(str(FUTURE), NOW)[0])
    check_("a window already closed is refused", not module._valid_window(str(PAST), NOW)[0])
    check_("MET releases to the provider", module._outcome("MET") == ("RELEASED", "provider"))
    check_("MISSED refunds the buyer", module._outcome("MISSED") == ("REFUNDED", "buyer"))
    check_("UNCLEAR holds the escrow", module._outcome("UNCLEAR") == ("FUNDED", ""))

    print("\nopening an SLA")
    c = fresh(module)
    as_(BUYER)
    check_("a buyer cannot open an SLA against themselves",
           not json.loads(c.open_sla(BUYER, SERVICE, URL, TARGET, str(FUTURE), "100"))["ok"])
    check_("a zero escrow is refused", not json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(FUTURE), "0"))["ok"])
    check_("a window in the past is refused", not json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(PAST), "100"))["ok"])
    opened = json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(FUTURE), "100"))
    sid = opened["id"]
    check_("a good SLA opens FUNDED", opened["ok"] and opened["status"] == "FUNDED")

    print("\nan SLA cannot be settled before its window closes")
    clock["now"] = NOW + 10
    early = json.loads(c.settle(sid))
    check_("settling before the window is refused", not early["ok"] and "too early" in early["error"])
    check_("and it stays funded", json.loads(c.status(sid))["status"] == "FUNDED")

    print("\nthe target is met over the window: the escrow is released to the provider")
    clock["now"] = FUTURE + 10
    gl.nondet.answer = answer("MET", figure="99.98%", reason="uptime above target, no long outage")
    met = json.loads(c.settle(sid))
    check_("MET releases the SLA", met["status"] == "RELEASED" and met["beneficiary"] == "provider")
    check_("the escrow is credited to the provider", bal(c, PROVIDER) == 100)
    check_("the buyer is credited nothing", bal(c, BUYER) == 0)
    check_("the window was put in front of the round",
           gl.nondet.last_prompt is not None and json.loads(c.get(sid))["window_iso"] in gl.nondet.last_prompt)
    check_("a released SLA cannot be settled again", not json.loads(c.settle(sid))["ok"])

    print("\nthe target is missed: the escrow is refunded to the buyer")
    as_(BUYER)
    clock["now"] = NOW
    miss = json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(FUTURE), "250"))["id"]
    clock["now"] = FUTURE + 10
    gl.nondet.answer = answer("MISSED", figure="96.2%", reason="a six-hour outage below target")
    r2 = json.loads(c.settle(miss))
    check_("MISSED refunds the SLA", r2["status"] == "REFUNDED" and r2["beneficiary"] == "buyer")
    check_("the escrow is credited back to the buyer", bal(c, BUYER) == 250)

    print("\nan unreadable or not-found page moves nothing")
    as_(BUYER)
    clock["now"] = NOW
    held = json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(FUTURE), "70"))["id"]
    clock["now"] = FUTURE + 10
    gl.nondet.web.page = None
    u1 = json.loads(c.settle(held))
    check_("an unreadable page is UNCLEAR and the SLA stays FUNDED", u1["verdict"] == "UNCLEAR" and json.loads(c.status(held))["status"] == "FUNDED")
    gl.nondet.web.page = "404: Not Found"
    u2 = json.loads(c.settle(held))
    check_("a not-found body is UNCLEAR and moves nothing", u2["verdict"] == "UNCLEAR" and json.loads(c.status(held))["status"] == "FUNDED")
    check_("no balance moved on an unreadable settlement", bal(c, PROVIDER) == 100 and bal(c, BUYER) == 250)

    print("\nthe book counts what moved")
    size = json.loads(c.size())
    check_("one released, one refunded, one still funded", size["released"] == 1 and size["refunded"] == 1 and size["funded"] == 1)
    check_("the totals match the escrows moved", size["released_units"] == 100 and size["refunded_units"] == 250)

    failed = [label for label, ok in RESULTS if not ok]
    print()
    if failed:
        print("%d of %d checks failed" % (len(failed), len(RESULTS)))
        return 1
    print("%d checks, all through open_sla() and settle() on a real Backstop, the window enforced"
          % len(RESULTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
