"""The escrow rules, with a real deposit, exercised through the real contract methods.

backstop.py is loaded against a stub of the runtime, a real Backstop is built, and the
assertions go through open_sla() (payable) and settle(). The stub controls the page the
round fetches, the verdict it returns, the clock, and the native value deposited, and it
records every emit_transfer. It proves opening an SLA escrows the value deposited, MET
transfers that deposit to the provider and MISSED refunds the buyer (each once), an invalid
open returns the deposit, and an unreadable page moves nothing.

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
    def __init__(self, hex_value): self.as_hex = str(hex_value)
    def __str__(self): return str(self.as_hex)


class _Message:
    def __init__(self):
        self.sender_address = _Address("0x" + "0" * 40)
        self.value = 0


class _Evm:
    def __init__(self):
        self.transfers = []
        outer = self

        def contract_interface(cls):
            class Bound:
                def __init__(self, address): self.address = str(address).lower()
                def emit_transfer(self, value): outer.transfers.append((self.address, int(value)))
            return Bound
        self.contract_interface = contract_interface


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
        self.evm = _Evm()
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
ESCROW = 100


def answer(verdict, figure="", reason="r", quote="q"):
    return json.dumps({"verdict": verdict, "figure": figure, "reason": reason, "quote": quote})


def main():
    module, gl = load()
    clock = {"now": NOW}
    module._now = lambda: clock["now"]

    def as_(address): gl.message.sender_address = _Address(address)
    def value(v): gl.message.value = int(v)

    print("the pure window and outcome rules")
    check_("a future window is valid", module._valid_window(str(FUTURE), NOW)[0])
    check_("a window already closed is refused", not module._valid_window(str(PAST), NOW)[0])
    check_("MET releases to the provider", module._outcome("MET") == ("RELEASED", "provider"))
    check_("MISSED refunds the buyer", module._outcome("MISSED") == ("REFUNDED", "buyer"))
    check_("UNCLEAR holds the escrow", module._outcome("UNCLEAR") == ("FUNDED", ""))

    print("\nopening an SLA escrows the value deposited, and an invalid open returns it")
    c = fresh(module)
    as_(BUYER); value(0)
    check_("an unfunded SLA is refused", not json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(FUTURE)))["ok"])
    gl.evm.transfers.clear()
    as_(BUYER); value(ESCROW)
    selfdeal = json.loads(c.open_sla(BUYER, SERVICE, URL, TARGET, str(FUTURE)))
    check_("a buyer cannot open an SLA against themselves, and the deposit is returned",
           not selfdeal["ok"] and gl.evm.transfers == [(BUYER, ESCROW)])
    gl.evm.transfers.clear()
    as_(BUYER); value(ESCROW)
    backdated = json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(PAST)))
    check_("a window in the past is refused and the deposit is returned",
           not backdated["ok"] and gl.evm.transfers == [(BUYER, ESCROW)])
    gl.evm.transfers.clear()
    as_(BUYER); value(ESCROW)
    opened = json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(FUTURE)))
    sid = opened["id"]
    check_("a funded SLA opens FUNDED with escrow equal to the deposit", opened["ok"] and opened["escrow"] == str(ESCROW))
    check_("nothing is paid out on open; the deposit is held", gl.evm.transfers == [])
    check_("the held escrow is tracked", json.loads(c.size())["escrow_held"] == str(ESCROW))

    print("\nan SLA cannot be settled before its window closes")
    value(0)
    clock["now"] = NOW + 10
    early = json.loads(c.settle(sid))
    check_("settling before the window is refused", not early["ok"] and "too early" in early["error"])

    print("\nthe target is met: the deposit is released to the provider, once")
    clock["now"] = FUTURE + 10
    gl.nondet.answer = answer("MET", figure="99.98%", reason="uptime above target")
    met = json.loads(c.settle(sid))
    check_("MET releases the SLA", met["status"] == "RELEASED" and met["beneficiary"] == "provider")
    check_("the deposit is transferred to the provider", gl.evm.transfers == [(PROVIDER, ESCROW)])
    check_("the window was put in front of the round",
           gl.nondet.last_prompt is not None and json.loads(c.get(sid))["window_iso"] in gl.nondet.last_prompt)
    gl.evm.transfers.clear()
    check_("a released SLA cannot be settled again", not json.loads(c.settle(sid))["ok"])
    check_("and pays no second time", gl.evm.transfers == [])

    print("\nthe target is missed: the deposit is refunded to the buyer")
    as_(BUYER); clock["now"] = NOW; value(250)
    miss = json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(FUTURE)))["id"]
    gl.evm.transfers.clear()
    clock["now"] = FUTURE + 10
    gl.nondet.answer = answer("MISSED", figure="96.2%", reason="a six-hour outage")
    r2 = json.loads(c.settle(miss))
    check_("MISSED refunds the SLA", r2["status"] == "REFUNDED" and r2["beneficiary"] == "buyer")
    check_("the deposit is refunded to the buyer", gl.evm.transfers == [(BUYER, 250)])

    print("\nan unreadable or not-found page moves nothing")
    as_(BUYER); clock["now"] = NOW; value(70)
    held = json.loads(c.open_sla(PROVIDER, SERVICE, URL, TARGET, str(FUTURE)))["id"]
    gl.evm.transfers.clear()
    clock["now"] = FUTURE + 10
    gl.nondet.web.page = None
    u1 = json.loads(c.settle(held))
    check_("an unreadable page is UNCLEAR and the SLA stays FUNDED", u1["verdict"] == "UNCLEAR" and json.loads(c.status(held))["status"] == "FUNDED")
    gl.nondet.web.page = "404: Not Found"
    u2 = json.loads(c.settle(held))
    check_("a not-found body is UNCLEAR and moves nothing", u2["verdict"] == "UNCLEAR" and gl.evm.transfers == [])

    print("\nthe book counts what it held and moved")
    size = json.loads(c.size())
    check_("one released, one refunded, one still funded", size["released"] == 1 and size["refunded"] == 1 and size["funded"] == 1)
    check_("the totals match the deposits", size["released_units"] == str(ESCROW) and size["refunded_units"] == "250" and size["escrow_held"] == "70")

    failed = [label for label, ok in RESULTS if not ok]
    print()
    if failed:
        print("%d of %d checks failed" % (len(failed), len(RESULTS)))
        return 1
    print("%d checks, all through open_sla() and settle() on a real Backstop, with a real deposit"
          % len(RESULTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
