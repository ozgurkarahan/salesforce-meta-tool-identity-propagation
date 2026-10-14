# Product

<!-- impeccable:product-schema 1 -->

> Facts confirmed by the owner in the 2026-10-14 planning session are unmarked.
> Facts marked *(inferred)* come from the repository and the brief; the owner
> was unavailable for the init round and should confirm them.

## Platform

web

## Users

- *(inferred)* **Primary:** enterprise decision-makers and architects (business
  and IT leaders, solution architects, Microsoft field and partner teams). They
  are evaluating how to give employees one AI assistant that can act across
  their systems of record without weakening security.
- *(inferred)* **Secondary:** developers and platform engineers who deploy,
  extend or adapt the reference implementation.

## Product Purpose

Customer 360 Assistant is an open-source (MIT) reference implementation of a
strategy: **build the engagement layer once, and let one agent act across all
your systems of record**. People ask in Microsoft Teams. One Microsoft Foundry
agent routes each request to the right tools, and every action runs as the
signed-in user with that user's own permissions in each system.

*(inferred)* Success means a visitor grasps the strategy within a minute, then
watches the story video, inspects the architecture, or deploys the sample.

## Positioning

The differentiator is one agent and many systems, carrying the user's own
identity from end to end. Per-system meta-tools discover objects and fields at
runtime instead of defining one tool per object. An Azure API Management
identity gateway exchanges the user's Entra ID sign-in for that same user's
token in each system (on-behalf-of). Each system therefore enforces its own
permissions, and no shared credential reads or writes business data.

## Operating Context

- Demonstrated with Salesforce (this repository, 7 tools) and ServiceNow
  (companion repository `snow-meta-tool`, 3 tools) on seeded demo data. The
  scenarios are in `docs/TEST_PROMPTS_360.md`.
- Runs on Azure: Bicep + `azd`, Container Apps, API Management, Microsoft
  Foundry. A Teams app package is published to the org catalog; Microsoft 365
  Copilot is reached through Foundry publishing; a web chat app is included.
- Part of the author's Identity Propagation series (LinkedIn articles and the
  companion repositories `propagate-id-entra` and `snow-meta-tool`). The project
  page is linked from `ozgurkarahan.com/customer-360/`.

## Capabilities and Constraints

- **Included:** Salesforce meta-tool server; APIM three-phase on-behalf-of
  policies; Foundry agents (`salesforce-assistant`, `customer360-assistant`);
  web chat app; Teams packaging; infrastructure as code; Salesforce org setup
  scripts; E2E tests.
- **Not included:** Jira, SAP, Workday or any other connector. These may only
  be shown as "same pattern, add your own".
- **Limits:** a reference implementation, not an audited production product.
  One-time consent is the intended experience, but a Foundry platform issue can
  ask users to consent again after about an hour. Deployment needs prerequisites
  (certificate, Salesforce org setup, Entra app registration, identity mapping).
- **Terms:** engagement layer, orchestration, action layer, systems of record,
  identity gateway, meta-tools, on-behalf-of (OBO).

## Brand Commitments

- Name: **Customer 360 Assistant**. Repository:
  `salesforce-meta-tool-identity-propagation`.
- Headline: **"Build the engagement layer once. Let one agent act across all
  your systems of record."**
- Identity message: **every action runs as the signed-in user, with their own
  permissions in each system** (no shared service account in the data path).
- Visual world: the Build style of the story video (`video/src/theme.ts`,
  `video/src/fonts.ts`): white, Azure blue, amber for the user's identity,
  Bricolage Grotesque, Instrument Sans and IBM Plex Mono.
- *(inferred)* Voice: precise, pragmatic, honest about scope; English.

## Evidence on Hand

- The 2:40 story video (Remotion source in `video/`; animated mock-ups with
  demo data; the MP4 is committed under `site/media/`).
- Architecture and sequence diagrams (`docs/diagrams/`), the deep dive, the
  installation guide, test prompts, the E2E test and the v2 CHANGELOG.
- LinkedIn articles "From Theory to Production: Salesforce Meta-Tool &
  Identity Propagation" and "The Meta-Tool Pattern Applied to Enterprise".
- **Absent; must not be fabricated:** customer logos, testimonials, production
  deployments, benchmarks, pricing, and token or timing figures that have not
  been re-measured.

## Product Principles

1. Lead with the business outcome and keep the technical path one click away.
2. Every claim matches the code; demos, mock-ups and systems that are not
   included are labelled as such.
3. The user's identity is the product. Never imply shared credentials or
   super-user access.
4. Extensibility is a pattern, not a promise.

## Accessibility & Inclusion

Target WCAG 2.2 AA on the project page. Every diagram and the silent video have
a text alternative.
