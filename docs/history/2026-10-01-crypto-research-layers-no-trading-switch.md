# The research layers open no trading switch (crypto refactor plan §M-2e)

- **What it adds:** a rule in `tests/test_mvp_runtime_crypto_layers.py`, tests only. No strategy or
  decision module may name any of the following, at module level or inside a function:
  - the live or the signed testnet trading switch, its opt-in value, its flags or its provider;
  - the switch's environment variable as a string (`MVP_LIVE_TRADING`, `MVP_TESTNET_TRADING`);
  - a selector built on a switch, derived from the lane's source.
- **Why:** the layer order already keeps research from importing the modules that send. It did not
  keep research from opening the switch itself, because `vocabulary` (foundation) holds the live
  switch's names and `safety_gate.select_env_gated` builds whatever it is handed. The paper store's
  switch (`MVP_PAPER_TRADING`) is not a trading switch and is not affected.
- **Today:** the rule finds nothing to forbid. It holds the line, and three pins keep it honest:
  - a synthetic lane with one module per form of reaching a switch;
  - a check that the derived selectors include the order adapter, the venue reader, the testnet
    adapter and the live stores;
  - three changes to `forward_book`, each of which the rule caught: a function-local import of the
    switch, an environment read by value, and a call to a live store selector.
