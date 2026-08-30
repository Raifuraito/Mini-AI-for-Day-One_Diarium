import sys

TARGET = sys.argv[1]

with open(TARGET, "r", encoding="utf-8") as f:
    src = f.read()

old = """Nothing here requires a subscription. What it costs is small, pay-as-you-go usage of Anthropic's API (the company that makes Claude, the AI model this uses to answer questions and extract tags):

- **Setting up and running the app itself**: free. Flask, the vector database, and the web page all run locally with no cost.
- **Embedding your journal into the local database**: free. This uses a local model, not an API call.
- **Extracting tags** (what powers Max Recall, see below) -- **entirely optional, off by default**: a small fraction of a cent per entry, if and when you turn it on. Tagging roughly 1,600 entries, for example, costs on the order of $1-3 total, one time -- after that, new entries get tagged automatically as you add them, a few fractions of a cent each. If you never turn tagging on, this cost simply never happens; everything else in the app works the same either way.
- **Asking a question**: roughly $0.001-0.004 (a tenth to four tenths of a cent) per question in typical use. This scales with how much context a question needs, not with how big your journal is -- a normal question and a "give me everything about X" Max Recall question cost differently, but neither gets more expensive just because you've been journaling for ten years instead of one.

Realistically, for one person's personal use, this comes out to somewhere between a few cents and a few dollars a month depending on how often you use it. You'll need to add a small amount of credit to an Anthropic API account to use it at all -- there's no free tier that avoids this step, but there's also no minimum spend or subscription; you're billed only for what you actually use."""

new = """Nothing here requires a subscription. This project works with several AI providers now (Anthropic, OpenAI, Google, Mistral, or a local Ollama model -- pick one in the setup wizard's Step 2), and what it costs is small, pay-as-you-go usage of whichever one you choose.

- **Setting up and running the app itself**: free. Flask, the vector database, and the web page all run locally with no cost.
- **Embedding your journal into the local database**: free. This uses a local model, not an API call.
- **Extracting tags** (what powers Max Recall, see below) -- **entirely optional, off by default**: a small amount per entry, using the cheapest model for whichever provider you picked. See the reference table just below for what tagging a first-time journal of about 1,000 entries actually costs by provider -- well under a dollar either way, but the exact number varies more than you'd guess between providers.
- **Asking a question**: cost scales with how much context a question needs, not with how big your journal is -- a normal question and a "give me everything about X" Max Recall question cost differently, but neither gets more expensive just because you've been journaling for ten years instead of one. Ballpark: a few tenths of a cent per question with OpenAI, Google, or Mistral's cheap-tier models; roughly $0.005-$0.02 per question with Claude (Haiku or Sonnet, depending which you picked); nothing with Ollama beyond your own electricity.

Realistically, for one person's personal use, this comes out to somewhere between a few cents and a few dollars a month depending on how often you use it and which provider you picked. Every provider except Ollama needs a small amount of prepaid API credit -- there's no free tier that avoids this step, but there's also no minimum spend or subscription; you're billed only for what you actually use.

### Reference: tagging a 1,000-entry journal from scratch

A concrete "how much will this actually cost me" number, instead of a vague "fraction of a cent" -- this is what a **first-time tagging backfill of about 1,000 journal entries** costs, by provider:

| Provider (cheapest/tagging model) | Input | Output | ~1,000 entries |
|---|---|---|---|
| OpenAI (`gpt-4o-mini`) | $0.15 / M tokens | $0.60 / M tokens | **~$0.05** |
| Google (`gemini-2.0-flash`) | $0.15 / M tokens | $0.60 / M tokens | **~$0.05** |
| Mistral (`mistral-small-latest`) | $0.15 / M tokens | $0.60 / M tokens | **~$0.05** |
| Anthropic (`claude-haiku-4-5`) | $1.00 / M tokens | $5.00 / M tokens | **~$0.35-$0.40** |
| Ollama (local model) | $0 | $0 | **$0** |

Token pricing above is current as of August 2026 (Anthropic's own pricing docs; independent pricing trackers for the others) -- check your provider's pricing page before budgeting against this, since rates do change over time.

Worth knowing: Claude Haiku is Anthropic's *cheapest* model, but it's still roughly 7-8x pricier per token than the cheap tier from OpenAI, Google, or Mistral. So if minimizing tagging cost specifically is your priority, Haiku isn't the cheapest option overall -- just the cheapest *Anthropic* one. All of these numbers are small enough (under a dollar for 1,000 entries either way) that this mostly matters if you're tagging a much larger journal, or you're just curious.

**How this is estimated**, so you can recompute it for your own journal size or if pricing changes: tagging batches `TAG_BATCH_SIZE` entries (15 by default) per API call, with each entry's text capped at `TAG_SNIPPET_CHARS` (800 characters) plus a short fixed instruction prompt. That works out to roughly 3,300 input tokens and 500 output tokens per batch of 15 entries -- about 67 batches for 1,000 entries, totaling roughly 220,000 input tokens and 33,000 output tokens. Multiply those by whichever provider's per-million-token rate above to get the total. Real cost is usually a bit lower than this estimate, since it assumes every entry uses the full 800-character cap -- shorter entries cost less."""

count = src.count(old)
if count != 1:
    print(f"MATCH_COUNT={count} -- ABORTING, expected exactly 1")
    sys.exit(1)

src = src.replace(old, new)

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(src)

print("README cost section updated successfully")
