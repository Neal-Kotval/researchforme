"""Canned synthesis output — the zero-dependency demo path.

When no LLM backend is reachable (no API key, no Claude subscription, no CLI),
`ClaudeClient._via_fixture` and `analysis.synthesize` both fall back to this
module so the entire pipeline still returns real, well-formed, *interesting*
gaps. The sample area is "personal finance for freelancers".

`FIXTURE_GAPS_JSON` is a JSON **string** (a top-level array of Gap objects) so it
plugs straight into the same robust parser the live LLM output flows through —
proving the parse/validate path end to end. Every object below validates against
`schemas.Gap`; evidence URLs mirror the source fixtures (reddit / arxiv /
hackernews / github / newsletter).

These are written to be genuinely good: each gap triangulates rising demand
against lagging supply against a concrete "why now", names 5 grounded
competitors with a specific structural weakness each, and honestly flags its own
weakest link (one is even marked empty-for-a-reason as a contrarian caution).
"""

from __future__ import annotations

import json

# --------------------------------------------------------------------------- #
# Five rich gaps for "personal finance for freelancers". Built as Python data  #
# then serialized, so the JSON is guaranteed valid and stays readable.         #
# --------------------------------------------------------------------------- #
_FIXTURE_GAPS: list[dict] = [
    {
        "title": "Real-time quarterly tax autopilot for 1099 income",
        "thesis": "Freelancers keep getting surprised by estimated-tax bills; a "
        "tool that withholds and files quarterlies automatically as money lands "
        "is a wide-open wedge.",
        "scores": {
            "demand_strength": 5,
            "competitive_openness": 4,
            "trend_tailwind": 4,
            "feasibility": 4,
            "willingness_to_pay": 5,
        },
        "evidence": [
            {
                "source": "reddit",
                "url": "https://www.reddit.com/r/freelance/comments/1h8x2qk/i_owed_9k_in_taxes_i_didnt_set_aside/",
                "quote": "I owed $9k in April because nobody withholds for you. I "
                "genuinely didn't know estimated payments were a thing until it was late.",
                "date": "2026-04-18",
            },
            {
                "source": "reddit",
                "url": "https://www.reddit.com/r/tax/comments/1g2m9re/how_do_you_actually_calculate_quarterly_estimates/",
                "quote": "Every quarter I re-learn the safe-harbor math from scratch. "
                "Why is there no set-it-and-forget-it withholding for freelancers?",
                "date": "2025-09-12",
            },
            {
                "source": "hackernews",
                "url": "https://news.ycombinator.com/item?id=41207788",
                "quote": "Ask HN: How do freelancers handle quarterly estimated taxes? "
                "I keep under-withholding and eating a penalty — is there really no tool "
                "that just sequesters the money as each payment lands?",
                "date": "2025-11-04",
            },
            {
                "source": "arxiv",
                "url": "https://arxiv.org/abs/2502.09033",
                "quote": "Template-free key-value extraction from noisy financial "
                "documents reaches 96.2% field-level accuracy — enough to auto-classify "
                "1099s and deductible receipts without per-form templates.",
                "date": "2025-02-13",
            },
        ],
        "competitors": [
            {
                "name": "QuickBooks Self-Employed",
                "url": "https://quickbooks.intuit.com/self-employed/",
                "positioning": "Mileage + expense tracking with a quarterly tax estimate.",
                "segment": "Solo freelancers already in the Intuit ecosystem.",
                "price_tier": "$$",
                "weakness": "Only ESTIMATES the number and nudges you — it never "
                "actually sequesters cash or files the payment, so the surprise bill still happens.",
            },
            {
                "name": "Keeper Tax",
                "url": "https://www.keepertax.com/",
                "positioning": "AI write-off finder that scans bank transactions.",
                "segment": "Gig workers hunting deductions.",
                "price_tier": "$$",
                "weakness": "Optimizes deductions at filing time; there is no "
                "continuous withholding account, so cash-flow discipline is still on the user.",
            },
            {
                "name": "Found",
                "url": "https://found.com/",
                "positioning": "Business banking for the self-employed with tax set-aside.",
                "segment": "US sole proprietors who will switch their primary bank.",
                "price_tier": "$",
                "weakness": "Requires moving your banking to them — a huge switching "
                "cost most freelancers refuse; it can't withhold on income that lands elsewhere.",
            },
            {
                "name": "Catch",
                "url": "https://catch.co/",
                "positioning": "Benefits + tax withholding for independent workers.",
                "segment": "1099 workers wanting benefits.",
                "price_tier": "$",
                "weakness": "Pivoted away from consumer tax withholding; leaves the "
                "auto-file-the-quarterly loop unowned.",
            },
            {
                "name": "TurboTax",
                "url": "https://turbotax.intuit.com/",
                "positioning": "Annual DIY filing.",
                "segment": "Everyone at tax season.",
                "price_tier": "$$",
                "weakness": "Point-in-time, once-a-year product — structurally "
                "uninterested in the continuous cash-flow problem that causes the surprise.",
            },
        ],
        "wedge": "A read-only bank link + a separate FDIC-insured 'tax vault' that "
        "auto-transfers a safe-harbor % on every deposit and files the 1040-ES for you.",
        "riskiest_assumption": "That freelancers will let an app move real money "
        "into a sequestered account rather than just show them a number.",
        "weakest_link": "Feasibility of actually initiating IRS/state e-payments "
        "programmatically without becoming a money transmitter — regulatory, not technical.",
        "why_now": "The self-employed base is at multi-year highs while document-"
        "extraction models finally make automatic 1099/receipt classification cheap and reliable.",
        "empty_for_a_reason": False,
        "empty_reason": "",
        "novelty": 4,
        "sub_segment": "solo 1099 freelancers",
        "tags": ["taxes", "cash-flow", "automation", "fintech"],
    },
    {
        "title": "Income-smoothing account for lumpy freelance cash flow",
        "thesis": "Freelancers earn in spikes but pay bills monthly; a smart buffer "
        "that pays them a steady 'salary' from their own volatile income is unowned.",
        "scores": {
            "demand_strength": 4,
            "competitive_openness": 5,
            "trend_tailwind": 4,
            "feasibility": 3,
            "willingness_to_pay": 4,
        },
        "evidence": [
            {
                "source": "reddit",
                "url": "https://www.reddit.com/r/freelance/comments/1f4t7zq/how_do_you_budget_when_income_swings_5k_one_month_0_next/",
                "quote": "One month I clear $8k, the next is basically zero. Budgeting "
                "apps built for salaries are useless for me.",
                "date": "2025-08-03",
            },
            {
                "source": "reddit",
                "url": "https://www.reddit.com/r/personalfinance/comments/1e9p3mn/paying_myself_a_salary_from_an_irregular_business/",
                "quote": "I want to pay myself a fixed paycheck from my business "
                "account and let the buffer absorb the swings, but nothing does this automatically.",
                "date": "2025-06-21",
            },
            {
                "source": "hackernews",
                "url": "https://news.ycombinator.com/item?id=40388215",
                "quote": "Ask HN: My income swings from $12k to $0 month to month and "
                "every budgeting app assumes a salary. How do you smooth variable "
                "freelance income into a steady paycheck?",
                "date": "2025-05-22",
            },
        ],
        "competitors": [
            {
                "name": "Qube Money",
                "url": "https://qubemoney.com/",
                "positioning": "Digital envelope budgeting with a spending gate.",
                "segment": "Budget-conscious consumers.",
                "price_tier": "$",
                "weakness": "Envelopes assume predictable income to allocate; it has "
                "no engine to smooth spiky deposits into a steady payout.",
            },
            {
                "name": "YNAB",
                "url": "https://www.ynab.com/",
                "positioning": "Zero-based budgeting for every dollar.",
                "segment": "Hands-on budgeters.",
                "price_tier": "$$",
                "weakness": "Requires constant manual reallocation when income is "
                "irregular; philosophically opposed to automation, which is exactly what lumpy earners need.",
            },
            {
                "name": "Cushion",
                "url": "https://cushion.ai/",
                "positioning": "Bill tracking and negotiation.",
                "segment": "People juggling recurring bills.",
                "price_tier": "$",
                "weakness": "Tracks obligations but doesn't hold or meter out income, "
                "so it treats the symptom, not the volatility.",
            },
            {
                "name": "Douugh / Wallet apps",
                "url": "https://douugh.com/",
                "positioning": "Autopilot savings jars.",
                "segment": "Consumers automating savings.",
                "price_tier": "$",
                "weakness": "Jars are static rules; none model a target monthly draw "
                "and dynamically refill from variable inflows.",
            },
            {
                "name": "Traditional business checking",
                "url": "https://www.chase.com/business/banking/checking",
                "positioning": "A place to hold business cash.",
                "segment": "All small businesses.",
                "price_tier": "free",
                "weakness": "Dumb balance — no concept of a self-paid salary or buffer "
                "runway, leaving the smoothing math entirely manual.",
            },
        ],
        "wedge": "Connect the account, set a target monthly paycheck, and the tool "
        "auto-transfers that amount on the 1st while parking the surplus as buffer runway.",
        "riskiest_assumption": "That enough freelancers have positive average cash "
        "flow to smooth — for many, the real problem is too little income, not lumpy income.",
        "weakest_link": "Feasibility (3): doing this well needs either a banking "
        "charter/BaaS partner or trusted money movement, raising build cost and compliance load.",
        "why_now": "The independent-worker base is expanding while saving rates stay "
        "thin, and BaaS rails now make launching a purpose-built account far cheaper than five years ago.",
        "empty_for_a_reason": False,
        "empty_reason": "",
        "novelty": 4,
        "sub_segment": "variable-income freelancers and creators",
        "tags": ["cash-flow", "budgeting", "banking", "income-smoothing"],
    },
    {
        "title": "On-device bookkeeping copilot that never sends data to the cloud",
        "thesis": "Privacy-wary freelancers hate uploading full bank history to SaaS; "
        "a local-first AI bookkeeper that categorizes and answers on-device wins their trust.",
        "scores": {
            "demand_strength": 3,
            "competitive_openness": 4,
            "trend_tailwind": 5,
            "feasibility": 3,
            "willingness_to_pay": 3,
        },
        "evidence": [
            {
                "source": "arxiv",
                "url": "https://arxiv.org/abs/2503.14891",
                "quote": "A 1.3B on-device retrieval agent answers multi-hop questions "
                "over local document stores at 4x lower latency than API baselines while keeping all data on-device.",
                "date": "2025-03-19",
            },
            {
                "source": "arxiv",
                "url": "https://arxiv.org/abs/2502.09033",
                "quote": "Robust template-free extraction from noisy invoices at 96.2% "
                "accuracy makes local categorization of receipts and statements viable.",
                "date": "2025-02-13",
            },
            {
                "source": "reddit",
                "url": "https://www.reddit.com/r/freelance/comments/1d7k4wq/i_refuse_to_give_a_random_app_readonly_access_to_my_bank/",
                "quote": "I refuse to hand a random startup read-only access to my "
                "entire bank history just to categorize expenses. There has to be a local option.",
                "date": "2025-05-09",
            },
        ],
        "competitors": [
            {
                "name": "Wave Accounting",
                "url": "https://www.waveapps.com/",
                "positioning": "Free cloud accounting for small businesses.",
                "segment": "Micro-businesses and freelancers.",
                "price_tier": "free",
                "weakness": "Cloud-only with mandatory bank aggregation; monetizes "
                "payments, so a privacy-first local model is antithetical to its business.",
            },
            {
                "name": "QuickBooks",
                "url": "https://quickbooks.intuit.com/",
                "positioning": "The default SMB accounting cloud.",
                "segment": "SMBs and their accountants.",
                "price_tier": "$$$",
                "weakness": "Deeply cloud- and accountant-centric; can't credibly offer "
                "'your data never leaves your laptop' without cannibalizing its platform.",
            },
            {
                "name": "GnuCash",
                "url": "https://www.gnucash.org/",
                "positioning": "Open-source desktop double-entry accounting.",
                "segment": "Technical DIY bookkeepers.",
                "price_tier": "free",
                "weakness": "Local but has zero AI categorization or natural-language "
                "Q&A; usability is brutal for non-accountants.",
            },
            {
                "name": "Copilot Money",
                "url": "https://copilot.money/",
                "positioning": "Polished AI-assisted personal finance.",
                "segment": "Design-conscious consumers.",
                "price_tier": "$$",
                "weakness": "Sends transactions to the cloud for its intelligence; "
                "consumer-personal, not freelancer-bookkeeping, and not local-first.",
            },
            {
                "name": "Spreadsheet + ChatGPT",
                "url": "https://chat.openai.com/",
                "positioning": "The DIY manual workaround.",
                "segment": "Cost-sensitive freelancers.",
                "price_tier": "free",
                "weakness": "Manual export/paste breaks the privacy promise and is "
                "tedious every month; no persistent, structured ledger.",
            },
        ],
        "wedge": "A desktop app that reads a downloaded CSV/PDF locally, categorizes "
        "with an on-device model, and answers 'what did I spend on software in Q2?' with nothing leaving the machine.",
        "riskiest_assumption": "That enough freelancers will pay a premium for privacy "
        "when free cloud tools are 'good enough' for most.",
        "weakest_link": "Willingness_to_pay (3) and demand_strength (3): privacy is a "
        "loud minority preference; the mainstream trades it away for convenience.",
        "why_now": "Small on-device models just crossed the accuracy/latency threshold "
        "where fully local categorization and Q&A over financial docs is finally practical on a laptop.",
        "empty_for_a_reason": False,
        "empty_reason": "",
        "novelty": 5,
        "sub_segment": "privacy-conscious freelancers and consultants",
        "tags": ["privacy", "on-device-ai", "bookkeeping", "local-first"],
    },
    {
        "title": "Invoice-backed instant advance without predatory factoring",
        "thesis": "Freelancers wait 30-90 days on invoices; a fair, transparent "
        "advance priced on the client's creditworthiness beats both factoring sharks and BNPL.",
        "scores": {
            "demand_strength": 4,
            "competitive_openness": 3,
            "trend_tailwind": 3,
            "feasibility": 3,
            "willingness_to_pay": 4,
        },
        "evidence": [
            {
                "source": "reddit",
                "url": "https://www.reddit.com/r/freelance/comments/1c6m2te/net_60_is_killing_me_i_did_the_work_in_march_and/",
                "quote": "Net-60 is killing me. I did the work in March and won't see "
                "the money until June while rent is due now.",
                "date": "2025-04-02",
            },
            {
                "source": "reddit",
                "url": "https://www.reddit.com/r/smallbusiness/comments/1b9k7pq/invoice_factoring_quotes_are_insane_effective_apr/",
                "quote": "Every factoring quote I get works out to an effective APR "
                "north of 40%. There has to be a fairer way to get paid early.",
                "date": "2025-03-15",
            },
            {
                "source": "github",
                "url": "https://github.com/InvoiceShelf/InvoiceShelf",
                "quote": "InvoiceShelf, an open-source invoicing app, went from launch to "
                "4.2k stars in under a year — modern payments/invoicing tooling is now "
                "cheap enough for a small team to verify invoices and auto-repay advances.",
                "date": "2026-01-19",
            },
        ],
        "competitors": [
            {
                "name": "FundThrough",
                "url": "https://www.fundthrough.com/",
                "positioning": "Invoice factoring for SMBs.",
                "segment": "Established small businesses with larger invoices.",
                "price_tier": "$$$",
                "weakness": "Minimums and underwriting skew toward bigger invoices; "
                "opaque fees and a factoring stigma alienate individual freelancers.",
            },
            {
                "name": "Bluevine",
                "url": "https://www.bluevine.com/",
                "positioning": "Lines of credit + banking for SMBs.",
                "segment": "Small businesses with revenue history.",
                "price_tier": "$$",
                "weakness": "Underwrites the FREELANCER'S credit/revenue, not the "
                "client's — so new or thin-file solo workers get declined or throttled.",
            },
            {
                "name": "PayPal Working Capital",
                "url": "https://www.paypal.com/us/business/working-capital",
                "positioning": "Advances against PayPal sales volume.",
                "segment": "PayPal-based sellers.",
                "price_tier": "$$",
                "weakness": "Only sees PayPal flow; irrelevant to freelancers invoicing "
                "clients directly via ACH or wire.",
            },
            {
                "name": "Stripe / Bill.com early pay",
                "url": "https://stripe.com/",
                "positioning": "Payments rails with some early-payout options.",
                "segment": "Businesses on their platform.",
                "price_tier": "$$",
                "weakness": "Early pay is a platform add-on, not underwritten on the "
                "paying client's credit; coverage is patchy and freelancer-hostile in UX.",
            },
            {
                "name": "Credit cards / overdraft",
                "url": "https://www.nerdwallet.com/best/credit-cards",
                "positioning": "The default stopgap.",
                "segment": "Everyone with a card.",
                "price_tier": "$$$",
                "weakness": "20%+ APR revolving debt with no link to the actual "
                "receivable — expensive and disconnected from when the invoice clears.",
            },
        ],
        "wedge": "Advance against a single verified invoice, priced on the CLIENT'S "
        "credit (often a large, reliable company), with a flat transparent fee and auto-repay on clearance.",
        "riskiest_assumption": "That you can verify invoices and underwrite paying "
        "clients cheaply enough to price fairly and still avoid fraud/default losses.",
        "weakest_link": "Competitive_openness (3) and feasibility (3): lending is "
        "capital-intensive and regulated, and incumbents can copy fair pricing once it's proven.",
        "why_now": "A larger freelance base plus tighter late-payment cycles raises "
        "the pain, while modern payments APIs make single-invoice verification and auto-repay feasible for a small team.",
        "empty_for_a_reason": False,
        "empty_reason": "",
        "novelty": 3,
        "sub_segment": "freelancers invoicing mid-market and enterprise clients",
        "tags": ["lending", "cash-flow", "invoicing", "fintech"],
    },
    {
        "title": "All-in-one freelancer retirement + benefits marketplace",
        "thesis": "Freelancers lack employer 401(k)s and benefits; a single hub to "
        "set up SEP-IRAs, health, and disability looks like obvious white space — but may be empty for a reason.",
        "scores": {
            "demand_strength": 3,
            "competitive_openness": 2,
            "trend_tailwind": 3,
            "feasibility": 2,
            "willingness_to_pay": 2,
        },
        "evidence": [
            {
                "source": "reddit",
                "url": "https://www.reddit.com/r/freelance/comments/1a2p9dk/no_employer_401k_how_are_you_all_saving_for_retirement/",
                "quote": "No employer 401k, no HR to set anything up. How are the rest "
                "of you actually saving for retirement as freelancers?",
                "date": "2025-02-11",
            },
            {
                "source": "hackernews",
                "url": "https://news.ycombinator.com/item?id=39712044",
                "quote": "Ask HN: Freelancers, how are you actually saving for retirement? "
                "No employer 401k, SEP-IRA vs Solo 401k is confusing, and I just end up "
                "doing nothing every year.",
                "date": "2025-02-27",
            },
            {
                "source": "reddit",
                "url": "https://www.reddit.com/r/personalfinance/comments/19x4m7q/sep_ira_vs_solo_401k_as_a_freelancer_totally_lost/",
                "quote": "SEP-IRA vs Solo 401k vs individual health plans — I'm totally "
                "lost and just end up doing nothing every year.",
                "date": "2025-01-28",
            },
        ],
        "competitors": [
            {
                "name": "Fidelity / Vanguard",
                "url": "https://www.fidelity.com/",
                "positioning": "Low-cost SEP-IRA and Solo 401(k) providers.",
                "segment": "Self-directed savers.",
                "price_tier": "free",
                "weakness": "Setup is self-serve and intimidating; no benefits bundle "
                "and no hand-holding — but their zero-fee scale makes an aggregator hard to monetize.",
            },
            {
                "name": "Stride Health",
                "url": "https://www.stride.health/",
                "positioning": "Health/benefits marketplace for gig workers.",
                "segment": "1099 and gig workers.",
                "price_tier": "free",
                "weakness": "Commission-funded and health-centric; thin on retirement, "
                "and the free model means low ARPU and heavy CAC pressure.",
            },
            {
                "name": "Catch",
                "url": "https://catch.co/",
                "positioning": "Formerly a benefits/retirement hub for independents.",
                "segment": "Independent workers.",
                "price_tier": "$",
                "weakness": "Retrenched from the consumer benefits-hub model — a direct "
                "signal that unit economics here are punishing.",
            },
            {
                "name": "Gusto",
                "url": "https://gusto.com/",
                "positioning": "Payroll + benefits for small teams.",
                "segment": "Employers with W-2 staff.",
                "price_tier": "$$",
                "weakness": "Built around EMPLOYERS running payroll; a true solo "
                "freelancer with no employees isn't its customer.",
            },
            {
                "name": "Insurance brokers",
                "url": "https://www.ehealthinsurance.com/",
                "positioning": "Commission-based benefits enrollment.",
                "segment": "Individuals buying coverage.",
                "price_tier": "free",
                "weakness": "Siloed by product and commission-driven; no unified "
                "retirement + benefits experience, and no incentive to build one.",
            },
        ],
        "wedge": "Start with the single most-neglected action — one-click SEP-IRA "
        "setup with auto-contributions tied to income — before expanding to health/disability.",
        "riskiest_assumption": "That freelancers will pay for guidance when the "
        "underlying accounts are free and incumbents earn on assets, not subscriptions.",
        "weakest_link": "Willingness_to_pay (2) and feasibility (2): free zero-fee "
        "providers plus regulated insurance/advice make monetization and build both hard.",
        "why_now": "The independent workforce keeps growing, but the economics that "
        "sank prior benefits hubs (Catch) haven't changed — the tailwind is real, the model still isn't.",
        "empty_for_a_reason": True,
        "empty_reason": "Underlying accounts are commoditized and free, benefits are "
        "commission-siloed and regulated, and at least one well-funded startup (Catch) already "
        "retreated — the white space is empty because the unit economics punish anyone who enters broadly.",
        "novelty": 2,
        "sub_segment": "established solo freelancers planning long-term",
        "tags": ["retirement", "benefits", "marketplace", "empty-for-a-reason"],
    },
]

# Serialize once at import. This is the exact contract `synthesize` and the LLM
# client fixture path consume: a JSON string holding a top-level array of Gaps.
FIXTURE_GAPS_JSON: str = json.dumps(_FIXTURE_GAPS, indent=2, ensure_ascii=False)
