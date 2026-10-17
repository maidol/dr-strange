## What breaks without this

<!-- The failure a reader could reproduce, not only what the patch does. One
or two sentences is plenty. -->

## How it was checked

<!-- Which tests, and whether `just gate` passed. If part of it could not be
checked locally, say which part and why — that is useful, not embarrassing. -->

- [ ] `just gate` passes
- [ ] Tests cover the change (inline `#[cfg(test)]`, beside the code)
- [ ] Docs updated in **both** `docs/en` and `docs/zh`, if behaviour changed
- [ ] One functional change per commit

<!-- Parsing a language? That belongs in the extensions repository, not here. -->
