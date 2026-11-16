"""Prices of the Gemini models, in dollars per million tokens (standard tier, text input).

Source: https://ai.google.dev/gemini-api/docs/pricing, read on 2026-11-16. Output tokens include thinking tokens.
A model missing here has no cost estimate (None) rather than a guessed one.
"""

PRICES = {
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
}


def cost_usd(model, input_tokens, output_tokens):
    price = PRICES.get(model)
    if price is None:
        return None
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1e6
