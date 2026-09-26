# T08-4 Production Acceptance

Date: 2026-09-26

## Accepted Baseline

- Implementation commit: `fa9176d`
- Production Core image: `fa9176d`
- Production database revision: `20260925_0006`
- Production default provider: `cloud`
- Public and local production health: PASS

## Cloud To Local Inference

- Cloud-to-local durable inference job transport: PASS
- Worker `home-5090-01`: PASS
- Native RTX 5090 execution through llama.cpp: PASS
- Model: `Qwen/Qwen3.5-9B`
- Retry 2 positive acceptance: PASS
- Result-to-chat mapping: PASS
- Trace linkage: PASS

## Failure And Recovery

- Managed model shutdown transitioned the Worker to `DEGRADED`: PASS
- Automatic fallback is not enabled and was not observed: PASS
- Recovery after restoring llama.cpp completed without restarting the Worker: PASS
- Production cleanup: PASS

## Marker Compliance Observations

- Attempt 1 marker compliance failed.
- Recovery marker compliance failed.

These observations are model instruction compliance only, not Cloud-to-local transport failures. Cloud verification confirmed durable job success, execution by `home-5090-01`, non-empty structured results, persisted assistant equality with `structured_output.speech`, result-to-chat mapping, and trace linkage.

## Production Cleanup

- Temporary Smoke Core removed: PASS
- Port `1516` closed: PASS
- Production Main Core, Nginx, production provider configuration, and database schema were not changed for the acceptance runs.

No service token, API key, SSH private key, complete prompt, or production password is recorded in this document.