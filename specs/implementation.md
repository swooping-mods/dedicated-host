# Dedicated Host Exploration: behavior and implementation

Describes the exported implementation **0.1.0**. The linked source and metadata define this version; gameplay qualification is a separate requirement.

## Behavior

This Python companion explores headless hosting; it is not an implemented dedicated server. inspect fingerprints an explicitly selected game and reports known hosting APIs and unresolved architecture. prepare creates a new, separately owned game-only experiment. run-client explicitly launches a headless client through a selected Wine executable with a unique fresh prefix. It never infers server readiness from batch/nographics flags or a running PID.

## State and lifecycle

Preparation manifests, game hashes, process IDs and client logs identify the experiment. Source game/profile data is read-only. Preparation rejects identity mismatches, loaders and reparse paths; launch strips inherited automation and uses the experiment-owned profile/prefix. It does not import personal saves.

## Integration and configuration

No additional feature-mod runtime dependency is declared. Included shared headers are source reuse, not extra DLL dependencies.

The [module metadata](../source/mods/dedicated-host/mod.json) owns the version and declared integration contract. This companion runs separately through its Python entry point; it does not inject a native gameplay DLL.

## Implementation sources

- [dedicated_host/__main__.py](../source/mods/dedicated-host/dedicated_host/__main__.py)

## Verification boundary

Clientless session bootstrap, supported unattended Steam authentication, remote joins/authority, durable save recovery and native Windows profile isolation remain unresolved. This companion must not be advertised as a working dedicated host.

`python3 build.py --check` verifies the exported source hashes. It does not execute the mod or qualify the behaviors above. Installation and independent build prerequisites are in the [README](../README.md).
