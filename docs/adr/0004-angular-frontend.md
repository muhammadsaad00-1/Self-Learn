# 0004. Angular instead of Next.js for the frontend

- Status: accepted
- Date: 2026-10-06

## Context
The spec baseline (01 §9) lists Next.js. The project owner prefers Angular.

## Decision
The frontend is an Angular (TypeScript, standalone components) single-page app in `frontend/`. It talks to the FastAPI backend only through the versioned REST API (`/v1`). There is no server-side rendering, because the dashboard is behind a login and SEO does not matter.

## Consequences
- In production the SPA is static files on S3 + CloudFront: cheap and free-tier friendly.
- The API contract is the boundary. We will generate TypeScript client types from FastAPI's OpenAPI schema so the two sides cannot drift.

## Alternatives considered
- Next.js (spec default): the owner's preference wins, and nothing in the product needs SSR.
