import sys

TARGET = sys.argv[1]

with open(TARGET, "r", encoding="utf-8") as f:
    src = f.read()

patches = []

# --- Patch 1: remove the OLD cost section from its current spot (between
# #limitations and #privacy), collapsing the double <hr> back to one. ---
patches.append((
"""  <hr class="divider">

  <section class="tight">
    <div class="wrap">
      <div class="section-head prose">
        <span class="eyebrow">What it costs</span>
        <h2>Pay-as-you-go, to Anthropic directly &mdash; nothing to this project.</h2>
      </div>
      <div class="cost-table">
        <div class="cost-row">
          <div class="what">Running the app, embedding entries<small>Local model, no API calls involved</small></div>
          <div class="amount">Free</div>
        </div>
        <div class="cost-row" id="tagging-cost">
          <div class="what">Tagging your existing journal<small>Optional, and off by default &mdash; the setup wizard asks before turning it on. Roughly 1,600 entries as a reference point.</small></div>
          <div class="amount">$1&ndash;3 total</div>
        </div>
        <div class="cost-row">
          <div class="what">Asking a question<small>Scales with the question, not your journal's size</small></div>
          <div class="amount">$0.001&ndash;0.004</div>
        </div>
      </div>
      <p class="cost-note">Tagging is what Max Recall (above) needs to work &mdash; without it, the journal still answers questions perfectly well, just via similarity search instead of a guaranteed-complete topic match.</p>
    </div>
  </section>

  <section id="privacy">""",
"""  <hr class="divider">

  <section id="privacy">"""
))

# --- Patch 2: insert the NEW cost section before #how (the setup steps),
# so the disclaimer is seen before anyone is asked to set anything up. ---
patches.append((
"""  <hr class="divider">

  <section id="how" class="tight">""",
"""  <hr class="divider">

  <section id="cost" class="tight">
    <div class="wrap">
      <div class="section-head prose">
        <span class="eyebrow">What it costs</span>
        <h2>Not free to run &mdash; but we don't take a cut.</h2>
        <p class="lede"><strong>This project itself is free, open-source, and we don't earn anything from it.</strong> But using it isn't free: asking questions and tagging entries both call an AI provider's API, and that provider bills <em>you</em> directly for it &mdash; Anthropic, OpenAI, Google, or Mistral, whichever you pick in the setup wizard. Every one of those dollars goes to them, none of it to us. We'd rather say that plainly here than let "open-source" be mistaken for "no cost to run."</p>
      </div>

      <div class="cost-table">
        <div class="cost-row">
          <div class="what">Running the app, embedding entries<small>Local model, no API calls involved</small></div>
          <div class="amount">Free</div>
        </div>
        <div class="cost-row" id="tagging-cost">
          <div class="what">Tagging your journal <em>(powers Max Recall)</em><small>Optional, off by default &mdash; scales with how many entries you have. Full per-provider table below.</small></div>
          <div class="amount"><strong>~$0.05&ndash;$0.40</strong><small>per 1,000 entries*</small></div>
        </div>
        <div class="cost-row">
          <div class="what">Asking a question<small>Scales with the question, not your journal's size</small></div>
          <div class="amount"><strong>~$0.0005&ndash;$0.02</strong><small>per question*</small></div>
        </div>
      </div>
      <p class="cost-note">* Paid directly to whichever AI provider you choose &mdash; never to us. Tagging is what Max Recall needs to work; without it, the journal still answers questions perfectly well, just via similarity search instead of a guaranteed-complete topic match.</p>

      <details class="tech-details">
        <summary>Technical details</summary>
        <div class="tech-content">
          <p>Tagging a first-time journal of about 1,000 entries, by provider (current as of August 2026 &mdash; check your provider's own pricing page, since rates change):</p>
          <ul>
            <li><strong>OpenAI</strong> (gpt-4o-mini): $0.15 / $0.60 per M tokens (in/out) &mdash; <strong>~$0.05</strong></li>
            <li><strong>Google</strong> (gemini-2.0-flash): $0.15 / $0.60 per M tokens &mdash; <strong>~$0.05</strong></li>
            <li><strong>Mistral</strong> (mistral-small): $0.15 / $0.60 per M tokens &mdash; <strong>~$0.05</strong></li>
            <li><strong>Anthropic</strong> (claude-haiku-4-5): $1.00 / $5.00 per M tokens &mdash; <strong>~$0.35&ndash;$0.40</strong></li>
            <li><strong>Ollama</strong> (local model, no API key): free &mdash; <strong>$0</strong></li>
          </ul>
          <p>Claude Haiku is Anthropic's cheapest model, but it's still roughly 7&ndash;8x pricier per token than the cheap tier from OpenAI, Google, or Mistral &mdash; so if minimizing this specific cost is your priority, Haiku isn't actually the cheapest option overall, just the cheapest <em>Anthropic</em> one. Full methodology, and the per-question cost math, are in <a href="https://github.com/Raifuraito/journal-rag#readme">the README</a>.</p>
        </div>
      </details>
    </div>
  </section>

  <hr class="divider">

  <section id="how" class="tight">"""
))

# --- Patch 3: the "paste in an API key" setup step named only Anthropic --
# now stale since multiple providers are supported. Small, same-topic fix. ---
patches.append((
"""            <p>A couple of minutes at console.anthropic.com. The setup page can install the required packages for you too &mdash; entirely optional, one click.</p>""",
"""            <p>A couple of minutes with whichever AI provider you choose &mdash; Anthropic, OpenAI, Google, or Mistral. The setup page can install the required packages for you too &mdash; entirely optional, one click.</p>"""
))

for i, (old, new) in enumerate(patches, start=1):
    count = src.count(old)
    if count != 1:
        print(f"PATCH {i}: MATCH_COUNT={count} -- ABORTING, expected exactly 1")
        sys.exit(1)
    src = src.replace(old, new)
    print(f"PATCH {i}: OK (1 match, applied)")

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(src)

print("ALL PATCHES APPLIED")
