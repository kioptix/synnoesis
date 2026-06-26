# Synnoesis

> **syn** (together) + **noēsis** (thinking) — *"minds thinking together."*

A personal-assistant system where multiple AI agents confer, chat, and
collaborate to solve problems together.

Built and published by **[Groupe Kioptix Inc.](https://synnoesis.dev)**

---

## Status

🚧 Early development. Watch this space.

## License

Synnoesis is released under the **Apache License 2.0** — free to use, modify, and
distribute, **including commercially**. See [`LICENSE`](LICENSE).

## Usage & Compliance

Synnoesis is an orchestration layer. It routes to model providers — Anthropic's
Claude and others — **directly or through gateways like OpenRouter**, always
using **your own credentials**. It bundles **no API keys** and grants no model
access of its own.

- **Personal / single-user use (Anthropic):** a Claude subscription via the
  official Claude CLI is fine.
- **Serving other users or customers (multi-user / commercial):** use **your own
  API key** (Anthropic's Commercial Terms, or a commercial gateway such as
  OpenRouter) — *not* a personal subscription. Routing other users through
  personal subscription credentials violates Anthropic's terms.
- **You're responsible for the terms of whichever providers and gateways you
  use** — e.g. [Anthropic](https://www.anthropic.com/legal),
  [OpenRouter](https://openrouter.ai/terms). Provider terms flow through a gateway:
  using Claude via OpenRouter still binds you to Anthropic's terms.
- **Export & eligibility:** model access is subject to each provider's geographic
  availability and to applicable export-control / sanctions laws (U.S. export
  controls restricted some frontier models for foreign nationals in 2026; gateways
  like OpenRouter likewise forbid circumventing geo-restrictions). You're
  responsible for ensuring you and your users are eligible to access the models you
  use. Synnoesis is model-agnostic and doesn't guarantee any specific model's
  availability.
- **Data path:** prompts/outputs routed through a gateway (e.g. OpenRouter) pass
  through that gateway and the downstream provider, each with its own retention
  and privacy terms. Keep gateway prompt-logging off for sensitive data.

**Bottom line:** use Synnoesis commercially all you like — just bring your own
keys, so you stay compliant and protected.

## Trademarks

"Kioptix", "Synnoesis", and associated logos are trademarks of Groupe
Kioptix Inc. and are **not** licensed under Apache 2.0 (see §6). Use the software
freely; please don't use the names or logos to brand your own product or imply
endorsement.
