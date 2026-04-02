# Architecture

## Flow

1. Hyperliquid/account state enters the system.
2. Quant expert, Prophet expert and News expert each produce structured evidence.
3. The `DecisionDossier` is built for a single asset.
4. The Judge LLM returns one allowed action.
5. The `RiskGate` can approve, resize or block it.
6. The exchange client executes or simulates.
7. The journal persists the full cycle.

## Current scope

- single service runtime
- dry-run by default
- journal on JSONL
- health server on `/health`
