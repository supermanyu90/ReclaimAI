"""Labelled synthetic transactions for the category classifier, and leakage-free splits.

Every row is generated: fictional institutions, no names, digits short enough that they are
never account numbers. Ambiguity is deliberate and realistic: a bare "NACH DR UMRN…" mandate
debit can be a SIP, a loan EMI or an insurance premium; an "IMPS CR …" credit can be income or
a transfer. Amount, roundness and day of month carry some signal; the text alone does not.

Splits are by account, and accounts that share an identical narrative are merged first, so no
narrative appears in two splits.
"""

import hashlib
import random
import re
from dataclasses import dataclass

CATEGORIES = ("income", "investment_contribution", "insurance_premium", "loan_payment", "transfer", "other")

FUNDS = ["KAVERI MF", "GODAVARI AMC", "TAPTI MF", "SABARMATI MF"]
INSURERS = ["NAVJEEVAN LIFE", "SURAKSHA LIFE", "AROGYA GEN INS"]
BANKS = ["SAGAR BHARAT", "VAIGAI COOP", "NARMADA FIN"]
EMPLOYERS = ["ACME TEXTILES", "NILGIRI SOFTWARE", "KONKAN FOODS", "DECCAN LOGISTICS"]
MERCHANTS = ["GROCERY MART", "MEDPLUS PHARMA", "CITY FUEL", "BOOK HOUSE", "FRESH DAIRY", "METRO RAIL", "MOBILE RECHARGE", "CLINIC"]
BILLERS = ["ELECTRICITY", "WATER BOARD", "PIPED GAS", "BROADBAND"]


@dataclass(frozen=True)
class Row:
    account: str
    narrative: str
    direction: str  # credit | debit
    amount_minor: int
    day: int
    category: str


def _norm(narrative: str) -> str:
    """Duplicate key: the same text, ignoring case and spacing (digits matter)."""
    return re.sub(r"\s+", " ", narrative.upper()).strip()


