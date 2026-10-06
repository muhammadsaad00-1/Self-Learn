# 0001. Modular monolith with domain modules

- Status: accepted
- Date: 2026-10-06

## Context
The spec (02 §3) defines 11 domain modules. Microservices from day one would add network calls, distributed transactions and deploy overhead before we know where the real load and ownership boundaries are. The spec itself says: "No microservice split without an explicit operational reason."

## Decision
One deployable Python package, `agentflow`, run as two processes from the same image: the API (FastAPI) and the worker (queue consumer). Each domain module is a subpackage with a public service interface. Modules never import another module's ORM models or query its tables directly.

## Consequences
- One repo, one image, one migration history, and simple local development.
- Boundaries stay real only if the import rule is enforced. We will add an import-linter contract once there are 2+ modules (Phase 1).
- Any module can later be extracted into a service with an explicit reason, because its interface already exists.

## Alternatives considered
- Microservices per module: high operational cost, no measured need.
- Unstructured monolith: fast now, but the modules would turn into spaghetti and could never be extracted.
