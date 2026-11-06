# ADR 0002: Banned-phrase filter as well as prompt rules

- **Status:** Accepted
- **Recorded:** 2026-11-06, after the choice was made and in use

## Context

When describing bronze text columns, the model invented details the data didn't show, such as calling an ordinary string "encoded" or "hashed". Prompt rules forbidding this didn't stop it.

An early version of the filter also had a flaw. If the retry still contained a banned phrase, it logged the failure and stored the text anyway, so banned text could reach the dictionary, and it did.

## Options

1. **Prompt rules only.** Simple and free, but nothing checks the result.
2. **Prompt rules plus a phrase filter.** Check the text in code, retry once with the phrase named, and refuse to store it if the retry also fails.
3. **A second model call to verify every description.** Could catch more kinds of invention, but it doubles the calls and is itself unverified.

## Decision

Use option 2, for bronze `STRING` and `VARCHAR` columns only. A second failure raises an error, so nothing is stored. The column stays pending, the run reports a failure, and the next run tries again. Human approval stays the real check on quality.

## Trade-offs

**Cost.** Each retry is one more generation call, about $0.0007 at the measured average. Option 3 would add a call for all 80 columns, roughly doubling the $0.054 cost of a full build.

**Accuracy**

- The filter only catches its 11 listed phrases, on 17 of the 80 columns. It can't catch a new way of inventing a detail.
- It's a plain text match, so it can reject acceptable text. "Rather than" is on the list, and it can appear in a perfectly accurate sentence.
- In the measured run of all 80 columns, it never fired: 0 retries and 0 rejections. It's a backstop rather than something the current prompt depends on. That run also means the retry path is covered by [unit tests](../../tests/test_guardrail.py) but hasn't been seen working against the live API.
- It doesn't replace review: 74 of the 80 generated descriptions were corrected afterwards, for problems a phrase list can't detect, such as claims about the data that turned out to be untrue.
- It only covers descriptions. Chatbot answers have no such check. They rely on the prompt alone, and 4 of 32 measured answers still added unsupported details.

## When to revisit

Add a phrase when review finds a repeated pattern. Consider option 3 if reviewing drafts becomes the bottleneck, and measure whether it catches enough to justify its cost.
