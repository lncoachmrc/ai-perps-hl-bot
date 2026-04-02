# AI Perps Hyperliquid Bot

Prima versione pulita della repo per un bot futures/perps su Hyperliquid con:

- **LLM finale**: `gpt-5.4-mini`
- **Expert layer**: Quant + Prophet + News
- **Risk gate** con veto assoluto
- **Deploy** su Railway tramite Dockerfile
- **Mercati**: BTC/USDC, ETH/USDC, SOL/USDC
- **Balance iniziale di riferimento**: 1000 USDC

## Filosofia

L'LLM non legge dati grezzi e non controlla liberamente size, leva o limiti.

Il flusso è:

1. Raccolta stato mercato/account/news
2. Expert layer produce evidenze strutturate
3. `DecisionDossier` compatto
4. Judge LLM sceglie tra azioni ammesse
5. `RiskGate` approva, riduce o blocca
6. Execution layer invia gli ordini
7. Journal salva tutto

## Modalità iniziale

La repo parte in **dry-run** di default. Nessun ordine live parte finché non imposti esplicitamente:

- `DRY_RUN=false`
- chiavi Hyperliquid valide
- OpenAI API key

## Avvio locale

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## Deploy Railway

- usa il `Dockerfile` in root
- entrypoint: `ops/offset_safe_entrypoint.sh`
- porta health: `PORT` (default 8080)

## Struttura principale

```text
app/
  main.py                # bootstrap runtime
  settings.py            # env/config centralizzata
  strategy/orchestrator.py
  llm/judge.py
  risk/risk_gate.py
  exchange/hyperliquid/
  experts/
  services/
```

## Stato della v1

Questa repo è uno **skeleton operativo**: parte, espone healthcheck, costruisce il flow logico e tiene separati i moduli chiave.
I client reali Hyperliquid/news/LLM sono pronti per essere estesi nel passo successivo.