def generate(n_accounts: int = 400, months: int = 12, seed: int = 7) -> list[Row]:
    rng = random.Random(seed)
    rows: list[Row] = []
    for a in range(n_accounts):
        acct = f"acct{a:04d}"
        tok = lambda: f"{rng.randint(100_000, 999_999)}"  # per-account references: 6 digits, never an account number
        profile = {
            "salary": rng.random() < 0.6, "pension": rng.random() < 0.25, "rent_in": rng.random() < 0.15,
            "sips": rng.sample(FUNDS, rng.randint(0, 2)), "loan": rng.random() < 0.45, "policy": rng.random() < 0.55,
            "mandate_style": rng.random() < 0.35,  # this account's bank shows bare NACH narratives
        }  # fmt: skip
        employer, bank, insurer = rng.choice(EMPLOYERS), rng.choice(BANKS), rng.choice(INSURERS)
        folio = {f: tok() for f in profile["sips"]}
        loan_ref, pol_ref, emp_id, ppo = tok(), tok(), tok(), tok()
        umrn = {k: f"{rng.randint(100000, 999999)}" for k in ("loan", "policy", *profile["sips"])}
        merchants = [(rng.choice(MERCHANTS), rng.randint(100_000, 999_999)) for _ in range(6)]
        payees = [f"PAYEE{rng.randint(100_000, 999_999)}" for _ in range(3)]
        # Overlapping amounts: step-up SIPs are not round, small EMIs and large premiums meet in the middle.
        sip_amt = {f: rng.choice([100_000, 200_000, 500_000, 1_000_000, rng.randint(150_000, 1_200_000)]) for f in profile["sips"]}
        emi_amt, prem_amt = rng.randint(300_000, 4_000_000), rng.randint(150_000, 1_500_000)
        rent_plain = rng.random() < 0.4  # rent arriving as a plain person-to-person credit
        mandate_day = rng.randint(1, 28)  # one debit date for all of this account's mandates
        shop_vpas = [f"{rng.choice(MERCHANTS).split()[0].lower()}{rng.randint(100_000, 999_999)}" for _ in range(3)]
        salary_amt = rng.randint(2_500_000, 15_000_000)

        def add(narr: str, direction: str, amount: int, day: int, cat: str, _acct: str = acct) -> None:
            rows.append(Row(_acct, narr, direction, max(amount, 100), min(max(day, 1), 28), cat))

        for m in range(months):
            if profile["salary"]:
                add(f"NEFT CR {employer} SAL {m + 1:02d} E{emp_id}", "credit", salary_amt, rng.choice([1, 28]), "income")
            if profile["pension"]:
                add(f"NEFT CR RASHTRIYA PENSION PPO{ppo}", "credit", rng.randint(1_500_000, 6_000_000), 1, "income")
            if profile["rent_in"]:
                narr = f"IMPS CR {payees[0]}" if rent_plain else f"IMPS CR {payees[0]} RENT"
                add(narr, "credit", rng.randint(500_000, 3_000_000), rng.randint(1, 10), "income")
            if m % 3 == 0:
                add(f"INT CR SB {tok()}", "credit", rng.randint(5_000, 90_000), 28, "income")
            for f in profile["sips"]:
                mandate = profile["mandate_style"]
                narr = f"NACH DR UMRN{umrn[f]}" if mandate else f"ACH DR {f} SIP F{folio[f]}"
                add(narr, "debit", sip_amt[f], mandate_day if mandate else rng.choice([5, 10, 15]), "investment_contribution")
            if profile["loan"]:
                mandate = profile["mandate_style"]
                narr = f"NACH DR UMRN{umrn['loan']}" if mandate else rng.choice([f"EMI DR {bank} HL {loan_ref}", f"EMI DR {bank} HOME LOAN {loan_ref}", f"SI DR {bank} LOAN EMI {loan_ref}"])
                add(narr, "debit", emi_amt, mandate_day if mandate else rng.randint(5, 10), "loan_payment")
            if profile["policy"]:
                mandate = profile["mandate_style"]
                narr = f"NACH DR UMRN{umrn['policy']}" if mandate else f"ECS DR {insurer} PREM {pol_ref}"
                add(narr, "debit", prem_amt, mandate_day if mandate else rng.randint(8, 20), "insurance_premium")
            for _ in range(rng.randint(1, 3)):
                payee = rng.choice(payees)
                forms = [f"IMPS DR TO {payee}", f"UPI DR {payee.lower()}@okbank", f"NEFT DR {payee}", f"IMPS DR {payee} TRANSFER",
                         f"NEFT DR SELF TRF {tok()}", f"RTGS DR TRANSFER {tok()}", f"IMPS DR FAMILY {payee}"]  # fmt: skip
                add(rng.choice(forms), "debit", rng.randint(50_000, 5_000_000), rng.randint(1, 28), "transfer")
            if rng.random() < 0.5:
                # Ambiguous credit: money from a person is a transfer here, unlike rent above.
                add(f"IMPS CR {rng.choice(payees)}", "credit", rng.randint(50_000, 2_000_000), rng.randint(1, 28), "transfer")
            for _ in range(rng.randint(4, 9)):
                name, mid = rng.choice(merchants)
                add(rng.choice([f"UPI DR {name} {mid}", f"POS {name} {mid}"]), "debit", rng.randint(5_000, 800_000), rng.randint(1, 28), "other")
            for _ in range(rng.randint(0, 2)):
                # A shop's UPI handle looks exactly like a person's: spending, not a transfer.
                add(f"UPI DR {rng.choice(shop_vpas)}@okbank", "debit", rng.randint(5_000, 400_000), rng.randint(1, 28), "other")
            if rng.random() < 0.8:
                add(f"BBPS DR {rng.choice(BILLERS)} {tok()}", "debit", rng.randint(30_000, 600_000), rng.randint(1, 28), "other")
            if rng.random() < 0.3:
                add(f"ATM WDL {tok()}", "debit", rng.choice([200_000, 500_000, 1_000_000]), rng.randint(1, 28), "other")
    return rows


def split(rows: list[Row], fractions: tuple[float, float, float] = (0.7, 0.15, 0.15)) -> dict[str, list[Row]]:
    """Group-aware split: accounts linked by an identical normalized narrative stay together."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first_account: dict[str, str] = {}
    for r in rows:
        key = _norm(r.narrative)
        if key in first_account:
            parent[find(r.account)] = find(first_account[key])
        else:
            first_account[key] = r.account
            find(r.account)

    def bucket(group: str) -> str:
        h = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return "train" if h < fractions[0] else "validation" if h < fractions[0] + fractions[1] else "test"

    out: dict[str, list[Row]] = {"train": [], "validation": [], "test": []}
    for r in rows:
        out[bucket(find(r.account))].append(r)
    return out


def cross_split_duplicates(splits: dict[str, list[Row]]) -> int:
    seen: dict[str, str] = {}
    clashes = set()
    for name, part in splits.items():
        for r in part:
            key = _norm(r.narrative)
            if seen.setdefault(key, name) != name:
                clashes.add(key)
    return len(clashes)
