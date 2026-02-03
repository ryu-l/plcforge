# Commercial licensing

plcforge is free under the [PolyForm Noncommercial License 1.0.0](LICENSE.md).
Commercial use needs a separate licence, and getting one is meant to take a short
email rather than a procurement cycle.

**Contact: alexanderhahn.br@gmail.com**

---

## Do I need a commercial licence?

The dividing line is the *purpose* of the use, not the size of the organisation
and not whether the code is redistributed.

### Free — no licence, no email, nothing to sign

- Learning automation, following a tutorial, building a portfolio project
- Research, experiments and publications at a university or public research institute
- Teaching: lectures, lab exercises, student projects
- Charities, public safety, health and environmental protection organisations,
  and government institutions — regardless of how they are funded
- Hobby projects and home automation with no commercial intent
- **Evaluating plcforge** inside a company to decide whether it fits, before it
  is used for actual work

### Requires a commercial licence

- Translating control code for a customer, a plant or an internal production system
- Shipping generated code, or the vendored runtime, inside a product you sell
- Running plcforge as part of a commercial service, hosted offering or CI pipeline
  that supports a commercial product
- Consulting, systems integration or engineering services that use plcforge to
  deliver client work
- Any use by a company where the purpose is commercial, including internal tooling
  that supports commercial operations

If you are unsure which side you are on, ask. Describing the use in three sentences
is usually enough, and an honest question has never been held against anyone.

---

## What a commercial licence covers

A commercial licence grants the rights the noncommercial terms withhold:

- use of plcforge for commercial purposes, without restriction on the number of
  translated projects
- redistribution of the vendored Python runtime and the C++ runtime header inside
  your own products
- use in internal and hosted build pipelines

It does **not** change anything about your own PLC sources or the translated output
derived from them. Those are yours; plcforge makes no claim over them either way.

---

## How to request one

Write to **alexanderhahn.br@gmail.com** with:

1. **Who** — the organisation and, roughly, its size
2. **What** — what you want to translate, and in which direction
3. **Where** — internal engineering only, or shipped in a product, or a hosted service
4. **Scale** — a rough sense of how many people or projects are involved

You will get a written answer with terms and pricing. Evaluation licences for a
proof of concept are straightforward to arrange, and academic collaborations are
usually just a note in the acknowledgements.

**Response time:** within five working days.

---

## Frequently asked

**We only want to try it out. Do we need a licence first?**
No. Evaluation is explicitly a permitted purpose. The licence is needed when you
start using it for actual commercial work.

**Does the licence apply to the code plcforge produces?**
No. The translated output is derived from your input and belongs to you. The one
exception is the runtime that `scaffold` vendors into a generated project — that
is plcforge's own code, and redistributing it commercially is what the commercial
licence covers.

**Can we get the source?**
You already have it. plcforge is source-available under the noncommercial terms;
the commercial licence changes the permitted purposes, not your access.

**We contribute a fix. Does that change our licensing position?**
No — but thank you, and see [CONTRIBUTING.md](CONTRIBUTING.md). Contributions are
appreciated and are not a substitute for a licence, nor a condition of getting one.

**Is there an open-source exception?**
If you are building something that is itself noncommercial and openly licensed,
you are already covered by the noncommercial terms. If it is an open-source project
with commercial backing, write and describe it — that is a conversation worth having.

---

## Legal note

The noncommercial licence disclaims warranties, and a commercial licence does not
turn plcforge into a safety-qualified tool. Generated code is a translation and
must be reviewed and tested like any other change to a control system. Nothing here
is approved for use in safety instrumented functions.
